"""Per-user provider connections (LLM keys), stored encrypted at rest.

Design (industry practice):
* Keys are AES-encrypted (Fernet, SHA-256-derived key from `SECRET_KEY`)
  and scoped to (user, purpose, provider). One active connection per purpose.
* The plaintext key exists only in memory for the duration of a provider
  call — it is never logged, never returned by any API, and never leaves
  the server except to the provider itself.
* `purpose` separates agent (chat) models from voice (realtime) models;
  the same provider key can be (and typically is) reused across purposes.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from voicefirst.db.models import ConnectionRecord, new_id
from voicefirst.db.session import Database

# ---------------------------------------------------------------------------
# Provider catalog (latest stable models, pydantic-ai 2.35)
# ---------------------------------------------------------------------------

# Model catalogs: latest-first, tier-tagged for the connection UI.
# Every id is a valid pydantic-ai model name for its provider (v2.35).

CHAT_MODELS: dict[str, list[dict[str, str]]] = {
    "openai": [
        {"id": "gpt-5.6-sol", "label": "GPT-5.6 Sol", "tag": "Flagship"},
        {"id": "gpt-5.6-terra", "label": "GPT-5.6 Terra", "tag": "Balanced"},
        {"id": "gpt-5.6-luna", "label": "GPT-5.6 Luna", "tag": "Fast"},
        {"id": "gpt-5.6-cyber", "label": "GPT-5.6 Cyber", "tag": "Security"},
        {"id": "gpt-5.5-pro", "label": "GPT-5.5 Pro", "tag": "Reasoning"},
        {"id": "gpt-5.5", "label": "GPT-5.5", "tag": ""},
        {"id": "gpt-5.4", "label": "GPT-5.4", "tag": ""},
        {"id": "gpt-5.3-chat-latest", "label": "GPT-5.3 Chat", "tag": ""},
        {"id": "gpt-5.2-pro", "label": "GPT-5.2 Pro", "tag": "Reasoning"},
        {"id": "gpt-5.2", "label": "GPT-5.2", "tag": ""},
        {"id": "gpt-5.1", "label": "GPT-5.1", "tag": ""},
        {"id": "gpt-5", "label": "GPT-5", "tag": ""},
    ],
    "anthropic": [
        {"id": "claude-opus-5", "label": "Claude Opus 5", "tag": "Flagship"},
        {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "tag": "Agentic"},
        {"id": "claude-fable-5", "label": "Claude Fable 5", "tag": "Adaptive"},
        {"id": "claude-mythos-5", "label": "Claude Mythos 5", "tag": ""},
        {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5", "tag": "Fast"},
        {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6", "tag": ""},
        {"id": "claude-opus-4-8", "label": "Claude Opus 4.8", "tag": ""},
    ],
    "google": [
        {"id": "gemini-3.7-flash", "label": "Gemini 3.7 Flash", "tag": "Newest"},
        {"id": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro", "tag": "Flagship"},
        {"id": "gemini-3.6-flash", "label": "Gemini 3.6 Flash", "tag": ""},
        {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash", "tag": ""},
        {"id": "gemini-3.5-flash-lite", "label": "Gemini 3.5 Flash Lite", "tag": "Fast"},
        {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "tag": ""},
        {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "tag": ""},
    ],
}

VOICE_MODELS: dict[str, list[dict[str, str]]] = {
    "openai": [
        {"id": "gpt-realtime-2.1", "label": "GPT Realtime 2.1", "tag": "Flagship"},
        {"id": "gpt-realtime-2.1-mini", "label": "GPT Realtime 2.1 Mini", "tag": "Fast"},
        {"id": "gpt-realtime", "label": "GPT Realtime", "tag": ""},
    ],
    "google": [
        {"id": "gemini-3.1-flash-live-preview", "label": "Gemini 3.1 Flash Live", "tag": "Newest"},
        {"id": "gemini-2.5-flash-native-audio-latest", "label": "Gemini 2.5 Native Audio", "tag": "Stable"},
    ],
    # Anthropic has no realtime speech-to-speech API.
}


# Prebuilt realtime voices, applied per session via model settings
# (`openai_voice` / `google_voice`). Anthropic ships none.
VOICE_CATALOG: dict[str, list[dict[str, str]]] = {
    "openai": [
        {"id": "alloy", "label": "Alloy"},
        {"id": "ash", "label": "Ash"},
        {"id": "ballad", "label": "Ballad"},
        {"id": "coral", "label": "Coral"},
        {"id": "echo", "label": "Echo"},
        {"id": "sage", "label": "Sage"},
        {"id": "shimmer", "label": "Shimmer"},
        {"id": "verse", "label": "Verse"},
    ],
    "google": [
        {"id": "Zephyr", "label": "Zephyr"},
        {"id": "Puck", "label": "Puck"},
        {"id": "Charon", "label": "Charon"},
        {"id": "Kore", "label": "Kore"},
        {"id": "Fenrir", "label": "Fenrir"},
        {"id": "Aoede", "label": "Aoede"},
        {"id": "Leda", "label": "Leda"},
        {"id": "Orus", "label": "Orus"},
    ],
}


PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {"label": "OpenAI", "tile": "#10A37F", "tint": "rgba(16,163,127,0.12)"},
    "anthropic": {"label": "Anthropic", "tile": "#D97757", "tint": "rgba(217,119,87,0.14)"},
    "google": {"label": "Google", "tile": "#4285F4", "tint": "rgba(66,133,244,0.12)"},
}


def models_for(purpose: str, provider: str) -> list[str]:
    return [m["id"] for m in (VOICE_MODELS if purpose == "voice" else CHAT_MODELS).get(provider, [])]


def model_entries(purpose: str, provider: str) -> list[dict[str, str]]:
    return (VOICE_MODELS if purpose == "voice" else CHAT_MODELS).get(provider, [])


def supports(purpose: str, provider: str) -> bool:
    return bool(models_for(purpose, provider))


# ---------------------------------------------------------------------------
# Key encryption
# ---------------------------------------------------------------------------


class KeyVault:
    def __init__(self, secret: str) -> None:
        from cryptography.fernet import Fernet

        derived = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self._fernet = Fernet(derived)

    def encrypt(self, key: str) -> str:
        return self._fernet.encrypt(key.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()


# ---------------------------------------------------------------------------
# Live provider tests (cheapest real call per provider)
# ---------------------------------------------------------------------------


async def test_provider_key(provider: str, key: str) -> tuple[bool, str]:
    """Make a minimal real API call. Returns (ok, detail). The key is used
    for this call only and never persisted."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if provider == "openai":
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
            elif provider == "anthropic":
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-haiku-4-5",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                )
            elif provider == "google":
                resp = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    headers={"x-goog-api-key": key},
                )
            else:
                return False, f"unknown provider {provider}"
    except httpx.HTTPError as exc:
        return False, f"network error: {exc}"

    if resp.status_code == 200:
        return True, "authenticated"
    if resp.status_code == 401:
        return False, "invalid API key"
    if resp.status_code == 403:
        return False, "key lacks access to this API"
    if resp.status_code == 429:
        return True, "key valid (rate limited on free tier)"
    return False, f"provider returned {resp.status_code}"


