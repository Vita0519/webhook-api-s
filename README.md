# Webhook API

消息收发中间件，SQLite 存储，支持全类型媒体、OpenAI 兼容接口、SSE 实时推流。

## 快速启动

```bash
cd /home/sxkiss/webhook-api
docker compose up -d
```

## API 端点

### 入站（接收方推送消息进来）
- `POST /webhook/inbound/{name}?request_id=xxx` — 带名称的入站（可选 request_id 关联回复）
- `POST /webhook/inbound` — 不带名称

### 出站（写入回复消息）
- `POST /webhook/outbound/{name}?request_id=xxx` — 带名称的出站
- `POST /webhook/outbound` — 不带名称

### 出站轮询
- `GET /webhook/outbound?name={name}&since={timestamp}&direction=inbound|outbound&limit=50`

### SSE 实时推流
- `GET /stream/{name}` — 连接后有新消息实时推送，替代 signal 轮询

### 信号接口（公开，无需鉴权）
- `GET /signal/{name}` — 返回 count 和 latest_timestamp，供外部钩子检测

### OpenAI 兼容接口
- `POST /v1/chat/completions` — 带 request_id 防串台

### 管理接口
- `GET /api/messages?name={name}&limit=50`
- `DELETE /api/messages/{id}`
- `POST /api/cleanup`

### 健康检查
- `GET /health`

### 文件下载
- `GET /uploads/{filename}`

## 鉴权

所有接口需要 Header：`Authorization: Bearer <API_KEY>`。未设置 `WEBHOOK_API_KEY` 时默认拒绝启动；仅本地开发可显式设置 `WEBHOOK_ALLOW_NO_AUTH=1` 临时关闭鉴权。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| WEBHOOK_API_KEY | 无（必填） | API 密钥，未设置时拒绝启动 |
| WEBHOOK_ALLOW_NO_AUTH | 关闭 | 设为 1 允许无鉴权（仅本地开发） |
| WEBHOOK_POLL_INTERVAL | 2 | 轮询间隔（秒） |
| WEBHOOK_MSG_RETENTION | 7 | 消息保留天数 |
| WEBHOOK_CHAT_TIMEOUT | 30 | /v1/chat/completions 等待回复秒数 |
| WEBHOOK_STORAGE_DIR | /app/storage | 存储目录 |
| WEBHOOK_MAX_UPLOAD_MB | 50 | 上传大小上限（MB） |
| WEBHOOK_CLEANUP_INTERVAL | 3600 | 过期消息清理间隔（秒） |

## 存储

- SQLite 数据库：`storage/webhook.db`
- 上传文件：`storage/uploads/`

## request_id 关联（防串台）

发起聊天时传入 request_id，回复时带回同一个 request_id：

```bash
# 发起请求（带 request_id，走 query 参数）
curl -X POST "http://localhost:8100/webhook/inbound/muse1号?request_id=abc123" \
  -H "Authorization: Bearer $KEY" \
  -F "type=text" -F "content=你好"

# 智能体回复时带上同一个 request_id
curl -X POST "http://localhost:8100/webhook/outbound/muse1号?request_id=abc123" \
  -H "Authorization: Bearer $KEY" \
  -F "type=text" -F "content=你好！我是 Muse"
```

`/v1/chat/completions` 会生成或复用 request_id，并只在本次请求之后等待 `direction=outbound` 的匹配回复；超时返回 504，不会回显用户输入。

## SSE 推流示例

```bash
# 订阅实时消息流
curl -N http://localhost:8100/stream/muse1号 \
  -H "Authorization: Bearer $KEY"
# 输出:
# data: {"id":"...","content":"新消息","timestamp":...}
#
# data: ...
```

连接建立后只推送新消息，不回放历史。

## 示例

```bash
KEY="webhook-default-key-change-me"

# 发送消息
curl -X POST http://localhost:8100/webhook/inbound/boss \
  -H "Authorization: Bearer $KEY" \
  -F "type=text" -F "content=你好"

# 轮询消息
curl "http://localhost:8100/webhook/outbound?name=boss" \
  -H "Authorization: Bearer $KEY"

# OpenAI 兼容
curl -X POST http://localhost:8100/v1/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"webhook","name":"boss","messages":[{"role":"user","content":"hi"}]}'

# 信号接口（无需鉴权）
curl http://localhost:8100/signal/boss
```

## 消息写入（入站/出站均支持三种方式）

**1. multipart/form-data 文件上传**
```bash
curl -X POST http://localhost:8100/webhook/outbound/muse1号 \
  -H "Authorization: Bearer $KEY" \
  -F "type=image" -F "caption=说明" -F "media=@/path/file.jpg"
```

**2. JSON + 外部媒体 URL**
```bash
curl -X POST http://localhost:8100/webhook/outbound/muse1号 \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"type":"video","media_url":"https://xx.com/v.mp4","caption":"说明"}'
```

**3. JSON 纯文本**
```bash
curl -X POST http://localhost:8100/webhook/outbound/muse1号 \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"type":"text","content":"你好"}'
```

type 不传时按文件后缀/Content-Type 自动推断（image/video/audio/file）。