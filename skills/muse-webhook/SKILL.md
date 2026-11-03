---
name: muse-webhook
displayName: Muse Webhook Agent
version: 1.1.0
summary: Muse 智能体 webhook 消息收发技能（SQLite + SSE + request_id）
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

# SSE 实时推流
curl -N "http://localhost:8100/stream/muse1号" -H "Authorization: Bearer webhook-default-key-change-me"
```

## 流程

1. 发送消息到 webhook（可选带 request_id）
2. 轮询 signal 接口检测新消息，或使用 SSE 实时推流
3. 获取回复内容

## request_id 关联（防串台）

发起消息时带 request_id，回复时原样带回：

```bash
# 发请求
curl -X POST http://localhost:8100/webhook/inbound/muse1号 \
  -H "Authorization: Bearer webhook-default-key-change-me" \
  -F "type=text" -F "content=你好" -F "request_id=abc123"

# 回复时带上同一个 request_id
curl -X POST http://localhost:8100/webhook/outbound/muse1号 \
  -H "Authorization: Bearer webhook-default-key-change-me" \
  -F "type=text" -F "content=收到" -F "request_id=abc123"
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| /webhook/inbound/{name}?request_id=xxx | POST | 发送消息（可选 request_id） |
| /webhook/outbound/{name}?request_id=xxx | POST | 写入回复（可选 request_id） |
| /webhook/outbound?name=&since=&limit= | GET | 轮询消息 |
| /stream/{name} | GET | SSE 实时推流 |
| /signal/{name} | GET | 信号检测（公开，无需鉴权） |
| /v1/chat/completions | POST | OpenAI 兼容接口 |
| /api/messages | GET | 管理接口 |
| /api/cleanup | POST | 清理过期消息 |

## 注意

- signal 接口无需鉴权，供钩子/检测脚本使用
- SSE `/stream/{name}` 连接后只推新消息，不回放历史
- request_id 用于关联请求和回复，防并发串台
- 消息通过 name 字段隔离，每个智能体独立
- 默认 API_KEY: `webhook-default-key-change-me`
- 存储：SQLite（storage/webhook.db）+ uploads/