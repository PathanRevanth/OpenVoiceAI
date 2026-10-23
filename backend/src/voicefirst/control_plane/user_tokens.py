"""Personal access tokens (PATs) for platform API calls, hashed at rest."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime

from sqlalchemy import select

from voicefirst.db.models import UserTokenRecord, new_id
from voicefirst.db.session import Database

PAT_PREFIX = "vf_pat_"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class UserTokenService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self, *, user_id: str, tenant_id: str, label: str, role: str
    ) -> tuple[UserTokenRecord, str]:
        plaintext = PAT_PREFIX + secrets.token_urlsafe(24)
        record = UserTokenRecord(
            id=new_id("pat"),
            user_id=user_id,
            tenant_id=tenant_id,
            label=label[:128] or "default",
            role=role,
            key_hash=_hash(plaintext),
            key_last4=plaintext[-4:],
        )
        async with self._db.session() as session:
            session.add(record)
        return record, plaintext

    async def verify(self, plaintext: str) -> UserTokenRecord | None:
        if not plaintext.startswith(PAT_PREFIX):
            return None
        target = _hash(plaintext)
        async with self._db.session() as session:
            rows = (
                (
                    await session.execute(
                        select(UserTokenRecord).where(
                            UserTokenRecord.key_hash == target,
                            UserTokenRecord.revoked.is_(False),
                        )
                    )
                )
                .scalars()
                .all()
            )
        for row in rows:
            if hmac.compare_digest(row.key_hash, target):
                async with self._db.session() as session:
                    fresh = await session.get(UserTokenRecord, row.id)
                    if fresh is not None:
                        fresh.last_used_at = datetime.now(UTC)
                return row
        return None

    async def revoke(self, token_id: str, user_id: str) -> bool:
        async with self._db.session() as session:
            record = await session.get(UserTokenRecord, token_id)
            if record is None or record.user_id != user_id:
                return False
            record.revoked = True
        return True

    async def list_for_user(self, user_id: str) -> list[UserTokenRecord]:
        async with self._db.session() as session:
            rows = (
                (
                    await session.execute(
                        select(UserTokenRecord)
                        .where(UserTokenRecord.user_id == user_id)
                        .order_by(UserTokenRecord.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    @staticmethod
    def to_dict(record: UserTokenRecord) -> dict:
        return {
            "id": record.id,
            "label": record.label,
            "role": record.role,
            "key_last4": f"••••{record.key_last4}",
            "revoked": record.revoked,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "last_used_at": record.last_used_at.isoformat() if record.last_used_at else None,
        }
