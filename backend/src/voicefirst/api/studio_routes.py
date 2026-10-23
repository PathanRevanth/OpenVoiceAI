"""Agent Studio (SKILL.md live editing + versions), A2A endpoint,
red-team scanner, and personal access token routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from voicefirst.agents.deps import AgentDeps
from voicefirst.api.invocations import run_agent_invocation
from voicefirst.control_plane.agent_access import AgentAccessService
from voicefirst.control_plane.auth import Identity, require_identity
from voicefirst.control_plane.policy import PolicyContext
from voicefirst.control_plane.security import run_red_team
from voicefirst.control_plane.user_tokens import UserTokenService
from voicefirst.db.models import SkillVersionRecord, new_id

router = APIRouter()

_KNOWN_SLUGS = {"orchestrator", "github", "slack", "mongodb", "research", "marketing"}


def _skill_path(request: Request, slug: str):
    from voicefirst.settings import SKILLS_DIR

    if slug not in _KNOWN_SLUGS:
        raise HTTPException(404, f"Unknown agent: {slug}")
    return SKILLS_DIR / slug / "SKILL.md"


def _runtime(request: Request):
    return request.app.state.runtime


# ---------------------------------------------------------------------------
# Agent Studio: SKILL.md live editing with version history
# ---------------------------------------------------------------------------


@router.get("/api/studio/skills/{slug}", tags=["studio"])
async def get_skill_content(
    slug: str, request: Request, identity: Identity = Depends(require_identity)
):
    path = _skill_path(request, slug)
    if not path.exists():
        raise HTTPException(404, "SKILL.md not found")
    return {"slug": slug, "content": path.read_text(encoding="utf-8")}


@router.put("/api/studio/skills/{slug}", tags=["studio"])
async def put_skill_content(
    slug: str,
    body: dict[str, str],
    request: Request,
    identity: Identity = Depends(require_identity),
):
    if identity.policy.role != "admin":
        raise HTTPException(403, "Admin role required to edit agent behavior")
    content = body.get("content", "")
    if len(content) < 20:
        raise HTTPException(422, "SKILL.md content looks too short")
    path = _skill_path(request, slug)

    runtime = _runtime(request)
    async with runtime.db.session() as session:
        from sqlalchemy import func, select

        current_version = (
            await session.execute(
                select(func.max(SkillVersionRecord.version)).where(
                    SkillVersionRecord.slug == slug
                )
            )
        ).scalar_one_or_none()
        version = (current_version or 0) + 1
        session.add(
            SkillVersionRecord(
                id=new_id("skv"),
                slug=slug,
                version=version,
                content=content,
                created_by=identity.policy.user_id,
            )
        )

    path.write_text(content, encoding="utf-8")
    runtime.rebuild_agents()
    await runtime.audit.record(
        actor=identity.policy.user_id,
        action="studio.skill_updated",
        resource=f"agent:{slug}",
        tenant_id=identity.policy.tenant_id,
        detail={"version": version, "chars": len(content)},
    )
    return {"slug": slug, "version": version, "rebuilt": True}


@router.get("/api/studio/skills/{slug}/versions", tags=["studio"])
async def list_skill_versions(
    slug: str, request: Request, identity: Identity = Depends(require_identity)
):
    runtime = _runtime(request)
    async with runtime.db.session() as session:
        from sqlalchemy import select

        rows = (
            (
                await session.execute(
                    select(SkillVersionRecord)
                    .where(SkillVersionRecord.slug == slug)
                    .order_by(SkillVersionRecord.version.desc())
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": r.id,
            "version": r.version,
            "created_by": r.created_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "chars": len(r.content),
        }
        for r in rows
    ]


@router.post("/api/studio/skills/{slug}/versions/{version}/rollback", tags=["studio"])
async def rollback_skill(
    slug: str,
    version: int,
    request: Request,
    identity: Identity = Depends(require_identity),
):
    if identity.policy.role != "admin":
        raise HTTPException(403, "Admin role required")
    runtime = _runtime(request)
    async with runtime.db.session() as session:
        from sqlalchemy import select

        record = (
            await session.execute(
                select(SkillVersionRecord).where(
                    SkillVersionRecord.slug == slug,
                    SkillVersionRecord.version == version,
                )
            )
        ).scalar_one_or_none()
        if record is None:
            raise HTTPException(404, "Version not found")
        content = record.content

    return await put_skill_content(
        slug, {"content": content}, request, identity
    )


# ---------------------------------------------------------------------------
# Agent-authored capabilities (harness capability_creation, admin-gated)
# ---------------------------------------------------------------------------


class CapabilityCreationToggle(BaseModel):
    enabled: bool


@router.get("/api/studio/capability-creation", tags=["studio"])
async def get_capability_creation(request: Request, identity: Identity = Depends(require_identity)):
    runtime = _runtime(request)
    from voicefirst.settings import get_settings

    settings = get_settings()
    directory = Path(settings.authored_capabilities_path)
    drafts = sorted(p.name for p in directory.glob("*")) if directory.exists() else []
    return {
        "enabled": runtime.settings.capability_creation_enabled,
        "directory": settings.authored_capabilities_path,
        "drafts": drafts,
    }


@router.patch("/api/studio/capability-creation", tags=["studio"])
async def set_capability_creation(
    body: CapabilityCreationToggle, request: Request, identity: Identity = Depends(require_identity)
):
    """Enable/disable agent-authored capabilities. Enabling rebuilds the fleet
    so the orchestrator gains the drafting tool immediately (admin only)."""
    if identity.policy.role != "admin":
        raise HTTPException(403, "Admin role required")
    runtime = _runtime(request)
    runtime.settings.capability_creation_enabled = body.enabled
    runtime.rebuild_agents()
    await runtime.audit.record(
        actor=identity.policy.user_id,
        action="studio.capability_creation_toggled",
        resource="orchestrator",
        tenant_id=identity.policy.tenant_id,
        detail={"enabled": body.enabled},
    )
    return {"enabled": runtime.settings.capability_creation_enabled, "rebuilt": True}


# ---------------------------------------------------------------------------
# A2A JSON-RPC endpoint (agent-to-agent)
# ---------------------------------------------------------------------------


class A2ABody(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("/api/a2a/{slug}", tags=["a2a"])
async def a2a_endpoint(slug: str, body: A2ABody, request: Request):
    """A2A-style JSON-RPC: `message/send` delegates to the agent.
    Auth: agent key (vf_ak_...) or a platform bearer token."""
    runtime = _runtime(request)
    access = AgentAccessService(request.app.state.db)
    auth = request.headers.get("authorization") or ""
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    identity = None
    key_record = await access.verify(slug, key) if key else None
    if key_record is not None:
        identity = Identity(
            policy=PolicyContext(
                tenant_id=key_record.tenant_id, user_id=f"a2a:{key_record.label}", role="ops"
            )
        )
    elif not runtime.identity.dev_mode:
        try:
            identity = await runtime.identity.verify_bearer(key)
        except Exception:  # noqa: BLE001
            identity = None
    else:
        identity = await runtime.identity.from_request(request)

    if identity is None:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": body.id, "error": {"code": -32001, "message": "Unauthorized"}},
            status_code=401,
        )

    if body.method != "message/send":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": body.id,
                "error": {"code": -32601, "message": f"Method not supported: {body.method}"},
            },
            status_code=400,
        )

    # Extract text from A2A message parts
    message = body.params.get("message") or {}
    parts = message.get("parts") or []
    text = "".join(
        p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"
    ).strip()
    if not text:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": body.id,
                "error": {"code": -32602, "message": "message.parts must include a text part"},
            },
            status_code=400,
        )

    deps = AgentDeps(
        tenant_id=identity.policy.tenant_id,
        user_id=identity.policy.user_id,
        role=identity.policy.role,
        runtime=runtime,
        channel="api",
    )
    payload = await run_agent_invocation(runtime, slug, text, deps, request.app.state.db)
    output = payload["output"]
    if payload["status"] == "awaiting_approval":
        output = "Paused for human approval: " + ", ".join(
            a["tool_name"] for a in payload["approvals"]
        )
    return {
        "jsonrpc": "2.0",
        "id": body.id,
        "result": {
            "status": {"state": payload["status"]},
            "run_id": payload["run_id"],
            "message": {
                "role": "agent",
                "parts": [{"type": "text", "text": output}],
            },
        },
    }


# ---------------------------------------------------------------------------
# Red-team scanner
# ---------------------------------------------------------------------------


@router.post("/api/security/redteam/{slug}", tags=["security"])
async def red_team_agent(
    slug: str, request: Request, identity: Identity = Depends(require_identity)
):
    if identity.policy.role != "admin":
        raise HTTPException(403, "Admin role required to run red-team scans")
    payload = await run_red_team(
        _runtime(request), slug, identity.policy.tenant_id, identity.policy.user_id
    )
    await _runtime(request).audit.record(
        actor=identity.policy.user_id,
        action="security.redteam",
        resource=f"agent:{slug}",
        tenant_id=identity.policy.tenant_id,
        detail={"resilience": payload["resilience"]},
    )
    return payload


# ---------------------------------------------------------------------------
# Personal access tokens
# ---------------------------------------------------------------------------


class TokenCreate(BaseModel):
    label: str = Field(min_length=1, max_length=128)


@router.get("/api/tokens", tags=["tokens"])
async def list_tokens(request: Request, identity: Identity = Depends(require_identity)):
    service: UserTokenService = request.app.state.runtime.user_tokens
    return [service.to_dict(r) for r in await service.list_for_user(identity.policy.user_id)]


@router.post("/api/tokens", tags=["tokens"])
async def create_token(
    body: TokenCreate, request: Request, identity: Identity = Depends(require_identity)
):
    service: UserTokenService = request.app.state.runtime.user_tokens
    record, plaintext = await service.create(
        user_id=identity.policy.user_id,
        tenant_id=identity.policy.tenant_id,
        label=body.label,
        role=identity.policy.role,
    )
    await _runtime(request).audit.record(
        actor=identity.policy.user_id,
        action="pat.created",
        resource=f"pat:{record.id}",
        tenant_id=identity.policy.tenant_id,
        detail={"label": body.label},
    )
    return {**service.to_dict(record), "key": plaintext}


@router.delete("/api/tokens/{token_id}", tags=["tokens"])
async def revoke_token(
    token_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    service: UserTokenService = request.app.state.runtime.user_tokens
    revoked = await service.revoke(token_id, identity.policy.user_id)
    if not revoked:
        raise HTTPException(404, "Token not found")
    return {"revoked": True}
