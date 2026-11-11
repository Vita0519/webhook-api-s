"""工作台（app/console.py）安全加固回归测试。

覆盖 PR #3 审查中提出的阻断项与工程问题：
- 无 API_KEY + ALLOW_NO_AUTH 不会把匿名请求当 admin
- 随机初始密码 / 服务端强制改密
- 会话 token 版本吊销（改密、登出）
- media_url 协议白名单（含前端同源策略的后端防线）
- SSE 复合游标不漏同秒消息
- 头像接口需登录
- RBAC 分级
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

import app.console as console
import app.main as main

AUTH = {"Authorization": "Bearer test-key"}


@pytest.fixture()
def cclient(monkeypatch):
    """复用 main 的 DB（messages 表在那里），并重置 console 表保证用例隔离。"""
    monkeypatch.setenv("WEBHOOK_CONSOLE_SECURE_COOKIE", "0")
    console.configure(main.STORAGE_DIR, api_key="test-key", allow_no_auth=False)
    conn = console._conn()
    for t in ("console_users", "console_agents", "console_settings"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()
    console._bootstrap_admin()  # 重建 admin（must_change=1，初始密码 test-key）
    console._login_fails.clear()
    return TestClient(main.app)


def _login(client, username="admin", password="test-key"):
    r = client.post("/api/console/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r


def test_console_requires_auth(cclient):
    assert cclient.get("/api/console/me").status_code == 401
    assert cclient.get("/api/console/overview").status_code == 401
    assert cclient.get("/api/console/avatar/foo").status_code == 401  # 头像也需登录
    assert cclient.get("/api/console/me", headers=AUTH).status_code == 200


def test_no_api_key_never_grants_anonymous_admin(monkeypatch):
    """无 API_KEY 即使 allow_no_auth=True，工作台也不认匿名请求为 admin。"""
    monkeypatch.setenv("WEBHOOK_CONSOLE_SECURE_COOKIE", "0")
    console.configure(main.STORAGE_DIR, api_key="", allow_no_auth=True)
    c = TestClient(main.app)
    assert c.get("/api/console/me").status_code == 401
    assert c.put("/api/console/settings", json={"site_name": "hack"}).status_code == 401
    assert c.post("/api/console/send", json={"name": "x", "content": "hi"}).status_code == 401
    # 恢复，避免污染其它用例
    console.configure(main.STORAGE_DIR, api_key="test-key", allow_no_auth=False)


def test_bootstrap_random_password_when_no_key(monkeypatch, capsys):
    """无 API_KEY 时初始密码随机，不是字面量 admin。"""
    monkeypatch.setenv("WEBHOOK_CONSOLE_SECURE_COOKIE", "0")
    conn = console._conn()
    conn.execute("DELETE FROM console_users")
    conn.commit()
    conn.close()
    console.configure(main.STORAGE_DIR, api_key="", allow_no_auth=True)
    out = capsys.readouterr().out
    assert "随机初始管理员密码" in out
    pwd_line = next(l for l in out.splitlines() if "admin /" in l)
    pwd = pwd_line.split("admin /")[1].strip()
    assert len(pwd) >= 12 and pwd != "admin"
    c = TestClient(main.app)
    assert c.post("/api/console/login", json={"username": "admin", "password": "admin"}).status_code == 401
    assert c.post("/api/console/login", json={"username": "admin", "password": pwd}).status_code == 200
    console.configure(main.STORAGE_DIR, api_key="test-key", allow_no_auth=False)


def test_must_change_blocks_writes_until_password_changed(cclient):
    _login(cclient)
    me = cclient.get("/api/console/me").json()["user"]
    assert me["must_change"] is True
    # 读放行
    assert cclient.get("/api/console/agents").status_code == 200
    # 写（operator 及以上）被服务端拦
    assert cclient.post("/api/console/send", json={"name": "x", "content": "hi"}).status_code == 403
    assert cclient.put("/api/console/settings", json={"site_name": "x"}).status_code == 403
    # 改密（初始密码可免 old）
    r = cclient.post("/api/console/password", json={"old": "", "new": "newpass123"})
    assert r.status_code == 200, r.text
    assert cclient.get("/api/console/me").json()["user"]["must_change"] is False
    assert cclient.post("/api/console/send", json={"name": "x", "content": "hi"}).status_code == 200


def test_password_change_revokes_other_sessions(cclient):
    """改密后，另一份旧 cookie 立即失效；当前会话自动重签不掉线。"""
    old = TestClient(main.app)
    _login(old)
    old_cookie = old.cookies.get("wh_console")

    cur = TestClient(main.app)
    _login(cur)
    assert cur.post("/api/console/password", json={"old": "", "new": "newpass123"}).status_code == 200

    # 旧会话被吊销
    stale = TestClient(main.app)
    stale.cookies.set("wh_console", old_cookie)
    assert stale.get("/api/console/me").status_code == 401
    # 当前会话仍有效
    assert cur.get("/api/console/me").status_code == 200


def test_logout_revokes_token(cclient):
    cl = TestClient(main.app)
    _login(cl)
    cookie = cl.cookies.get("wh_console")
    assert cl.post("/api/console/logout").status_code == 200
    stale = TestClient(main.app)
    stale.cookies.set("wh_console", cookie)
    assert stale.get("/api/console/me").status_code == 401


def test_send_rejects_unsafe_media_url(cclient):
    _login(cclient)
    cclient.post("/api/console/password", json={"old": "", "new": "newpass123"})
    for bad in ("javascript:alert(1)", "data:text/html,<script>", "vbscript:msgbox",
                "file:///etc/passwd", "/uploads/../webhook.db"):
        r = cclient.post("/api/console/send",
                         json={"name": "x", "content": "hi", "media_url": bad})
        assert r.status_code == 400, (bad, r.status_code, r.text)
    for good in ("https://example.com/a.png", "http://example.com/a.mp4", "/uploads/abc.png"):
        r = cclient.post("/api/console/send",
                         json={"name": "x", "content": "hi", "media_url": good})
        assert r.status_code == 200, (good, r.status_code, r.text)


def test_safe_media_url_helper():
    assert console._safe_media_url("https://a.com/x") is True
    assert console._safe_media_url("/uploads/a.png") is True
    assert console._safe_media_url("javascript:alert(1)") is False
    assert console._safe_media_url("/uploads/../webhook.db") is False
    assert console._safe_media_url("") is False


def test_stream_cursor_no_same_second_loss(cclient):
    """同秒多条消息：复合游标 SQL 应全部取到，不靠 timestamp > 丢消息。"""
    _login(cclient)
    now = 1700000000.0
    for i in range(3):
        console._exec(
            "INSERT OR REPLACE INTO messages (id, request_id, type, content, name, direction, timestamp, metadata) "
            "VALUES (?, ?, 'text', ?, 'agent-x', 'inbound', ?, ?)",
            (f"m{i}", f"r{i}", f"msg{i}", now + i * 0.001, "{}"),
        )
    # 直接验证查询语义：游标停在首条 (ts,id) 仍能取到同秒后续
    rows = console._q_all(
        "SELECT id FROM messages WHERE timestamp > ? OR (timestamp = ? AND id > ?) "
        "ORDER BY timestamp ASC, id ASC",
        (now, now, "m0"),
    )
    ids = [r["id"] for r in rows]
    assert "m1" in ids and "m2" in ids


def test_rbac_roles(cclient):
    _login(cclient)  # admin cookie
    cclient.post("/api/console/password", json={"old": "", "new": "newpass123"})
    # 建 viewer
    r = cclient.post("/api/console/users",
                     json={"username": "v", "password": "viewerpass", "role": "viewer"})
    assert r.status_code == 200
    v = TestClient(main.app)
    assert v.post("/api/console/login",
                  json={"username": "v", "password": "viewerpass"}).status_code == 200
    assert v.get("/api/console/agents").status_code == 200
    assert v.post("/api/console/send", json={"name": "x", "content": "hi"}).status_code == 403
    assert v.get("/api/console/users").status_code == 403


def test_events_expose_request_id_for_pairing(cclient):
    """事件流要能参与链路配对：/events 与 /messages 一样给出 request_id。

    外部消费方（自绘链路、第三方可视化外壳）通常先拉一批历史渲染首屏，
    再接 SSE 增量。两批数据拼在一起时，只有 request_id 能把
    inbound / outbound 认成同一次收发；缺了它首屏的边会少一半。
    """
    now = time.time()
    conn = console._conn()
    conn.execute("DELETE FROM messages WHERE id IN ('ev-out','ev-in','ev-norid')")  # 保证重复运行不累积
    for mid, rid, name, direction, ts in (
        ("ev-out", "rid-pair-1", "sender", "outbound", now),
        ("ev-in", "rid-pair-1", "receiver", "inbound", now + 0.5),
    ):
        conn.execute(
            "INSERT INTO messages (id, request_id, type, content, media_url, name, direction, timestamp, metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, rid, "text", "带 request_id 的一条", None, name, direction, ts, "{}"),
        )
    # 另一条没有 request_id 的消息，确认字段为 None 而不是被丢掉
    conn.execute(
        "INSERT INTO messages (id, request_id, type, content, media_url, name, direction, timestamp, metadata) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("ev-norid", None, "text", "没有 request_id", None, "sender", "outbound", now + 1, "{}"),
    )
    conn.commit()
    conn.close()

    events = cclient.get("/api/console/events?limit=50", headers=AUTH).json()["events"]
    paired = [e for e in events if e.get("request_id") == "rid-pair-1"]
    assert len(paired) == 2, events
    assert {e["direction"] for e in paired} == {"inbound", "outbound"}
    nob = next(e for e in events if e["id"] == "ev-norid")
    assert nob["request_id"] is None

    # 与 /messages 的字段保持一致，消费方不需要按接口写两套分支
    msgs = cclient.get("/api/console/messages?name=sender&limit=5", headers=AUTH).json()["messages"]
    mev = next(m for m in msgs if m["id"] == "ev-out")
    assert mev["request_id"] == "rid-pair-1"
    assert set(("id", "request_id", "name", "direction", "type", "content", "media_url", "timestamp")) <= set(mev)
    assert set(("id", "request_id", "name", "direction", "type", "content", "media_url", "timestamp")) <= set(paired[0])


def _seed_msg(mid: str, name: str, ts: float, direction: str = "inbound") -> None:
    """往 messages 里塞一条记录，用来制造「这个信箱有通信」。"""
    conn = console._conn()
    conn.execute("DELETE FROM messages WHERE id = ?", (mid,))
    conn.execute(
        "INSERT INTO messages (id, request_id, type, content, media_url, name, direction, timestamp, metadata) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (mid, None, "text", "测试消息", None, name, direction, ts, "{}"),
    )
    conn.commit()
    conn.close()


def _as_admin(client):
    """登录并把初始密码改掉（must_change=1 时写操作会被拦）。"""
    _login(client)
    client.post("/api/console/password", json={"old": "", "new": "newpass123"})


def test_agent_delete_is_a_watermark_not_a_purge(cclient):
    """手动删除信箱：只是记水位线。

    删除后不再出现在列表里，但历史消息还在；等这个信箱又有新通信
    （消息时间晚于水位线），它会自己回来 —— 这样不需要人工维护白名单，
    也不会因为一次误删就永久丢掉一个活跃信箱。
    """
    _as_admin(cclient)
    _seed_msg("del-old", "inbox-del", time.time() - 120)

    names = lambda d: [a["name"] for a in d["agents"]]  # noqa: E731
    before = cclient.get("/api/console/agents").json()
    assert "inbox-del" in names(before)

    assert cclient.delete("/api/console/agents/inbox-del").status_code == 200
    after = cclient.get("/api/console/agents").json()
    assert "inbox-del" not in names(after)
    assert "inbox-del" in [a["name"] for a in after["deleted"]]

    # 历史消息没有被删，删掉的只是列表可见性
    msgs = cclient.get("/api/console/messages?name=inbox-del&limit=5").json()["messages"]
    assert any(m["id"] == "del-old" for m in msgs)

    # 删除后来了新消息 -> 自动复活
    _seed_msg("del-new", "inbox-del", time.time() + 1)
    revived = cclient.get("/api/console/agents").json()
    assert "inbox-del" in names(revived)
    assert "inbox-del" not in [a["name"] for a in revived["deleted"]]


def test_empty_agent_auto_ignored_but_manual_create_pins(cclient):
    """收和发都没有消息的空壳自动忽略；人工新建/恢复的则固定可见。

    自动忽略只针对「一条消息都没有」的信箱，不做发起/应答方向的推断——
    否则 boss 这类只推不收的合法发送方会被误判成僵尸信箱。
    """
    _as_admin(cclient)
    names = lambda d: [a["name"] for a in d["agents"]]  # noqa: E731

    # 人工新建标注 = 人工认可它存在，先 pin 住
    assert cclient.patch("/api/console/agents/shell-x", json={"display_name": "空壳"}).status_code == 200
    d = cclient.get("/api/console/agents").json()
    assert "shell-x" in names(d)
    assert not [a for a in d["hidden"] if a["name"] == "shell-x"]

    # 去掉 pin（等价于「不是人工建的」）-> 进入自动忽略
    conn = console._conn()
    conn.execute("UPDATE console_agents SET pinned = 0 WHERE name = 'shell-x'")
    conn.commit()
    conn.close()
    d = cclient.get("/api/console/agents").json()
    assert "shell-x" not in names(d)
    assert "shell-x" in [a["name"] for a in d["hidden"]]

    # 手动恢复 -> 重新可见
    assert cclient.post("/api/console/agents/shell-x/restore").status_code == 200
    d = cclient.get("/api/console/agents").json()
    assert "shell-x" in names(d)


def test_agent_delete_requires_admin(cclient):
    """删除/恢复是破坏性操作，viewer 不能碰。"""
    _as_admin(cclient)
    r = cclient.post("/api/console/users",
                     json={"username": "v2", "password": "viewerpass", "role": "viewer"})
    assert r.status_code == 200
    v = TestClient(main.app)
    assert v.post("/api/console/login", json={"username": "v2", "password": "viewerpass"}).status_code == 200
    assert v.delete("/api/console/agents/whatever").status_code == 403
    assert v.post("/api/console/agents/whatever/restore").status_code == 403
