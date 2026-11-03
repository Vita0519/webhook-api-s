#!/usr/bin/env python3
"""Muse Webhook - 完整流程测试"""

import sys
import time
import requests

WEBHOOK_URL = "http://localhost:8100"
API_KEY = "webhook-default-key-change-me"
TARGET_NAME = "muse1号"

def get_signal():
    return requests.get(f"{WEBHOOK_URL}/signal/{TARGET_NAME}").json()

def send_message(content: str) -> dict:
    resp = requests.post(
        f"{WEBHOOK_URL}/webhook/inbound/{TARGET_NAME}",
        headers={"Authorization": f"Bearer {API_KEY}"},
        data={"type": "text", "content": content}
    )
    return resp.json()

def get_messages(limit=10) -> list:
    resp = requests.get(
        f"{WEBHOOK_URL}/webhook/outbound",
        params={"name": TARGET_NAME, "limit": limit},
        headers={"Authorization": f"Bearer {API_KEY}"}
    )
    return resp.json().get("messages", [])

if __name__ == "__main__":
    print("=== Muse Webhook 测试 ===\n")
    
    # 1. 查看当前信号
    signal = get_signal()
    print(f"1. 当前信号: {signal}")
    
    # 2. 发送测试消息
    test_msg = sys.argv[1] if len(sys.argv) > 1 else "你好，我是来自技能的测试消息"
    print(f"\n2. 发送消息: {test_msg}")
    result = send_message(test_msg)
    print(f"   结果: {result}")
    
    # 3. 等待并查看信号变化
    print("\n3. 等待信号变化...")
    time.sleep(2)
    new_signal = get_signal()
    print(f"   新信号: {new_signal}")
    
    # 4. 查看所有消息
    print("\n4. 消息列表:")
    messages = get_messages()
    for i, m in enumerate(messages[-5:], 1):  # 显示最近5条
        direction = "→" if m["direction"] == "inbound" else "←"
        print(f"   {i}. {direction} {m['content']}")
    
    print("\n测试完成!")
