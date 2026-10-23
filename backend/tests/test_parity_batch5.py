"""Parity batch 5: shared-view payload shape and personal data export."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEFIRST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/batch5.db")
    monkeypatch.setenv("VOICEFIRST_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("VOICEFIRST_RATE_LIMIT_PER_MINUTE", "10000")
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
            yield c


def _headers(user: str) -> dict[str, str]:
    return {"X-VF-User": user}


async def _chat(client: AsyncClient, user: str, message: str) -> str:
    resp = await client.post("/api/chat", json={"message": message}, headers=_headers(user))
    assert resp.status_code == 200, resp.text
    convs = (await client.get("/api/conversations", headers=_headers(user))).json()
    assert convs
    return str(convs[0]["id"])


async def test_shared_payload_carries_sources_and_model(client):
    conv_id = await _chat(client, "sharer", "hello shared world")
    token = (
        await client.post(f"/api/conversations/{conv_id}/share", headers=_headers("sharer"))
    ).json()["share_token"]
    public = await client.get(f"/api/shared/{token}")
    assert public.status_code == 200
    body = public.json()
    assert body["title"]
    assert body["messages"], "expected the user message in the shared view"
    for m in body["messages"]:
        assert set(m) >= {"role", "text", "sources", "model", "created_at"}


async def test_export_mine_returns_only_my_conversations(client):
    await _chat(client, "alice", "alice private note")
    await _chat(client, "bob", "bob private note")
    mine = (await client.get("/api/export/mine", headers=_headers("alice"))).json()
    assert "exported_at" in mine
    titles = [c["title"] for c in mine["conversations"]]
    assert len(titles) == 1
    assert all("messages" in c and "archived" in c for c in mine["conversations"])
    texts = [m["text"] for c in mine["conversations"] for m in c["messages"]]
    assert any("alice private note" in t for t in texts)
    assert not any("bob private note" in t for t in texts)
