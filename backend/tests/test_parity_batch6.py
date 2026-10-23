"""Parity batch 6: project icon/color identity persists and validates."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEFIRST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/batch6.db")
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


async def test_project_icon_color_roundtrip(client):
    created = (
        await client.post(
            "/api/projects",
            json={"name": "Copenhagen Trip", "icon": "plane", "color": "pink"},
            headers=_headers("pam"),
        )
    ).json()
    assert created["icon"] == "plane"
    assert created["color"] == "pink"

    listed = (await client.get("/api/projects", headers=_headers("pam"))).json()
    assert len(listed) == 1
    assert listed[0]["icon"] == "plane"
    assert listed[0]["color"] == "pink"

    updated = (
        await client.patch(
            f"/api/projects/{created['id']}",
            json={"icon": "gift", "color": "yellow"},
            headers=_headers("pam"),
        )
    ).json()
    assert updated["icon"] == "gift"
    assert updated["color"] == "yellow"


async def test_project_defaults_and_bad_color(client):
    created = (
        await client.post("/api/projects", json={"name": "Plain"}, headers=_headers("pam"))
    ).json()
    assert created["icon"] == "folder"
    assert created["color"] == "slate"

    bad = await client.post(
        "/api/projects", json={"name": "Nope", "color": "chartreuse"}, headers=_headers("pam")
    )
    assert bad.status_code == 422

    bad_patch = await client.patch(
        f"/api/projects/{created['id']}", json={"color": "chartreuse"}, headers=_headers("pam")
    )
    assert bad_patch.status_code == 422
