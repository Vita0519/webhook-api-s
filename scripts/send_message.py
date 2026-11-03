#!/usr/bin/env python3
"""Muse Webhook - 发送消息到 muse1号"""

import sys
import time
import requests

WEBHOOK_URL = "http://localhost:8100"
API_KEY = "webhook-default-key-change-me"
TARGET_NAME = "muse1号"

def send_message(content: str) -> dict:
    """发送文本消息"""
    resp = requests.post(
        f"{WEBHOOK_URL}/webhook/inbound/{TARGET_NAME}",
        headers={"Authorization": f"Bearer {API_KEY}"},
        data={"type": "text", "content": content}
    )
    return resp.json()

def get_signal() -> dict:
    """获取信号（无需鉴权）"""
    resp = requests.get(f"{WEBHOOK_URL}/signal/{TARGET_NAME}")
    return resp.json()

def get_messages(since: float = None, limit: int = 50) -> list:
    """获取消息列表"""
    params = {"name": TARGET_NAME, "limit": limit}
    if since:
        params["since"] = str(since)
    resp = requests.get(
        f"{WEBHOOK_URL}/webhook/outbound",
        params=params,
        headers={"Authorization": f"Bearer {API_KEY}"}
    )
    data = resp.json()
    return data.get("messages", [])

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 send_message.py 'your message'")
        sys.exit(1)
    
    msg = " ".join(sys.argv[1:])
    result = send_message(msg)
    print(f"发送结果: {result}")
    
    # 等待并获取信号
    time.sleep(1)
    signal = get_signal()
    print(f"当前信号: {signal}")
