"""Webhook API — 消息收发中间件 (SQLite 版本)

功能：
- 入站 webhook（接收方推送消息进来）
- 出站 webhook（轮询取消息发给接收方）
- 支持文本/图片/视频/音频/文件等全类型媒体
- Bearer Token 鉴权
- OpenAI 兼容接口（/v1/chat/completions）
- SSE 实时推流（GET /stream/{name}）
"""

import asyncio
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, UploadFile, HTTPException, Header, Query, Path as FastAPIPath, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# ─── 配置 ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
STORAGE_DIR = Path(os.getenv("WEBHOOK_STORAGE_DIR", "/app/storage"))
if not STORAGE_DIR.exists():
    STORAGE_DIR = BASE_DIR / "storage"
UPLOADS_DIR = STORAGE_DIR / "uploads"
DB_PATH = STORAGE_DIR / "webhook.db"

API_KEY = os.getenv("WEBHOOK_API_KEY", "")
POLL_INTERVAL = int(os.getenv("WEBHOOK_POLL_INTERVAL", "2"))
MESSAGE_RETENTION_DAYS = int(os.getenv("WEBHOOK_MSG_RETENTION", "7"))
MAX_UPLOAD_SIZE = int(os.getenv("WEBHOOK_MAX_UPLOAD_MB", "50")) * 1024 * 1024  # 默认 50MB

