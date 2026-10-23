"""Hash-chained, append-only audit service."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from voicefirst.control_plane.events import EventBus
from voicefirst.db.models import AuditRecord, new_id
from voicefirst.db.session import Database

GENESIS_HASH = "0" * 64


class AuditService:
    """Records every consequential action in a tamper-evident chain.

    Each entry's `hash` is `sha256(prev_hash + canonical(entry))`, so any
    retroactive mutation breaks the chain and is detectable by `verify()`.
    """

    def __init__(self, db: Database, events: EventBus) -> None:
        self._db = db
        self._events = events

    @staticmethod
    def _ts_key(ts: datetime) -> str:
        """Canonical timestamp string for hashing.

        SQLite drops tzinfo on read-back, so naive datetimes are normalized as
        UTC. Both record() and verify() hash through this, keeping the chain
        stable across write/read round-trips.
        """
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC).isoformat()

    @classmethod
    def _canonical(
        cls, *, ts_value: datetime, actor: str, action: str, resource: str, tenant_id: str, detail: dict[str, Any] | None, prev_hash: str
    ) -> str:
        payload = json.dumps(
            {
                "ts": cls._ts_key(ts_value),
                "actor": actor,
                "action": action,
                "resource": resource,
                "tenant_id": tenant_id,
                "detail": detail or {},
                "prev_hash": prev_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    async def record(
        self,
        *,
        actor: str,
        action: str,
        resource: str = "",
        tenant_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> AuditRecord:
        async with self._db.session() as session:
            prev = (
                await session.execute(select(AuditRecord.hash).order_by(AuditRecord.ts.desc(), AuditRecord.id.desc()).limit(1))
            ).scalar_one_or_none()
            prev_hash = prev or GENESIS_HASH
            ts = datetime.now(UTC)
            entry_hash = self._canonical(
                ts_value=ts,
                actor=actor,
                action=action,
                resource=resource,
                tenant_id=tenant_id,
                detail=detail,
                prev_hash=prev_hash,
            )
            record = AuditRecord(
                id=new_id("aud"),
                ts=ts,
                actor=actor,
                action=action,
                resource=resource,
                tenant_id=tenant_id,
                detail=detail,
                prev_hash=prev_hash,
                hash=entry_hash,
            )
            session.add(record)
        await self._events.publish(
            "audit.recorded",
            {"action": action, "actor": actor, "resource": resource, "tenant_id": tenant_id},
        )
        return record

    async def list_entries(self, limit: int = 100, action: str | None = None) -> list[AuditRecord]:
        async with self._db.session() as session:
            stmt = select(AuditRecord).order_by(AuditRecord.ts.desc()).limit(limit)
            if action:
                stmt = stmt.where(AuditRecord.action == action)
            rows = (await session.execute(stmt)).scalars().all()
            return list(rows)

    async def verify(self, limit: int = 1000) -> tuple[bool, str | None]:
        """Verify the hash chain over the most recent `limit` entries."""
        async with self._db.session() as session:
            rows = (
                (await session.execute(select(AuditRecord).order_by(AuditRecord.ts, AuditRecord.id).limit(limit)))
                .scalars()
                .all()
            )
        prev = GENESIS_HASH
        for row in rows:
            if row.prev_hash != prev:
                return False, f"broken chain at {row.id}"
            expected = self._canonical(
                ts_value=row.ts,
                actor=row.actor,
                action=row.action,
                resource=row.resource,
                tenant_id=row.tenant_id,
                detail=row.detail,
                prev_hash=row.prev_hash,
            )
            if expected != row.hash:
                return False, f"hash mismatch at {row.id}"
            prev = row.hash
        return True, None
