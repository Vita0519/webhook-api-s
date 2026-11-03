---
name: muse-webhook
displayName: Muse Webhook Agent
version: 1.0.0
summary: Muse 智能体 webhook 消息收发技能
license: MIT
---

# Muse Webhook Agent

通过 webhook-api 与 muse1号 智能体通信。

## 接口地址

- Webhook API: http://localhost:8100
- API Key: `webhook-default-key-change-me`
- 目标名称: muse1号

## 快速使用

```bash
# 发送消息到 muse1号
python3 scripts/send_message.py "你的消息内容"

# 查询信号（无需密钥）
curl -s http://localhost:8100/signal/muse1号

# 轮询获取回复
curl -s "http://localhost:8100/webhook/outbound?name=muse1号" -H "Authorization: Bearer webhook-default-key-change-me"
```

## 流程

1. 发送消息到 webhook
2. 轮询信号接口检测新消息
3. 获取回复内容


## 使用示例

```bash
# 发送消息给 muse1号
python3 scripts/send_and_poll.py "你好"

# 完整测试
python3 scripts/full_test.py "测试消息"

# 查看信号（无需密钥）
curl -s http://localhost:8100/signal/muse1号

# 查看消息列表
curl -s "http://localhost:8100/webhook/outbound?name=muse1号" \
  -H "Authorization: Bearer webhook-default-key-change-me"
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| /webhook/inbound/{name} | POST | 发送消息（需鉴权） |
| /webhook/outbound | GET | 轮询消息（需鉴权） |
| /signal/{name} | GET | 信号检测（公开） |
| /v1/chat/completions | POST | OpenAI 兼容接口 |

## 注意

- signal 接口无需鉴权，供钩子/检测脚本使用
- 消息通过 name 字段隔离，每个智能体独立
- 默认 API_KEY: `webhook-default-key-change-me`
