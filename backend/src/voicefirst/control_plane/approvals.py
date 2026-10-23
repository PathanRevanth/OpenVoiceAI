"""Human-in-the-loop approval service.

When a tool call requires approval (mutating/destructive operations per policy),
the agent run pauses with pydantic-ai `DeferredToolRequests`. The pending calls
are recorded here; an operator approves or denies via the API/UI, and the run is
resumed with `DeferredToolResults`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from voicefirst.control_plane.audit import AuditService
from voicefirst.control_plane.events import EventBus
from voicefirst.control_plane.tenant_settings import TenantSettingService
from voicefirst.db.models import ApprovalRecord, new_id
from voicefirst.db.session import Database


class ApprovalService:
    def __init__(self, db: Database, events: EventBus, audit: AuditService, tenant_settings: TenantSettingService) -> None:
        self._db = db
        self._events = events
        self._audit = audit
        self._tenant_settings = tenant_settings
        self._waiters: dict[str, asyncio.Future[bool]] = {}

    async def create(
        self,
        *,
        run_id: str,
        conversation_id: str | None,
        tool_name: str,
        tool_call_id: str,
        args: dict[str, Any],
        risk: str = "mutating",
        rationale: str | None = None,
        tenant_id: str = "",
    ) -> ApprovalRecord:
        record = ApprovalRecord(
            id=new_id("apr"),
            run_id=run_id,
            conversation_id=conversation_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            args_json=args,
            risk=risk,
            rationale=rationale,
            status="pending",
            tenant_id=tenant_id,
        )
        async with self._db.session() as session:
            session.add(record)
        await self._events.publish(
            "approval.requested",
            {
                "approval_id": record.id,
                "run_id": run_id,
                "tool_name": tool_name,
                "args": args,
                "risk": risk,
            },
            tenant_id=tenant_id,
        )
        await self._audit.record(
            actor="system",
            action="approval.requested",
            resource=f"tool:{tool_name}",
            tenant_id=tenant_id,
            detail={"approval_id": record.id, "run_id": run_id, "args": args},
        )
        self._notify_webhook(tenant_id, record.id, run_id, tool_name, args)
        return record

    async def create_for_deferred_requests(
        self,
        *,
        run_id: str,
        conversation_id: str | None,
        deferred: Any,
        tenant_id: str = "",
    ) -> list[ApprovalRecord]:
        """Create approval records for every call in a pydantic-ai `DeferredToolRequests`."""
        import json

        created: list[ApprovalRecord] = []
        for request in list(getattr(deferred, "approvals", []) or []):
            # Deferred args arrive either as a dict or as a raw JSON string
            # depending on how the provider serialized the tool call.
            args_raw = getattr(request, "args", {}) or {}
            if isinstance(args_raw, str):
                try:
                    args_raw = json.loads(args_raw)
                except json.JSONDecodeError:
                    args_raw = {"raw": args_raw[:200]}
            if not isinstance(args_raw, dict):
                args_raw = {"raw": str(args_raw)[:200]}
            created.append(
                await self.create(
                    run_id=run_id,
                    conversation_id=conversation_id,
                    tool_name=getattr(request, "tool_name", "unknown"),
                    tool_call_id=getattr(request, "tool_call_id", str(uuid.uuid4())),
                    args=args_raw,
                    risk="mutating",
                    rationale=None,
                    tenant_id=tenant_id,
                )
            )
        return created

    async def decide(
        self,
        approval_id: str,
        *,
        approved: bool,
        decided_by: str = "operator",
        note: str | None = None,
    ) -> ApprovalRecord | None:
        async with self._db.session() as session:
            record = await session.get(ApprovalRecord, approval_id)
            if record is None or record.status != "pending":
                return None
            record.status = "approved" if approved else "denied"
            record.decided_by = decided_by
            record.decided_at = datetime.now(UTC)
            record.decision_note = note
            snapshot = {
                "approval_id": record.id,
                "tool_name": record.tool_name,
                "approved": approved,
            }
            tenant_id = record.tenant_id
            tool_name = record.tool_name
            run_id = record.run_id
        await self._events.publish(
            "approval.decided",
            {**snapshot, "run_id": run_id},
            tenant_id=tenant_id,
        )
        await self._audit.record(
            actor=decided_by,
            action="approval.decided",
            resource=f"tool:{tool_name}",
            tenant_id=tenant_id,
            detail=snapshot,
        )
        waiter = self._waiters.pop(approval_id, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(approved)
        return record

    def _notify_webhook(
        self, tenant_id: str, approval_id: str, run_id: str, tool_name: str, args: dict[str, Any]
    ) -> None:
        """Fire-and-forget POST of the approval request to the tenant webhook."""
        import asyncio

        async def deliver() -> None:
            url = await self._tenant_settings.get(tenant_id, "approval_webhook_url")
            if not url:
                return
            from voicefirst.control_plane.webhooks import deliver_webhook

            await deliver_webhook(
                url,
                {
                    "event": "approval.requested",
                    "approval_id": approval_id,
                    "run_id": run_id,
                    "tool_name": tool_name,
                    "args": args,
                    "decide_url": None,
                },
                event="approval.requested",
            )

        try:
            asyncio.get_running_loop().create_task(deliver())
        except RuntimeError:
            pass

    async def wait_for_decision(self, approval_id: str, timeout: float | None = None) -> bool:
        """Await an in-process decision; returns False on timeout."""
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._waiters[approval_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout)
        except (TimeoutError, asyncio.TimeoutError):
            self._waiters.pop(approval_id, None)
            return False

    async def pending(self, limit: int = 50, tenant_id: str | None = None) -> list[ApprovalRecord]:
        async with self._db.session() as session:
            stmt = (
                select(ApprovalRecord)
                .where(ApprovalRecord.status == "pending")
                .order_by(ApprovalRecord.created_at.desc())
                .limit(limit)
            )
            if tenant_id is not None:
                stmt = stmt.where(ApprovalRecord.tenant_id == tenant_id)
            rows = (await session.execute(stmt)).scalars()
            return list(rows)

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        async with self._db.session() as session:
            return await session.get(ApprovalRecord, approval_id)

    async def list_for_run(self, run_id: str) -> list[ApprovalRecord]:
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(ApprovalRecord).where(ApprovalRecord.run_id == run_id).order_by(ApprovalRecord.created_at)
                )
            ).scalars()
            return list(rows)

    @staticmethod
    def build_deferred_results(decisions: dict[str, bool]) -> Any:
        """Build pydantic-ai `DeferredToolResults` from {tool_call_id: approved}."""
        from pydantic_ai import DeferredToolResults

        return DeferredToolResults(approvals=dict(decisions))
