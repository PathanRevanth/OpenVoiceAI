"""Scheduled agent runs: CRUD, next-run computation, and the firing loop.

The scheduler runs as one asyncio task started at app startup. Every 20
seconds it fires due triggers through the same invocation core as the API
and webhooks (so approvals, usage, and audit apply identically), then
optionally delivers the result to the owner's webhook URL.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from voicefirst.agents.deps import AgentDeps
from voicefirst.api.invocations import run_agent_invocation
from voicefirst.db.models import TriggerRecord, new_id
from voicefirst.db.session import Database

LOOP_INTERVAL_SECONDS = 20


def compute_next_run(trigger: TriggerRecord, from_dt: datetime) -> datetime:
    """Next due datetime (UTC) for a trigger, strictly after from_dt."""
    if trigger.schedule_type == "daily" and trigger.time_of_day:
        hour, minute = (int(x) for x in trigger.time_of_day.split(":")[:2])
        candidate = from_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= from_dt:
            candidate += timedelta(days=1)
        return candidate
    minutes = max(1, trigger.interval_minutes or 60)
    return from_dt + timedelta(minutes=minutes)


def schedule_summary(trigger: TriggerRecord) -> str:
    if trigger.schedule_type == "daily":
        return f"daily at {trigger.time_of_day} UTC"
    minutes = trigger.interval_minutes or 0
    if minutes % 60 == 0 and minutes >= 60:
        return f"every {minutes // 60}h"
    return f"every {minutes}m"


class TriggerService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        name: str,
        agent_slug: str,
        message: str,
        schedule_type: str,
        interval_minutes: int | None,
        time_of_day: str | None,
        webhook_url: str | None,
    ) -> TriggerRecord:
        record = TriggerRecord(
            id=new_id("trg"),
            tenant_id=tenant_id,
            user_id=user_id,
            name=name[:128],
            agent_slug=agent_slug,
            message=message,
            schedule_type=schedule_type,
            interval_minutes=interval_minutes,
            time_of_day=time_of_day,
            webhook_url=webhook_url or None,
            enabled=True,
            next_run_at=compute_next_run(
                TriggerRecord(
                    schedule_type=schedule_type,
                    interval_minutes=interval_minutes,
                    time_of_day=time_of_day,
                ),
                datetime.now(UTC),
            ),
        )
        async with self._db.session() as session:
            session.add(record)
        return record

    async def list_for_tenant(self, tenant_id: str) -> list[TriggerRecord]:
        async with self._db.session() as session:
            rows = (
                (
                    await session.execute(
                        select(TriggerRecord)
                        .where(TriggerRecord.tenant_id == tenant_id)
                        .order_by(TriggerRecord.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    async def get(self, trigger_id: str, tenant_id: str) -> TriggerRecord | None:
        async with self._db.session() as session:
            record = await session.get(TriggerRecord, trigger_id)
            if record is None or record.tenant_id != tenant_id:
                return None
            return record

    async def set_enabled(self, trigger_id: str, tenant_id: str, enabled: bool) -> TriggerRecord | None:
        async with self._db.session() as session:
            record = await session.get(TriggerRecord, trigger_id)
            if record is None or record.tenant_id != tenant_id:
                return None
            record.enabled = enabled
            record.next_run_at = (
                compute_next_run(record, datetime.now(UTC)) if enabled else None
            )
        return await self.get(trigger_id, tenant_id)

    async def delete(self, trigger_id: str, tenant_id: str) -> bool:
        async with self._db.session() as session:
            record = await session.get(TriggerRecord, trigger_id)
            if record is None or record.tenant_id != tenant_id:
                return False
            await session.delete(record)
        return True

    @staticmethod
    def to_dict(record: TriggerRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "name": record.name,
            "agent_slug": record.agent_slug,
            "message": record.message,
            "schedule": schedule_summary(record),
            "enabled": record.enabled,
            "webhook_url": record.webhook_url,
            "last_run_at": record.last_run_at.isoformat() if record.last_run_at else None,
            "next_run_at": record.next_run_at.isoformat() if record.next_run_at else None,
            "last_status": record.last_status,
            "last_run_id": record.last_run_id,
        }


class TriggerScheduler:
    """Background loop that fires due triggers."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self._db: Database = runtime.db
        self._service = TriggerService(db=runtime.db)
        self._task: asyncio.Task | None = None

    @property
    def alive(self) -> bool:
        """Whether the scheduler loop task is running (used by /ready)."""
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.fire_due()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive anything
                pass
            await asyncio.sleep(LOOP_INTERVAL_SECONDS)

    async def fire_due(self) -> list[str]:
        """Fire every enabled trigger whose next_run_at is due."""
        now = datetime.now(UTC)
        async with self._db.session() as session:
            due = (
                (
                    await session.execute(
                        select(TriggerRecord).where(
                            TriggerRecord.enabled.is_(True),
                            TriggerRecord.next_run_at.is_not(None),
                            TriggerRecord.next_run_at <= now,
                        )
                    )
                )
                .scalars()
                .all()
            )
            due = list(due)

        fired: list[str] = []
        for trigger in due:
            fired.append(trigger.id)
            status, run_id = "failed", None
            payload: dict[str, Any] | None = None
            deps = AgentDeps(
                tenant_id=trigger.tenant_id,
                user_id=f"trigger:{trigger.name}",
                role="ops",
                runtime=self._runtime,
                channel="api",
            )
            try:
                payload = await run_agent_invocation(
                    self._runtime, trigger.agent_slug, trigger.message, deps, self._db
                )
                status = payload["status"]
                run_id = payload["run_id"]
            except Exception as exc:  # noqa: BLE001 - record, keep scheduling
                status = "failed"
                payload = {"error": str(exc)[:300]}

            next_at = compute_next_run(trigger, datetime.now(UTC))
            async with self._db.session() as session:
                stored = await session.get(TriggerRecord, trigger.id)
                if stored is not None:
                    stored.last_run_at = datetime.now(UTC)
                    stored.next_run_at = next_at
                    stored.last_status = status
                    stored.last_run_id = run_id

            await self._runtime.events.publish(
                "trigger.fired",
                {
                    "trigger_id": trigger.id,
                    "name": trigger.name,
                    "agent": trigger.agent_slug,
                    "status": status,
                },
                tenant_id=trigger.tenant_id,
            )

            if trigger.webhook_url:
                delivery = {
                    "event": "trigger.completed",
                    "trigger_id": trigger.id,
                    "name": trigger.name,
                    "agent": trigger.agent_slug,
                    "status": status,
                    "run_id": run_id,
                    "result": payload,
                }
                asyncio.create_task(deliver_webhook(trigger.webhook_url, delivery))
        return fired


async def deliver_webhook(url: str, payload: dict[str, Any]) -> None:
    """Signed, best-effort result delivery; never raises."""
    from voicefirst.control_plane.webhooks import deliver_webhook as _signed_deliver

    await _signed_deliver(url, payload, event="trigger.completed")
