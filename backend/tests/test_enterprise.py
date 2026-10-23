"""Tests for the enterprise feature pack: PII redaction, trace replay,
exports, voice analytics, event subscriptions, A2A card,
and native pydantic-ai limits."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from voicefirst.api.middleware import (
    Metrics,
    RateLimiter,  # noqa: F401 - shared import surface
)
from voicefirst.control_plane.event_subscriptions import (
    EventSubscriptionService,
    event_matches,
)
from voicefirst.control_plane.policy import redact_pii
from voicefirst.control_plane.voice_analytics import VoiceAnalyticsService
from voicefirst.db.session import Database

# ---------------------------------------------------------------------------
# PII redaction
# ---------------------------------------------------------------------------


def test_redact_pii_all_types():
    text = (
        "Email jane@corp.com, SSN 123-45-6789, IP 10.1.2.3, "
        "card 4111 1111 1111 1111, phone (555) 123-4567."
    )
    out, found = redact_pii(text)
    assert "jane@corp.com" not in out and "[REDACTED-EMAIL]" in out
    assert "123-45-6789" not in out and "[REDACTED-SSN]" in out
    assert "10.1.2.3" not in out and "[REDACTED-IP-ADDRESS]" in out
    assert "4111" not in out and "[REDACTED-CREDIT-CARD]" in out
    assert "(555)" not in out and "[REDACTED-PHONE]" in out
    assert set(found) == {"EMAIL", "SSN", "IP-ADDRESS", "CREDIT-CARD", "PHONE"}


def test_redact_pii_luhn_avoids_false_positives():
    out, _ = redact_pii("Order 1234567890123 shipped in 4-6 days on route 42.")
    assert "1234567890123" in out  # fails Luhn, must survive
    out, _ = redact_pii("Card: 4222222222222 is Luhn-valid (13 digits).")
    assert "4222222222222" not in out


def test_redact_pii_clean_text_unchanged():
    text = "The quarterly revenue grew 12% across all regions."
    out, found = redact_pii(text)
    assert out == text and found == []


# ---------------------------------------------------------------------------
# Event subscription matching
# ---------------------------------------------------------------------------


def test_event_matching_patterns():
    assert event_matches(["*"], "anything.at.all")
    assert event_matches(["run.*"], "run.failed")
    assert event_matches(["run.failed", "budget.warning"], "budget.warning")
    assert not event_matches(["approval.*"], "run.failed")
    assert not event_matches([], "run.failed")


async def test_subscription_lifecycle_and_dead_letter(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/subs.db")
    await db.create_all()
    try:
        from voicefirst.control_plane.connections import KeyVault

        service = EventSubscriptionService(db, KeyVault("test-secret"))
        record = await service.create(
            tenant_id="t1", user_id="u1", url="https://hooks.example.com/x",
            events=["run.*"], secret="my-signing-secret",
        )
        assert record.secret_encrypted and record.events == ["run.*"]

        # Successful deliveries reset the failure counter.
        for _ in range(5):
            dl = await service.record_delivery(record.id, ok=True, status_code=200)
            assert not dl
        # Failures accumulate; at 10 consecutive the subscription dead-letters.
        for i in range(9):
            dl = await service.record_delivery(record.id, ok=False, status_code=500)
            assert not dl
        dl = await service.record_delivery(record.id, ok=False, status_code=None)
        assert dl  # dead-lettered now
        stored = await service.get(record.id, "t1")
        assert stored.dead_lettered and not stored.enabled

        # Re-enabling clears the dead-letter state.
        revived = await service.set_enabled(record.id, "t1", True)
        assert revived.enabled and not revived.dead_lettered and revived.failure_count == 0

        d = service.to_dict(stored)
        assert d["has_secret"] and not d.get("secret")
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Voice analytics
# ---------------------------------------------------------------------------


async def test_voice_session_lifecycle_and_summary(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/voice.db")
    await db.create_all()
    try:
        svc = VoiceAnalyticsService(db)
        s1 = await svc.start_session(
            tenant_id="t1", user_id="u1", channel="browser",
            provider="openai", model="gpt-realtime", voice="alloy",
        )
        await svc.update_metrics(s1.id, turns=4, ttfw_ms=420, barge_ins=1)
        await svc.end_session(s1.id, status="ended")
        s2 = await svc.start_session(tenant_id="t1", user_id="u2", channel="twilio")
        await svc.end_session(s2.id, status="failed", error="socket dropped")

        rows = await svc.list_sessions("t1")
        assert len(rows) == 2
        assert rows[0].status in ("ended", "failed")  # ordered newest first

        summary = await svc.summary(tenant_id="t1", days=7)
        assert summary["sessions"] == 2
        assert summary["turns"] == 4
        assert summary["barge_ins"] == 1
        assert summary["avg_ttfw_ms"] == 420.0
        d = svc.to_dict(rows[0])
        assert d["duration_ms"] is not None
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Metrics registry (sanity for the export/dashboard surface)
# ---------------------------------------------------------------------------


def test_metrics_histogram_cumulative_buckets():
    m = Metrics()
    for d in (0.02, 0.3, 7.0):
        m.observe("GET", "/x", 200, d)
    body = m.render().decode()
    assert 'le="0.05"} 1' in body
    assert 'le="0.5"} 2' in body
    assert 'le="+Inf"} 3' in body


# ---------------------------------------------------------------------------
# Full-app endpoint tests (demo/dev mode)
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEFIRST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("VOICEFIRST_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("VOICEFIRST_RATE_LIMIT_PER_MINUTE", "100000")
    monkeypatch.setenv("VOICEFIRST_AUTH_MODE", "disabled")
    import importlib

    from voicefirst.settings import get_settings as _gs

    _gs.cache_clear()
    import voicefirst.main as main_module

    importlib.reload(main_module)
    app = main_module.app
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def _run_one_chat(client: AsyncClient, message: str) -> str:
    resp = await client.post(
        "/api/chat", json={"message": message}, headers={"X-VF-User": "tester"}
    )
    assert resp.status_code == 200
    assert "run.started" in resp.text
    return resp.text


async def test_playground_is_removed(client):
    resp = await client.post(
        "/api/playground",
        json={"message": "Say hi", "agent_slug": "orchestrator"},
        headers={"X-VF-User": "tester"},
    )
    assert resp.status_code == 404


async def test_replay_endpoint(client):
    await _run_one_chat(client, "hello replay")
    runs = (await client.get("/api/runs", headers={"X-VF-User": "tester"})).json()
    run_id = runs["runs"][0]["id"]
    resp = await client.post(
        f"/api/runs/{run_id}/replay", json={}, headers={"X-VF-User": "tester"}
    )
    assert resp.status_code == 200
    assert "run.started" in resp.text


async def test_replay_unknown_run_404(client):
    resp = await client.post(
        "/api/runs/run_nope/replay", json={}, headers={"X-VF-User": "tester"}
    )
    assert resp.status_code == 404


async def test_resume_accepts_model_and_rejects_unknown_run(client):
    resp = await client.post(
        "/api/runs/run_nope/resume", json={"model": "demo"}, headers={"X-VF-User": "tester"}
    )
    assert resp.status_code == 404


async def test_audit_and_usage_exports(client):
    await _run_one_chat(client, "export me")
    csv_resp = await client.get("/api/audit/export?format=csv", headers={"X-VF-User": "tester", "X-VF-Role": "ops"})
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert "id,ts,actor,action" in csv_resp.text

    json_resp = await client.get("/api/audit/export?format=json", headers={"X-VF-User": "tester", "X-VF-Role": "ops"})
    assert json_resp.status_code == 200
    assert json_resp.json()["count"] >= 1

    usage_csv = await client.get("/api/usage/export?days=1", headers={"X-VF-User": "tester", "X-VF-Role": "ops"})
    assert usage_csv.status_code == 200
    assert usage_csv.text.startswith("run_id,created_at,agent")


async def test_usage_summary_has_per_user(client):
    await _run_one_chat(client, "chargeback")
    usage = (await client.get("/api/usage?days=1", headers={"X-VF-User": "tester"})).json()
    assert "by_user" in usage
    assert usage["by_user"] and usage["by_user"][0]["user_id"] == "tester"


async def test_subscription_crud_and_test(client, monkeypatch):
    # Local HTTP server stand-in so delivery has a target.
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("content-length", 0))
            body = self.rfile.read(length).decode()
            received.append((self.headers.get("X-VoiceFirst-Event", ""), body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):  # noqa: N802
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/hook"
        created = await client.post(
            "/api/event-subscriptions",
            json={"url": url, "events": ["run.*"], "secret": "s3cret"},
            headers={"X-VF-User": "tester"},
        )
        assert created.status_code == 200
        sub = created.json()
        assert sub["has_secret"] and sub["events"] == ["run.*"]

        listed = await client.get("/api/event-subscriptions", headers={"X-VF-User": "tester"})
        assert any(x["id"] == sub["id"] for x in listed.json())

        test_resp = await client.post(f"/api/event-subscriptions/{sub['id']}/test", headers={"X-VF-User": "tester"})
        assert test_resp.status_code == 200
        assert test_resp.json()["ok"] is True
        assert received and received[0][0] == "subscription.test"
        payload = json.loads(received[0][1])
        assert payload["event"] == "subscription.test"

        deleted = await client.delete(f"/api/event-subscriptions/{sub['id']}", headers={"X-VF-User": "tester"})
        assert deleted.status_code == 200
    finally:
        server.shutdown()


async def test_a2a_agent_card(client):
    card = await client.get("/.well-known/agent-card.json")
    assert card.status_code == 200
    data = card.json()
    assert data["protocolVersion"] == "0.3.0"
    assert data["capabilities"]["streaming"] is True
    slugs = {s["id"] for s in data["skills"]}
    assert "orchestrator" in slugs and "github" in slugs


async def test_voice_analytics_endpoint(client):
    resp = await client.get("/api/voice/analytics?days=7", headers={"X-VF-User": "tester"})
    assert resp.status_code == 200
    assert resp.json()["sessions"] == 0
    sessions = await client.get("/api/voice/sessions", headers={"X-VF-User": "tester"})
    assert sessions.status_code == 200 and sessions.json() == []


async def test_platform_exposes_redact_pii_policy(client):
    info = (await client.get("/api/platform", headers={"X-VF-User": "tester", "X-VF-Role": "admin"})).json()
    assert "redact_pii" in info["policies"]
    updated = await client.patch(
        "/api/policies", json={"redact_pii": True}, headers={"X-VF-User": "tester", "X-VF-Role": "admin"}
    )
    assert updated.status_code == 200
    assert updated.json()["redact_pii"] is True