# ---------------------------------------------------------------------------
# Model builders (per-request; keys decrypted in memory only)
# ---------------------------------------------------------------------------


def build_chat_model(provider: str, key: str, model: str) -> Any:
    """Construct a pydantic-ai chat model bound to a user's key."""
    if provider == "openai":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        return OpenAIChatModel(model, provider=OpenAIProvider(api_key=key))
    if provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        return AnthropicModel(model, provider=AnthropicProvider(api_key=key))
    if provider == "google":
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        return GoogleModel(model, provider=GoogleProvider(api_key=key))
    raise ValueError(f"unsupported chat provider: {provider}")


def build_realtime_model(provider: str, key: str, model: str) -> Any:
    """Construct a pydantic-ai realtime (speech-to-speech) model."""
    if provider == "openai":
        from pydantic_ai.providers.openai import OpenAIProvider
        from pydantic_ai.realtime.openai import OpenAIRealtimeModel

        return OpenAIRealtimeModel(model, provider=OpenAIProvider(api_key=key))
    if provider == "google":
        from pydantic_ai.providers.google import GoogleProvider
        from pydantic_ai.realtime.google import GoogleRealtimeModel

        return GoogleRealtimeModel(model, provider=GoogleProvider(api_key=key))
    raise ValueError(f"unsupported realtime provider: {provider}")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class ResolvedConnection:
    provider: str
    model: str
    key: str  # decrypted; in-memory only


