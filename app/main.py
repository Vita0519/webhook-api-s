"""Webhook API — 消息收发中间件

功能：
- 入站 webhook（接收方推送消息进来）
- 出站 webhook（轮询取消息发给接收方）
- 支持文本/图片/视频/音频/文件等全类型媒体
- Bearer Token 鉴权
- OpenAI 兼容接口（/v1/chat/completions）
- name 字段透传：进站时记录，出站轮询时可指定发往哪个 name
"""

import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header, Query, Path as FastAPIPath, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# ─── 配置 ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
# 存储目录优先用环境变量，默认 /app/storage（容器挂载点）
STORAGE_DIR = Path(os.getenv("WEBHOOK_STORAGE_DIR", "/app/storage"))
if not STORAGE_DIR.exists():
    STORAGE_DIR = BASE_DIR / "storage"
MESSAGES_DIR = STORAGE_DIR / "messages"
UPLOADS_DIR = STORAGE_DIR / "uploads"

# 默认鉴权 key（通过环境变量 WEBHOOK_API_KEY 覆盖）
API_KEY = os.getenv("WEBHOOK_API_KEY", "webhook-default-key-change-me")

# 入站轮询间隔（秒）
POLL_INTERVAL = int(os.getenv("WEBHOOK_POLL_INTERVAL", "2"))

# 消息保留天数
MESSAGE_RETENTION_DAYS = int(os.getenv("WEBHOOK_MSG_RETENTION", "7"))

# ─── 目录初始化 ─────────────────────────────────────────────────────────────

MESSAGES_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ─── 数据模型 ───────────────────────────────────────────────────────────────

class Message(BaseModel):
    id: str
    type: str  # text | image | video | audio | file
    content: Optional[str] = None          # 文本内容 / URL
    media_url: Optional[str] = None        # 媒体文件访问路径
    name: Optional[str] = None             # 发送者/接收者标识
    direction: str = "inbound"           # inbound | outbound
    timestamp: float
    metadata: dict = {}


class WebhookInbound(BaseModel):
    """入站消息体"""
    name: Optional[str] = None            # 发送方标识
    type: str = "text"                    # text | image | video | audio | file
    content: Optional[str] = None         # 文本内容
    url: Optional[str] = None             # 媒体 URL（type=media 时）
    caption: Optional[str] = None         # 图片/视频描述文字


class WebhookOutbound(BaseModel):
    """出站消息体"""
    name: str                              # 目标接收方
    type: str = "text"
    content: str
    media_url: Optional[str] = None


class ChatMessage(BaseModel):
    role: str   # user | assistant
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "webhook"
    messages: list[ChatMessage]
    name: Optional[str] = None          # 指定会话名


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[dict]
    usage: dict


# ─── 工具函数 ───────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_message(msg: Message) -> str:
    """持久化消息到磁盘，返回消息 ID"""
    mid = msg.id or str(uuid.uuid4())
    msg.id = mid
    payload = msg.model_dump()
    path = MESSAGES_DIR / f"{mid}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return mid


def _load_message(mid: str) -> Optional[Message]:
    path = MESSAGES_DIR / f"{mid}.json"
    if not path.exists():
        return None
    return Message(**json.loads(path.read_text()))


