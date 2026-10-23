"""Tests for the per-user connection vault (crypto + service)."""

from __future__ import annotations

import pytest

from voicefirst.control_plane.connections import (
    KeyVault,
    build_chat_model,
    build_realtime_model,
    models_for,
    supports,
)
from voicefirst.settings import Settings


def test_keyvault_roundtrip_and_at_rest():
    vault = KeyVault("master-secret")
    token = vault.encrypt("sk-super-secret-key")
    assert token != "sk-super-secret-key"
    assert "sk-super" not in token
    assert vault.decrypt(token) == "sk-super-secret-key"
    # different master secret cannot decrypt
    other = KeyVault("other-secret")
    with pytest.raises(Exception):
        other.decrypt(token)


def test_provider_catalogs():
    assert supports("agents", "openai")
    assert supports("agents", "anthropic")
    assert supports("voice", "openai")
    assert supports("voice", "google")
    # Anthropic has no realtime speech-to-speech API
    assert not supports("voice", "anthropic")
    assert "gpt-realtime-2.1" in models_for("voice", "openai")
    assert "claude-sonnet-4-6" in models_for("agents", "anthropic")


def test_model_builders():
    model = build_chat_model("openai", "sk-test", "gpt-5.2")
    assert model.model_name == "gpt-5.2"
    model = build_chat_model("anthropic", "sk-ant-test", "claude-sonnet-4-6")
    assert "claude-sonnet-4-6" in model.model_name
    rt = build_realtime_model("openai", "sk-test", "gpt-realtime-2.1")
    assert "gpt-realtime" in str(rt)
    with pytest.raises(ValueError):
        build_realtime_model("anthropic", "key", "whatever")


async def test_connection_service_lifecycle(tmp_path):
    from voicefirst.agents.runtime import AgentRuntime

    settings = Settings(
        default_model="demo",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/conn.db",
        secret_key="test-master",
    )
    rt = await AgentRuntime.create(settings)
    try:
        service = rt.connections
        # Upsert without a real provider key -> stored, marked invalid (live
        # test fails), but never raises and never stores plaintext.
        record = await service.upsert(
            user_id="u1",
            tenant_id="t1",
            purpose="agents",
            provider="openai",
            model="gpt-5.2",
            key="sk-not-a-real-key-123456",
        )
        assert record.status == "invalid"
        assert record.key_last4 == "3456"
        assert "sk-not" not in record.key_encrypted

        # active resolution only returns valid connections
        assert await service.get_active("u1", "agents") is None

        records = await service.list_for_user("u1")
        assert len(records) == 1
        view = service.to_dict(records[0])
        # Masked view: only last-4, never any decryptable material
        assert view["key_last4"] == "••••3456"
        assert "key_encrypted" not in view

        # delete works, scoped to the owner
        assert await service.delete("u2", record.id) is False
        assert await service.delete("u1", record.id) is True
    finally:
        await rt.close()
