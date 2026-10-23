"""Tests for scheduled agent runs (triggers)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from voicefirst.control_plane.triggers import (
    TriggerScheduler,
    TriggerService,
    compute_next_run,
    schedule_summary,
)
from voicefirst.settings import Settings


def _make_record(**overrides) -> "TriggerRecord":  # type: ignore[name-defined]
    from voicefirst.db.models import TriggerRecord

    defaults = {
        "schedule_type": "interval",
        "interval_minutes": 30,
        "time_of_day": None,
    }
    defaults.update(overrides)
    return TriggerRecord(**defaults)


def test_compute_next_run_interval():
    now = datetime.now(UTC)
    nxt = compute_next_run(_make_record(interval_minutes=45), now)
    assert nxt - now == timedelta(minutes=45)


def test_compute_next_run_daily():
    now = datetime.now(UTC).replace(hour=10, minute=0, second=0, microsecond=0)
    nxt = compute_next_run(_make_record(schedule_type="daily", time_of_day="09:00"), now)
    # 09:00 already passed today -> tomorrow 09:00
    assert (nxt - now) == timedelta(days=23, hours=0) or (nxt - now) == timedelta(
        hours=23
    ) or nxt.date() == (now + timedelta(days=1)).date()
    assert nxt.hour == 9 and nxt.minute == 0


def test_schedule_summary():
    assert schedule_summary(_make_record(interval_minutes=60)) == "every 1h"
    assert schedule_summary(_make_record(interval_minutes=15)) == "every 15m"
    assert schedule_summary(_make_record(schedule_type="daily", time_of_day="09:00")) == (
        "daily at 09:00 UTC"
    )


async def test_trigger_lifecycle_and_firing(tmp_path):
    from voicefirst.agents.runtime import AgentRuntime

    settings = Settings(
        default_model="demo",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/triggers.db",
    )
    rt = await AgentRuntime.create(settings)
    try:
        service = TriggerService(rt.db)
        record = await service.create(
            tenant_id="t1",
            user_id="u1",
            name="hourly digest",
            agent_slug="research",
            message="Say hi",
            schedule_type="interval",
            interval_minutes=60,
            time_of_day=None,
            webhook_url=None,
        )
        assert record.enabled is True
        assert record.next_run_at is not None

        # force due and fire through the scheduler
        async with rt.db.session() as session:
            stored = await session.get(type(record), record.id)
            stored.next_run_at = datetime.now(UTC) - timedelta(seconds=1)

        scheduler = TriggerScheduler(rt)
        fired = await scheduler.fire_due()
        assert record.id in fired

        updated = await service.get(record.id, "t1")
        assert updated is not None
        assert updated.last_status == "completed"
        assert updated.last_run_id is not None
        nxt = updated.next_run_at
        assert nxt is not None
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=UTC)
        assert nxt > datetime.now(UTC)

        # disable -> no longer fires
        await service.set_enabled(record.id, "t1", False)
        scheduler2 = TriggerScheduler(rt)
        assert await scheduler2.fire_due() == []

        # delete
        assert await service.delete(record.id, "t1") is True
        assert await service.get(record.id, "t1") is None
    finally:
        await rt.close()