class ConnectionService:
    def __init__(self, db: Database, vault: KeyVault) -> None:
        self._db = db
        self._vault = vault

    # -- persistence ---------------------------------------------------------

    async def upsert(
        self,
        *,
        user_id: str,
        tenant_id: str,
        purpose: str,
        provider: str,
        model: str,
        key: str,
        fallback_model: str | None = None,
    ) -> ConnectionRecord:
        if purpose not in ("agents", "voice"):
            raise ValueError("purpose must be 'agents' or 'voice'")
        if provider not in PROVIDERS:
            raise ValueError(f"unsupported provider: {provider}")
        if not supports(purpose, provider):
            raise ValueError(f"{PROVIDERS[provider]['label']} does not support {purpose} models")

        ok, detail = await test_provider_key(provider, key)
        encrypted = self._vault.encrypt(key)
        now = datetime.now(UTC)

        async with self._db.session() as session:
            record = (
                await session.execute(
                    select(ConnectionRecord).where(
                        ConnectionRecord.user_id == user_id,
                        ConnectionRecord.purpose == purpose,
                        ConnectionRecord.provider == provider,
                    )
                )
            ).scalar_one_or_none()
            if record is None:
                record = ConnectionRecord(id=new_id("cnx"), user_id=user_id, tenant_id=tenant_id)
                session.add(record)
            record.purpose = purpose
            record.provider = provider
            record.model = model
            record.fallback_model = fallback_model or None
            record.key_encrypted = encrypted
            record.key_last4 = key[-4:]
            record.status = "valid" if ok else "invalid"
            record.test_detail = detail
            record.last_tested_at = now
            record.is_active = True
            connection_id = record.id
        if not ok:
            # Keep the record so the user sees the invalid state in the UI.
            pass
        await self._set_active(user_id, purpose, connection_id)
        return await self.get_by_id(connection_id)  # type: ignore[return-value]

    async def _set_active(self, user_id: str, purpose: str, connection_id: str) -> None:
        """Only one active connection per (user, purpose)."""
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(ConnectionRecord).where(
                        ConnectionRecord.user_id == user_id,
                        ConnectionRecord.purpose == purpose,
                    )
                )
            ).scalars()
            for row in rows:
                row.is_active = row.id == connection_id

    async def activate(self, user_id: str, connection_id: str) -> ConnectionRecord | None:
        record = await self.get_by_id(connection_id)
        if record is None or record.user_id != user_id:
            return None
        await self._set_active(user_id, record.purpose, connection_id)
        return await self.get_by_id(connection_id)

    async def delete(self, user_id: str, connection_id: str) -> bool:
        async with self._db.session() as session:
            record = await session.get(ConnectionRecord, connection_id)
            if record is None or record.user_id != user_id:
                return False
            await session.delete(record)
        return True

    async def get_by_id(self, connection_id: str) -> ConnectionRecord | None:
        async with self._db.session() as session:
            return await session.get(ConnectionRecord, connection_id)

    async def list_for_user(self, user_id: str) -> list[ConnectionRecord]:
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(ConnectionRecord)
                    .where(ConnectionRecord.user_id == user_id)
                    .order_by(ConnectionRecord.purpose, ConnectionRecord.provider)
                )
            ).scalars()
            return list(rows)

    async def get_active(self, user_id: str, purpose: str) -> ConnectionRecord | None:
        async with self._db.session() as session:
            return (
                await session.execute(
                    select(ConnectionRecord).where(
                        ConnectionRecord.user_id == user_id,
                        ConnectionRecord.purpose == purpose,
                        ConnectionRecord.is_active.is_(True),
                        ConnectionRecord.status == "valid",
                    )
                )
            ).scalar_one_or_none()

    async def retest(self, user_id: str, connection_id: str) -> ConnectionRecord | None:
        record = await self.get_by_id(connection_id)
        if record is None or record.user_id != user_id:
            return None
        key = self._vault.decrypt(record.key_encrypted)
        ok, detail = await test_provider_key(record.provider, key)
        async with self._db.session() as session:
            row = await session.get(ConnectionRecord, connection_id)
            if row is None:
                return None
            row.status = "valid" if ok else "invalid"
            row.test_detail = detail
            row.last_tested_at = datetime.now(UTC)
        return await self.get_by_id(connection_id)

    # -- runtime resolution ----------------------------------------------------

    async def resolve_chat_model(self, user_id: str) -> Any:
        """Decrypted, provider-bound chat model for the user's active
        connection — or None to fall back to platform config.

        When the connection defines a `fallback_model` (same provider), the
        result is a pydantic-ai `FallbackModel`: the primary model is tried
        first and the fallback takes over on provider errors/rate limits.
        """
        record = await self.get_active(user_id, "agents")
        if record is None:
            return None
        key = self._vault.decrypt(record.key_encrypted)
        primary = build_chat_model(record.provider, key, record.model)
        fallback_name = (record.fallback_model or "").strip()
        if fallback_name and fallback_name != record.model and fallback_name in models_for("agents", record.provider):
            from pydantic_ai.models.fallback import FallbackModel

            return FallbackModel(primary, build_chat_model(record.provider, key, fallback_name))
        return primary

    async def key_for_provider(self, user_id: str, provider: str) -> str | None:
        """The user's own key for `provider` (any saved connection), else the
        platform key from configuration. Keys are decrypted in memory only."""
        for record in await self.list_for_user(user_id):
            if record.provider == provider and record.key_encrypted:
                return self._vault.decrypt(record.key_encrypted)
        from voicefirst.settings import get_settings

        settings = get_settings()
        return {
            "openai": settings.openai_api_key,
            "anthropic": settings.anthropic_api_key,
            "google": settings.google_api_key,
        }.get(provider)

    async def model_for_selection(self, user_id: str, model_id: str) -> Any | None:
        """A provider-bound chat model for a picker selection (`provider:model`)."""
        provider, _, name = model_id.partition(":")
        if not name:
            return None
        key = await self.key_for_provider(user_id, provider)
        if not key:
            return None
        return build_chat_model(provider, key, name)

    async def resolve_embedding_provider(self, user_id: str) -> tuple[str, str, str] | None:
        """(provider, key, label) for embedding-capable connections."""
        record = await self.get_active(user_id, "agents")
        if record is None or record.provider not in ("openai", "google"):
            return None
        return (record.provider, self._vault.decrypt(record.key_encrypted), record.model)

    async def resolve_voice_model(self, user_id: str) -> Any:
        record = await self.get_active(user_id, "voice")
        if record is None:
            return None
        key = self._vault.decrypt(record.key_encrypted)
        return build_realtime_model(record.provider, key, record.model)

    # -- masked view for APIs ---------------------------------------------

    @staticmethod
    def to_dict(record: ConnectionRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "purpose": record.purpose,
            "provider": record.provider,
            "model": record.model,
            "key_last4": f"••••{record.key_last4}",
            "fallback_model": record.fallback_model,
            "status": record.status,
            "test_detail": record.test_detail,
            "last_tested_at": record.last_tested_at.isoformat() if record.last_tested_at else None,
            "is_active": record.is_active,
        }
