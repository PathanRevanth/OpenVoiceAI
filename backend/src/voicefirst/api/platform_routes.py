"""Enterprise platform routes: trace replay, compliance exports, voice
analytics, event subscriptions, and the A2A agent card."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from voicefirst.agents.deps import AgentDeps
from voicefirst.control_plane.auth import Identity, require_identity, require_role_ops
from voicefirst.control_plane.event_subscriptions import EventSubscriptionService
from voicefirst.control_plane.policy import Role
from voicefirst.db.models import AuditRecord, UsageRecord

router = APIRouter()


def _runtime(request: Request) -> Any:
    return request.app.state.runtime


def _deps(identity: Identity, runtime: Any, channel: str = "chat") -> AgentDeps:
    return AgentDeps.from_identity(identity, runtime=runtime, channel=channel)


# ---------------------------------------------------------------------------
# Trace replay
# ---------------------------------------------------------------------------


class ReplayRequest(BaseModel):
    model: str | None = None  # optional model swap for the replay
    agent: str | None = None  # optionally re-route to a different specialist


@router.post("/api/runs/{run_id}/replay", tags=["chat"])
async def replay_run(run_id: str, body: ReplayRequest, request: Request, identity: Identity = Depends(require_identity)):
    """Regenerate a stored run from its recorded history (optionally on a
    different model). Streams SSE events like a normal chat run."""
    from voicefirst.api.chat_service import ChatService
    from voicefirst.db.models import RunRecord as RunRecordModel

    runtime = _runtime(request)
    service = ChatService(request.app.state.db, runtime)
    deps = _deps(identity, runtime)

    async with request.app.state.db.session() as session:
        original = await session.get(RunRecordModel, run_id)
        if original is None or original.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Run not found")
    deps.conversation_id = original.conversation_id

    async def event_stream():
        try:
            async for event in service.replay_run(
                run_id, deps, model_override=body.model, agent_slug=body.agent
            ):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'run.failed', 'data': {'error': str(exc)}})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Compliance exports (audit trail + usage)
# ---------------------------------------------------------------------------


def _csv_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    if any(c in text for c in (",", '"', "\n")):
        return '"' + text.replace('"', '""') + '"'
    return text


@router.get("/api/audit/export", tags=["governance"])
async def export_audit(
    request: Request,
    format: str = Query("csv", pattern="^(csv|json)$"),
    limit: int = Query(5000, le=50_000),
    identity: Identity = Depends(require_role_ops),
):
    """Export the audit trail (ops+). Admins export all tenants; ops export
    their own. CSV is the regulator-friendly form."""
    _runtime(request)
    scope_all = identity.policy.role == Role.ADMIN
    async with request.app.state.db.session() as session:
        stmt = select(AuditRecord).order_by(AuditRecord.ts.desc()).limit(limit)
        if not scope_all:
            stmt = stmt.where(AuditRecord.tenant_id == identity.policy.tenant_id)
        rows = (await session.execute(stmt)).scalars().all()[::-1]  # chronological

    if format == "json":
        return JSONResponse(
            {
                "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "count": len(rows),
                "entries": [
                    {
                        "id": r.id, "ts": r.ts.isoformat(), "actor": r.actor, "action": r.action,
                        "resource": r.resource, "tenant_id": r.tenant_id, "detail": r.detail,
                        "prev_hash": r.prev_hash, "hash": r.hash,
                    }
                    for r in rows
                ],
            }
        )

    lines = ["id,ts,actor,action,resource,tenant_id,detail_json,prev_hash,hash"]
    for r in rows:
        lines.append(
            ",".join(
                _csv_escape(v)
                for v in (r.id, r.ts.isoformat(), r.actor, r.action, r.resource, r.tenant_id,
                          json.dumps(r.detail or {}, default=str), r.prev_hash, r.hash)
            )
        )
    from fastapi.responses import Response

    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit-export-{time.strftime("%Y%m%d")}.csv"'},
    )


@router.get("/api/usage/export", tags=["governance"])
async def export_usage(
    request: Request,
    days: int = Query(30, le=365),
    format: str = Query("csv", pattern="^(csv|json)$"),
    identity: Identity = Depends(require_role_ops),
):
    """Per-run usage/cost export for chargeback (ops+; admin sees all tenants)."""
    from datetime import UTC, datetime, timedelta

    _runtime(request)
    since = datetime.now(UTC) - timedelta(days=days)
    async with request.app.state.db.session() as session:
        stmt = (
            select(UsageRecord)
            .where(UsageRecord.created_at >= since)
            .order_by(UsageRecord.created_at.desc())
            .limit(50_000)
        )
        if identity.policy.role != Role.ADMIN:
            stmt = stmt.where(UsageRecord.tenant_id == identity.policy.tenant_id)
        rows = (await session.execute(stmt)).scalars().all()

    if format == "json":
        return JSONResponse({
            "count": len(rows),
            "runs": [
                {
                    "run_id": r.run_id, "agent": r.agent_slug, "model": r.model,
                    "tenant_id": r.tenant_id, "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens, "cost_usd": r.cost_usd,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ],
        })

    lines = ["run_id,created_at,agent,model,tenant_id,input_tokens,output_tokens,cost_usd"]
    for r in rows:
        lines.append(
            ",".join(
                _csv_escape(v)
                for v in (r.run_id, r.created_at.isoformat(), r.agent_slug, r.model or "",
                          r.tenant_id, r.input_tokens, r.output_tokens, f"{r.cost_usd:.6f}")
            )
        )
    from fastapi.responses import Response

    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="usage-export-{time.strftime("%Y%m%d")}.csv"'},
    )


# ---------------------------------------------------------------------------
# Voice analytics
# ---------------------------------------------------------------------------


@router.get("/api/voice/sessions", tags=["voice"])
async def list_voice_sessions(
    request: Request, limit: int = Query(50, le=200), identity: Identity = Depends(require_identity)
):
    runtime = _runtime(request)
    rows = await runtime.voice_analytics.list_sessions(identity.policy.tenant_id, limit=limit)
    return [runtime.voice_analytics.to_dict(r) for r in rows]


@router.get("/api/voice/analytics", tags=["voice"])
async def voice_analytics(
    request: Request, days: int = Query(7, le=90), identity: Identity = Depends(require_identity)
):
    runtime = _runtime(request)
    tenant_id = identity.policy.tenant_id
    return await runtime.voice_analytics.summary(tenant_id=tenant_id, days=days)


# ---------------------------------------------------------------------------
# Event subscriptions (outbound webhooks with retry + dead-letter)
# ---------------------------------------------------------------------------


class SubscriptionCreate(BaseModel):
    url: str = Field(min_length=8, max_length=1024, pattern=r"^https?://")
    events: list[str] = Field(default_factory=lambda: ["*"], max_length=32)
    secret: str | None = Field(default=None, max_length=256)


@router.get("/api/event-subscriptions", tags=["events"])
async def list_subscriptions(request: Request, identity: Identity = Depends(require_identity)):
    service = EventSubscriptionService(request.app.state.db, request.app.state.runtime.vault)
    return [service.to_dict(r) for r in await service.list_for_tenant(identity.policy.tenant_id)]


@router.post("/api/event-subscriptions", tags=["events"])
async def create_subscription(
    body: SubscriptionCreate, request: Request, identity: Identity = Depends(require_identity)
):
    runtime = _runtime(request)
    service = EventSubscriptionService(request.app.state.db, runtime.vault)
    record = await service.create(
        tenant_id=identity.policy.tenant_id,
        user_id=identity.policy.user_id,
        url=body.url,
        events=body.events,
        secret=body.secret,
    )
    await runtime.audit.record(
        actor=identity.policy.user_id,
        action="subscription.created",
        resource=f"subscription:{record.id}",
        tenant_id=identity.policy.tenant_id,
        detail={"url": body.url, "events": body.events},
    )
    return service.to_dict(record)


@router.patch("/api/event-subscriptions/{subscription_id}", tags=["events"])
async def set_subscription_enabled(
    subscription_id: str, request: Request, enabled: bool, identity: Identity = Depends(require_identity)
):
    service = EventSubscriptionService(request.app.state.db, request.app.state.runtime.vault)
    record = await service.set_enabled(subscription_id, identity.policy.tenant_id, enabled)
    if record is None:
        raise HTTPException(404, "Subscription not found")
    return service.to_dict(record)


@router.delete("/api/event-subscriptions/{subscription_id}", tags=["events"])
async def delete_subscription(
    subscription_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    runtime = _runtime(request)
    service = EventSubscriptionService(request.app.state.db, runtime.vault)
    deleted = await service.delete(subscription_id, identity.policy.tenant_id)
    if not deleted:
        raise HTTPException(404, "Subscription not found")
    await runtime.audit.record(
        actor=identity.policy.user_id,
        action="subscription.deleted",
        resource=f"subscription:{subscription_id}",
        tenant_id=identity.policy.tenant_id,
    )
    return {"deleted": True}


@router.post("/api/event-subscriptions/{subscription_id}/test", tags=["events"])
async def test_subscription(
    subscription_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    """Send a signed sample event to the subscription immediately."""
    from voicefirst.control_plane.webhooks import deliver_webhook

    runtime = _runtime(request)
    vault = runtime.vault
    service = EventSubscriptionService(request.app.state.db, vault)
    record = await service.get(subscription_id, identity.policy.tenant_id)
    if record is None:
        raise HTTPException(404, "Subscription not found")
    secret = vault.decrypt(record.secret_encrypted) if record.secret_encrypted else None
    ok, status_code = await deliver_webhook(
        record.url,
        {"event": "subscription.test", "subscription_id": record.id, "note": "VoiceFirst test event"},
        event="subscription.test",
        secret=secret,
    )
    return {"ok": ok, "status_code": status_code}


# ---------------------------------------------------------------------------
# A2A agent card (Linux Foundation Agent2Agent discovery)
# ---------------------------------------------------------------------------


@router.get("/.well-known/agent-card.json", tags=["a2a"])
async def a2a_agent_card(request: Request):
    """A2A AgentCard for the platform: the orchestrator plus every specialist
    as a skill, per the A2A discovery spec."""
    runtime = _runtime(request)
    base = str(request.base_url).rstrip("/")
    skills = []
    for descriptor in await runtime.agents_registry.list_agents():
        if descriptor.status != "enabled":
            continue
        skills.append(
            {
                "id": descriptor.slug,
                "name": descriptor.name,
                "description": descriptor.description or descriptor.name,
                "tags": [descriptor.kind, descriptor.slug],
                "examples": [f"Ask the {descriptor.name} agent: …"],
            }
        )
    return {
        "name": runtime.settings.app_name,
        "description": "Voice-first agentic control plane: governed orchestrator and specialist agents.",
        "url": base,
        "protocolVersion": "0.3.0",
        "version": "1.0.0",
        "capabilities": {"streaming": True, "pushNotifications": True, "stateTransitionHistory": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "provider": {"organization": "Voice-First AI", "url": base},
        "skills": skills,
        "endpoints": {
            "messageSend": f"{base}/api/a2a/orchestrator",
            "events": f"{base}/api/events",
        },
    }
