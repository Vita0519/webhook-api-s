#!/usr/bin/env python3
"""Muse Webhook - 轮询获取回复"""

import time
import requests
import json

WEBHOOK_URL = "http://localhost:8100"
API_KEY = "webhook-default-key-change-me"
TARGET_NAME = "muse1号"
POLL_INTERVAL = 2  # 秒
MAX_WAIT = 60      # 最多等待60秒

def poll_until_new(since_ts: float) -> dict:
    """轮询直到有新消息或超时"""
    deadline = time.time() + MAX_WAIT
    last_count = None
    
    while time.time() < deadline:
        resp = requests.get(f"{WEBHOOK_URL}/signal/{TARGET_NAME}")
        signal = resp.json()
        
        count = signal.get("count", 0)
        latest_ts = signal.get("latest_timestamp", 0)
        
        if count > 0 and latest_ts > since_ts:
            print(f"检测到新消息! count={count}, latest_timestamp={latest_ts}")
            return signal
        
        if count != last_count:
            print(f"当前信号: {json.dumps(signal)}")
            last_count = count
        
        time.sleep(POLL_INTERVAL)
    
    print("等待超时")
    return {"error": "timeout"}

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
    # 获取当前信号作为基准
    baseline = requests.get(f"{WEBHOOK_URL}/signal/{TARGET_NAME}").json()
    since_ts = baseline.get("latest_timestamp", 0)
    print(f"基准信号: {baseline}")
    
    # 轮询
    result = poll_until_new(since_ts)
    
    # 获取消息内容
    messages = get_messages(since=since_ts)
    print(f"\n新消息 ({len(messages)} 条):")
    for m in messages:
        print(f"  [{m['type']}] {m.get('content', '')}")
