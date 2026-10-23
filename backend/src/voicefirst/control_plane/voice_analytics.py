"""Voice session analytics: the metrics voice platforms compete on.

Per realtime session (browser WebSocket or Twilio Media Stream) we capture:

* **TTFW** — time to first spoken assistant word (the voice analogue of TTFT).
* **Turns** — final user + assistant transcript segments.
* **Barge-ins** — how often the caller interrupted the agent mid-utterance
  (detected as an assistant→user speaker transition in the transcript stream).
* **Outcome** — ended cleanly, failed, or still active; plus duration.

The Observability page aggregates these into the voice quality dashboard.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from voicefirst.db.models import VoiceSessionRecord, new_id
from voicefirst.db.session import Database


class VoiceAnalyticsService:
    def __init__(self, db: Database, events: Any = None) -> None:
        self._db = db
        self._events = events

    async def start_session(
        self,
        *,
        tenant_id: str,
        user_id: str,
        channel: str = "browser",
        provider: str | None = None,
        model: str | None = None,
        voice: str | None = None,
    ) -> VoiceSessionRecord:
        record = VoiceSessionRecord(
            id=new_id("vox"),
            tenant_id=tenant_id,
            user_id=user_id,
            channel=channel,
            provider=provider,
            model=model,
            voice=voice,
            status="active",
        )
        async with self._db.session() as session:
            session.add(record)
        if self._events is not None:
            await self._events.publish(
                "voice.session.started",
                {"session_id": record.id, "channel": channel, "model": model},
                tenant_id=tenant_id,
            )
        return record

    async def update_metrics(
        self,
        session_id: str,
        *,
        turns: int | None = None,
        ttfw_ms: int | None = None,
        barge_ins: int | None = None,
    ) -> None:
        async with self._db.session() as session:
            record = await session.get(VoiceSessionRecord, session_id)
            if record is None:
                return
            if turns is not None:
                record.turns = turns
            if ttfw_ms is not None and record.ttfw_ms is None:
                record.ttfw_ms = ttfw_ms
            if barge_ins is not None:
                record.barge_ins = barge_ins

    async def end_session(
        self, session_id: str, *, status: str = "ended", error: str | None = None
    ) -> None:
        now = datetime.now(UTC)
        async with self._db.session() as session:
            record = await session.get(VoiceSessionRecord, session_id)
            if record is None:
                return
            started = record.started_at if record.started_at.tzinfo else record.started_at.replace(tzinfo=UTC)
            record.status = status
            record.ended_at = now
            record.duration_ms = int((now - started).total_seconds() * 1000)
            record.error = error
        if self._events is not None:
            await self._events.publish(
                "voice.session.ended",
                {"session_id": session_id, "status": status},
                tenant_id=None,
            )

    async def list_sessions(self, tenant_id: str, limit: int = 50) -> list[VoiceSessionRecord]:
        async with self._db.session() as session:
            rows = (
                (
                    await session.execute(
                        select(VoiceSessionRecord)
                        .where(VoiceSessionRecord.tenant_id == tenant_id)
                        .order_by(VoiceSessionRecord.created_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    async def summary(self, tenant_id: str | None = None, days: int = 7) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(days=days)
        async with self._db.session() as session:
            stmt = select(
                func.count(VoiceSessionRecord.id),
                func.coalesce(func.sum(VoiceSessionRecord.turns), 0),
                func.coalesce(func.sum(VoiceSessionRecord.barge_ins), 0),
                func.coalesce(func.avg(VoiceSessionRecord.ttfw_ms), 0),
                func.coalesce(func.avg(VoiceSessionRecord.duration_ms), 0),
            ).where(VoiceSessionRecord.created_at >= since)
            if tenant_id:
                stmt = stmt.where(VoiceSessionRecord.tenant_id == tenant_id)
            total, turns, barge_ins, avg_ttfw, avg_duration = (await session.execute(stmt)).one()

            by_day_stmt = select(
                func.date(VoiceSessionRecord.created_at),
                func.count(VoiceSessionRecord.id),
                func.coalesce(func.avg(VoiceSessionRecord.ttfw_ms), 0),
            ).where(VoiceSessionRecord.created_at >= since)
            if tenant_id:
                by_day_stmt = by_day_stmt.where(VoiceSessionRecord.tenant_id == tenant_id)
            by_day_stmt = by_day_stmt.group_by(func.date(VoiceSessionRecord.created_at)).order_by(
                func.date(VoiceSessionRecord.created_at)
            )
            by_day = [
                {"date": str(day), "sessions": int(n), "avg_ttfw_ms": float(t)}
                for day, n, t in (await session.execute(by_day_stmt)).all()
            ]

        return {
            "window_days": days,
            "sessions": int(total),
            "turns": int(turns),
            "barge_ins": int(barge_ins),
            "avg_ttfw_ms": round(float(avg_ttfw), 1),
            "avg_duration_ms": round(float(avg_duration), 1),
            "by_day": by_day,
        }

    @staticmethod
    def to_dict(record: VoiceSessionRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "channel": record.channel,
            "provider": record.provider,
            "model": record.model,
            "voice": record.voice,
            "status": record.status,
            "turns": record.turns,
            "ttfw_ms": record.ttfw_ms,
            "barge_ins": record.barge_ins,
            "duration_ms": record.duration_ms,
            "error": record.error,
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "ended_at": record.ended_at.isoformat() if record.ended_at else None,
        }
