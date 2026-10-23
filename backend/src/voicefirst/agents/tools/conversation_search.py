"""Cross-conversation memory: search an operator's own past conversations.

The orchestrator exposes this as the `conversation_search` tool so a returning
user can say "what did we decide about X last week?" and the agent can find the
actual exchange — scoped strictly to the caller's tenant and user id.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from sqlalchemy import select

from voicefirst.agents.deps import AgentDeps
from voicefirst.db.models import ConversationRecord, MessageRecord

MAX_SNIPPET_CHARS = 400
MAX_RESULTS = 8


async def search_conversations(
    ctx: RunContext[AgentDeps], query: str, limit: int = 5
) -> list[dict[str, Any]]:
    """Search the caller's past conversation messages by keyword. Read-only.

    Tenant- and user-scoped: an operator can only ever find their own
    exchanges, never anyone else's.
    """
    query = (query or "").strip()
    if not query:
        return [{"info": "Provide a keyword to search past conversations."}]
    limit = max(1, min(int(limit), MAX_RESULTS))

    runtime = ctx.deps.runtime
    if runtime is None:
        return [{"error": "runtime_unavailable", "detail": "Conversation store is not reachable."}]

    f"%{query.lower()}%"
    async with runtime.db.session() as session:
        rows = (
            await session.execute(
                select(MessageRecord, ConversationRecord.title)
                .join(ConversationRecord, MessageRecord.conversation_id == ConversationRecord.id)
                .where(
                    ConversationRecord.tenant_id == ctx.deps.tenant_id,
                    ConversationRecord.user_id == ctx.deps.user_id,
                    MessageRecord.role.in_(["user", "assistant"]),
                )
                .order_by(MessageRecord.created_at.desc())
                .limit(500)
            )
        ).all()

    results: list[dict[str, Any]] = []
    for message, title in rows:
        text = (message.content or {}).get("text", "") or ""
        if query.lower() in text.lower():
            results.append(
                {
                    "conversation_id": message.conversation_id,
                    "conversation_title": title,
                    "role": message.role,
                    "snippet": text[:MAX_SNIPPET_CHARS],
                    "at": message.created_at.isoformat() if message.created_at else None,
                }
            )
        if len(results) >= limit:
            break
    if not results:
        return [{"info": f"No past messages match '{query}'."}]
    return results
