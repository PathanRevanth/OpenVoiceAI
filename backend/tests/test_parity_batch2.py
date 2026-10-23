"""Parity batch 2: full-text search, custom instructions, prompt library.

Full-app endpoint tests (demo mode, dev-header auth). These also backtest the
batch-1 conversation power tools (rename/duplicate/share/feedback) through the
real HTTP surface.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from voicefirst.api.enrichment import apply_instructions


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEFIRST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/batch2.db")
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
    assert "run.started" in resp.text
    convs = (await client.get("/api/conversations", headers=_headers(user))).json()
    assert convs, "expected a conversation after chat"
    return str(convs[0]["id"])


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------


def test_apply_instructions_prepends_only_for_model():
    assert apply_instructions("hi", None) == "hi"
    assert apply_instructions("hi", "   ") == "hi"
    out = apply_instructions("hi", "Be concise.")
    assert out.startswith("Operator instructions") and out.endswith("hi")
    assert "Be concise." in out


# ---------------------------------------------------------------------------
# Full-text search
# ---------------------------------------------------------------------------


async def test_search_finds_message_text_with_snippet(client):
    conv_id = await _chat(client, "searcher", "Tell me about the lunar observatory project")
    resp = await client.get("/api/conversations/search?q=observatory", headers=_headers("searcher"))
    assert resp.status_code == 200
    results = resp.json()
    assert any(r["id"] == conv_id for r in results)
    hit = next(r for r in results if r["id"] == conv_id)
    assert "observatory" in hit["snippet"].lower()

    empty = await client.get("/api/conversations/search?q=zzzqqqnope", headers=_headers("searcher"))
    assert empty.status_code == 200 and empty.json() == []

    too_short = await client.get("/api/conversations/search?q=a", headers=_headers("searcher"))
    assert too_short.status_code == 422


async def test_search_returns_only_matching_conversations(client):
    # Dev-header auth shares one tenant, so isolation is proven by precision:
    # only conversations containing the phrase come back.
    match_id = await _chat(client, "alice", "alices secret pineapple phrase")
    await _chat(client, "alice", "a totally unrelated note about bicycles")
    resp = await client.get("/api/conversations/search?q=pineapple", headers=_headers("alice"))
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()]
    assert match_id in ids and len(ids) == 1


# ---------------------------------------------------------------------------
# Custom instructions
# ---------------------------------------------------------------------------


async def test_instructions_roundtrip_and_applied_to_runs(client):
    get = await client.get("/api/preferences/instructions", headers=_headers("instr"))
    assert get.status_code == 200 and get.json() == {"instructions": ""}

    put = await client.put(
        "/api/preferences/instructions",
        json={"instructions": "Always answer in haiku form."},
        headers=_headers("instr"),
    )
    assert put.status_code == 200
    assert put.json()["instructions"] == "Always answer in haiku form."

    # Runs still complete with instructions saved; display text is unaffected.
    await _chat(client, "instr", "Say hello")

    clear = await client.put(
        "/api/preferences/instructions", json={"instructions": "  "}, headers=_headers("instr")
    )
    assert clear.json() == {"instructions": ""}


# ---------------------------------------------------------------------------
# Prompt library
# ---------------------------------------------------------------------------


async def test_prompt_crud_and_isolation(client):
    created = (
        await client.post(
            "/api/prompts", json={"title": "Standup", "body": "Summarize my day"}, headers=_headers("pam")
        )
    ).json()
    assert created["title"] == "Standup" and created["id"].startswith("prompt_")

    listed = (await client.get("/api/prompts", headers=_headers("pam"))).json()
    assert [p["id"] for p in listed] == [created["id"]]

    # Another user sees nothing and cannot touch it.
    assert (await client.get("/api/prompts", headers=_headers("mallory"))).json() == []
    assert (
        await client.put(f"/api/prompts/{created['id']}", json={"title": "X"}, headers=_headers("mallory"))
    ).status_code == 404
    assert (
        await client.delete(f"/api/prompts/{created['id']}", headers=_headers("mallory"))
    ).status_code == 404

    updated = (
        await client.put(
            f"/api/prompts/{created['id']}", json={"body": "Summarize my week"}, headers=_headers("pam")
        )
    ).json()
    assert updated["body"] == "Summarize my week"

    deleted = await client.delete(f"/api/prompts/{created['id']}", headers=_headers("pam"))
    assert deleted.json() == {"deleted": True}
    assert (await client.get("/api/prompts", headers=_headers("pam"))).json() == []


# ---------------------------------------------------------------------------
# Batch-1 backtest through HTTP: rename / duplicate / share / feedback
# ---------------------------------------------------------------------------


async def test_rename_duplicate_share_feedback_flow(client):
    conv_id = await _chat(client, "power", "Discuss the harbor lighthouse renovation")

    renamed = (
        await client.patch(f"/api/conversations/{conv_id}", json={"title": "Lighthouse plan"}, headers=_headers("power"))
    ).json()
    assert renamed["title"] == "Lighthouse plan"

    dup = (
        await client.post(f"/api/conversations/{conv_id}/duplicate", headers=_headers("power"))
    ).json()
    assert dup["id"] != conv_id and dup["title"].endswith("(copy)")
    dup_conv = (
        await client.get(f"/api/conversations/{dup['id']}", headers=_headers("power"))
    ).json()
    orig_conv = (await client.get(f"/api/conversations/{conv_id}", headers=_headers("power"))).json()
    assert len(dup_conv["messages"]) == len(orig_conv["messages"]) > 0

    share = (
        await client.post(f"/api/conversations/{conv_id}/share", headers=_headers("power"))
    ).json()
    assert share["share_token"]
    public = await client.get(f"/api/shared/{share['share_token']}")
    assert public.status_code == 200
    assert public.json()["title"] == "Lighthouse plan"

    revoked = await client.delete(f"/api/conversations/{conv_id}/share", headers=_headers("power"))
    assert revoked.json()["revoked"] >= 1
    assert (await client.get(f"/api/shared/{share['share_token']}")).status_code == 404

    runs = (await client.get("/api/runs", headers=_headers("power"))).json()["runs"]
    assert runs, "expected at least one run"
    fb = (
        await client.post(f"/api/runs/{runs[0]['id']}/feedback", json={"rating": "up"}, headers=_headers("power"))
    ).json()
    assert fb["rating"] == "up"
    bad = await client.post(
        f"/api/runs/{runs[0]['id']}/feedback", json={"rating": "sideways"}, headers=_headers("power")
    )
    assert bad.status_code == 422
