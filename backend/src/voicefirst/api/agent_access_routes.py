"""Direct agent invocation + per-agent webhook and manifest endpoints.

Every active agent is callable two ways:
* Session API: ``POST /api/agents/{slug}/invoke`` with a user JWT.
* Webhook:    ``POST /api/webhooks/agents/{slug}`` with an agent key
  (``Authorization: Bearer vf_ak_...`` or ``X-API-Key``), for external
  systems like n8n, cron jobs, or other services.

``GET /api/agents/{slug}/manifest`` returns the agent's public card
(name, skills, endpoints) in an A2A-style discoverable format.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from voicefirst.agents.deps import AgentDeps
from voicefirst.api.invocations import run_agent_invocation
from voicefirst.control_plane.agent_access import AgentAccessService
from voicefirst.control_plane.auth import Identity, require_identity

router = APIRouter()

_MANIFEST_VERSION = "1.0.0"


class InvokeBody(BaseModel):
    message: str = Field(min_length=1, max_length=16_000)


def _get_runtime(request: Request) -> Any:
    return request.app.state.runtime


def _resolve_agent(runtime: Any, slug: str):
    """Return (agent, slug) or raise 404/409."""
    if slug == "orchestrator":
        agent = runtime.orchestrator
    else:
        agent = runtime.specialists.get(slug)
    if agent is None:
        raise HTTPException(404, f"Unknown agent: {slug}")
    descriptor = None
    for candidate in runtime.agents_registry._descriptors.values():  # noqa: SLF001
        if candidate.slug == slug:
            descriptor = candidate
            break
    if descriptor is not None and descriptor.status != "enabled":
        raise HTTPException(409, f"Agent {slug} is disabled")
    return agent


# ---------------------------------------------------------------------------
# Manifest (public agent card, A2A-style discovery)
# ---------------------------------------------------------------------------


@router.get("/api/agents/{slug}/manifest", tags=["agents"])
async def agent_manifest(slug: str, request: Request):
    runtime = _get_runtime(request)
    agent = _resolve_agent(runtime, slug)
    base = str(request.base_url).rstrip("/")
    return {
        "name": agent.name,
        "slug": slug,
        "version": _MANIFEST_VERSION,
        "description": agent.description or "",
        "skills": [slug],
        "provider": runtime.settings.default_model,
        "endpoints": {
            "manifest": f"{base}/api/agents/{slug}/manifest",
            "invoke": f"{base}/api/agents/{slug}/invoke",
            "webhook": f"{base}/api/webhooks/agents/{slug}",
        },
        "auth": {
            "invoke": "Bearer user JWT",
            "webhook": "Bearer agent key (vf_ak_...)",
        },
        "protocol": "REST + JSON; streaming SSE available via /api/chat",
    }


# ---------------------------------------------------------------------------
# Session-authenticated invoke
# ---------------------------------------------------------------------------


@router.post("/api/agents/{slug}/invoke", tags=["agents"])
async def invoke_agent(
    slug: str,
    body: InvokeBody,
    request: Request,
    identity: Identity = Depends(require_identity),
):
    runtime = _get_runtime(request)
    deps = AgentDeps.from_identity(identity, runtime=runtime, channel="api")
    payload = await run_agent_invocation(runtime, slug, body.message, deps, request.app.state.db)
    await runtime.audit.record(
        actor=identity.policy.user_id,
        action="agent.invoked",
        resource=f"agent:{slug}",
        tenant_id=identity.policy.tenant_id,
        detail={"channel": "api"},
    )
    return JSONResponse(payload)


# ---------------------------------------------------------------------------
# Webhook (agent-key auth, for external systems)
# ---------------------------------------------------------------------------


def _extract_agent_key(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key") or ""


@router.post("/api/webhooks/agents/{slug}", tags=["agents"])
async def agent_webhook(slug: str, body: InvokeBody, request: Request):
    runtime = _get_runtime(request)
    key = _extract_agent_key(request)
    if not key:
        raise HTTPException(401, "Missing agent key (Authorization: Bearer vf_ak_... or X-API-Key)")
    access = AgentAccessService(request.app.state.db)
    record = await access.verify(slug, key)
    if record is None:
        raise HTTPException(401, "Invalid agent key")

    deps = AgentDeps(
        tenant_id=record.tenant_id,
        user_id=f"webhook:{record.label}",
        role="ops",
        runtime=runtime,
        channel="api",
    )
    payload = await run_agent_invocation(runtime, slug, body.message, deps, request.app.state.db)
    await runtime.audit.record(
        actor=f"webhook:{record.label}",
        action="agent.webhook_invoked",
        resource=f"agent:{slug}",
        tenant_id=record.tenant_id,
        detail={"channel": "webhook"},
    )
    return JSONResponse(payload)


# ---------------------------------------------------------------------------
# Key management (session-authenticated)
# ---------------------------------------------------------------------------


@router.get("/api/agents/{slug}/keys", tags=["agents"])
async def list_agent_keys(slug: str, request: Request, identity: Identity = Depends(require_identity)):
    access = AgentAccessService(request.app.state.db)
    records = await access.list_keys(slug, identity.policy.tenant_id)
    base = str(request.base_url).rstrip("/")
    return {
        "invoke_url": f"{base}/api/agents/{slug}/invoke",
        "webhook_url": f"{base}/api/webhooks/agents/{slug}",
        "manifest_url": f"{base}/api/agents/{slug}/manifest",
        "keys": [access.to_dict(r) for r in records],
    }


@router.post("/api/agents/{slug}/keys", tags=["agents"])
async def create_agent_key(
    slug: str,
    request: Request,
    body: dict[str, str] | None = None,
    identity: Identity = Depends(require_identity),
):
    _resolve_agent(_get_runtime(request), slug)  # 404/409 on unknown or disabled
    label = (body or {}).get("label", "default")
    access = AgentAccessService(request.app.state.db)
    record, plaintext = await access.create_key(
        agent_slug=slug, tenant_id=identity.policy.tenant_id, label=label
    )
    await _get_runtime(request).audit.record(
        actor=identity.policy.user_id,
        action="agent_key.created",
        resource=f"agent:{slug}",
        tenant_id=identity.policy.tenant_id,
        detail={"label": label, "key_id": record.id},
    )
    return {**access.to_dict(record), "key": plaintext}


@router.delete("/api/agent-keys/{key_id}", tags=["agents"])
async def revoke_agent_key(
    key_id: str, request: Request, slug: str, identity: Identity = Depends(require_identity)
):
    access = AgentAccessService(request.app.state.db)
    deleted = await access.revoke(key_id, slug, identity.policy.tenant_id)
    if not deleted:
        raise HTTPException(404, "Key not found")
    await _get_runtime(request).audit.record(
        actor=identity.policy.user_id,
        action="agent_key.revoked",
        resource=f"agent:{slug}",
        tenant_id=identity.policy.tenant_id,
        detail={"key_id": key_id},
    )
    return {"revoked": True}
