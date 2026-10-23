"""Tests for per-agent API/webhook access keys."""

from __future__ import annotations

import pytest

from voicefirst.control_plane.agent_access import KEY_PREFIX, AgentAccessService
from voicefirst.db.session import Database


@pytest.fixture
async def service(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/keys.db")
    await db.create_all()
    yield AgentAccessService(db)
    await db.close()


async def test_key_roundtrip(service: AgentAccessService):
    record, plaintext = await service.create_key(
        agent_slug="github", tenant_id="t1", label="n8n"
    )
    assert plaintext.startswith(KEY_PREFIX)
    assert record.key_last4 == plaintext[-4:]
    assert "vf_ak_" not in record.key_hash

    found = await service.verify("github", plaintext)
    assert found is not None and found.id == record.id
    assert await service.verify("github", "vf_ak_wrong") is None
    assert await service.verify("slack", plaintext) is None  # key is per-agent


async def test_revoke_and_tenant_scoping(service: AgentAccessService):
    record, plaintext = await service.create_key(
        agent_slug="slack", tenant_id="t1", label="ci"
    )
    # another tenant cannot revoke someone else's key
    assert await service.revoke(record.id, "slack", "t2") is False
    assert await service.revoke(record.id, "slack", "t1") is True
    assert await service.verify("slack", plaintext) is None

    keys = await service.list_keys("slack", "t1")
    assert len(keys) == 1 and keys[0].revoked is True
