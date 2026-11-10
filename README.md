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

首次启动自动创建 `admin` 账号：设置了 `WEBHOOK_API_KEY` 时初始密码等于该 Key；**未设置 Key 时生成随机密码并在启动日志打印一次**（不再是弱口令 `admin`）。首次登录后强制要求设置新密码，且**服务端**在改密前拒绝所有 operator/admin 级写操作（不只在前端拦）。密码用 PBKDF2-SHA256 加盐存储，会话是签名过的 HttpOnly Cookie（7 天，带 SameSite=Lax）。

| 角色 | 权限 |
|------|------|
| admin | 全部：改设置、改信箱标注、管理账号 |
| operator | 发消息、上传形象与附件 |
| viewer | 只读 |

工作台接口同时接受 Cookie 与 `Authorization: Bearer <API_KEY>`（后者视为 admin），原有 API 客户端行为不变。
**`WEBHOOK_ALLOW_NO_AUTH=1` 不会放开工作台**：无 API Key 时工作台只认 Cookie 登录，绝不把匿名请求当成 admin。
改密码或登出会递增账号的 token 版本，使此前签发的所有旧会话立即失效（当前会话自动重签，不掉线）。

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
| WEBHOOK_CONSOLE_SECURE_COOKIE | auto | `auto`=按实际协议/X-Forwarded-Proto 自动判断；`1` 强制 Secure；`0` 强制关闭（仅本地 HTTP） |

### 安全说明

- 无 `WEBHOOK_API_KEY` 时工作台不因 `WEBHOOK_ALLOW_NO_AUTH` 变成匿名 admin，初始管理员密码随机生成并只打印一次。
- 会话 token 带账号版本号：改密 / 登出后旧 token 立即失效。
- `/api/console/send` 的 `media_url` 只允许 http(s) 绝对地址或本站 `/uploads/` 路径；前端渲染再做一层协议白名单，杜绝 `javascript:`/`data:` 注入。
- 头像读取、除 login/logout 外的所有工作台接口均需登录。
- SSE 实时流使用 `(timestamp, id)` 复合游标，同一秒内的多条消息不会漏。

### 响应式

桌面为左侧导航 + 多栏；≤860px 导航收成抽屉、通信台变成「列表 → 对话」两层栈式导航；链路拓扑在 ≤640px 自动从横向分层改为纵向分层。已在 390 / 820 / 1440px 三档宽度验证 `scrollWidth === clientWidth`，无横向滚动。

### 接入外部可视化外壳（可选）

工作台的数据全部走 HTTP + SSE，后端不假定前端长什么样。所以除了内置的五个页面，也可以把同一批数据喂给别的可视化外壳——3D 沙盘、车间/园区类大屏、自绘 Canvas 看板都行，不需要改后端。

> 本地做过一个实验：把信箱当成员工/车间放进第三方 3D 场景，消息进来就有人走动、车间开工。那套主题素材是第三方作品（PolyForm-Noncommercial-1.0.0），**没有**放进本仓库，这里只记录桥接方法。

**先定映射关系**

| webhook 概念 | 可视化里通常对应 |
|---|---|
| 信箱（`name`） | 一个人、一个工位、一座车间 |
| 角色（`role`） | 部门 / 房间 / 分组 |
| 一次收发（inbound + outbound 配对） | 一个任务单、一张工单 |
| 最近一条消息原文 | 状态栏、进度标题 |
| 窗口内有流量 | 忙碌、开工；无流量则闲置、停机 |

**数据从哪来**

| 需要 | 接口 |
|---|---|
| 信箱名单与标注 | `GET /api/console/agents` |
| 预热用的历史消息 | `GET /api/console/events?limit=200` |
| 增量推送 | `GET /api/console/stream?since=`（SSE） |
| 已配对好的链路 | `GET /api/console/lineage` |

`/events` 与 `/messages` 返回的是同一种实体，字段也保持一致（都含 `request_id`）。这条很重要：外部外壳通常先拉一批历史渲染首屏，再接 SSE 增量，两边拼在一起才完整；缺了 `request_id` 就配不出收/发两侧，首屏的链路会缺一半的边。

**三个实际会踩的坑**

1. **名单必须在挂载场景之前拿到**。不少可视化库在 `mount()` 里一次性组装实体数组，之后再塞数据它不会重建。用同步请求预取，或者干脆把场景初始化推迟到数据到手之后。
2. **用哈希算下标要 `(n >>> 0) % len`**。JS 位运算是有符号 32 位，哈希值 ≥ 2^31 时下标变负，取到 `undefined`，报错位置往往离真正原因很远。
3. **无头浏览器里模拟不会动**。带帧循环的可视化常用 `if (document.hidden) return` 省电，而 headless 环境下 `document.hidden` 恒为 `true`。用自动化截图验证时得手动推一把（多数库留了快进钩子），否则会误判成「数据没接上」。

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