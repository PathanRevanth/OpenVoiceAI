"""Control-plane REST + SSE routes."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select

from voicefirst.agents.deps import AgentDeps
from voicefirst.api.chat_service import ChatService
from voicefirst.api.schemas import (
    AgentStatusUpdate,
    ApprovalDecision,
    ChatRequest,
    ConversationMove,
    ConversationRename,
    InstructionsIn,
    MCPServerUpsert,
    PolicyUpdate,
    ProjectCreate,
    ProjectUpdate,
    PromptCreate,
    PromptUpdate,
    RunFeedbackIn,
)
from voicefirst.control_plane.auth import Identity, require_identity, require_role_admin, require_role_ops
from voicefirst.control_plane.policy import classify_tool
from voicefirst.db.models import (
    ConversationRecord,
    ConversationShareRecord,
    MessageRecord,
    ProjectRecord,
    PromptTemplateRecord,
    RunFeedbackRecord,
    RunRecord,
    new_id,
)

router = APIRouter()


def _chat(request: Request) -> ChatService:
    return ChatService(request.app.state.db, request.app.state.runtime)


def _deps(identity: Identity, runtime: Any, channel: str = "chat") -> AgentDeps:
    return AgentDeps.from_identity(identity, runtime=runtime, channel=channel)


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


@router.post("/api/chat", tags=["chat"])
async def chat(request: Request, body: ChatRequest, identity: Identity = Depends(require_identity)):
    """Send a message to the orchestrator; returns an SSE event stream."""
    runtime = request.app.state.runtime
    service = _chat(request)
    deps = _deps(identity, runtime, channel=body.channel)
    conversation = await service.get_or_create_conversation(
        body.conversation_id,
        tenant_id=deps.tenant_id,
        user_id=deps.user_id,
        channel=body.channel,
        title_from=body.message,
    )

    async def event_stream():
        try:
            async for event in service.stream_run(
                message=body.message, conversation=conversation, deps=deps, model_override=body.model,
                attachments=[a.model_dump() for a in body.attachments], agent_slug=body.agent,
                effort=body.effort,
            ):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'run.failed', 'data': {'error': str(exc)}})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@router.post("/api/runs/{run_id}/resume", tags=["chat"])
async def resume_run(
    run_id: str,
    request: Request,
    model: str | None = Body(default=None, embed=True),
    identity: Identity = Depends(require_identity),
):
    """Resume a run that was paused for approval, applying recorded decisions.

    `model` is the composer's selection at resume time; it resolves the same
    way as for a fresh chat turn (saved connection → override → default).
    """
    runtime = request.app.state.runtime
    service = _chat(request)
    deps = _deps(identity, runtime)

    # Tenant isolation: a run may only be resumed within its own tenant.
    async with request.app.state.db.session() as session:
        run = await session.get(RunRecord, run_id)
        if run is None or run.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Run not found")
    deps.conversation_id = run.conversation_id
    deps.run_id = run_id

    async def event_stream():
        try:
            async for event in service.resume_run(run_id, deps, model_override=model):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'run.failed', 'data': {'error': str(exc)}})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/api/conversations", tags=["chat"])
async def list_conversations(request: Request, identity: Identity = Depends(require_identity)):
    db = request.app.state.db
    async with db.session() as session:
        rows = (
            (
                await session.execute(
                    select(ConversationRecord)
                    .where(ConversationRecord.tenant_id == identity.policy.tenant_id)
                    .order_by(ConversationRecord.updated_at.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "id": r.id, "title": r.title, "channel": r.channel,
            "project_id": r.project_id, "archived": bool(r.archived),
            "created_at": r.created_at.isoformat(), "updated_at": r.updated_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/api/conversations/search", tags=["chat"])
async def search_conversations(
    request: Request,
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(20, le=50),
    identity: Identity = Depends(require_identity),
):
    """Full-text search across the tenant's message history.

    Matches case-insensitively against stored message text and returns the
    newest matching conversation first with a short snippet around the hit.
    """
    return await _search_conversations(request.app.state.db, identity.policy.tenant_id, q, limit)


async def _search_conversations(db: Any, tenant_id: str, query: str, limit: int) -> list[dict[str, Any]]:
    needle = query.strip().lower()
    if len(needle) < 2:
        return []
    async with db.session() as session:
        conv_rows = (
            (
                await session.execute(
                    select(ConversationRecord)
                    .where(ConversationRecord.tenant_id == tenant_id)
                    .order_by(ConversationRecord.updated_at.desc())
                    .limit(200)
                )
            )
            .scalars()
            .all()
        )
        if not conv_rows:
            return []
        conv_ids = [c.id for c in conv_rows]
        msg_rows = (
            (
                await session.execute(
                    select(MessageRecord)
                    .where(MessageRecord.conversation_id.in_(conv_ids))
                    .order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
                    .limit(2000)
                )
            )
            .scalars()
            .all()
        )
    by_conv = {c.id: c for c in conv_rows}
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for m in msg_rows:
        if m.conversation_id in seen:
            continue
        text = str((m.content or {}).get("text", "") or "")
        idx = text.lower().find(needle)
        if idx == -1:
            continue
        seen.add(m.conversation_id)
        conv = by_conv[m.conversation_id]
        start = max(0, idx - 60)
        snippet = text[start:idx + len(needle) + 60].strip()
        if start > 0:
            snippet = "..." + snippet
        if idx + len(needle) + 60 < len(text):
            snippet = snippet + "..."
        out.append(
            {
                "id": conv.id,
                "title": conv.title,
                "channel": conv.channel,
                "updated_at": conv.updated_at.isoformat(),
                "snippet": " ".join(snippet.split()),
                "matched_role": m.role,
            }
        )
        if len(out) >= limit:
            break
    return out


@router.get("/api/conversations/{conversation_id}", tags=["chat"])
async def get_conversation(conversation_id: str, request: Request, identity: Identity = Depends(require_identity)):
    db = request.app.state.db
    async with db.session() as session:
        conv = await session.get(ConversationRecord, conversation_id)
        if conv is None or conv.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Conversation not found")
        rows = (
            (
                await session.execute(
                    select(MessageRecord)
                    .where(MessageRecord.conversation_id == conversation_id)
                    .order_by(MessageRecord.created_at, MessageRecord.id)
                )
            )
            .scalars()
            .all()
        )
    return {
        "id": conv.id,
        "title": conv.title,
        "channel": conv.channel,
        "project_id": conv.project_id,
        "archived": bool(conv.archived),
        "messages": [
            {
                "id": m.id, "role": m.role, "agent": m.agent_slug,
                "content": m.content, "created_at": m.created_at.isoformat(),
            }
            for m in rows
        ],
    }


@router.delete("/api/conversations/{conversation_id}", tags=["chat"])
async def delete_conversation(
    conversation_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    """Delete a conversation and its messages (runs are retained for audit)."""
    from sqlalchemy import delete as sa_delete

    db = request.app.state.db
    async with db.session() as session:
        conv = await session.get(ConversationRecord, conversation_id)
        if conv is None or conv.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Conversation not found")
        await session.execute(
            sa_delete(MessageRecord).where(MessageRecord.conversation_id == conversation_id)
        )
        await session.delete(conv)
    await request.app.state.runtime.audit.record(
        actor=identity.policy.user_id,
        action="conversation.deleted",
        resource=f"conversation:{conversation_id}",
        tenant_id=identity.policy.tenant_id,
    )
    return {"deleted": True}


@router.patch("/api/conversations/{conversation_id}", tags=["chat"])
async def rename_conversation(
    conversation_id: str,
    body: ConversationRename,
    request: Request,
    identity: Identity = Depends(require_identity),
):
    """Rename a conversation inline (ChatGPT-grade history management)."""
    title = body.title.strip()[:256]
    if not title:
        raise HTTPException(422, "Title must not be empty")
    db = request.app.state.db
    async with db.session() as session:
        conv = await session.get(ConversationRecord, conversation_id)
        if conv is None or conv.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Conversation not found")
        conv.title = title
    return {"id": conversation_id, "title": title}


@router.post("/api/conversations/{conversation_id}/duplicate", tags=["chat"])
async def duplicate_conversation(
    conversation_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    """Fork a conversation: copy its messages into a new conversation."""
    db = request.app.state.db
    async with db.session() as session:
        conv = await session.get(ConversationRecord, conversation_id)
        if conv is None or conv.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Conversation not found")
        rows = (
            (
                await session.execute(
                    select(MessageRecord)
                    .where(MessageRecord.conversation_id == conversation_id)
                    .order_by(MessageRecord.created_at, MessageRecord.id)
                )
            )
            .scalars()
            .all()
        )
        copy = ConversationRecord(
            id=new_id("conv"),
            title=f"{conv.title} (copy)"[:256],
            tenant_id=conv.tenant_id,
            user_id=identity.policy.user_id,
            channel=conv.channel,
            history_json=conv.history_json,
        )
        session.add(copy)
        for m in rows:
            session.add(
                MessageRecord(
                    id=new_id("msg"),
                    conversation_id=copy.id,
                    run_id=m.run_id,
                    role=m.role,
                    agent_slug=m.agent_slug,
                    content=dict(m.content or {}),
                )
            )
    await request.app.state.runtime.audit.record(
        actor=identity.policy.user_id,
        action="conversation.duplicated",
        resource=f"conversation:{conversation_id}",
        tenant_id=identity.policy.tenant_id,
        detail={"copy_id": copy.id},
    )
    return {"id": copy.id, "title": copy.title}


@router.post("/api/conversations/{conversation_id}/move", tags=["chat"])
async def move_conversation(
    conversation_id: str,
    body: ConversationMove,
    request: Request,
    identity: Identity = Depends(require_identity),
):
    """File a conversation into a project (or back to the ungrouped list)."""
    db = request.app.state.db
    async with db.session() as session:
        conv = await session.get(ConversationRecord, conversation_id)
        if conv is None or conv.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Conversation not found")
        if body.project_id is not None:
            project = await session.get(ProjectRecord, body.project_id)
            if (
                project is None
                or project.tenant_id != identity.policy.tenant_id
                or project.user_id != identity.policy.user_id
            ):
                raise HTTPException(404, "Project not found")
        conv.project_id = body.project_id
    return {"id": conversation_id, "project_id": body.project_id}


@router.post("/api/conversations/{conversation_id}/archive", tags=["chat"])
async def archive_conversation(
    conversation_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    """Archive a conversation (hidden from the main list, restorable)."""
    db = request.app.state.db
    async with db.session() as session:
        conv = await session.get(ConversationRecord, conversation_id)
        if conv is None or conv.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Conversation not found")
        conv.archived = True
    return {"id": conversation_id, "archived": True}


@router.post("/api/conversations/{conversation_id}/unarchive", tags=["chat"])
async def unarchive_conversation(
    conversation_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    """Restore an archived conversation to the main list."""
    db = request.app.state.db
    async with db.session() as session:
        conv = await session.get(ConversationRecord, conversation_id)
        if conv is None or conv.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Conversation not found")
        conv.archived = False
    return {"id": conversation_id, "archived": False}


def _project_dict(p: ProjectRecord, count: int = 0) -> dict[str, Any]:
    return {
        "id": p.id, "name": p.name, "instructions": p.instructions or "",
        "icon": p.icon or "folder", "color": p.color or "slate",
        "conversation_count": count,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@router.get("/api/projects", tags=["chat"])
async def list_projects(request: Request, identity: Identity = Depends(require_identity)):
    """The caller's projects with live conversation counts."""
    db = request.app.state.db
    async with db.session() as session:
        projects = (
            (
                await session.execute(
                    select(ProjectRecord)
                    .where(
                        ProjectRecord.tenant_id == identity.policy.tenant_id,
                        ProjectRecord.user_id == identity.policy.user_id,
                    )
                    .order_by(ProjectRecord.created_at, ProjectRecord.id)
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        counts = (
            (
                await session.execute(
                    select(ConversationRecord.project_id, func.count())
                    .where(ConversationRecord.tenant_id == identity.policy.tenant_id)
                    .group_by(ConversationRecord.project_id)
                )
            )
            .all()
        )
    by_project = {pid: n for pid, n in counts if pid}
    return [_project_dict(p, by_project.get(p.id, 0)) for p in projects]


@router.post("/api/projects", tags=["chat"])
async def create_project(
    body: ProjectCreate, request: Request, identity: Identity = Depends(require_identity)
):
    record = ProjectRecord(
        id=new_id("proj"),
        tenant_id=identity.policy.tenant_id,
        user_id=identity.policy.user_id,
        name=body.name.strip(),
        instructions="",
        icon=body.icon.strip().lower() or "folder",
        color=body.color,
    )
    async with request.app.state.db.session() as session:
        session.add(record)
    return _project_dict(record)


@router.patch("/api/projects/{project_id}", tags=["chat"])
async def update_project(
    project_id: str, body: ProjectUpdate, request: Request, identity: Identity = Depends(require_identity)
):
    db = request.app.state.db
    async with db.session() as session:
        record = await session.get(ProjectRecord, project_id)
        if (
            record is None
            or record.tenant_id != identity.policy.tenant_id
            or record.user_id != identity.policy.user_id
        ):
            raise HTTPException(404, "Project not found")
        if body.name is not None:
            record.name = body.name.strip()
        if body.instructions is not None:
            record.instructions = body.instructions.strip()[:4000]
        if body.icon is not None:
            record.icon = body.icon.strip().lower()[:32] or "folder"
        if body.color is not None:
            record.color = body.color
    return _project_dict(record)


@router.delete("/api/projects/{project_id}", tags=["chat"])
async def delete_project(
    project_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    """Delete a project; its conversations return to the ungrouped list."""
    db = request.app.state.db
    async with db.session() as session:
        record = await session.get(ProjectRecord, project_id)
        if (
            record is None
            or record.tenant_id != identity.policy.tenant_id
            or record.user_id != identity.policy.user_id
        ):
            raise HTTPException(404, "Project not found")
        members = (
            (
                await session.execute(
                    select(ConversationRecord).where(ConversationRecord.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        for conv in members:
            conv.project_id = None
        await session.delete(record)
    return {"deleted": True}


@router.post("/api/conversations/{conversation_id}/share", tags=["chat"])
async def share_conversation(
    conversation_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    """Create (or reuse) a revocable public link for a conversation."""
    import secrets

    db = request.app.state.db
    async with db.session() as session:
        conv = await session.get(ConversationRecord, conversation_id)
        if conv is None or conv.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Conversation not found")
        existing = (
            (
                await session.execute(
                    select(ConversationShareRecord)
                    .where(
                        ConversationShareRecord.conversation_id == conversation_id,
                        ConversationShareRecord.revoked.is_(False),
                    )
                    .order_by(ConversationShareRecord.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            return {"share_token": existing.token}
        record = ConversationShareRecord(
            id=new_id("share"),
            conversation_id=conversation_id,
            tenant_id=identity.policy.tenant_id,
            token=secrets.token_urlsafe(24),
            revoked=False,
        )
        session.add(record)
    return {"share_token": record.token}


@router.delete("/api/conversations/{conversation_id}/share", tags=["chat"])
async def revoke_share(
    conversation_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    """Revoke all public links for a conversation."""
    db = request.app.state.db
    revoked = 0
    async with db.session() as session:
        conv = await session.get(ConversationRecord, conversation_id)
        if conv is None or conv.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Conversation not found")
        rows = (
            (
                await session.execute(
                    select(ConversationShareRecord).where(
                        ConversationShareRecord.conversation_id == conversation_id,
                        ConversationShareRecord.revoked.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.revoked = True
            revoked += 1
    return {"revoked": revoked}


@router.get("/api/shared/{token}", tags=["chat"])
async def read_shared_conversation(token: str, request: Request):
    """Public read-only view of a shared conversation. No auth required."""
    db = request.app.state.db
    async with db.session() as session:
        share = (
            (
                await session.execute(
                    select(ConversationShareRecord).where(ConversationShareRecord.token == token)
                )
            )
            .scalars()
            .first()
        )
        if share is None or share.revoked:
            raise HTTPException(404, "Shared conversation not found")
        conv = await session.get(ConversationRecord, share.conversation_id)
        if conv is None:
            raise HTTPException(404, "Shared conversation not found")
        rows = (
            (
                await session.execute(
                    select(MessageRecord)
                    .where(MessageRecord.conversation_id == conv.id)
                    .order_by(MessageRecord.created_at, MessageRecord.id)
                )
            )
            .scalars()
            .all()
        )
    return {
        "title": conv.title,
        "messages": [
            {
                "role": m.role,
                "text": (m.content or {}).get("text", ""),
                "sources": (m.content or {}).get("sources", []),
                "model": (m.content or {}).get("model"),
                "created_at": m.created_at.isoformat(),
            }
            for m in rows
            if (m.content or {}).get("text")
        ],
    }


@router.get("/api/export/mine", tags=["chat"])
async def export_my_data(request: Request, identity: Identity = Depends(require_identity)):
    """Download every conversation the caller owns as one JSON archive."""
    db = request.app.state.db
    async with db.session() as session:
        convs = (
            (
                await session.execute(
                    select(ConversationRecord)
                    .where(
                        ConversationRecord.tenant_id == identity.policy.tenant_id,
                        ConversationRecord.user_id == identity.policy.user_id,
                    )
                    .order_by(ConversationRecord.created_at)
                    .limit(500)
                )
            )
            .scalars()
            .all()
        )
        conv_ids = [c.id for c in convs]
        msgs = (
            (
                await session.execute(
                    select(MessageRecord)
                    .where(MessageRecord.conversation_id.in_(conv_ids))
                    .order_by(MessageRecord.created_at, MessageRecord.id)
                    .limit(20000)
                )
            )
            .scalars()
            .all()
            if conv_ids
            else []
        )
    by_conv: dict[str, list] = {c.id: [] for c in convs}
    for m in msgs:
        if m.conversation_id in by_conv:
            by_conv[m.conversation_id].append(
                {
                    "role": m.role,
                    "text": (m.content or {}).get("text", ""),
                    "created_at": m.created_at.isoformat(),
                }
            )
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "conversations": [
            {
                "id": c.id,
                "title": c.title,
                "channel": c.channel,
                "archived": bool(c.archived),
                "created_at": c.created_at.isoformat(),
                "messages": by_conv[c.id],
            }
            for c in convs
        ],
    }


@router.get("/api/artifacts/index", tags=["chat"])
async def artifacts_index(
    request: Request,
    limit: int = Query(200, le=200),
    identity: Identity = Depends(require_identity),
):
    """Candidate assistant messages that declare artifacts, newest first.

    The browser parses each candidate with the thread's own artifact rules,
    so this index always agrees with what chat renders.
    """
    from voicefirst.api.artifacts_idx import scan_artifact_sources

    return await scan_artifact_sources(request.app.state.db, identity.policy.tenant_id, limit)


@router.post("/api/runs/{run_id}/feedback", tags=["chat"])
async def submit_run_feedback(
    run_id: str,
    body: RunFeedbackIn,
    request: Request,
    identity: Identity = Depends(require_identity),
):
    """Thumbs up/down on one assistant run. Used to rank answer quality."""
    comment = (body.comment or "").strip()[:2000] or None
    db = request.app.state.db
    async with db.session() as session:
        run = await session.get(RunRecord, run_id)
        if run is None or run.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Run not found")
        record = RunFeedbackRecord(
            id=new_id("fb"),
            run_id=run_id,
            tenant_id=identity.policy.tenant_id,
            user_id=identity.policy.user_id,
            rating=body.rating,
            comment=comment,
        )
        session.add(record)
    return {"id": record.id, "run_id": run_id, "rating": body.rating}


# ---------------------------------------------------------------------------
# Preferences: custom instructions + saved prompt library
# ---------------------------------------------------------------------------


def _instructions_key(user_id: str) -> str:
    return f"custom_instructions:{user_id}"


@router.get("/api/preferences/instructions", tags=["settings"])
async def get_instructions(request: Request, identity: Identity = Depends(require_identity)):
    """The caller's saved custom instructions (ChatGPT-grade memory-lite)."""
    value = await request.app.state.runtime.tenant_settings.get(
        identity.policy.tenant_id, _instructions_key(identity.policy.user_id)
    )
    return {"instructions": value or ""}


@router.put("/api/preferences/instructions", tags=["settings"])
async def set_instructions(
    body: InstructionsIn, request: Request, identity: Identity = Depends(require_identity)
):
    """Save custom instructions; blank clears them. Applied to every new run."""
    clean = body.instructions.strip()[:4000]
    await request.app.state.runtime.tenant_settings.set(
        identity.policy.tenant_id, _instructions_key(identity.policy.user_id), clean
    )
    return {"instructions": clean}


def _prompt_dict(p: PromptTemplateRecord) -> dict[str, Any]:
    return {
        "id": p.id, "title": p.title, "body": p.body,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@router.get("/api/prompts", tags=["chat"])
async def list_prompts(request: Request, identity: Identity = Depends(require_identity)):
    db = request.app.state.db
    async with db.session() as session:
        rows = (
            (
                await session.execute(
                    select(PromptTemplateRecord)
                    .where(
                        PromptTemplateRecord.tenant_id == identity.policy.tenant_id,
                        PromptTemplateRecord.user_id == identity.policy.user_id,
                    )
                    .order_by(PromptTemplateRecord.created_at, PromptTemplateRecord.id)
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
    return [_prompt_dict(p) for p in rows]


@router.post("/api/prompts", tags=["chat"])
async def create_prompt(
    body: PromptCreate, request: Request, identity: Identity = Depends(require_identity)
):
    db = request.app.state.db
    record = PromptTemplateRecord(
        id=new_id("prompt"),
        tenant_id=identity.policy.tenant_id,
        user_id=identity.policy.user_id,
        title=body.title.strip(),
        body=body.body.strip(),
    )
    async with db.session() as session:
        session.add(record)
    await request.app.state.runtime.audit.record(
        actor=identity.policy.user_id, action="prompt.created",
        resource=f"prompt:{record.id}", tenant_id=identity.policy.tenant_id,
    )
    return _prompt_dict(record)


@router.put("/api/prompts/{prompt_id}", tags=["chat"])
async def update_prompt(
    prompt_id: str, body: PromptUpdate, request: Request, identity: Identity = Depends(require_identity)
):
    db = request.app.state.db
    async with db.session() as session:
        record = await session.get(PromptTemplateRecord, prompt_id)
        if (
            record is None
            or record.tenant_id != identity.policy.tenant_id
            or record.user_id != identity.policy.user_id
        ):
            raise HTTPException(404, "Prompt not found")
        if body.title is not None:
            record.title = body.title.strip()
        if body.body is not None:
            record.body = body.body.strip()
    return _prompt_dict(record)


@router.delete("/api/prompts/{prompt_id}", tags=["chat"])
async def delete_prompt(
    prompt_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    db = request.app.state.db
    async with db.session() as session:
        record = await session.get(PromptTemplateRecord, prompt_id)
        if (
            record is None
            or record.tenant_id != identity.policy.tenant_id
            or record.user_id != identity.policy.user_id
        ):
            raise HTTPException(404, "Prompt not found")
        await session.delete(record)
    return {"deleted": True}


@router.get("/api/conversations/{conversation_id}/export", tags=["chat"])
async def export_conversation(
    conversation_id: str,
    request: Request,
    format: str = "md",
    identity: Identity = Depends(require_identity),
):
    """Download a conversation as Markdown or JSON."""
    db = request.app.state.db
    async with db.session() as session:
        conv = await session.get(ConversationRecord, conversation_id)
        if conv is None or conv.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Conversation not found")
        rows = (
            (
                await session.execute(
                    select(MessageRecord)
                    .where(MessageRecord.conversation_id == conversation_id)
                    .order_by(MessageRecord.created_at, MessageRecord.id)
                )
            )
            .scalars()
            .all()
        )
    messages = [
        {
            "role": m.role,
            "agent": m.agent_slug,
            "text": (m.content or {}).get("text", ""),
            "created_at": m.created_at.isoformat(),
        }
        for m in rows
    ]
    if format == "json":

        return JSONResponse(
            content={
                "id": conv.id,
                "title": conv.title,
                "channel": conv.channel,
                "created_at": conv.created_at.isoformat(),
                "messages": messages,
            },
            headers={
                "Content-Disposition": f'attachment; filename="{conv.id}.json"'
            },
        )
    lines = [f"# {conv.title}", ""]
    for m in messages:
        who = {"user": "You", "assistant": "Assistant"}.get(m["role"], m["role"])
        lines.append(f"**{who}:** {m['text']}")
        lines.append("")
    from fastapi.responses import Response

    return Response(
        content="\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{conv.id}.md"'},
    )


@router.get("/api/runs", tags=["chat"])
async def list_runs(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    agent: str | None = None,
    since_hours: int | None = None,
    identity: Identity = Depends(require_identity),
):
    from datetime import timedelta

    db = request.app.state.db
    stmt = select(RunRecord).where(RunRecord.tenant_id == identity.policy.tenant_id)
    count_stmt = (
        select(func.count())
        .select_from(RunRecord)
        .where(RunRecord.tenant_id == identity.policy.tenant_id)
    )
    if status:
        stmt = stmt.where(RunRecord.status == status)
        count_stmt = count_stmt.where(RunRecord.status == status)
    if agent:
        stmt = stmt.where(RunRecord.agent_slug == agent)
        count_stmt = count_stmt.where(RunRecord.agent_slug == agent)
    if since_hours:
        since = datetime.now(UTC) - timedelta(hours=since_hours)
        stmt = stmt.where(RunRecord.created_at >= since)
        count_stmt = count_stmt.where(RunRecord.created_at >= since)
    async with db.session() as session:
        total = (await session.execute(count_stmt)).scalar_one()
        rows = (
            (
                await session.execute(
                    stmt.order_by(RunRecord.created_at.desc()).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )
    return {
        "total": int(total),
        "runs": [
            {
                "id": r.id, "conversation_id": r.conversation_id, "agent": r.agent_slug,
                "status": r.status, "model": r.model, "duration_ms": r.duration_ms,
                "usage": r.usage_json, "error": r.error, "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.delete("/api/runs/{run_id}", tags=["chat"])
async def delete_run(
    run_id: str, request: Request, identity: Identity = Depends(require_role_ops)
):
    db = request.app.state.db
    async with db.session() as session:
        run = await session.get(RunRecord, run_id)
        if run is None or run.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Run not found")
        await session.delete(run)
    return {"deleted": True}


@router.post("/api/runs/{run_id}/cancel", tags=["chat"])
async def cancel_run_endpoint(
    run_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    service = _chat(request)
    cancelled = await service.cancel_run(run_id)
    if not cancelled:
        return {"cancelled": False, "detail": "Run is not active (already finished or unknown)."}
    await request.app.state.runtime.audit.record(
        actor=identity.policy.user_id,
        action="run.cancelled",
        resource=f"run:{run_id}",
        tenant_id=identity.policy.tenant_id,
    )
    return {"cancelled": True}


@router.post("/api/runs/clear", tags=["chat"])
async def clear_runs(request: Request, identity: Identity = Depends(require_role_ops)):
    from sqlalchemy import delete

    db = request.app.state.db
    async with db.session() as session:
        result = await session.execute(
            delete(RunRecord).where(RunRecord.tenant_id == identity.policy.tenant_id)
        )
    await request.app.state.runtime.audit.record(
        actor=identity.policy.user_id,
        action="runs.cleared",
        resource="observability",
        tenant_id=identity.policy.tenant_id,
        detail={"deleted": result.rowcount},
    )
    return {"deleted": result.rowcount}


# ---------------------------------------------------------------------------
# Approvals (human-in-the-loop)
# ---------------------------------------------------------------------------


@router.get("/api/approvals", tags=["approvals"])
async def list_approvals(request: Request, identity: Identity = Depends(require_identity)):
    records = await request.app.state.runtime.approvals.pending(
        tenant_id=identity.policy.tenant_id
    )
    return [
        {
            "id": r.id, "run_id": r.run_id, "tool_name": r.tool_name,
            "args": r.args_json, "risk": r.risk or classify_tool(r.tool_name),
            "status": r.status, "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]


@router.post("/api/approvals/{approval_id}/decide", tags=["approvals"])
async def decide_approval(
    approval_id: str,
    body: ApprovalDecision,
    request: Request,
    identity: Identity = Depends(require_identity),
):
    if not identity.policy.role or identity.policy.role == "viewer":
        raise HTTPException(403, "Viewers cannot decide approvals")
    # Tenant isolation: the approval must belong to the caller's tenant.
    existing = await request.app.state.runtime.approvals.get(approval_id)
    if existing is None or existing.tenant_id != identity.policy.tenant_id:
        raise HTTPException(404, "Approval not found")
    record = await request.app.state.runtime.approvals.decide(
        approval_id,
        approved=body.approved,
        decided_by=body.decided_by or identity.policy.user_id,
        note=body.note,
    )
    if record is None:
        raise HTTPException(404, "Approval not found or already decided")
    return {"id": record.id, "status": record.status}


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------


@router.get("/api/agents", tags=["registry"])
async def list_agents(request: Request, identity: Identity = Depends(require_identity)):
    agents = await request.app.state.runtime.agents_registry.list_agents()
    from voicefirst.db.models import AgentKeyRecord

    async with request.app.state.db.session() as session:
        rows = (
            await session.execute(
                select(AgentKeyRecord.agent_slug, AgentKeyRecord.id)
                .where(
                    AgentKeyRecord.tenant_id == identity.policy.tenant_id,
                    AgentKeyRecord.revoked.is_(False),
                )
            )
        ).all()
    key_counts: dict[str, int] = {}
    for slug, _ in rows:
        key_counts[slug] = key_counts.get(slug, 0) + 1
    out = []
    for a in agents:
        d = a.to_dict()
        d["keys_active"] = key_counts.get(a.slug, 0)
        out.append(d)
    return out


@router.get("/api/runs/{run_id}", tags=["chat"])
async def get_run(
    run_id: str, request: Request, identity: Identity = Depends(require_identity)
):
    db = request.app.state.db
    async with db.session() as session:
        run = await session.get(RunRecord, run_id)
        if run is None or run.tenant_id != identity.policy.tenant_id:
            raise HTTPException(404, "Run not found")
    spans = json.loads(run.spans_json) if run.spans_json else []
    return {
        "id": run.id,
        "conversation_id": run.conversation_id,
        "agent": run.agent_slug,
        "status": run.status,
        "model": run.model,
        "duration_ms": run.duration_ms,
        "ttft_ms": run.ttft_ms,
        "usage": run.usage_json,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
        "spans": spans,
    }


@router.patch("/api/agents/{slug}/status", tags=["registry"])
async def set_agent_status(
    slug: str, body: AgentStatusUpdate, request: Request, _: Identity = Depends(require_role_admin)
):
    updated = await request.app.state.runtime.agents_registry.set_status(slug, body.status)
    if updated is None:
        raise HTTPException(404, "Agent not found")
    return updated.to_dict()


@router.get("/api/skills", tags=["registry"])
async def list_skills(request: Request, identity: Identity = Depends(require_identity)):
    return await request.app.state.runtime.skills_registry.list_skills()


@router.get("/api/mcp", tags=["registry"])
async def list_mcp(request: Request, identity: Identity = Depends(require_identity)):
    rows = await request.app.state.runtime.mcp_registry.list_servers()
    return [
        {
            "id": r.id, "slug": r.slug, "name": r.name, "transport": r.transport,
            "url": r.url, "command": r.command, "status": r.status,
            "health": r.health, "last_checked_at": r.last_checked_at.isoformat() if r.last_checked_at else None,
        }
        for r in rows
    ]


@router.post("/api/mcp/{slug}/check", tags=["registry"])
async def check_mcp(request: Request, slug: str, identity: Identity = Depends(require_identity)):
    """Live-probe an MCP server: connect and list tools within a timeout."""
    import asyncio

    runtime = request.app.state.runtime
    record = await runtime.mcp_registry.get(slug)
    if record is None:
        raise HTTPException(404, "MCP server not found")
    toolset = runtime.mcp_registry.build_toolset(record)
    if toolset is None:
        await runtime.mcp_registry.set_health(slug, "not configured")
        return {"slug": slug, "health": "not configured", "tools": 0}

    async def probe():
        async with toolset:
            tools = await toolset.list_tools()
        return len(tools)

    try:
        tool_count = await asyncio.wait_for(probe(), timeout=10.0)
        health = "healthy"
    except Exception as exc:  # noqa: BLE001
        await runtime.mcp_registry.set_health(slug, "unreachable")
        return {"slug": slug, "health": "unreachable", "detail": str(exc)[:200], "tools": 0}
    await runtime.mcp_registry.set_health(slug, health)
    await runtime.events.publish(
        "registry.mcp.health", {"slug": slug, "health": health, "tools": tool_count}
    )
    return {"slug": slug, "health": health, "tools": tool_count}


@router.post("/api/mcp", tags=["registry"])
async def upsert_mcp(body: MCPServerUpsert, request: Request, _: Identity = Depends(require_role_admin)):
    record = await request.app.state.runtime.mcp_registry.upsert(
        slug=body.name.lower().replace(" ", "-"),
        name=body.name,
        transport=body.transport,
        url=body.url,
        command=body.command,
        headers=body.headers,
        status=body.status,
    )
    return {"id": record.id, "slug": record.slug, "status": record.status}


# ---------------------------------------------------------------------------
# Governance: audit, usage, events
# ---------------------------------------------------------------------------


@router.get("/api/audit", tags=["governance"])
async def list_audit(
    request: Request,
    limit: int = Query(100, le=500),
    action: str | None = None,
    identity: Identity = Depends(require_identity),
):
    rows = await request.app.state.runtime.audit.list_entries(limit=limit, action=action)
    return [
        {
            "id": r.id, "ts": r.ts.isoformat(), "actor": r.actor, "action": r.action,
            "resource": r.resource, "detail": r.detail,
        }
        for r in rows
    ]


@router.get("/api/audit/verify", tags=["governance"])
async def verify_audit(request: Request, identity: Identity = Depends(require_identity)):
    ok, broken_at = await request.app.state.runtime.audit.verify()
    return {"valid": ok, "broken_at": broken_at}


@router.get("/api/usage", tags=["governance"])
async def usage_summary(
    request: Request,
    days: int = Query(30, le=365),
    identity: Identity = Depends(require_identity),
):
    tenant_id = None if identity.policy.role == "admin" else identity.policy.tenant_id
    return await request.app.state.runtime.usage.summary(tenant_id=tenant_id, days=days)


@router.get("/api/events", tags=["governance"])
async def event_stream(request: Request, identity: Identity = Depends(require_identity)):
    """Global platform activity as SSE (runs, approvals, audit, registry)."""
    events = request.app.state.runtime.events
    queue = await events.subscribe()

    async def stream():
        try:
            yield f"data: {json.dumps({'type': 'connected', 'data': {'tenant': identity.policy.tenant_id}})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except asyncio.TimeoutError:
                    # SSE comment: keeps proxies alive without polluting the UI
                    yield ": keepalive\n\n"
        finally:
            await events.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Notifications (tenant settings)
# ---------------------------------------------------------------------------


@router.get("/api/notifications", tags=["settings"])
async def get_notifications(request: Request, identity: Identity = Depends(require_identity)):
    url = await request.app.state.runtime.tenant_settings.get(
        identity.policy.tenant_id, "approval_webhook_url"
    )
    return {"approval_webhook_url": url}


@router.put("/api/notifications", tags=["settings"])
async def set_notifications(
    request: Request,
    body: dict[str, str | None],
    identity: Identity = Depends(require_identity),
):
    url = (body or {}).get("approval_webhook_url") or None
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(422, "Webhook URL must start with http:// or https://")
    await request.app.state.runtime.tenant_settings.set(
        identity.policy.tenant_id, "approval_webhook_url", url or ""
    )
    await request.app.state.runtime.audit.record(
        actor=identity.policy.user_id,
        action="notifications.updated",
        resource="tenant",
        tenant_id=identity.policy.tenant_id,
        detail={"set": bool(url)},
    )
    return {"approval_webhook_url": url}


@router.post("/api/notifications/test", tags=["settings"])
async def test_notifications(request: Request, identity: Identity = Depends(require_identity)):
    """POST a sample approval payload to the configured webhook."""
    import httpx

    url = await request.app.state.runtime.tenant_settings.get(
        identity.policy.tenant_id, "approval_webhook_url"
    )
    if not url:
        raise HTTPException(422, "No webhook URL configured")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={
                    "event": "approval.requested",
                    "approval_id": "test",
                    "run_id": "test",
                    "tool_name": "sample_tool",
                    "args": {"note": "This is a test notification"},
                },
                headers={"X-VoiceFirst-Event": "approval.requested"},
            )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)[:200]}
    return {"ok": resp.status_code < 400, "status_code": resp.status_code}


# ---------------------------------------------------------------------------
# Platform meta
# ---------------------------------------------------------------------------


@router.get("/api/platform", tags=["meta"])
async def platform_info(request: Request, identity: Identity = Depends(require_identity)):
    from voicefirst.agents.factory import capability_catalog

    runtime = request.app.state.runtime
    settings = runtime.settings
    engine = runtime.policy_engine
    connections = await runtime.connections.list_for_user(identity.policy.user_id)
    active = {
        c.purpose: {"provider": c.provider, "model": c.model, "status": c.status}
        for c in connections
        if c.is_active and c.status == "valid"
    }
    return {
        "name": settings.app_name,
        "version": "0.1.0",
        "model": settings.default_model,
        "demo_mode": not settings.has_chat_key,
        "realtime_model": settings.realtime_model,
        "realtime_available": settings.has_realtime_key,
        "auth_mode": "betterauth" if not runtime.identity.dev_mode else "dev",
        "identity": {
            "user_id": identity.policy.user_id,
            "role": identity.policy.role,
            "tenant_id": identity.policy.tenant_id,
            "name": identity.name,
            "email": identity.email,
        },
        "capabilities": capability_catalog(),
        "connections": active,
        "policies": {
            # live engine values (Settings page mutations apply to these immediately)
            "mutating_min_role": engine.mutating_min_role,
            "require_approval_for_mutations": engine.require_approval_for_mutations,
            "redact_pii": engine.redact_pii,
            "daily_spend_limit_usd": settings.daily_spend_limit_usd,
            "per_run_token_limit": settings.per_run_token_limit,
        },
    }


@router.patch("/api/policies", tags=["governance"])
async def update_policies(
    body: PolicyUpdate,
    request: Request,
    identity: Identity = Depends(require_role_admin),
):
    """Runtime policy overrides. Applied instantly; env config wins after restart."""
    runtime = request.app.state.runtime
    engine = runtime.policy_engine
    if body.require_approval_for_mutations is not None:
        engine.require_approval_for_mutations = body.require_approval_for_mutations
    if body.mutating_min_role is not None:
        engine.mutating_min_role = body.mutating_min_role
    if body.redact_pii is not None:
        engine.redact_pii = body.redact_pii
    await runtime.audit.record(
        actor=identity.policy.user_id,
        action="policy.updated",
        resource="control-plane",
        tenant_id=identity.policy.tenant_id,
        detail=body.model_dump(exclude_none=True),
    )
    settings = runtime.settings
    return {
        "mutating_min_role": engine.mutating_min_role,
        "require_approval_for_mutations": engine.require_approval_for_mutations,
        "redact_pii": engine.redact_pii,
        "daily_spend_limit_usd": settings.daily_spend_limit_usd,
        "per_run_token_limit": settings.per_run_token_limit,
    }
