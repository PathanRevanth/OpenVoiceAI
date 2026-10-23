"""Production-hardening tests: middleware, schemas, tenant isolation,
cancellation wiring, webhook signing, and the new platform endpoints."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from voicefirst.api.middleware import Metrics, RateLimiter
from voicefirst.api.schemas import ChatAttachment, ChatRequest
from voicefirst.control_plane.webhooks import sign_payload, verify_signature

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


def test_rate_limiter_buckets_and_recovery():
    limiter = RateLimiter(3)  # 3 requests/minute
    assert limiter.check("a") == (True, 0)
    assert limiter.check("a") == (True, 0)
    assert limiter.check("a") == (True, 0)
    allowed, retry_after = limiter.check("a")
    assert not allowed and retry_after >= 1
    # Independent buckets per caller.
    assert limiter.check("b") == (True, 0)


def test_metrics_render_prometheus_text():
    m = Metrics()
    m.observe("GET", "/api/agents", 200, 0.03)
    m.observe("POST", "/api/chat", 500, 2.0)
    body = m.render().decode()
    assert 'voicefirst_http_requests_total{method="GET",path="/api/agents",status="200"} 1' in body
    assert 'le="0.05"' in body
    assert 'le="+Inf"' in body
    assert "_sum" in body and "_count" in body


# ---------------------------------------------------------------------------
# Webhook signing
# ---------------------------------------------------------------------------


def test_webhook_signature_roundtrip():
    secret = "test-secret"
    body = b'{"event": "approval.requested"}'
    signature = sign_payload(body, secret)
    assert signature.startswith("sha256=")
    assert verify_signature(body, signature, secret)
    assert not verify_signature(body + b"x", signature, secret)
    assert not verify_signature(body, signature, "other-secret")


# ---------------------------------------------------------------------------
# Chat schema limits
# ---------------------------------------------------------------------------


def test_chat_request_rejects_invalid_base64():
    with pytest.raises(ValueError):
        ChatAttachment(name="f.png", media_type="image/png", data="not base64!!!")


def test_chat_request_rejects_oversized_attachments():
    import base64

    big = base64.b64encode(b"x" * (10_000_001)).decode()
    with pytest.raises(ValueError):
        ChatAttachment(name="f.png", media_type="image/png", data=big)


def test_chat_request_accepts_valid_attachment():
    import base64

    req = ChatRequest(
        message="hello",
        attachments=[
            ChatAttachment(
                name="f.png",
                media_type="image/png",
                data=base64.b64encode(b"data").decode(),
            )
        ],
    )
    assert req.attachments[0].name == "f.png"


# ---------------------------------------------------------------------------
# Full-app endpoint tests (demo mode, dev auth)
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEFIRST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("VOICEFIRST_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("VOICEFIRST_RATE_LIMIT_PER_MINUTE", "10000")
    # Dev-header auth regardless of a local .env (which may set betterauth).
    monkeypatch.setenv("VOICEFIRST_AUTH_MODE", "disabled")
    # Rebuild the settings cache + app for the temp database. Every module
    # shares the same lru_cached get_settings function object, so clearing its
    # cache (rather than reloading the module) keeps all references consistent.
    import importlib

    from voicefirst.settings import get_settings as _get_settings

    _get_settings.cache_clear()
    import voicefirst.main as main_module

    importlib.reload(main_module)
    app = main_module.app
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_export_conversation_json(client):
    """Regression: JSON export used to crash with NameError (missing import)."""
    chat = await client.post(
        "/api/chat",
        json={"message": "hello"},
        headers={"X-VF-User": "tester"},
    )
    assert chat.status_code == 200
    # Consume the SSE stream to finish the run.
    body = chat.text
    assert "run.started" in body

    convs = (await client.get("/api/conversations", headers={"X-VF-User": "tester"})).json()
    conv_id = convs[0]["id"]
    for fmt in ("json", "md"):
        resp = await client.get(
            f"/api/conversations/{conv_id}/export?format={fmt}",
            headers={"X-VF-User": "tester"},
        )
        assert resp.status_code == 200, f"export {fmt} failed"
    if fmt == "json":
        assert "messages" in json.loads(resp.text)


async def test_security_headers_and_request_id(client):
    resp = await client.get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("X-Request-ID")


async def test_ready_and_metrics(client):
    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["ready"] is True

    metrics = await client.get("/metrics")
    assert metrics.status_code == 200
    assert "voicefirst_http_requests_total" in metrics.text


async def test_rate_limit_returns_429(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEFIRST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/rl.db")
    monkeypatch.setenv("VOICEFIRST_RATE_LIMIT_PER_MINUTE", "3")
    monkeypatch.setenv("VOICEFIRST_AUTH_MODE", "disabled")
    import importlib

    from voicefirst.settings import get_settings as _get_settings

    _get_settings.cache_clear()
    import voicefirst.main as main_module

    importlib.reload(main_module)
    app = main_module.app
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            codes = []
            for _ in range(6):
                resp = await c.get("/api/conversations", headers={"X-VF-User": "rl-tester"})
                codes.append(resp.status_code)
            assert 429 in codes
            assert codes.count(200) == 3
            # Probe paths are exempt.
            assert (await c.get("/health")).status_code == 200


async def test_delete_conversation(client):
    await client.post("/api/chat", json={"message": "hi"}, headers={"X-VF-User": "deleter"})
    convs = (await client.get("/api/conversations", headers={"X-VF-User": "deleter"})).json()
    conv_id = convs[0]["id"]

    # Another tenant/user cannot delete someone else's conversation is enforced
    # by tenant match; dev mode shares a tenant, so verify happy path here.
    resp = await client.delete(f"/api/conversations/{conv_id}", headers={"X-VF-User": "deleter"})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    assert (await client.get(f"/api/conversations/{conv_id}", headers={"X-VF-User": "deleter"})).status_code == 404


async def test_cancel_unknown_run(client):
    resp = await client.post("/api/runs/run_does_not_exist/cancel", headers={"X-VF-User": "canceller"})
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is False
