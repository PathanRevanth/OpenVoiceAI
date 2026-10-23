"""Per-user LLM connection routes.

Keys arrive over TLS on save, are tested against the provider, encrypted at
rest, and never returned — only masked last-4 + status.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from voicefirst.control_plane.auth import Identity, require_identity
from voicefirst.control_plane.connections import (
    PROVIDERS,
    model_entries,
    models_for,
    supports,
)

router = APIRouter()


class ConnectionSave(BaseModel):
    purpose: str = Field(pattern="^(agents|voice)$")
    provider: str
    model: str
    key: str = Field(min_length=8, max_length=512)
    # Optional same-provider fallback model (pydantic-ai FallbackModel chain):
    # takes over automatically when the primary hits provider errors/rate limits.
    fallback_model: str | None = None


@router.get("/api/connections/catalog", tags=["connections"])
async def connection_catalog(identity: Identity = Depends(require_identity)):
    """Providers + model options per purpose (no secrets involved)."""
    return {
        "providers": PROVIDERS,
        "models": {
            "agents": {p: model_entries("agents", p) for p in PROVIDERS},
            "voice": {p: model_entries("voice", p) for p in PROVIDERS},
        },
        "supports": {
            "agents": {p: supports("agents", p) for p in PROVIDERS},
            "voice": {p: supports("voice", p) for p in PROVIDERS},
        },
    }


@router.get("/api/connections", tags=["connections"])
async def list_connections(request: Request, identity: Identity = Depends(require_identity)):
    service = request.app.state.runtime.connections
    records = await service.list_for_user(identity.policy.user_id)
    return [service.to_dict(r) for r in records]


@router.post("/api/connections", tags=["connections"])
async def save_connection(
    body: ConnectionSave, request: Request, identity: Identity = Depends(require_identity)
):
    """Save + immediately test a connection. The key is encrypted at rest and
    never returned."""
    runtime = request.app.state.runtime
    if body.model not in models_for(body.purpose, body.provider):
        raise HTTPException(422, f"Model {body.model} is not offered for {body.purpose}/{body.provider}")
    if body.fallback_model and body.fallback_model not in models_for(body.purpose, body.provider):
        raise HTTPException(422, f"Fallback model {body.fallback_model} is not offered for {body.purpose}/{body.provider}")
    try:
        record = await runtime.connections.upsert(
            user_id=identity.policy.user_id,
            tenant_id=identity.policy.tenant_id,
            purpose=body.purpose,
            provider=body.provider,
            model=body.model,
            key=body.key,
            fallback_model=body.fallback_model,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await runtime.audit.record(
        actor=identity.policy.user_id,
        action="connection.saved",
        resource=f"{body.purpose}:{body.provider}",
        tenant_id=identity.policy.tenant_id,
        detail={"model": body.model, "status": record.status},
    )
    return runtime.connections.to_dict(record)


@router.post("/api/connections/{connection_id}/test", tags=["connections"])
async def test_connection(
    connection_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    """Re-test the stored key against the provider."""
    runtime = request.app.state.runtime
    record = await runtime.connections.retest(identity.policy.user_id, connection_id)
    if record is None:
        raise HTTPException(404, "Connection not found")
    await runtime.audit.record(
        actor=identity.policy.user_id,
        action="connection.tested",
        resource=f"{record.purpose}:{record.provider}",
        tenant_id=identity.policy.tenant_id,
        detail={"status": record.status},
    )
    return runtime.connections.to_dict(record)


@router.post("/api/connections/{connection_id}/activate", tags=["connections"])
async def activate_connection(
    connection_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    runtime = request.app.state.runtime
    record = await runtime.connections.activate(identity.policy.user_id, connection_id)
    if record is None:
        raise HTTPException(404, "Connection not found")
    return runtime.connections.to_dict(record)


@router.delete("/api/connections/{connection_id}", tags=["connections"])
async def delete_connection(
    connection_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    runtime = request.app.state.runtime
    deleted = await runtime.connections.delete(identity.policy.user_id, connection_id)
    if not deleted:
        raise HTTPException(404, "Connection not found")
    await runtime.audit.record(
        actor=identity.policy.user_id,
        action="connection.deleted",
        resource=connection_id,
        tenant_id=identity.policy.tenant_id,
    )
    return {"deleted": True}
