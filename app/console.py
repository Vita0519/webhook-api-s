"""工作台后端：账号权限、信箱检测、链路推断、实时推送。

设计约束：
- 本模块不 import app.main，由 main 在启动时调用 configure()，避免循环依赖。
- 只读写自己新增的三张表，不动 messages 表结构，现有 API 行为不变。
- 鉴权同时接受 Session Cookie 与 Bearer API_KEY（后者视为 admin）。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from base64 import b64decode, b64encode
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

# ─── 常量 ───────────────────────────────────────────────────────────────────

COOKIE_NAME = "wh_console"
COOKIE_TTL = 7 * 86400
PBKDF2_ROUNDS = 120_000
ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}
ROLE_LABEL = {"viewer": "只读", "operator": "操作员", "admin": "管理员"}
WINDOWS = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ONLINE_WINDOW = 300          # 5 分钟内算在线
ACTIVE_WINDOW = 3600         # 1 小时内算活跃
SESSION_SECRET_KEY = "session_secret"

DEFAULT_SETTINGS: dict[str, Any] = {
    "site_name": "Webhook 中继台",
    "default_window": "24h",
    "refresh_ms": 4000,
    "show_inferred": True,
    "notices": [],
    "rules": [],
}

_STATE: dict[str, Any] = {
    "storage": None,
    "db": None,
    "avatars": None,
    "uploads": None,
    "api_key": "",
    "allow_no_auth": False,
    "ready": False,
}
_login_fails: dict[str, list[float]] = {}


# ─── 初始化 ─────────────────────────────────────────────────────────────────

def configure(storage_dir: str | Path, api_key: str = "", allow_no_auth: bool = False) -> None:
    """由 app.main 调用，注入存储路径与密钥。"""
    storage = Path(storage_dir)
    storage.mkdir(parents=True, exist_ok=True)
    avatars = storage / "avatars"
    avatars.mkdir(parents=True, exist_ok=True)
    uploads = storage / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)

    _STATE.update(
        storage=storage,
        db=storage / "webhook.db",
        avatars=avatars,
        uploads=uploads,
        api_key=(api_key or "").strip(),
        allow_no_auth=allow_no_auth,
        ready=True,
    )
    _init_db()
    _bootstrap_admin()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_STATE["db"], timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _q(sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    conn = _conn()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _q_all(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = _conn()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _exec(sql: str, params: tuple = ()) -> int:
    conn = _conn()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _init_db() -> None:
    conn = _conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS console_users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                created_at REAL NOT NULL,
                last_login REAL,
                must_change INTEGER NOT NULL DEFAULT 0,
                token_version INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS console_agents (
                name TEXT PRIMARY KEY,
                display_name TEXT,
                role TEXT,
                note TEXT,
                color TEXT,
                avatar TEXT,
                avatar_ts REAL,
                deleted_at REAL,
                pinned INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS console_settings (
                k TEXT PRIMARY KEY,
                v TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()
    # 旧库升级：补 token_version 列（已存在则忽略）
    try:
        _exec("ALTER TABLE console_users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # 旧库升级：信箱的「删除水位线」与「人工固定」
    # deleted_at：手动删除信箱的时间点。消息时间晚于它才算「又有通信」，信箱才会重新出现。
    # pinned：人工恢复/人工新建的标注，即使没有任何消息也保持可见。
    for ddl in (
        "ALTER TABLE console_agents ADD COLUMN deleted_at REAL",
        "ALTER TABLE console_agents ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            _exec(ddl)
        except sqlite3.OperationalError:
            pass


def _bootstrap_admin() -> None:
    """首次启动建一个 admin。密码取 API_KEY；无 API_KEY 时生成随机密码并打印一次。
    INSERT OR IGNORE：多 worker 并发启动时只有真正插入成功的那个才打印密码。"""
    has_key = bool(_STATE["api_key"])
    password = _STATE["api_key"] if has_key else secrets.token_urlsafe(12)
    try:
        inserted = _exec(
            "INSERT OR IGNORE INTO console_users (id, username, password_hash, role, created_at, must_change) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (str(secrets.token_hex(8)), "admin", _hash_pw(password), "admin", time.time()),
        )
    except sqlite3.IntegrityError:
        return  # 另一个 worker 已创建
    if not has_key and inserted:
        print(
            "[console] 未设置 WEBHOOK_API_KEY，已生成随机初始管理员密码（仅本次打印）：\n"
            f"[console]   admin / {password}\n"
            "[console] 登录后请立即修改密码。",
            flush=True,
        )


# ─── 密码与会话 ─────────────────────────────────────────────────────────────

def _hash_pw(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${b64encode(salt).decode()}${b64encode(dk).decode()}"


def _verify_pw(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), b64decode(salt_b64), int(rounds)
        )
        return hmac.compare_digest(dk, b64decode(hash_b64))
    except Exception:
        return False


def _secret() -> bytes:
    """会话签名密钥：首次生成后落库，重启不掉线。"""
    row = _q("SELECT v FROM console_settings WHERE k = ?", (SESSION_SECRET_KEY,))
    if not row:
        value = secrets.token_hex(32)
        _exec("INSERT OR IGNORE INTO console_settings (k, v) VALUES (?, ?)",
              (SESSION_SECRET_KEY, value))
        row = _q("SELECT v FROM console_settings WHERE k = ?", (SESSION_SECRET_KEY,))
        return str(row["v"]).encode()
    return str(row["v"]).encode()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def _make_token(user_id: str, token_version: int = 0) -> str:
    payload = f"{user_id}.{token_version}.{int(time.time()) + COOKIE_TTL}"
    return f"{payload}.{_sign(payload)}"


def _read_token(token: str) -> Optional[sqlite3.Row]:
    try:
        user_id, ver, exp, sig = token.rsplit(".", 3)
        if not hmac.compare_digest(_sign(f"{user_id}.{ver}.{exp}"), sig):
            return None
        if int(exp) < time.time():
            return None
    except Exception:
        return None
    row = _q("SELECT * FROM console_users WHERE id = ?", (user_id,))
    if row and int(row["token_version"] or 0) != int(ver):
        return None  # 改密后旧会话全部失效
    return row


def _safe_media_url(url: str) -> bool:
    """只放行 http(s) 绝对地址与本站 /uploads/ 相对路径，挡住 javascript:/data: 等。"""
    u = (url or "").strip()
    if not u:
        return False
    low = u.lower()
    if low.startswith(("javascript:", "data:", "vbscript:", "file:")):
        return False
    if u.startswith("/uploads/"):
        return ".." not in u and "\\" not in u
    from urllib.parse import urlparse
    parsed = urlparse(u)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _user_public(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "role_label": ROLE_LABEL.get(row["role"], row["role"]),
        "created_at": row["created_at"],
        "last_login": row["last_login"],
        "must_change": bool(row["must_change"]),
    }


def _bearer_ok(authorization: Optional[str]) -> bool:
    key = _STATE["api_key"]
    if not key:
        # 无 API_KEY 时绝不把「无 Bearer」当成 admin：工作台只认 cookie 登录。
        # ALLOW_NO_AUTH 只影响既有 webhook 接口，不放大到工作台。
        return False
    if not authorization or not authorization.startswith("Bearer "):
        return False
    return hmac.compare_digest(authorization[7:].strip(), key)


def current_user(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> dict:
    """Cookie 或 Bearer 二选一。Bearer 等同于 admin（服务账号）。"""
    if _bearer_ok(authorization):
        return {
            "id": "token",
            "username": "service-token",
            "role": "admin",
            "role_label": "服务令牌",
            "via": "bearer",
            "must_change": False,
        }
    token = request.cookies.get(COOKIE_NAME)
    row = _read_token(token) if token else None
    if not row:
        raise HTTPException(401, "未登录或会话已过期", headers={"WWW-Authenticate": "Cookie"})
    user = _user_public(row)
    user["via"] = "cookie"
    return user


def need(min_role: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        if ROLE_RANK.get(user["role"], 0) < ROLE_RANK[min_role]:
            raise HTTPException(403, f"需要 {ROLE_LABEL[min_role]} 及以上权限")
        if user.get("must_change") and ROLE_RANK[min_role] >= ROLE_RANK["operator"]:
            raise HTTPException(403, "初始密码未修改，请先修改密码后再执行写操作")
        return user

    return dep


def _set_session_cookie(request: Request, response: Response, user_id: str, token_version: int) -> None:
    """统一签发会话 Cookie。secure 默认按实际协议判断（兼容反代 X-Forwarded-Proto）。"""
    forced = os.getenv("WEBHOOK_CONSOLE_SECURE_COOKIE", "auto")
    if forced == "1":
        secure = True
    elif forced == "0":
        secure = False
    else:
        proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
        secure = proto == "https"
    response.set_cookie(
        COOKIE_NAME,
        _make_token(user_id, int(token_version or 0)),
        max_age=COOKIE_TTL,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


# ─── 设置读写 ───────────────────────────────────────────────────────────────

def _load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    for row in _q_all("SELECT k, v FROM console_settings"):
        if row["k"] == SESSION_SECRET_KEY:
            continue
        try:
            data[row["k"]] = json.loads(row["v"])
        except Exception:
            data[row["k"]] = row["v"]
    return data


def _save_settings(patch: dict) -> dict:
    for key, value in patch.items():
        if key == SESSION_SECRET_KEY:
            continue
        _exec("INSERT OR REPLACE INTO console_settings (k, v) VALUES (?, ?)",
              (key, json.dumps(value, ensure_ascii=False)))
    return _load_settings()


# ─── 信箱（agent）───────────────────────────────────────────────────────────

def _annotations() -> dict[str, dict]:
    return {row["name"]: dict(row) for row in _q_all("SELECT * FROM console_agents")}


def _avatar_url(name: str, ann: Optional[dict]) -> str:
    if not ann or not ann.get("avatar"):
        return ""
    ts = int(ann.get("avatar_ts") or 0)
    return f"/api/console/avatar/{quote(name)}?v={ts}"


def _status_of(last_ts: Optional[float], now: float) -> str:
    if not last_ts:
        return "empty"
    delta = now - last_ts
    if delta <= ONLINE_WINDOW:
        return "online"
    if delta <= ACTIVE_WINDOW:
        return "active"
    if delta <= 86400:
        return "idle"
    return "silent"


def _discover_agents() -> tuple[list[dict], dict[str, dict]]:
    """从 messages 动态发现信箱，叠加人工标注。不依赖任何手写名单。

    返回「全部」信箱，每项带 state（可见性）：
      visible  正常显示
      deleted  被手动删除，且删除后没有新通信 —— 仍在返回值里，供界面「恢复」用
      empty    收和发都没有任何消息（空壳标注）—— 自动忽略
    调用方自行按 state 过滤；_collect_agents 只给可见的那批。
    """
    now = time.time()
    rows = _q_all(
        """
        SELECT name,
               SUM(CASE WHEN direction = 'inbound' THEN 1 ELSE 0 END)  AS inbound,
               SUM(CASE WHEN direction = 'outbound' THEN 1 ELSE 0 END) AS outbound,
               COUNT(*) AS total,
               MIN(timestamp) AS first_ts,
               MAX(timestamp) AS last_ts,
               SUM(CASE WHEN timestamp >= ? THEN 1 ELSE 0 END) AS recent
        FROM messages
        WHERE name IS NOT NULL AND name != ''
        GROUP BY name
        """,
        (now - 86400,),
    )
    ann = _annotations()
    found: dict[str, dict] = {}

    for row in rows:
        name = row["name"]
        found[name] = {
            "name": name,
            "inbound": int(row["inbound"] or 0),
            "outbound": int(row["outbound"] or 0),
            "total": int(row["total"] or 0),
            "recent": int(row["recent"] or 0),
            "first_ts": row["first_ts"],
            "last_ts": row["last_ts"],
        }

    # 只在标注表里、还没有消息的信箱也列出来，方便提前配好形象
    for name, meta in ann.items():
        found.setdefault(name, {
            "name": name, "inbound": 0, "outbound": 0,
            "total": 0, "recent": 0, "first_ts": None, "last_ts": None,
        })

    agents = []
    for name, item in found.items():
        meta = ann.get(name, {})
        deleted_at = meta.get("deleted_at")
        state = "visible"

        if deleted_at:
            last_ts = item["last_ts"] or 0
            if last_ts > deleted_at:
                # 删除之后又有通信了：自动复活，水位线清掉
                _exec("UPDATE console_agents SET deleted_at = NULL WHERE name = ?", (name,))
            else:
                state = "deleted"

        item["display_name"] = meta.get("display_name") or name
        item["role"] = meta.get("role") or ""
        item["note"] = meta.get("note") or ""
        item["color"] = meta.get("color") or ""
        item["avatar"] = _avatar_url(name, meta)
        item["has_custom_avatar"] = bool(meta.get("avatar"))
        item["status"] = _status_of(item["last_ts"], now)
        item["pinned"] = bool(meta.get("pinned"))
        item["deleted_at"] = None if state == "visible" and deleted_at else deleted_at
        # 收和发都没有信息 → 自动忽略。人工固定（pinned）过的除外。
        if state == "visible" and item["total"] == 0 and not item["pinned"]:
            state = "empty"
        item["state"] = state
        agents.append(item)

    agents.sort(key=lambda a: (-a["recent"], -a["total"], a["name"]))
    return agents, ann


def _collect_agents() -> tuple[list[dict], dict[str, dict]]:
    """只要可见信箱（KPI、通信台、大屏都用这个口径）。"""
    agents, ann = _discover_agents()
    return [a for a in agents if a["state"] == "visible"], ann


# ─── 链路推断 ───────────────────────────────────────────────────────────────

def _meta(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _pair_window() -> float:
    try:
        return float(_load_settings().get("pair_window") or 60)
    except Exception:
        return 60.0


def build_lineage(
    rows: list[sqlite3.Row],
    since: float,
    *,
    include_inferred: bool = True,
    pair_window: float = 60.0,
) -> dict:
    """把「半条边」拼起来。

    inbound.name  = 发送方（谁发进中继）
    outbound.name = 接收方（中继发给谁）
    一条真实链路 X→Y 在库里是两行记录，必须配对才能还原。
    """
    ins: list[dict] = []
    outs: list[dict] = []
    # 控制台发出的消息：语义上是 console → 该信箱。
    # 新数据是 inbound（人发给信箱），早期数据是 outbound（当时写错了方向）。
    console_ins: list[dict] = []

    for row in rows:
        name = row["name"]
        if not name:
            continue
        meta = _meta(row["metadata"] if "metadata" in row.keys() else None)
        item = {
            "id": row["id"],
            "name": name,
            "ts": row["timestamp"],
            "rid": row["request_id"],
            "type": row["type"],
            "via": (meta.get("via") or "").strip(),
        }
        if row["direction"] == "inbound":
            if item["via"] == "console":
                console_ins.append(item)      # 控制台发的，不当成「外部进来」
            else:
                ins.append(item)
        else:
            outs.append(item)

    edges: dict[tuple[str, str], dict] = {}

    def add_edge(src: str, dst: str, conf: float, ts: float, mid: str, kind: str) -> None:
        if not src or not dst or src == dst:
            return
        key = (src, dst)
        edge = edges.get(key)
        if edge is None:
            edge = {
                "from": src, "to": dst, "count": 0, "confidence": conf,
                "last_ts": ts, "kinds": [], "samples": [],
            }
            edges[key] = edge
        edge["count"] += 1
        edge["confidence"] = max(edge["confidence"], conf)
        edge["last_ts"] = max(edge["last_ts"], ts)
        if kind not in edge["kinds"]:
            edge["kinds"].append(kind)
        if len(edge["samples"]) < 3:
            edge["samples"].append(mid)

    by_rid_in: dict[str, list[dict]] = {}
    by_rid_out: dict[str, list[dict]] = {}
    for item in ins:
        if item["rid"]:
            by_rid_in.setdefault(item["rid"], []).append(item)
    for item in outs:
        if item["rid"]:
            by_rid_out.setdefault(item["rid"], []).append(item)

    handled_out: set[str] = set()

    # 控制台发出去的：一条边 console → 信箱；同时记住 request_id，
    # 这样信箱的回复能配回 console，而不是被当成「中继匿名转发」。
    console_rid: dict[str, str] = {}
    for c in console_ins:
        add_edge("console", c["name"], 1.0, c["ts"], c["id"], "rid")
        if c["rid"]:
            console_rid[c["rid"]] = c["name"]

    # 规则一：request_id 精确配对（置信 1.0）
    for rid in set(by_rid_in) | set(by_rid_out):
        ilist = by_rid_in.get(rid, [])
        olist = by_rid_out.get(rid, [])
        console_round = any(o["via"] == "console" for o in olist) or rid in console_rid

        for out in olist:
            handled_out.add(out["id"])
            if out["via"] == "console":
                add_edge("console", out["name"], 1.0, out["ts"], out["id"], "rid")
                continue
            if rid in console_rid and out["name"] == console_rid[rid]:
                add_edge(out["name"], "console", 1.0, out["ts"], out["id"], "rid")
                continue
            if ilist:
                before = [i for i in ilist if i["ts"] <= out["ts"] + 5]
                src = (before[-1] if before else ilist[0])["name"]
                add_edge(src, out["name"], 1.0, out["ts"], out["id"], "rid")
            else:
                add_edge("relay", out["name"], 1.0, out["ts"], out["id"], "rid")

        if console_round:
            for inbound in ilist:
                add_edge(inbound["name"], "console", 1.0,
                         inbound["ts"], inbound["id"], "rid")

    # 规则二：时间窗唯一候选（0.6）/ 多候选取最近（0.35）
    for out in outs:
        if out["id"] in handled_out:
            continue
        if out["via"] == "console":
            add_edge("console", out["name"], 1.0, out["ts"], out["id"], "rid")
            continue

        candidates = [
            i for i in ins
            if 0 <= out["ts"] - i["ts"] <= pair_window and i["name"] != out["name"]
        ]
        if not candidates:
            add_edge("relay", out["name"], 0.8, out["ts"], out["id"], "relay")
            continue

        candidates.sort(key=lambda i: out["ts"] - i["ts"])
        nearest = candidates[0]
        best_gap = out["ts"] - nearest["ts"]
        rivals = [c for c in candidates if (out["ts"] - c["ts"]) - best_gap < 5]
        conf = 0.6 if len(rivals) == 1 else 0.35
        add_edge(nearest["name"], out["name"], conf, out["ts"], out["id"], "window")

    # 节点聚合：窗口内出现过的信箱都要有，孤立节点也画出来
    node_stats: dict[str, dict] = {}

    def node(name: str) -> dict:
        if name not in node_stats:
            node_stats[name] = {
                "name": name, "inbound": 0, "outbound": 0,
                "last_ts": 0.0, "volume": 0,
                "virtual": name in ("relay", "console"),
            }
        return node_stats[name]

    for item in ins:
        n = node(item["name"])
        n["inbound"] += 1
        n["volume"] += 1
        n["last_ts"] = max(n["last_ts"], item["ts"])
    for item in outs:
        n = node(item["name"])
        n["outbound"] += 1
        n["volume"] += 1
        n["last_ts"] = max(n["last_ts"], item["ts"])

    edge_list = []
    for edge in edges.values():
        if not include_inferred and edge["confidence"] < 0.5:
            continue
        edge["inferred"] = edge["confidence"] < 0.5
        edge_list.append(edge)
        node(edge["from"])
        node(edge["to"])

    # 虚拟节点（中继 / 控制台）不会以自己的名字落库，用边上的流量补齐计数
    for edge in edge_list:
        src = node_stats.get(edge["from"])
        if src and src["virtual"]:
            src["outbound"] += edge["count"]
            src["volume"] += edge["count"]
        dst = node_stats.get(edge["to"])
        if dst and dst["virtual"]:
            dst["inbound"] += edge["count"]
            dst["volume"] += edge["count"]

    ann = _annotations()
    now = time.time()
    nodes = []
    for name, stats in node_stats.items():
        meta = ann.get(name, {})
        stats["degree"] = sum(
            1 for e in edge_list if e["from"] == name or e["to"] == name
        )
        stats["display_name"] = meta.get("display_name") or name
        stats["role"] = meta.get("role") or ""
        stats["avatar"] = _avatar_url(name, meta)
        stats["status"] = _status_of(stats["last_ts"] or None, now)
        nodes.append(stats)

    nodes.sort(key=lambda n: (-n["volume"], n["name"]))
    edge_list.sort(key=lambda e: -e["count"])

    total_out = len(outs)
    paired = sum(1 for e in edge_list if "rid" in e["kinds"])
    return {
        "since": since,
        "nodes": nodes,
        "edges": edge_list,
        "stats": {
            "messages": len(rows),
            "inbound": len(ins),
            "outbound": len(outs),
            "nodes": len([n for n in nodes if not n["virtual"]]),
            "edges": len(edge_list),
            "paired": paired,
            "paired_ratio": round(len([e for e in edge_list if "rid" in e["kinds"]]) / max(len(edge_list), 1), 3),
            "inferred": len([e for e in edge_list if e["inferred"]]),
            "unpaired_outbound": total_out - len(handled_out),
        },
    }


def _window_seconds(window: str) -> int:
    return WINDOWS.get(window, WINDOWS["24h"])


def _lineage_rows(since: float, limit: int = 20000) -> list[sqlite3.Row]:
    return _q_all(
        """
        SELECT id, request_id, name, direction, timestamp, type, metadata
        FROM messages
        WHERE timestamp >= ? AND name IS NOT NULL AND name != ''
        ORDER BY timestamp ASC
        LIMIT ?
        """,
        (since, limit),
    )


# ─── 请求模型 ───────────────────────────────────────────────────────────────

class LoginIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    old: str
    new: str


class UserIn(BaseModel):
    username: str
    password: str
    role: str = "viewer"


class AgentPatch(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    note: Optional[str] = None
    color: Optional[str] = None


class SendIn(BaseModel):
    name: str
    content: str
    type: str = "text"
    media_url: Optional[str] = None
    request_id: Optional[str] = None


# ─── 路由 ───────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/console", tags=["console"])


@router.post("/login")
def login(request: Request, body: LoginIn, response: Response) -> dict:
    username = body.username.strip()
    now = time.time()
    fails = [t for t in _login_fails.get(username, []) if now - t < 300]
    _login_fails[username] = fails
    if len(fails) >= 5:
        raise HTTPException(429, "尝试次数过多，请 5 分钟后再试")

    row = _q("SELECT * FROM console_users WHERE username = ?", (username,))
    if not row or not _verify_pw(body.password, row["password_hash"]):
        fails.append(now)
        _login_fails[username] = fails
        raise HTTPException(401, "用户名或密码错误")

    _login_fails.pop(username, None)
    _exec("UPDATE console_users SET last_login = ? WHERE id = ?", (now, row["id"]))
    _set_session_cookie(request, response, row["id"], row["token_version"])
    fresh = _q("SELECT * FROM console_users WHERE id = ?", (row["id"],))
    return {"ok": True, "user": _user_public(fresh)}


@router.post("/logout")
def logout(request: Request, response: Response, authorization: Optional[str] = Header(None)) -> dict:
    # Cookie 登录的会话：递增 token_version 使已签发的 token 立即失效（真正吊销）
    tok = request.cookies.get(COOKIE_NAME)
    if tok and not _bearer_ok(authorization):
        try:
            user_id = tok.rsplit(".", 3)[0]
            _exec("UPDATE console_users SET token_version = token_version + 1 WHERE id = ?", (user_id,))
        except Exception:
            pass
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user)) -> dict:
    settings = _load_settings()
    return {
        "user": user,
        "settings": settings,
        "roles": [{"value": k, "label": v} for k, v in ROLE_LABEL.items()],
    }


@router.post("/password")
def change_password(request: Request, body: PasswordIn, response: Response, user: dict = Depends(current_user)) -> dict:
    if user["id"] == "token":
        raise HTTPException(400, "服务令牌不通过此接口改密码")
    if len(body.new) < 8:
        raise HTTPException(400, "新密码至少 8 位")
    row = _q("SELECT * FROM console_users WHERE id = ?", (user["id"],))
    if not row:
        raise HTTPException(404, "账号不存在")
    # 初始密码（即 API Key）状态下允许免旧密码直接改，其余情况必须校验
    if not (row["must_change"] and not body.old):
        if not _verify_pw(body.old, row["password_hash"]):
            raise HTTPException(401, "原密码不正确")
    _exec(
        "UPDATE console_users SET password_hash = ?, must_change = 0, "
        "token_version = token_version + 1 WHERE id = ?",
        (_hash_pw(body.new), user["id"]),
    )
    # 就地重签当前会话（版本号 +1），其余设备旧 token 全部失效，当前不掉线
    fresh = _q("SELECT * FROM console_users WHERE id = ?", (user["id"],))
    if fresh and user.get("via") == "cookie":
        _set_session_cookie(request, response, fresh["id"], fresh["token_version"])
    return {"ok": True}


@router.get("/overview")
def overview(window: str = "24h", user: dict = Depends(current_user)) -> dict:
    since = time.time() - _window_seconds(window)
    agents, _ann = _collect_agents()
    rows = _lineage_rows(since)
    lineage = build_lineage(rows, since, include_inferred=True, pair_window=_pair_window())

    buckets = 24
    span = max(time.time() - since, 1)
    series = [{"ts": since + span * i / buckets, "in": 0, "out": 0} for i in range(buckets)]
    for row in rows:
        idx = int((row["timestamp"] - since) / span * buckets)
        idx = min(max(idx, 0), buckets - 1)
        key = "in" if row["direction"] == "inbound" else "out"
        series[idx][key] += 1

    online = len([a for a in agents if a["status"] in ("online", "active")])
    return {
        "window": window,
        "since": since,
        "agents": agents,
        "kpis": {
            "inboxes": len(agents),
            "online": online,
            "messages": lineage["stats"]["messages"],
            "inbound": lineage["stats"]["inbound"],
            "outbound": lineage["stats"]["outbound"],
            "edges": lineage["stats"]["edges"],
            "inferred": lineage["stats"]["inferred"],
            "paired_ratio": lineage["stats"]["paired_ratio"],
        },
        "series": series,
        "top": [
            {"name": a["display_name"], "raw": a["name"], "volume": a["total"]}
            for a in sorted(agents, key=lambda a: -a["total"])[:5]
        ],
    }


@router.get("/agents")
def list_agents(user: dict = Depends(current_user)) -> dict:
    everything, _ann = _discover_agents()
    visible = [a for a in everything if a["state"] == "visible"]
    hidden = [a for a in everything if a["state"] == "empty"]
    deleted = [a for a in everything if a["state"] == "deleted"]
    return {
        "agents": visible,
        "count": len(visible),
        # 自动忽略（收和发都没有信息）与已删除的信箱单独返回，界面上可以恢复
        "hidden": hidden,
        "deleted": deleted,
    }


@router.patch("/agents/{name}")
def patch_agent(name: str, body: AgentPatch, user: dict = Depends(need("admin"))) -> dict:
    exists = _q("SELECT 1 FROM console_agents WHERE name = ?", (name,))
    if not exists:
        # 人工新建的标注 = 人工认可它存在，pin 住，别被「空壳自动忽略」吃掉
        _exec("INSERT INTO console_agents (name, pinned) VALUES (?, 1)", (name,))
    fields, values = [], []
    for key in ("display_name", "role", "note", "color"):
        value = getattr(body, key)
        if value is not None:
            fields.append(f"{key} = ?")
            values.append(value.strip() or None)
    if fields:
        values.append(name)
        _exec(f"UPDATE console_agents SET {', '.join(fields)} WHERE name = ?", tuple(values))
    everything, _ann = _discover_agents()
    return {"ok": True, "agent": next((a for a in everything if a["name"] == name), None)}


@router.delete("/agents/{name}")
def delete_agent(name: str, user: dict = Depends(need("admin"))) -> dict:
    """手动删除信箱：只打一条删除水位线，消息与标注都保留。

    删除后它从列表消失；等这个信箱再有通信（消息时间晚于水位线），
    例如桥接又推来一条消息，它会自动重新出现。
    """
    exists = _q("SELECT 1 FROM console_agents WHERE name = ?", (name,))
    if not exists:
        _exec("INSERT INTO console_agents (name) VALUES (?)", (name,))
    _exec(
        "UPDATE console_agents SET deleted_at = ?, pinned = 0 WHERE name = ?",
        (time.time(), name),
    )
    return {"ok": True, "deleted": name, "until_next_message": True}


@router.post("/agents/{name}/restore")
def restore_agent(name: str, user: dict = Depends(need("admin"))) -> dict:
    """恢复信箱：清掉删除水位线并 pin 住。

    pin 是为了让「本来就没有消息」的空壳信箱恢复后也留得住，
    否则会被自动忽略规则立刻再收走。
    """
    exists = _q("SELECT 1 FROM console_agents WHERE name = ?", (name,))
    if not exists:
        _exec("INSERT INTO console_agents (name) VALUES (?)", (name,))
    _exec(
        "UPDATE console_agents SET deleted_at = NULL, pinned = 1 WHERE name = ?",
        (name,),
    )
    everything, _ann = _discover_agents()
    return {"ok": True, "agent": next((a for a in everything if a["name"] == name), None)}


def _sniff_image(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"GIF8"):
        return "gif"
    return None


@router.post("/agents/{name}/avatar")
async def upload_avatar(
    name: str,
    file: UploadFile,
    user: dict = Depends(need("operator")),
) -> dict:
    data = await file.read()
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(413, f"图片需小于 {MAX_AVATAR_BYTES // 1024 // 1024}MB")
    ext = _sniff_image(data)
    if not ext:
        raise HTTPException(415, "只支持 PNG / JPEG / WEBP / GIF")

    folder: Path = _STATE["avatars"]
    stem = hashlib.sha1(name.encode()).hexdigest()[:24]
    for old in folder.glob(f"{stem}.*"):
        old.unlink(missing_ok=True)
    target = folder / f"{stem}.{ext}"
    target.write_bytes(data)

    now = time.time()
    exists = _q("SELECT 1 FROM console_agents WHERE name = ?", (name,))
    if not exists:
        _exec("INSERT INTO console_agents (name) VALUES (?)", (name,))
    _exec(
        "UPDATE console_agents SET avatar = ?, avatar_ts = ? WHERE name = ?",
        (target.name, now, name),
    )
    return {"ok": True, "url": f"/api/console/avatar/{quote(name)}?v={int(now)}"}


@router.delete("/agents/{name}/avatar")
def delete_avatar(name: str, user: dict = Depends(need("operator"))) -> dict:
    row = _q("SELECT avatar FROM console_agents WHERE name = ?", (name,))
    if row and row["avatar"]:
        (_STATE["avatars"] / row["avatar"]).unlink(missing_ok=True)
        _exec("UPDATE console_agents SET avatar = NULL, avatar_ts = NULL WHERE name = ?", (name,))
    return {"ok": True}


@router.get("/avatar/{name}")
def get_avatar(name: str, user: dict = Depends(current_user)):
    row = _q("SELECT avatar FROM console_agents WHERE name = ?", (name,))
    if not row or not row["avatar"]:
        raise HTTPException(404, "没有自定义形象")
    path: Path = _STATE["avatars"] / row["avatar"]
    if not path.is_file():
        raise HTTPException(404, "形象文件丢失")
    media = {"png": "image/png", "jpg": "image/jpeg",
             "webp": "image/webp", "gif": "image/gif"}.get(path.suffix.lstrip("."), "application/octet-stream")
    return FileResponse(path, media_type=media,
                        headers={"Cache-Control": "public, max-age=60"})


@router.get("/lineage")
def lineage(
    window: str = "24h",
    include_inferred: bool = True,
    user: dict = Depends(current_user),
) -> dict:
    since = time.time() - _window_seconds(window)
    rows = _lineage_rows(since)
    data = build_lineage(
        rows, since,
        include_inferred=include_inferred,
        pair_window=_pair_window(),
    )
    data["window"] = window
    return data


@router.get("/events")
def events(limit: int = Query(40, ge=1, le=200), user: dict = Depends(current_user)) -> dict:
    """最近事件流：跨信箱的原始消息，供总览右侧的时间线使用。"""
    rows = _q_all("SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?", (limit,))
    items = []
    for row in reversed(rows):
        content = (row["content"] or "").replace("\n", " ").strip()
        items.append({
            "id": row["id"],
            "name": row["name"] or "未命名",
            "direction": row["direction"],
            "type": row["type"],
            "content": content[:140],
            "media_url": row["media_url"],
            "request_id": row["request_id"],
            "timestamp": row["timestamp"],
        })
    return {"events": items, "count": len(items)}


@router.get("/messages")
def messages(
    name: str = Query(..., description="信箱名"),
    limit: int = Query(60, ge=1, le=200),
    before: Optional[float] = Query(None, description="取这个时间戳之前的历史"),
    after: Optional[float] = Query(None, description="增量拉取"),
    user: dict = Depends(current_user),
) -> dict:
    sql = ["SELECT * FROM messages WHERE name = ?"]
    params: list[Any] = [name]
    if before is not None:
        sql.append("AND timestamp < ?")
        params.append(before)
    if after is not None:
        sql.append("AND timestamp > ?")
        params.append(after)
    sql.append("ORDER BY timestamp DESC LIMIT ?")
    params.append(limit)
    rows = _q_all(" ".join(sql), tuple(params))
    items = []
    for row in reversed(rows):
        meta = _meta(row["metadata"])
        items.append({
            "id": row["id"],
            "request_id": row["request_id"],
            "type": row["type"],
            "content": row["content"],
            "media_url": row["media_url"],
            "name": row["name"],
            "direction": row["direction"],
            "timestamp": row["timestamp"],
            "via": meta.get("via") or "",
            "author": meta.get("author") or "",
        })
    return {"messages": items, "count": len(items), "name": name}


@router.post("/send")
def send(body: SendIn, user: dict = Depends(need("operator"))) -> dict:
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "内容不能为空")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "缺少接收方信箱")
    if body.media_url and not _safe_media_url(body.media_url):
        raise HTTPException(400, "media_url 只允许 http(s) 或本站 /uploads/ 路径")

    mid = str(uuid.uuid4())
    now = time.time()
    # direction 用 inbound：这条是「人发给信箱的消息」，信箱侧的桥接/Agent 会当作
    # 收到的消息处理。写成 outbound 会被误当成「该信箱要发出去的内容」——
    # 实测会把控制台里发的话直接转发到微信群。
    _exec(
        """INSERT INTO messages (id, request_id, type, content, media_url, name,
                                 direction, timestamp, metadata)
           VALUES (?, ?, ?, ?, ?, ?, 'inbound', ?, ?)""",
        (
            mid,
            body.request_id or mid[:8],
            body.type or "text",
            content,
            body.media_url,
            name,
            now,
            json.dumps({"via": "console", "author": user["username"]}, ensure_ascii=False),
        ),
    )
    return {"ok": True, "id": mid, "timestamp": now}


@router.post("/upload")
async def upload_media(
    file: UploadFile,
    user: dict = Depends(need("operator")),
) -> dict:
    """通信台附件上传：落到 storage/uploads，返回可直接发送的 media_url。"""
    data = await file.read()
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"文件需小于 {MAX_UPLOAD_BYTES // 1024 // 1024}MB")

    content_type = (file.content_type or "").split(";")[0].strip()
    if content_type.startswith("image/"):
        kind = "image"
    elif content_type.startswith("video/"):
        kind = "video"
    elif content_type.startswith("audio/"):
        kind = "audio"
    else:
        kind = "file"

    suffix = Path(file.filename or "").suffix.lower()
    if len(suffix) > 8 or not suffix.replace(".", "").isalnum():
        suffix = {"image": ".png", "video": ".mp4", "audio": ".mp3"}.get(kind, ".bin")

    name = f"{uuid.uuid4().hex}{suffix}"
    (_STATE["uploads"] / name).write_bytes(data)
    return {"ok": True, "url": f"/uploads/{name}", "type": kind,
            "size": len(data), "name": file.filename or name}


@router.get("/settings")
def get_settings(user: dict = Depends(current_user)) -> dict:
    return {"settings": _load_settings()}


@router.put("/settings")
def put_settings(patch: dict, user: dict = Depends(need("admin"))) -> dict:
    return {"settings": _save_settings(patch)}


@router.get("/users")
def list_users(user: dict = Depends(need("admin"))) -> dict:
    rows = _q_all("SELECT * FROM console_users ORDER BY created_at ASC")
    return {"users": [_user_public(r) for r in rows]}


@router.post("/users")
def create_user(body: UserIn, user: dict = Depends(need("admin"))) -> dict:
    username = body.username.strip()
    if not username or len(body.password) < 8:
        raise HTTPException(400, "用户名不能为空，密码至少 8 位")
    if body.role not in ROLE_RANK:
        raise HTTPException(400, "角色无效")
    if _q("SELECT 1 FROM console_users WHERE username = ?", (username,)):
        raise HTTPException(409, "用户名已存在")
    uid = secrets.token_hex(8)
    _exec(
        "INSERT INTO console_users (id, username, password_hash, role, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, username, _hash_pw(body.password), body.role, time.time()),
    )
    return {"ok": True, "user": _user_public(_q("SELECT * FROM console_users WHERE id = ?", (uid,)))}


@router.delete("/users/{user_id}")
def delete_user(user_id: str, user: dict = Depends(need("admin"))) -> dict:
    if user_id == user["id"]:
        raise HTTPException(400, "不能删除当前登录账号")
    row = _q("SELECT * FROM console_users WHERE id = ?", (user_id,))
    if not row:
        raise HTTPException(404, "账号不存在")
    if row["role"] == "admin":
        admins = _q("SELECT COUNT(*) AS c FROM console_users WHERE role = 'admin'")
        if admins and admins["c"] <= 1:
            raise HTTPException(400, "至少要保留一个管理员")
    _exec("DELETE FROM console_users WHERE id = ?", (user_id,))
    return {"ok": True}


@router.get("/stream")
async def stream(
    request: Request,
    since: Optional[float] = Query(None),
    user: dict = Depends(current_user),
) -> StreamingResponse:
    """SSE：新消息增量推送 + 心跳。前端据此实现链路与通信台的实时刷新。

    游标用 (timestamp, id) 复合，避免同一秒内多条消息被 `timestamp > cursor` 漏掉。
    前端只传 since（秒级时间戳），初始 cursor_id 取一个极大哨兵值，使首帧语义
    仍是严格的 "timestamp > since"；之后游标推进到最后一条的 (ts, id)。
    """
    cursor_ts = since if since is not None else time.time()
    cursor_id = "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz"  # 初始：只取严格大于 since 的

    async def event_gen():
        nonlocal cursor_ts, cursor_id
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            rows = _q_all(
                """SELECT * FROM messages
                   WHERE timestamp > ? OR (timestamp = ? AND id > ?)
                   ORDER BY timestamp ASC, id ASC LIMIT 200""",
                (cursor_ts, cursor_ts, cursor_id),
            )
            if rows:
                idle = 0
                items = []
                for row in rows:
                    cursor_ts = row["timestamp"]
                    cursor_id = row["id"]
                    meta = _meta(row["metadata"])
                    items.append({
                        "id": row["id"],
                        "name": row["name"],
                        "direction": row["direction"],
                        "type": row["type"],
                        "content": row["content"],
                        "media_url": row["media_url"],
                        "request_id": row["request_id"],
                        "timestamp": row["timestamp"],
                        "via": meta.get("via") or "",
                    })
                yield f"event: messages\ndata: {json.dumps(items, ensure_ascii=False)}\n\n"
            else:
                idle += 1
                if idle % 5 == 0:
                    yield f"event: tick\ndata: {json.dumps({'ts': time.time(), 'cursor': cursor_ts})}\n\n"
                else:
                    yield ": ping\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