# ─── 目录初始化 ─────────────────────────────────────────────────────────────

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ─── SQLite 数据库 ──────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _init_db():
    conn = _get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            request_id TEXT,
            type TEXT NOT NULL,
            content TEXT,
            media_url TEXT,
            name TEXT,
            direction TEXT NOT NULL DEFAULT 'inbound',
            timestamp REAL NOT NULL,
            metadata TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_name ON messages(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_direction ON messages(direction)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_request_id ON messages(request_id)")
    conn.commit()
    conn.close()


_init_db()


# ─── 数据模型 ───────────────────────────────────────────────────────────────

class Message(BaseModel):
    id: str
    request_id: Optional[str] = None
    type: str
    content: Optional[str] = None
    media_url: Optional[str] = None
    name: Optional[str] = None
    direction: str = "inbound"
    timestamp: float
    metadata: dict = {}


class WebhookPayload(BaseModel):
    type: str = "text"
    content: Optional[str] = None
    caption: Optional[str] = None
    media_url: Optional[str] = None
    metadata: dict = {}


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "webhook"
    messages: list[ChatMessage]
    name: Optional[str] = None
    request_id: Optional[str] = None


class ChatCompletionChoice(BaseModel):
    index: int
    message: dict
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


# ─── 工具函数 ───────────────────────────────────────────────────────────────

def _save_message(msg: Message) -> str:
    mid = msg.id or str(uuid.uuid4())
    msg.id = mid
    conn = _get_db()
    conn.execute(
        """INSERT OR REPLACE INTO messages
           (id, request_id, type, content, media_url, name, direction, timestamp, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mid, msg.request_id, msg.type, msg.content, msg.media_url,
         msg.name, msg.direction, msg.timestamp,
         json.dumps(msg.metadata, ensure_ascii=False) if msg.metadata else None)
    )
    conn.commit()
    conn.close()
    return mid


def _list_messages(name: Optional[str] = None, limit: int = 50,
                   direction: Optional[str] = None,
                   since: Optional[float] = None,
                   request_id: Optional[str] = None) -> list[Message]:
    conn = _get_db()
    conditions = []
    params = []

    if name:
        conditions.append("name = ?")
        params.append(name)
    if direction:
        conditions.append("direction = ?")
        params.append(direction)
    if since:
        conditions.append("timestamp > ?")
        params.append(since)
    if request_id:
        conditions.append("request_id = ?")
        params.append(request_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM messages {where} ORDER BY timestamp DESC LIMIT ?",
        [*params, limit]
    ).fetchall()
    conn.close()

    results = []
    for row in rows:
        results.append(Message(
            id=row["id"],
            request_id=row["request_id"],
            type=row["type"],
            content=row["content"],
            media_url=row["media_url"],
            name=row["name"],
            direction=row["direction"],
            timestamp=row["timestamp"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        ))
    return results


def _clean_old_messages() -> int:
    cutoff = time.time() - MESSAGE_RETENTION_DAYS * 86400
    conn = _get_db()
    cursor = conn.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def _check_auth(authorization: Optional[str] = Header(None)):
    if not API_KEY:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = authorization[len("Bearer "):]
    if token != API_KEY:
        raise HTTPException(403, "Invalid API key")


def _detect_type_from_ct(ct: str) -> str:
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("video/"):
        return "video"
    if ct.startswith("audio/"):
        return "audio"
    return "file"


async def _parse_message_payload(request) -> dict:
    ct = (request.headers.get("content-type") or "").lower()
    payload = {"type": "text", "content": None, "caption": None,
               "media_url": None, "media": None, "metadata": {}}

    if "application/json" in ct:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if isinstance(body, dict):
            payload["type"] = body.get("type") or "text"
            payload["content"] = body.get("content")
            payload["caption"] = body.get("caption")
            payload["media_url"] = body.get("media_url") or body.get("url")
            payload["metadata"] = body.get("metadata") or {}
        return payload

    try:
        form = await request.form()
    except Exception:
        form = {}
    payload["type"] = form.get("type") or "text"
    payload["content"] = form.get("content")
    payload["caption"] = form.get("caption")
    payload["media_url"] = form.get("media_url") or form.get("url")
    media = form.get("media")
    if media is not None and getattr(media, "filename", None):
        payload["media"] = media
    return payload


def _build_message(name: Optional[str], direction: str, p: dict,
                   request_id: Optional[str] = None) -> Message:
    msg_type = p["type"] or "text"
    media_url = p["media_url"]

    if p["media"] is not None:
        rel_url, content_type = _read_upload(p["media"])
        media_url = rel_url
        if msg_type == "text":
            msg_type = _detect_type_from_ct(content_type)

    if media_url and msg_type == "text":
        low = media_url.lower().split("?")[0]
        if low.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")):
            msg_type = "image"
        elif low.endswith((".mp4", ".mov", ".webm", ".mkv", ".avi")):
            msg_type = "video"
        elif low.endswith((".mp3", ".wav", ".ogg", ".m4a", ".amr", ".silk")):
            msg_type = "audio"
        elif media_url:
            msg_type = "file"

    return Message(
        id=str(uuid.uuid4()),
        request_id=request_id,
        type=msg_type,
        content=p["caption"] or p["content"],
        media_url=media_url,
        name=name,
        direction=direction,
        timestamp=time.time(),
        metadata=p["metadata"] or {},
    )


def _read_upload(file: UploadFile) -> tuple[str, str]:
    ext = Path(file.filename or "file").suffix or ".bin"
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOADS_DIR / fname

    # 流式读取，限制大小
    content = b""
    total = 0
    while True:
        chunk = file.file.read(8192)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_SIZE:
            dest.unlink(missing_ok=True)
            raise HTTPException(413, f"File too large, max {MAX_UPLOAD_SIZE // 1024 // 1024}MB")
        content += chunk

    dest.write_bytes(content)
    return f"/uploads/{fname}", file.content_type or "application/octet-stream"


def _stream_file(path: Path, content_type: str):
    if not path.exists():
        raise HTTPException(404, "File not found")

    size = path.stat().st_size
    if size == 0:
        return JSONResponse({"error": "empty file"}, status_code=204)

    def gen():
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                yield chunk

    return StreamingResponse(gen(), media_type=content_type)


# ─── FastAPI 应用 ───────────────────────────────────────────────────────────

app = FastAPI(
    title="Webhook API",
    description="消息收发中间件 — SQLite 存储、OpenAI 兼容接口、SSE 推流",
    version="1.2.0",
)


# ─── 健康检查 ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "api_key_set": bool(API_KEY)}


# ─── 入站 Webhook ───────────────────────────────────────────────────────────

@app.post("/webhook/inbound")
async def inbound_no_name(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    _check_auth(authorization)
    p = await _parse_message_payload(request)
    msg = _build_message(None, "inbound", p)
    return {"status": "ok", "message_id": _save_message(msg)}


@app.post("/webhook/inbound/{name}")
async def inbound(
    request: Request,
    name: str = FastAPIPath(..., description="发送方标识"),
    authorization: Optional[str] = Header(None),
    request_id: Optional[str] = Query(default=None, description="请求 ID 用于关联回复"),
):
    _check_auth(authorization)
    p = await _parse_message_payload(request)
    msg = _build_message(name, "inbound", p, request_id=request_id)
    return {"status": "ok", "message_id": _save_message(msg), "request_id": msg.request_id}


# ─── 出站 Webhook ───────────────────────────────────────────────────────────

@app.post("/webhook/outbound")
async def outbound_write_no_name(
    request: Request,
    authorization: Optional[str] = Header(None),
    request_id: Optional[str] = Query(default=None),
):
    _check_auth(authorization)
    p = await _parse_message_payload(request)
    msg = _build_message(None, "outbound", p, request_id=request_id)
    return {"status": "ok", "message_id": _save_message(msg)}


@app.post("/webhook/outbound/{name}")
async def outbound_write(
    request: Request,
    name: str = FastAPIPath(..., description="接收方标识"),
    authorization: Optional[str] = Header(None),
    request_id: Optional[str] = Query(default=None),
):
    _check_auth(authorization)
    p = await _parse_message_payload(request)
    msg = _build_message(name, "outbound", p, request_id=request_id)
    return {"status": "ok", "message_id": _save_message(msg)}


@app.get("/webhook/outbound")
async def outbound(
    name: Optional[str] = Query(default=None),
    since: Optional[float] = Query(default=None),
    direction: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=200),
    authorization: Optional[str] = Header(None),
):
    _check_auth(authorization)
    msgs = _list_messages(name=name, limit=limit, direction=direction, since=since)
    msgs.reverse()
    return {"status": "ok", "messages": [m.model_dump() for m in msgs], "count": len(msgs)}


# ─── SSE 实时推流 ───────────────────────────────────────────────────────────

@app.get("/stream/{name}")
async def stream(
    name: str = FastAPIPath(..., description="订阅名称"),
    authorization: Optional[str] = Header(None),
):
    """SSE 实时推流：当有新消息时推送，替代轮询"""
    _check_auth(authorization)

    async def event_stream() -> AsyncGenerator[str, None]:
        last_count = len(_list_messages(name=name, limit=1))
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            current_count = len(_list_messages(name=name, limit=100))
            if current_count > last_count:
                msgs = _list_messages(name=name, limit=100)
                new_msgs = [m for m in msgs if m.timestamp > (msgs[-1].timestamp if msgs else 0)]
                for m in new_msgs:
                    yield f"data: {json.dumps(m.model_dump(), ensure_ascii=False)}\n\n"
                last_count = current_count

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )


# ─── 信号接口 ───────────────────────────────────────────────────────────────

@app.get("/signal/{name}")
async def signal(
    name: str = FastAPIPath(..., description="消息名称/发送方标识"),
):
    """公开信号接口：返回指定 name 的最新消息数量和最新时间戳"""
    msgs = _list_messages(name=name, limit=200)
    if not msgs:
        return {"name": name, "count": 0, "latest_timestamp": 0}
    latest = max(msgs, key=lambda m: m.timestamp)
    return {"name": name, "count": len(msgs), "latest_timestamp": latest.timestamp}


# ─── OpenAI 兼容接口 ────────────────────────────────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    authorization: Optional[str] = Header(None),
):
    """OpenAI 兼容聊天接口：把用户消息入站，等待回复后返回"""
    _check_auth(authorization)

    request_id = body.request_id or str(uuid.uuid4())[:8]

    user_msg = None
    for m in reversed(body.messages):
        if m.role == "user":
            user_msg = m.content
            break
    if not user_msg:
        raise HTTPException(400, "No user message found")

    # 存入 inbound（带 request_id）
    msg = Message(
        id=str(uuid.uuid4()),
        request_id=request_id,
        type="text",
        content=user_msg,
        name=body.name,
        direction="inbound",
        timestamp=time.time(),
    )
    _save_message(msg)

    # 轮询 outbound 等回复（用 request_id 精确匹配）
    replies = []
    deadline = time.time() + 30
    while time.time() < deadline:
        await asyncio.sleep(0.5)
        out_msgs = _list_messages(name=body.name, request_id=request_id, limit=10)
        if out_msgs:
            replies.extend(out_msgs)
            break

    final_content = user_msg
    if replies:
        final_content = replies[-1].content or str(replies[-1].model_dump())

    return ChatCompletionResponse(
        id=str(uuid.uuid4()),
        created=int(time.time()),
        model=body.model,
        choices=[{"index": 0, "message": {"role": "assistant", "content": final_content}}],
    )


# ─── 文件下载 ───────────────────────────────────────────────────────────────

@app.get("/uploads/{filename}")
async def download_file(filename: str = FastAPIPath(..., description="文件名")):
    """下载上传的媒体文件"""
    path = UPLOADS_DIR / filename

    # 安全检查：防止路径穿越
    if not path.resolve().startswith(UPLOADS_DIR.resolve()):
        raise HTTPException(404, "File not found")

    ct = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
        ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
        ".pdf": "application/pdf",
    }.get(path.suffix, "application/octet-stream")

    return _stream_file(path, ct)


# ─── 管理接口 ───────────────────────────────────────────────────────────────

@app.get("/api/messages")
async def list_api(
    name: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=200),
    authorization: Optional[str] = Header(None),
):
    _check_auth(authorization)
    msgs = _list_messages(name=name, limit=limit)
    msgs.reverse()
    return {"messages": [m.model_dump() for m in msgs]}


@app.delete("/api/messages/{message_id}")
async def delete_message(
    message_id: str,
    authorization: Optional[str] = Header(None),
):
    _check_auth(authorization)
    conn = _get_db()
    cursor = conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    if deleted == 0:
        raise HTTPException(404, "Message not found")
    return {"status": "ok"}


@app.post("/api/cleanup")
async def cleanup(
    authorization: Optional[str] = Header(None),
):
    _check_auth(authorization)
    deleted = _clean_old_messages()
    return {"status": "ok", "cleaned": deleted}


# ─── 定时清理（启动时）──────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    _clean_old_messages()


# ─── 启动入口 ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)