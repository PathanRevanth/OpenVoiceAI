"""Artifact index: find assistant messages that declare artifacts.

Artifacts are declared inline in assistant text (fenced `artifact` blocks or
long auto-detected fences); they have no dedicated table. This module scans
recent assistant messages for fence markers and returns the candidates with
their conversation context. The browser parses them with the same
`parseSegments` rules as the thread, so the index always agrees with chat.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from voicefirst.db.models import ConversationRecord, MessageRecord

# A message can only hold an artifact if it contains a code fence.
FENCE_MARKER = "```"
MAX_CANDIDATES = 200


async def scan_artifact_sources(db: Any, tenant_id: str, limit: int = MAX_CANDIDATES) -> list[dict[str, Any]]:
    """Newest assistant messages containing fences, with conversation context."""
    limit = max(1, min(int(limit), MAX_CANDIDATES))
    async with db.session() as session:
        conv_rows = (
            (
                await session.execute(
                    select(ConversationRecord).where(ConversationRecord.tenant_id == tenant_id)
                )
            )
            .scalars()
            .all()
        )
        by_conv = {c.id: c for c in conv_rows}
        if not by_conv:
            return []
        msg_rows = (
            (
                await session.execute(
                    select(MessageRecord)
                    .where(
                        MessageRecord.conversation_id.in_(list(by_conv)),
                        MessageRecord.role == "assistant",
                    )
                    .order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
                    .limit(2000)
                )
            )
            .scalars()
            .all()
        )
    out: list[dict[str, Any]] = []
    for m in msg_rows:
        text = str((m.content or {}).get("text", "") or "")
        if FENCE_MARKER not in text:
            continue
        conv = by_conv.get(m.conversation_id)
        if conv is None:
            continue
        out.append(
            {
                "message_id": m.id,
                "conversation_id": conv.id,
                "conversation_title": conv.title,
                "project_id": conv.project_id,
                "text": text,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
        )
        if len(out) >= limit:
            break
    return out
