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
import hmac
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, UploadFile, HTTPException, Header, Query, Path as FastAPIPath, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

# ─── 配置 ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
STORAGE_DIR = Path(os.getenv("WEBHOOK_STORAGE_DIR", "/app/storage"))
if not STORAGE_DIR.exists():
    STORAGE_DIR = BASE_DIR / "storage"
UPLOADS_DIR = STORAGE_DIR / "uploads"
DB_PATH = STORAGE_DIR / "webhook.db"

API_KEY = os.getenv("WEBHOOK_API_KEY", "").strip()
ALLOW_NO_AUTH = os.getenv("WEBHOOK_ALLOW_NO_AUTH", "") == "1"
POLL_INTERVAL = int(os.getenv("WEBHOOK_POLL_INTERVAL", "2"))
MESSAGE_RETENTION_DAYS = int(os.getenv("WEBHOOK_MSG_RETENTION", "7"))
MAX_UPLOAD_SIZE = int(os.getenv("WEBHOOK_MAX_UPLOAD_MB", "50")) * 1024 * 1024  # 默认 50MB
CHAT_TIMEOUT = float(os.getenv("WEBHOOK_CHAT_TIMEOUT", "30"))
MAX_CONTENT_CHARS = int(os.getenv("WEBHOOK_MAX_CONTENT_CHARS", "200000"))
CLEANUP_INTERVAL_SECONDS = int(os.getenv("WEBHOOK_CLEANUP_INTERVAL", "3600"))
ALLOWED_MESSAGE_TYPES = {"text", "image", "video", "audio", "file"}

if not API_KEY and not ALLOW_NO_AUTH:
    raise RuntimeError(
        "WEBHOOK_API_KEY is required. Set it in the environment, "
        "or set WEBHOOK_ALLOW_NO_AUTH=1 only for local development."
    )

# ─── 目录初始化 ─────────────────────────────────────────────────────────────

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ─── SQLite 数据库 ──────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _init_db():
    conn = _get_db()
    conn.execute("PRAGMA journal_mode=WAL")
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
    metadata: dict = Field(default_factory=dict)


