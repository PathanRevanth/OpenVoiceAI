"""Tenant-scoped platform settings (key/value), e.g. notification webhooks."""

from __future__ import annotations

from sqlalchemy import select

from voicefirst.db.models import TenantSettingRecord, new_id
from voicefirst.db.session import Database


class TenantSettingService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, tenant_id: str, key: str) -> str | None:
        async with self._db.session() as session:
            record = (
                await session.execute(
                    select(TenantSettingRecord).where(
                        TenantSettingRecord.tenant_id == tenant_id,
                        TenantSettingRecord.key == key,
                    )
                )
            ).scalar_one_or_none()
            return record.value if record else None

    async def set(self, tenant_id: str, key: str, value: str) -> None:
        async with self._db.session() as session:
            record = (
                await session.execute(
                    select(TenantSettingRecord).where(
                        TenantSettingRecord.tenant_id == tenant_id,
                        TenantSettingRecord.key == key,
                    )
                )
            ).scalar_one_or_none()
            if record is None:
                session.add(
                    TenantSettingRecord(
                        id=new_id("tset"), tenant_id=tenant_id, key=key, value=value
                    )
                )
            else:
                record.value = value
