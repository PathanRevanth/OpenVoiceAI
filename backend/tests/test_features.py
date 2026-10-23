"""Tests for personal access tokens (platform API auth)."""

from __future__ import annotations

import pytest

from voicefirst.control_plane.user_tokens import PAT_PREFIX, UserTokenService
from voicefirst.db.session import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/pat.db")
    await database.create_all()
    yield database
    await database.close()


async def test_pat_roundtrip_and_revocation(db):
    service = UserTokenService(db)
    record, plaintext = await service.create(
        user_id="u1", tenant_id="t1", label="ci", role="ops"
    )
    assert plaintext.startswith(PAT_PREFIX)

    found = await service.verify(plaintext)
    assert found is not None and found.user_id == "u1" and found.role == "ops"
    assert await service.verify("vf_pat_wrong") is None

    assert await service.revoke(record.id, "u2") is False  # not the owner
    assert await service.revoke(record.id, "u1") is True
    assert await service.verify(plaintext) is None


async def test_pat_identity_roles(db):
    service = UserTokenService(db)
    _, admin_pat = await service.create(
        user_id="a1", tenant_id="t1", label="admin", role="admin"
    )
    record = await service.verify(admin_pat)
    assert record is not None and record.role == "admin"
