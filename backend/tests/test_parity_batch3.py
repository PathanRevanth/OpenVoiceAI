"""Parity batch 3: projects, archives, attachment previews.

Full-app endpoint tests (demo mode, dev-header auth).
"""

from __future__ import annotations

import base64

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEFIRST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/batch3.db")
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


async def _chat(client: AsyncClient, user: str, message: str, **extra) -> str:
    body: dict = {"message": message}
    body.update(extra)
    resp = await client.post("/api/chat", json=body, headers=_headers(user))
    assert resp.status_code == 200, resp.text
    assert "run.started" in resp.text
    convs = (await client.get("/api/conversations", headers=_headers(user))).json()
    assert convs, "expected a conversation after chat"
    return str(convs[0]["id"])


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


async def test_project_crud_and_isolation(client):
    created = (
        await client.post("/api/projects", json={"name": "Launch"}, headers=_headers("pam"))
    ).json()
    assert created["name"] == "Launch" and created["id"].startswith("proj_")
    assert created["conversation_count"] == 0

    assert (await client.get("/api/projects", headers=_headers("mallory"))).json() == []

    updated = (
        await client.patch(
            f"/api/projects/{created['id']}",
            json={"name": "Relaunch", "instructions": "Be terse."},
            headers=_headers("pam"),
        )
    ).json()
    assert updated["name"] == "Relaunch" and updated["instructions"] == "Be terse."

    assert (
        await client.patch(f"/api/projects/{created['id']}", json={"name": "X"}, headers=_headers("mallory"))
    ).status_code == 404

    conv_id = await _chat(client, "pam", "hello project")
    moved = (
        await client.post(
            f"/api/conversations/{conv_id}/move", json={"project_id": created["id"]}, headers=_headers("pam")
        )
    ).json()
    assert moved["project_id"] == created["id"]

    listed = (await client.get("/api/projects", headers=_headers("pam"))).json()
    assert listed[0]["conversation_count"] == 1

    conv = (await client.get(f"/api/conversations/{conv_id}", headers=_headers("pam"))).json()
    assert conv["project_id"] == created["id"]

    # Moving to an unknown project is a 404, not a silent unassign.
    assert (
        await client.post(
            f"/api/conversations/{conv_id}/move", json={"project_id": "proj_nope"}, headers=_headers("pam")
        )
    ).status_code == 404

    deleted = await client.delete(f"/api/projects/{created['id']}", headers=_headers("pam"))
    assert deleted.json() == {"deleted": True}
    conv = (await client.get(f"/api/conversations/{conv_id}", headers=_headers("pam"))).json()
    assert conv["project_id"] is None


async def test_project_instructions_do_not_break_runs(client):
    created = (
        await client.post("/api/projects", json={"name": "Terse"}, headers=_headers("pam"))
    ).json()
    await client.patch(
        f"/api/projects/{created['id']}", json={"instructions": "Keep answers short."}, headers=_headers("pam")
    )
    conv_id = await _chat(client, "pam", "Say hello")
    await client.post(f"/api/conversations/{conv_id}/move", json={"project_id": created["id"]}, headers=_headers("pam"))
    # A second turn in the project-scoped conversation still completes.
    resp = await client.post(
        "/api/chat",
        json={"message": "And now say goodbye", "conversation_id": conv_id},
        headers=_headers("pam"),
    )
    assert resp.status_code == 200 and "run.started" in resp.text


# ---------------------------------------------------------------------------
# Archives
# ---------------------------------------------------------------------------


async def test_archive_and_unarchive_flow(client):
    conv_id = await _chat(client, "arch", "remember the lighthouse")
    convs = (await client.get("/api/conversations", headers=_headers("arch"))).json()
    assert convs[0]["archived"] is False

    archived = (
        await client.post(f"/api/conversations/{conv_id}/archive", headers=_headers("arch"))
    ).json()
    assert archived == {"id": conv_id, "archived": True}
    convs = (await client.get("/api/conversations", headers=_headers("arch"))).json()
    assert convs[0]["archived"] is True

    restored = (
        await client.post(f"/api/conversations/{conv_id}/unarchive", headers=_headers("arch"))
    ).json()
    assert restored == {"id": conv_id, "archived": False}


# ---------------------------------------------------------------------------
# Attachment previews
# ---------------------------------------------------------------------------


async def test_small_image_persists_preview_and_big_one_does_not(client):
    tiny = base64.b64encode(b"\x89PNG" + b"x" * 100).decode()
    conv_id = await _chat(
        client,
        "shots",
        "look at this",
        attachments=[{"name": "tiny.png", "media_type": "image/png", "data": tiny}],
    )
    conv = (await client.get(f"/api/conversations/{conv_id}", headers=_headers("shots"))).json()
    user_msgs = [m for m in conv["messages"] if m["role"] == "user"]
    assert user_msgs, "expected a persisted user message"
    atts = user_msgs[0]["content"].get("attachments", [])
    assert len(atts) == 1 and atts[0]["name"] == "tiny.png"
    assert atts[0]["preview"].startswith("data:image/png;base64,")

    big = base64.b64encode(b"y" * 400_000).decode()
    conv_id2 = await _chat(
        client,
        "shots",
        "look at this big one",
        attachments=[{"name": "big.png", "media_type": "image/png", "data": big}],
    )
    conv2 = (await client.get(f"/api/conversations/{conv_id2}", headers=_headers("shots"))).json()
    user_msgs2 = [m for m in conv2["messages"] if m["role"] == "user"]
    atts2 = user_msgs2[0]["content"].get("attachments", [])
    assert len(atts2) == 1 and "preview" not in atts2[0]
