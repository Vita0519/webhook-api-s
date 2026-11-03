#!/usr/bin/env python3
"""Muse Webhook - 发送消息并等待回复"""

import sys
import time
import requests
import json

WEBHOOK_URL = "http://localhost:8100"
API_KEY = "webhook-default-key-change-me"
TARGET_NAME = "muse1号"
POLL_INTERVAL = 2
MAX_WAIT = 60

def send_message(content: str) -> str:
    """发送消息，返回 message_id"""
    resp = requests.post(
        f"{WEBHOOK_URL}/webhook/inbound/{TARGET_NAME}",
        headers={"Authorization": f"Bearer {API_KEY}"},
        data={"type": "text", "content": content}
    )
    data = resp.json()
    return data.get("message_id")

def get_signal():
    """获取信号"""
    resp = requests.get(f"{WEBHOOK_URL}/signal/{TARGET_NAME}")
    return resp.json()

def get_messages(since: float = None) -> list:
    """获取消息"""
    params = {"name": TARGET_NAME, "limit": 50}
    if since:
        params["since"] = str(since)
    resp = requests.get(
        f"{WEBHOOK_URL}/webhook/outbound",
        params=params,
        headers={"Authorization": f"Bearer {API_KEY}"}
    )
    return resp.json().get("messages", [])

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 send_and_poll.py 'message'")
        sys.exit(1)
    
    msg = " ".join(sys.argv[1:])
    
    # 获取发送前的信号
    before = get_signal()
    before_ts = before.get("latest_timestamp", 0)
    before_count = before.get("count", 0)
    
    print(f"发送前信号: {before}")
    
    # 发送消息
    msg_id = send_message(msg)
    print(f"消息已发送, ID: {msg_id}")
    
    # 轮询等待回复
    print(f"等待 muse1号 回复...")
    deadline = time.time() + MAX_WAIT
    
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        signal = get_signal()
        
        if signal.get("count", 0) > before_count or signal.get("latest_timestamp", 0) > before_ts:
            print(f"\n检测到新消息! 信号: {signal}")
            break
    else:
        print("\n等待超时")
    
    # 获取消息
    messages = get_messages(since=before_ts)
    print(f"\n共 {len(messages)} 条消息:")
    for m in messages:
        direction = "→" if m.get("direction") == "inbound" else "←"
        content = m.get("content", "")[:100]
        print(f"  {direction} {content}")
