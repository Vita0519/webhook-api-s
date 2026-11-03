# Webhook API

消息收发中间件，支持全类型媒体、OpenAI 兼容接口。

## 快速启动

```bash
cd /home/sxkiss/webhook-api
docker compose up -d
```

## API 端点

### 入站（接收方推送消息进来）
- `POST /webhook/inbound/{name}` — 带名称的入站
- `POST /webhook/inbound` — 不带名称

支持 form-data 上传文件（图片/视频/音频/文档）

### 出站轮询（轮询取消息发给接收方）
- `GET /webhook/outbound?name={name}&since={timestamp}&limit=50`

### OpenAI 兼容接口
- `POST /v1/chat/completions`

### 管理接口
- `GET /api/messages?name={name}&limit=50`
- `DELETE /api/messages/{id}`
- `POST /api/cleanup`

## 鉴权

所有接口需要 Header：`Authorization: Bearer <API_KEY>`

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| WEBHOOK_API_KEY | webhook-default-key-change-me | API 密钥 |
| WEBHOOK_POLL_INTERVAL | 2 | 轮询间隔（秒） |
| WEBHOOK_MSG_RETENTION | 7 | 消息保留天数 |

## 存储

- `storage/messages/` — 消息 JSON 文件
- `storage/uploads/` — 上传的媒体文件

## 示例

```bash
# 发送消息
curl -X POST http://localhost:8100/webhook/inbound/boss \
  -H "Authorization: Bearer sk-my-secret-key" \
  -F "type=text" \
  -F "content=你好"

# 轮询消息
curl "http://localhost:8100/webhook/outbound?name=boss" \
  -H "Authorization: Bearer sk-my-secret-key"

# OpenAI 兼容
curl -X POST http://localhost:8100/v1/chat/completions \
  -H "Authorization: Bearer sk-my-secret-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"webhook","name":"boss","messages":[{"role":"user","content":"hi"}]}'
```

## 信号接口（公开，无需鉴权）

```
GET /signal/{name}
```

返回：
```json
{"name": "muse1号", "count": 2, "latest_timestamp": 1793685876.4}
```

用途：外部检测脚本/钩子每 5 秒轮询一次，发现 count 或 latest_timestamp 变化就唤醒处理流程，再用 API_KEY 调 `/webhook/outbound` 取具体内容。这样检测脚本本身不需要携带任何密钥。

## 当前 API_KEY

`webhook-default-key-change-me`（在 .env 中修改 `WEBHOOK_API_KEY` 后 `docker compose up -d` 生效）

## 消息方向（重要）

- `POST /webhook/inbound/{name}` — 写入 **inbound** 消息（用户发给智能体）
- `POST /webhook/outbound/{name}` — 写入 **outbound** 消息（智能体回复）
- 两者参数完全一致：`type` / `content` / `caption` / `media`，都需 Bearer 鉴权

### 轮询取消息

```
GET /webhook/outbound?name=xxx&since=时间戳&direction=inbound|outbound&limit=50
```

`direction` 可选，用于只取某一方向的消息（比如智能体只取 inbound 当输入）。

### 示例

```bash
# 智能体回复（写 outbound）
curl -X POST http://localhost:8100/webhook/outbound/muse1号 \
  -H "Authorization: Bearer webhook-default-key-change-me" \
  -F "type=text" -F "content=收到，我处理好了"

# 只取发给我的消息
curl "http://localhost:8100/webhook/outbound?name=muse1号&direction=inbound" \
  -H "Authorization: Bearer webhook-default-key-change-me"
```

## 存储

容器内存储路径 `/app/storage`（挂载到宿主机 `./storage`），可用 `WEBHOOK_STORAGE_DIR` 覆盖。容器重启数据不丢。

## 消息写入（入站/出站均支持三种方式）

入站 `POST /webhook/inbound/{name}` 和出站 `POST /webhook/outbound/{name}` 参数完全一致，都支持：

**1. multipart/form-data 文件上传**（落盘到本地 storage/uploads/）
```bash
curl -X POST http://localhost:8100/webhook/outbound/muse1号 \
  -H "Authorization: Bearer $KEY" \
  -F "type=image" -F "caption=说明" -F "media=@/path/file.jpg"
```

**2. JSON + 外部媒体 URL**（直接引用，不下载）
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
