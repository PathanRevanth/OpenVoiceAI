"""Per-agent programmatic access: API keys for direct agent invocation.

External systems (n8n, cron, other services) call an agent's webhook with a
key of the form ``vf_ak_...``. Keys are shown once at creation and stored as
SHA-256 hashes; revocation is immediate.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select

from voicefirst.db.models import AgentKeyRecord, new_id
from voicefirst.db.session import Database

KEY_PREFIX = "vf_ak_"


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class AgentAccessService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_key(
        self, *, agent_slug: str, tenant_id: str, label: str
    ) -> tuple[AgentKeyRecord, str]:
        """Create a key. Returns (record, plaintext) — plaintext is shown once."""
        plaintext = KEY_PREFIX + secrets.token_urlsafe(24)
        record = AgentKeyRecord(
            id=new_id("akey"),
            agent_slug=agent_slug,
            tenant_id=tenant_id,
            label=label[:128] or "default",
            key_hash=_hash_key(plaintext),
            key_last4=plaintext[-4:],
        )
        async with self._db.session() as session:
            session.add(record)
        return record, plaintext

    async def list_keys(self, agent_slug: str, tenant_id: str) -> list[AgentKeyRecord]:
        async with self._db.session() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentKeyRecord)
                        .where(
                            AgentKeyRecord.agent_slug == agent_slug,
                            AgentKeyRecord.tenant_id == tenant_id,
                        )
                        .order_by(AgentKeyRecord.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    async def verify(self, agent_slug: str, key: str) -> AgentKeyRecord | None:
        """Timing-safe hash lookup; returns the record if active, else None."""
        if not key.startswith(KEY_PREFIX):
            return None
        target = _hash_key(key)
        async with self._db.session() as session:
            rows = (
                (
                    await session.execute(
                        select(AgentKeyRecord).where(
                            AgentKeyRecord.agent_slug == agent_slug,
                            AgentKeyRecord.key_hash == target,
                            AgentKeyRecord.revoked.is_(False),
                        )
                    )
                )
                .scalars()
                .all()
            )
        import hmac

        for row in rows:
            if hmac.compare_digest(row.key_hash, target):
                async with self._db.session() as session:
                    fresh = await session.get(AgentKeyRecord, row.id)
                    if fresh is not None:
                        fresh.last_used_at = datetime.now(UTC)
                return row
        return None

    async def revoke(self, key_id: str, agent_slug: str, tenant_id: str) -> bool:
        async with self._db.session() as session:
            record = await session.get(AgentKeyRecord, key_id)
            if record is None or record.agent_slug != agent_slug or record.tenant_id != tenant_id:
                return False
            record.revoked = True
        return True

    @staticmethod
    def to_dict(record: AgentKeyRecord) -> dict:
        return {
            "id": record.id,
            "label": record.label,
            "key_last4": f"••••{record.key_last4}",
            "revoked": record.revoked,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "last_used_at": record.last_used_at.isoformat() if record.last_used_at else None,
        }
