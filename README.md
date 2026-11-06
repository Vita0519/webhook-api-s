# Webhook API

消息收发中间件，SQLite 存储，支持全类型媒体、OpenAI 兼容接口、SSE 实时推流。

## 快速启动

镜像由 GitHub Actions 在 push main 时自动构建并推送 GHCR：

```
ghcr.io/sxkiss/webhook-api:latest
ghcr.io/sxkiss/webhook-api:<commit>
```

国内服务器启动文件使用 NJU 镜像加速地址（同一镜像）：

```
ghcr.nju.edu.cn/sxkiss/webhook-api:latest
```

```bash
cp .env.example .env   # 填入 WEBHOOK_API_KEY
docker compose pull
docker compose up -d
```

启动文件 `docker-compose.yml` 直接使用远程镜像 `ghcr.nju.edu.cn/sxkiss/webhook-api:latest`（`pull_policy: always`），不在本机构建。

本地开发需要自己构建时：

```bash
docker build -t ghcr.nju.edu.cn/sxkiss/webhook-api:latest .
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

## 工作台（网页控制台）

仓库内置一套管理工作台，随服务一起提供，无需单独部署，也不引入任何前端依赖（原生 ES Module）。

| 路径 | 说明 |
|------|------|
| `GET /` | 307 跳转到 `/viz/` |
| `GET /viz/` | 工作台（静态资源位于 `app/static/viz/`） |
| `GET /api/console/*` | 工作台后端接口，见 `app/console.py` |

### 登录与角色

首次启动自动创建 `admin` 账号，**密码等于 `WEBHOOK_API_KEY`**，登录后强制要求设置新密码。密码用 PBKDF2-SHA256 加盐存储，会话是签名过的 HttpOnly Cookie（7 天）。

| 角色 | 权限 |
|------|------|
| admin | 全部：改设置、改信箱标注、管理账号 |
| operator | 发消息、上传形象与附件 |
| viewer | 只读 |

工作台接口同时接受 Cookie 与 `Authorization: Bearer <API_KEY>`（后者视为 admin），原有 API 客户端行为不变。

### 五个页面

| 页面 | 内容 |
|------|------|
| 总览 · 链路 | 指标卡 + 链路拓扑（1 小时 / 24 小时 / 7 天）+ 实时事件流 + 信箱往来排行 |
| 通信台 | 三栏对话：会话列表 / 消息气泡 / 信箱详情，支持图片、音视频、附件 |
| 中继办公室 | 环形工位图：中心中继核，活跃信箱在内环、静默在外环，弧线为真实链路 |
| 信箱与成员 | 自动检测到的信箱清单，可上传形象、改显示名与角色标签 |
| 设置与权限 | 基础设置、公告与规则、账号管理、关于 |

### 信箱是自动检测的

不维护手写名单：`messages` 表里出现过任何 `name`，都会自动成为信箱。人工标注（显示名、角色、备注、形象）单独存在 `console_agents` 表，形象文件在 `storage/avatars/`，两者互不影响；即使一个信箱从未发过消息，也可以先标注好。

### 链路是怎么推断出来的

一条真实链路在库里是两行记录（`inbound.name` 是发送方，`outbound.name` 是接收方），必须配对才能还原：

1. **request_id 精确配对** → 置信度 1.0
2. **时间窗唯一候选**（默认 ±60 秒）→ 0.6
3. **多候选取最近** → 0.35，前端画虚线，可一键隐藏

`relay`（中继自身）与 `console`（工作台发出）作为虚拟节点参与拓扑，便于区分「谁转发的」和「谁从控制台发的」。

### 新增的环境变量（可选）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| WEBHOOK_CONSOLE_SECURE_COOKIE | 关闭 | 设为 1 时 Cookie 带 `Secure`（HTTPS 部署时开启） |

### 响应式

桌面为左侧导航 + 多栏；≤860px 导航收成抽屉、通信台变成「列表 → 对话」两层栈式导航；链路拓扑在 ≤640px 自动从横向分层改为纵向分层。已在 390 / 820 / 1440px 三档宽度验证 `scrollWidth === clientWidth`，无横向滚动。

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