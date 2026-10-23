"""Usage (tokens/cost) capture and aggregation."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from voicefirst.db.models import UsageRecord, new_id
from voicefirst.db.session import Database


def _usage_dict(usage: Any) -> dict[str, Any]:
    try:
        return dataclasses.asdict(usage)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        return {}


class UsageService:
    def __init__(self, db: Database, events: Any = None) -> None:
        self._db = db
        self._events = events
        self._warned: dict[str, str] = {}  # tenant -> day (warn once per crossing per day)

    async def record_run_usage(
        self,
        *,
        run_id: str,
        agent_slug: str,
        model: str | None,
        tenant_id: str,
        usage: Any,
        user_id: str = "",
    ) -> UsageRecord | None:
        """Persist a pydantic-ai `RunUsage` (or compatible) object for a run."""
        if usage is None:
            return None
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        record = UsageRecord(
            id=new_id("usg"),
            run_id=run_id,
            agent_slug=agent_slug,
            model=model,
            tenant_id=tenant_id,
            user_id=user_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=int(getattr(usage, "total_tokens", input_tokens + output_tokens) or input_tokens + output_tokens),
            requests=int(getattr(usage, "requests", 0) or 0),
            tool_calls=int(getattr(usage, "tool_calls", 0) or 0),
            cost_usd=float(getattr(usage, "cost", 0.0) or 0.0),
        )
        async with self._db.session() as session:
            session.add(record)
        if self._events is not None:
            await self._check_budget(tenant_id)
        return record

    async def _check_budget(self, tenant_id: str, warn_ratio: float = 0.8) -> None:
        """Publish a budget.warning event when daily spend crosses 80%% (once/day)
        or 100%% of the configured daily limit."""
        from voicefirst.db.models import UsageRecord

        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        async with self._db.session() as session:
            from sqlalchemy import func

            spent = (
                await session.execute(
                    select(func.sum(UsageRecord.cost_usd)).where(
                        UsageRecord.tenant_id == tenant_id,
                        UsageRecord.created_at >= day_start,
                    )
                )
            ).scalar_one_or_none() or 0.0

        from voicefirst.settings import get_settings

        limit = get_settings().daily_spend_limit_usd or 0
        if limit <= 0:
            return
        ratio = float(spent) / float(limit)
        today = day_start.date().isoformat()
        level = None
        if ratio >= 1.0 and self._warned.get(tenant_id) != "exceeded:" + today:
            level = "exceeded"
        elif ratio >= warn_ratio and self._warned.get(tenant_id) != "warned:" + today:
            level = "warned"
        if level:
            self._warned[tenant_id] = level + ":" + today
            await self._events.publish(
                "budget.warning",
                {
                    "tenant_id": tenant_id,
                    "spend_today": round(float(spent), 4),
                    "daily_limit": limit,
                    "level": level,
                },
                tenant_id=tenant_id,
            )

    async def summary(self, tenant_id: str | None = None, days: int = 30) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(days=days)
        async with self._db.session() as session:
            stmt = select(
                func.count(UsageRecord.id),
                func.coalesce(func.sum(UsageRecord.input_tokens), 0),
                func.coalesce(func.sum(UsageRecord.output_tokens), 0),
                func.coalesce(func.sum(UsageRecord.requests), 0),
                func.coalesce(func.sum(UsageRecord.tool_calls), 0),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0.0),
            ).where(UsageRecord.created_at >= since)
            if tenant_id:
                stmt = stmt.where(UsageRecord.tenant_id == tenant_id)
            total, inp, outp, requests, tool_calls, cost = (await session.execute(stmt)).one()

            by_agent_stmt = select(
                UsageRecord.agent_slug,
                func.count(UsageRecord.id),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0.0),
            ).where(UsageRecord.created_at >= since)
            if tenant_id:
                by_agent_stmt = by_agent_stmt.where(UsageRecord.tenant_id == tenant_id)
            by_agent_stmt = by_agent_stmt.group_by(UsageRecord.agent_slug)
            by_agent = [
                {"agent": slug, "runs": runs, "cost_usd": float(agent_cost)}
                for slug, runs, agent_cost in (await session.execute(by_agent_stmt)).all()
            ]

            daily_stmt = select(
                func.date(UsageRecord.created_at),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0.0),
            ).where(UsageRecord.created_at >= since)
            if tenant_id:
                daily_stmt = daily_stmt.where(UsageRecord.tenant_id == tenant_id)
            daily_stmt = daily_stmt.group_by(func.date(UsageRecord.created_at)).order_by(func.date(UsageRecord.created_at))
            daily = [{"date": str(day), "cost_usd": float(day_cost)} for day, day_cost in (await session.execute(daily_stmt)).all()]

            # Per-user cost attribution (chargeback / showback reports).
            by_user_stmt = select(
                UsageRecord.user_id,
                func.count(UsageRecord.id),
                func.coalesce(func.sum(UsageRecord.total_tokens), 0),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0.0),
            ).where(UsageRecord.created_at >= since)
            if tenant_id:
                by_user_stmt = by_user_stmt.where(UsageRecord.tenant_id == tenant_id)
            by_user_stmt = by_user_stmt.group_by(UsageRecord.user_id)
            by_user = [
                {
                    "user_id": user_id,
                    "runs": int(runs),
                    "total_tokens": int(tokens),
                    "cost_usd": float(user_cost),
                }
                for user_id, runs, tokens, user_cost in (await session.execute(by_user_stmt)).all()
            ]

        return {
            "window_days": days,
            "runs": int(total),
            "input_tokens": int(inp),
            "output_tokens": int(outp),
            "requests": int(requests),
            "tool_calls": int(tool_calls),
            "cost_usd": float(cost),
            "by_agent": by_agent,
            "by_user": by_user,
            "daily": daily,
        }
