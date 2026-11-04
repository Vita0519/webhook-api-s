import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

import app.main as main

AUTH = {"Authorization": "Bearer test-key"}
client = TestClient(main.app)


def test_auth_required_and_health_hides_key():
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/messages").status_code == 401
    assert client.get("/api/messages", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/messages", headers=AUTH).status_code == 200


def test_path_traversal_blocked_and_file_download():
    (main.UPLOADS_DIR / "ok.txt").write_bytes(b"hello")
    ok = client.get("/uploads/ok.txt")
    assert ok.status_code == 200 and ok.content == b"hello"
    assert client.get("/uploads/../webhook.db").status_code == 404
    assert client.get("/uploads/%2e%2e/webhook.db").status_code == 404


def test_limit_validation_rejects_negative():
    assert client.get("/webhook/outbound?limit=-1", headers=AUTH).status_code == 422
    assert client.get("/api/messages?limit=-1", headers=AUTH).status_code == 422


def test_signal_returns_aggregate_count():
    name = "signal-test"
    for i in range(3):
        r = client.post(
            f"/webhook/inbound/{name}", headers=AUTH,
            data={"type": "text", "content": f"m{i}"},
        )
        assert r.status_code == 200
    sig = client.get(f"/signal/{name}").json()
    assert sig["count"] == 3
    assert sig["latest_timestamp"] > 0


def test_empty_message_rejected():
    r = client.post("/webhook/inbound/empty", headers=AUTH, data={})
    assert r.status_code == 400


def test_upload_streams_and_downloads():
    r = client.post(
        "/webhook/inbound/upload-test", headers=AUTH,
        data={"type": "image"},
        files={"media": ("x.png", b"png-bytes", "image/png")},
    )
    assert r.status_code == 200
    msgs = client.get("/webhook/outbound?name=upload-test&direction=inbound", headers=AUTH).json()["messages"]
    url = msgs[-1]["media_url"]
    got = client.get(url)
    assert got.status_code == 200 and got.content == b"png-bytes"


def test_chat_completion_returns_outbound_reply_not_echo():
    request_id = "rid-chat-1"
    payload = {
        "model": "webhook",
        "name": "chat-test",
        "request_id": request_id,
        "messages": [{"role": "user", "content": "你好"}],
    }

    def reply_later():
        time.sleep(0.5)
        return client.post(
            f"/webhook/outbound/chat-test?request_id={request_id}",
            headers=AUTH,
            data={"type": "text", "content": "这是智能体回复"},
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(reply_later)
        r = client.post("/v1/chat/completions", headers=AUTH, json=payload)
        reply = fut.result()

    assert reply.status_code == 200
    assert r.status_code == 200
    data = r.json()
    assert data["request_id"] == request_id
    assert data["choices"][0]["message"]["content"] == "这是智能体回复"


def test_chat_completion_timeout_does_not_echo(monkeypatch):
    monkeypatch.setattr(main, "CHAT_TIMEOUT", 0.5)
    r = client.post(
        "/v1/chat/completions", headers=AUTH,
        json={
            "model": "webhook",
            "name": "chat-timeout",
            "request_id": "rid-timeout",
            "messages": [{"role": "user", "content": "没人回我"}],
        },
    )
    assert r.status_code == 504