def _list_messages(name: Optional[str] = None, limit: int = 50) -> list[Message]:
    """列出消息，可按 name 过滤"""
    results = []
    for f in sorted(MESSAGES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            msg = Message(**json.loads(f.read_text()))
            if name and msg.name != name:
                continue
            results.append(msg)
            if len(results) >= limit:
                break
        except Exception:
            continue
    return results


def _clean_old_messages():
    """清理过期消息"""
    cutoff = time.time() - MESSAGE_RETENTION_DAYS * 86400
    for f in MESSAGES_DIR.glob("*.json"):
        if f.stat().st_mtime < cutoff:
            f.unlink()


def _check_auth(authorization: Optional[str] = Header(None)):
    """Bearer token 鉴权"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = authorization[len("Bearer "):]
    if not hmac.compare_digest(token, API_KEY):
        raise HTTPException(403, "Invalid API key")


def _read_upload(file: UploadFile) -> tuple[str, str]:
    """保存上传文件，返回 (relative_url, content_type)"""
    ext = Path(file.filename or "file").suffix or ".bin"
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOADS_DIR / fname
    content = file.file.read()
    dest.write_bytes(content)
    rel_url = f"/uploads/{fname}"
    ct = file.content_type or "application/octet-stream"
    return rel_url, ct


def _stream_file(path: Path, content_type: str):
    """流式返回文件"""
    if not path.exists():
        raise HTTPException(404, "File not found")

    def gen():
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                yield chunk

    return StreamingResponse(gen(), media_type=content_type)


# ─── FastAPI 应用 ───────────────────────────────────────────────────────────

app = FastAPI(
    title="Webhook API",
    description="消息收发中间件 — 支持全类型媒体、OpenAI 兼容接口",
    version="1.0.0",
)


# ─── 健康检查 ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "api_key_set": API_KEY != "webhook-default-key-change-me"}


# ─── 消息写入公共逻辑 ───────────────────────────────────────────────────────

def _detect_type_from_ct(ct: str) -> str:
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("video/"):
        return "video"
    if ct.startswith("audio/"):
        return "audio"
    return "file"


async def _parse_message_payload(request) -> dict:
    """同时支持 form-data / multipart / JSON 三种请求体，返回统一字段"""
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

    # form-data / multipart
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


def _build_message(name: Optional[str], direction: str, p: dict) -> Message:
    """根据解析出的 payload 构造消息"""
    msg_type = p["type"] or "text"
    media_url = p["media_url"]

    # 1) 上传文件优先（落盘到本地 uploads）
    if p["media"] is not None:
        rel_url, content_type = _read_upload(p["media"])
        media_url = rel_url
        if msg_type == "text":
            msg_type = _detect_type_from_ct(content_type)

    # 2) 外部 URL 直接引用，不下载
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
        type=msg_type,
        content=p["caption"] or p["content"],
        media_url=media_url,
        name=name,
        direction=direction,
        timestamp=time.time(),
        metadata=p["metadata"] or {},
    )


# ─── 入站 Webhook（接收方推送消息进来）────────────────────────────────────

@app.post("/webhook/inbound")
async def inbound_no_name(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """接收入站消息（无 name），支持 form-data / multipart / JSON"""
    _check_auth(authorization)
    p = await _parse_message_payload(request)
    msg = _build_message(None, "inbound", p)
    return {"status": "ok", "message_id": _save_message(msg)}


@app.post("/webhook/inbound/{name}")
async def inbound_with_name(
    request: Request,
    name: str = FastAPIPath(..., description="发送方标识"),
    authorization: Optional[str] = Header(None),
):
    """接收入站消息，支持 form-data / multipart / JSON"""
    _check_auth(authorization)
    p = await _parse_message_payload(request)
    msg = _build_message(name, "inbound", p)
    return {"status": "ok", "message_id": _save_message(msg)}


# ─── 出站 Webhook（写入回复消息）────────────────────────────────────────────

@app.post("/webhook/outbound")
async def outbound_write_no_name(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """写入 outbound 消息（无 name），支持 form-data / multipart / JSON"""
    _check_auth(authorization)
    p = await _parse_message_payload(request)
    msg = _build_message(None, "outbound", p)
    return {"status": "ok", "message_id": _save_message(msg)}


@app.post("/webhook/outbound/{name}")
async def outbound_write(
    request: Request,
    name: str = FastAPIPath(..., description="接收方标识"),
    authorization: Optional[str] = Header(None),
):
    """写入 outbound 回复消息，支持 form-data / multipart / JSON"""
    _check_auth(authorization)
    p = await _parse_message_payload(request)
    msg = _build_message(name, "outbound", p)
    return {"status": "ok", "message_id": _save_message(msg)}


@app.get("/webhook/outbound")
async def outbound(
    name: Optional[str] = Query(default=None),
    since: Optional[float] = Query(default=None, description="只返回此时间戳之后的消息"),
    direction: Optional[str] = Query(default=None, description="过滤方向: inbound | outbound"),
    limit: int = Query(default=50, le=200),
    authorization: Optional[str] = Header(None),
):
    _check_auth(authorization)
    if since:
        msgs = [m for m in _list_messages(name=name, limit=200) if m.timestamp > since]
    else:
        msgs = _list_messages(name=name, limit=200)
    if direction:
        msgs = [m for m in msgs if m.direction == direction]
    msgs = msgs[:limit]
    # 反向（最新的在前），然后反转成时间正序
    msgs.reverse()
    return {
        "status": "ok",
        "messages": [m.model_dump() for m in msgs],
        "count": len(msgs),
    }


# ─── 公开信号接口（无需鉴权，供外部轮询检查是否有新消息）────────────────────

@app.get("/signal/{name}")
async def signal(
    name: str = FastAPIPath(..., description="消息名称/发送方标识"),
):
    """公开信号接口：返回指定 name 的最新消息数量和最新时间戳，无需鉴权。
    用于外部钩子检测是否有新消息，避免轮询时携带密钥。"""
    msgs = _list_messages(name=name, limit=200)
    if not msgs:
        return {"name": name, "count": 0, "latest_timestamp": 0}
    # 找最新的一条
    latest = max(msgs, key=lambda m: m.timestamp)
    return {
        "name": name,
        "count": len(msgs),
        "latest_timestamp": latest.timestamp,
    }


# ─── OpenAI 兼容接口 ────────────────────────────────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    authorization: Optional[str] = Header(None),
):
    """OpenAI 兼容聊天接口：把用户消息入站，轮询接收方回复"""
    _check_auth(authorization)

    # 取最后一条用户消息
    user_msg = None
    for m in reversed(body.messages):
        if m.role == "user":
            user_msg = m.content
            break
    if not user_msg:
        raise HTTPException(400, "No user message found")

    # 存入 inbound
    msg = Message(
        id=str(uuid.uuid4()),
        type="text",
        content=user_msg,
        name=body.name,
        direction="inbound",
        timestamp=time.time(),
    )
    _save_message(msg)

    # 轮询 outbound 等回复（最多等 30 秒）
    replies = []
    deadline = time.time() + 30
    last_since = msg.timestamp
    while time.time() < deadline:
        await asyncio.sleep(0.5)
        out_msgs = _list_messages(name=body.name, limit=10)
        new_replies = [m for m in out_msgs if m.timestamp > last_since and m.direction == "outbound"]
        if new_replies:
            replies.extend(new_replies)
            break

    reply_text = "\n".join(m.content or "" for m in replies) or "（暂无回复）"

    return ChatCompletionResponse(
        id=str(uuid.uuid4()),
        created=int(time.time()),
        model=body.model,
        choices=[{"index": 0, "message": {"role": "assistant", "content": reply_text}, "finish_reason": "stop"}],
        usage={"prompt_tokens": len(user_msg), "completion_tokens": len(reply_text), "total_tokens": len(user_msg) + len(reply_text)},
    )


# ─── 媒体文件下载 ───────────────────────────────────────────────────────────

@app.get("/uploads/{filename}")
async def get_upload(filename: str, authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    if ".." in filename or filename.startswith("/"):
        raise HTTPException(400, "Invalid filename")
    path = UPLOADS_DIR / filename
    ct = path.stat().st_size and {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp",
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
    path = MESSAGES_DIR / f"{message_id}.json"
    if not path.exists():
        raise HTTPException(404, "Message not found")
    path.unlink()
    return {"status": "ok"}


@app.post("/api/cleanup")
async def cleanup(
    authorization: Optional[str] = Header(None),
):
    _check_auth(authorization)
    _clean_old_messages()
    return {"status": "ok", "cleaned": True}


# ─── 启动入口 ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)