class WebhookPayload(BaseModel):
    type: str = "text"
    content: Optional[str] = None
    caption: Optional[str] = None
    media_url: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


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
    request_id: Optional[str] = None
    choices: list[ChatCompletionChoice]
    usage: dict = Field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})


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
                   after: Optional[float] = None,
                   request_id: Optional[str] = None,
                   ascending: bool = False) -> list[Message]:
    conn = _get_db()
    conditions = []
    params = []

    if name:
        conditions.append("name = ?")
        params.append(name)
    if direction:
        conditions.append("direction = ?")
        params.append(direction)
    if since is not None:
        conditions.append("timestamp > ?")
        params.append(since)
    if after is not None:
        conditions.append("timestamp > ?")
        params.append(after)
    if request_id:
        conditions.append("request_id = ?")
        params.append(request_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    order = "ASC" if ascending else "DESC"
    rows = conn.execute(
        f"SELECT * FROM messages {where} ORDER BY timestamp {order} LIMIT ?",
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
        raise HTTPException(
            401, "Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[len("Bearer "):].strip()
    if not hmac.compare_digest(token, API_KEY):
        raise HTTPException(
            401, "Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


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
        except Exception as exc:
            raise HTTPException(400, "Invalid JSON body") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON body must be an object")
        payload["type"] = body.get("type") or "text"
        payload["content"] = body.get("content")
        payload["caption"] = body.get("caption")
        payload["media_url"] = body.get("media_url") or body.get("url")
        payload["metadata"] = body.get("metadata") or {}
        return payload

    try:
        form = await request.form()
    except Exception as exc:
        raise HTTPException(400, "Invalid form payload") from exc
    payload["type"] = form.get("type") or "text"
    payload["content"] = form.get("content")
    payload["caption"] = form.get("caption")
    payload["media_url"] = form.get("media_url") or form.get("url")
    payload["request_id"] = form.get("request_id")
    media = form.get("media")
    if media is not None and getattr(media, "filename", None):
        payload["media"] = media
    return payload


def _build_message(name: Optional[str], direction: str, p: dict,
                   request_id: Optional[str] = None) -> Message:
    msg_type = p["type"] or "text"
    if msg_type not in ALLOWED_MESSAGE_TYPES:
        raise HTTPException(400, f"Unsupported message type: {msg_type}")
    if not any([p.get("content"), p.get("caption"), p.get("media_url"), p.get("media")]):
        raise HTTPException(400, "Empty message: content, caption, media_url or media is required")
    if p.get("content") and len(str(p["content"])) > MAX_CONTENT_CHARS:
        raise HTTPException(413, f"content exceeds {MAX_CONTENT_CHARS} characters")
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
    ext = Path(file.filename or "file").suffix.lower()
    if len(ext) > 10 or not re.fullmatch(r"\.[a-z0-9]+", ext):
        ext = ".bin"
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOADS_DIR / fname

    # 流式写入，避免把整个文件读进内存
    total = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = file.file.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_SIZE:
                    raise HTTPException(413, f"File too large, max {MAX_UPLOAD_SIZE // 1024 // 1024}MB")
                out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    return f"/uploads/{fname}", file.content_type or "application/octet-stream"


def _stream_file(path: Path, content_type: str):
    if not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(
        path,
        media_type=content_type,
        headers={"Content-Disposition": "inline"},
    )


# ─── FastAPI 应用 ───────────────────────────────────────────────────────────

app = FastAPI(
    title="Webhook API",
    description="消息收发中间件 — SQLite 存储、OpenAI 兼容接口、SSE 推流",
    version="1.2.0",
)


# ─── 健康检查 ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


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
    msg = _build_message(name, "inbound", p, request_id=request_id or p.get("request_id"))
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
    msg = _build_message(name, "outbound", p, request_id=request_id or p.get("request_id"))
    return {"status": "ok", "message_id": _save_message(msg)}


@app.get("/webhook/outbound")
async def outbound(
    name: Optional[str] = Query(default=None),
    since: Optional[float] = Query(default=None),
    direction: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
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
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(timestamp), 0) FROM messages WHERE name = ?",
                (name,)
            ).fetchone()
            last_ts = max(time.time(), row[0])
        finally:
            conn.close()

        while True:
            # 每批最多取 200 条，游标推进到“本批最后实际推送的那条”，
            # 积压超过 200 条时下一轮继续取，不丢消息。
            msgs = _list_messages(name=name, after=last_ts, limit=200, ascending=True)
            if not msgs:
                yield ": ping\n\n"
                await asyncio.sleep(POLL_INTERVAL)
                continue

            for m in msgs:
                yield f"data: {json.dumps(m.model_dump(), ensure_ascii=False)}\n\n"
            last_ts = msgs[-1].timestamp

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ─── 信号接口 ───────────────────────────────────────────────────────────────

@app.get("/signal/{name}")
async def signal(
    name: str = FastAPIPath(..., description="消息名称/发送方标识"),
):
    """公开信号接口：返回指定 name 的消息总数和最新时间戳"""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS count, COALESCE(MAX(timestamp), 0) AS latest FROM messages WHERE name = ?",
            (name,),
        ).fetchone()
    finally:
        conn.close()
    return {"name": name, "count": row["count"], "latest_timestamp": row["latest"]}


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
    started = time.time()
    msg = Message(
        id=str(uuid.uuid4()),
        request_id=request_id,
        type="text",
        content=user_msg,
        name=body.name,
        direction="inbound",
        timestamp=started,
    )
    _save_message(msg)

    # 只等待本次请求之后、同 request_id 的 outbound 回复
    reply = None
    deadline = started + CHAT_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(0.25)
        replies = _list_messages(
            name=body.name,
            direction="outbound",
            request_id=request_id,
            since=started,
            limit=1,
            ascending=True,
        )
        if replies:
            reply = replies[0]
            break

    if reply is None:
        raise HTTPException(504, "No reply from agent before timeout")

    final_content = reply.content or str(reply.model_dump())

    return ChatCompletionResponse(
        id=str(uuid.uuid4()),
        created=int(time.time()),
        model=body.model,
        request_id=request_id,
        choices=[{"index": 0, "message": {"role": "assistant", "content": final_content}}],
    )


# ─── 文件下载 ───────────────────────────────────────────────────────────────

@app.get("/uploads/{filename}")
async def download_file(filename: str = FastAPIPath(..., description="文件名")):
    """下载上传的媒体文件"""
    path = UPLOADS_DIR / filename

    # 安全检查：防止路径穿越
    if not path.resolve().is_relative_to(UPLOADS_DIR.resolve()):
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
    limit: int = Query(default=50, ge=1, le=200),
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