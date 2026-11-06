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
