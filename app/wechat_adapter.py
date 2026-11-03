"""微信消息适配器 - 将微信消息转发到 webhook API"""

import asyncio
import time
from typing import Optional

import aiohttp

# 配置
WEBHOOK_API_URL = "http://localhost:8100"
WEBHOOK_API_KEY = "sk-my-secret-key"


async def send_to_webhook(
    name: str,
    msg_type: str,
    content: Optional[str] = None,
    media_url: Optional[str] = None,
    caption: Optional[str] = None,
):
    """发送消息到 webhook API"""
    data = {
        "type": msg_type,
        "content": content or "",
    }
    if caption:
        data["caption"] = caption
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{WEBHOOK_API_URL}/webhook/inbound/{name}",
            data=data,
            headers={"Authorization": f"Bearer {WEBHOOK_API_KEY}"},
        ) as resp:
            return await resp.json()


async def poll_outbound(name: str, since: Optional[float] = None) -> list:
    """轮询出站消息"""
    params = {"name": name}
    if since:
        params["since"] = str(since)
    
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{WEBHOOK_API_URL}/webhook/outbound",
            params=params,
            headers={"Authorization": f"Bearer {WEBHOOK_API_KEY}"},
        ) as resp:
            data = await resp.json()
            return data.get("messages", [])


async def send_reply(reply_msg: dict):
    """发送回复到微信（需要适配具体平台）"""
    # TODO: 根据平台实现具体发送逻辑
    pass


if __name__ == "__main__":
    print("微信适配器已加载")
    print(f"Webhook API: {WEBHOOK_API_URL}")
    print(f"API Key: {WEBHOOK_API_KEY[:8]}...")
