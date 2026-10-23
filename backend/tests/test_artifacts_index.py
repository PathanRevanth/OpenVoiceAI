"""Artifact index: fence-message scanning."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from voicefirst.api.artifacts_idx import scan_artifact_sources
from voicefirst.db.models import ConversationRecord, MessageRecord, new_id
from voicefirst.db.session import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/artifacts.db")
    await database.create_all()
    yield database
    await database.close()


async def _seed(db: Database) -> None:
    async with db.session() as session:
        session.add(
            ConversationRecord(id="conv_a", title="Alpha", tenant_id="t1", user_id="u1", channel="chat")
        )
        session.add(
            ConversationRecord(id="conv_b", title="Beta", tenant_id="t1", user_id="u1", channel="chat")
        )
        session.add(
            MessageRecord(
                id=new_id("msg"), conversation_id="conv_a", role="assistant",
                content={"text": "plain answer with no fences"},
            )
        )
        session.add(
            MessageRecord(
                id=new_id("msg"), conversation_id="conv_a", role="user", content={"text": "hi ``` not mine"},
            )
        )
        session.add(
            MessageRecord(
                id=new_id("msg"), conversation_id="conv_b", role="assistant",
                content={"text": '```artifact title="Plan" type="markdown"\n# Hi\n```'},
            )
        )


async def test_scan_returns_only_assistant_fence_messages(db):
    await _seed(db)
    rows = await scan_artifact_sources(db, "t1")
    assert len(rows) == 1
    assert rows[0]["conversation_id"] == "conv_b"
    assert rows[0]["conversation_title"] == "Beta"
    assert "artifact" in rows[0]["text"]


async def test_scan_is_tenant_scoped_and_capped(db):
    await _seed(db)
    assert await scan_artifact_sources(db, "other") == []
    rows = await scan_artifact_sources(db, "t1", limit=1)
    assert len(rows) == 1


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEFIRST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/artidx.db")
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


async def test_artifacts_index_endpoint_starts_empty(client):
    resp = await client.get("/api/artifacts/index", headers={"X-VF-User": "nobody"})
    assert resp.status_code == 200 and resp.json() == []
