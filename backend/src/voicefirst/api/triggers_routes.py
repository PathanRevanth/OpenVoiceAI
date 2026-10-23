"""Trigger CRUD and manual-run routes."""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from voicefirst.agents.deps import AgentDeps
from voicefirst.api.invocations import run_agent_invocation
from voicefirst.control_plane.auth import Identity, require_identity
from voicefirst.control_plane.triggers import (
    TriggerScheduler,
    TriggerService,
)

router = APIRouter()

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class TriggerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    agent_slug: str
    message: str = Field(min_length=1, max_length=16_000)
    schedule_type: Literal["interval", "daily"]
    interval_minutes: int | None = Field(default=None, ge=1, le=10_080)
    time_of_day: str | None = None
    webhook_url: str | None = Field(default=None, max_length=1024)


def _service(request: Request) -> TriggerService:
    return TriggerService(db=request.app.state.db)


def _scheduler(request: Request) -> TriggerScheduler:
    return request.app.state.trigger_scheduler


@router.get("/api/triggers", tags=["triggers"])
async def list_triggers(request: Request, identity: Identity = Depends(require_identity)):
    records = await _service(request).list_for_tenant(identity.policy.tenant_id)
    return [_service(request).to_dict(r) for r in records]


@router.post("/api/triggers", tags=["triggers"])
async def create_trigger(
    body: TriggerCreate, request: Request, identity: Identity = Depends(require_identity)
):
    if body.schedule_type == "interval":
        if body.interval_minutes is None:
            raise HTTPException(422, "interval_minutes is required for interval schedules")
    else:
        if not body.time_of_day or not _TIME_RE.match(body.time_of_day):
            raise HTTPException(422, "time_of_day must be HH:MM (UTC) for daily schedules")
    service = _service(request)
    descriptor = next(
        (
            d
            for d in request.app.state.runtime.agents_registry._descriptors.values()  # noqa: SLF001
            if d.slug == body.agent_slug
        ),
        None,
    )
    if descriptor is None:
        raise HTTPException(404, f"Unknown agent: {body.agent_slug}")
    if descriptor.status != "enabled":
        raise HTTPException(409, f"Agent {body.agent_slug} is disabled")

    record = await service.create(
        tenant_id=identity.policy.tenant_id,
        user_id=identity.policy.user_id,
        name=body.name,
        agent_slug=body.agent_slug,
        message=body.message,
        schedule_type=body.schedule_type,
        interval_minutes=body.interval_minutes,
        time_of_day=body.time_of_day,
        webhook_url=body.webhook_url,
    )
    await request.app.state.runtime.audit.record(
        actor=identity.policy.user_id,
        action="trigger.created",
        resource=f"trigger:{record.name}",
        tenant_id=identity.policy.tenant_id,
        detail={"agent": body.agent_slug, "schedule": service.to_dict(record)["schedule"]},
    )
    return service.to_dict(record)


@router.patch("/api/triggers/{trigger_id}", tags=["triggers"])
async def set_trigger_enabled(
    trigger_id: str,
    request: Request,
    enabled: bool,
    identity: Identity = Depends(require_identity),
):
    record = await _service(request).set_enabled(trigger_id, identity.policy.tenant_id, enabled)
    if record is None:
        raise HTTPException(404, "Trigger not found")
    return _service(request).to_dict(record)


@router.delete("/api/triggers/{trigger_id}", tags=["triggers"])
async def delete_trigger(
    trigger_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    deleted = await _service(request).delete(trigger_id, identity.policy.tenant_id)
    if not deleted:
        raise HTTPException(404, "Trigger not found")
    return {"deleted": True}


@router.post("/api/triggers/{trigger_id}/run", tags=["triggers"])
async def run_trigger_now(
    trigger_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    """Fire the trigger immediately (next_run_at schedule is unchanged)."""
    service = _service(request)
    runtime = request.app.state.runtime
    trigger = await service.get(trigger_id, identity.policy.tenant_id)
    if trigger is None:
        raise HTTPException(404, "Trigger not found")
    deps = AgentDeps(
        tenant_id=trigger.tenant_id,
        user_id=f"trigger:{trigger.name}",
        role="ops",
        runtime=runtime,
        channel="api",
    )
    payload = await run_agent_invocation(runtime, trigger.agent_slug, trigger.message, deps, request.app.state.db)
    async with request.app.state.db.session() as session:
        from voicefirst.db.models import TriggerRecord

        stored = await session.get(TriggerRecord, trigger_id)
        if stored is not None:
            stored.last_status = payload["status"]
            stored.last_run_id = payload["run_id"]
    return payload
