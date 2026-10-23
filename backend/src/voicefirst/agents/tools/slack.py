"""Slack tools.

Real posting requires `SLACK_BOT_TOKEN`; listing works against the public API
when a token is present. Without credentials, tools return a clear structured
"not configured" result so agents can explain rather than fail hard.

Production behavior: Slack's `ok: false` payload and HTTP failures are mapped
to structured errors / `ModelRetry` (rate limits and 5xx are retryable),
message length is capped, and every successful post is audit-logged.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic_ai import ModelRetry, RunContext, Tool

from voicefirst.agents.deps import AgentDeps
from voicefirst.agents.tools import _http

SLACK_API = "https://slack.com/api"

MAX_MESSAGE_CHARS = 40_000  # Slack rejects >40k in chat.postMessage.


def _token(ctx: RunContext[AgentDeps] | None = None) -> str | None:
    explicit = os.getenv("VOICEFIRST_SLACK_BOT_TOKEN")
    if explicit:
        return explicit
    if ctx is not None and ctx.deps.runtime is not None:
        return getattr(ctx.deps.runtime.settings, "slack_bot_token", None)
    return os.getenv("SLACK_BOT_TOKEN")


async def _post_json(path: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to the Slack API with unified error mapping."""
    async with _http.make_client(timeout=20.0) as client:
        resp = await client.post(
            f"{SLACK_API}{path}", json=payload, headers={"Authorization": f"Bearer {token}"}
        )
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After", "a few seconds")
        raise ModelRetry(f"Slack rate limit hit — wait {retry_after} seconds, then retry the same call.")
    if resp.status_code >= 500:
        raise ModelRetry(f"Slack returned a server error ({resp.status_code}). Retry the same call.")
    if resp.status_code >= 400:
        return {"ok": False, "error": f"http_{resp.status_code}", "detail": resp.text[:200]}
    data = resp.json()
    if data.get("ok") is False and data.get("error") in ("ratelimited", "rate_limited"):
        raise ModelRetry("Slack rate limited — wait a moment, then retry the same call.")
    return data


async def list_channels(ctx: RunContext[AgentDeps], limit: int = 20) -> list[dict[str, Any]]:
    """List public channels the bot can see. Read-only."""
    token = _token(ctx)
    if not token:
        return [{"error": "not_configured", "detail": "SLACK_BOT_TOKEN not configured."}]
    limit = max(1, min(int(limit), 100))
    data = await _post_json(
        "/conversations.list",
        token,
        {"limit": limit, "types": "public_channel", "exclude_archived": True},
    )
    if not data.get("ok"):
        return [{"error": data.get("error", "unknown"), "detail": "Slack conversations.list failed."}]
    return [
        {"id": c["id"], "name": c["name"], "members": c.get("num_members")}
        for c in data.get("channels", [])
    ]


async def post_message(ctx: RunContext[AgentDeps], channel: str, text: str) -> dict[str, Any]:
    """Post a message to a Slack channel. Mutating — requires human approval."""
    token = _token(ctx)
    if not token:
        return {"error": "not_configured", "detail": "SLACK_BOT_TOKEN not configured; cannot post."}
    channel = (channel or "").strip()
    text = (text or "")[:MAX_MESSAGE_CHARS]
    if not channel or not text:
        return {"error": "missing_args", "detail": "Both channel and text are required."}
    data = await _post_json("/chat.postMessage", token, {"channel": channel, "text": text})
    if not data.get("ok"):
        detail = data.get("detail") or f"Slack rejected the post: {data.get('error', 'unknown error')}."
        return {"error": data.get("error", "unknown"), "detail": detail}
    if ctx.deps.runtime is not None:
        await ctx.deps.runtime.audit.record(
            actor=ctx.deps.user_id,
            action="slack.message_posted",
            resource=f"channel:{channel}",
            tenant_id=ctx.deps.tenant_id,
            detail={"length": len(text)},
        )
    return {"ts": data.get("ts"), "channel": data.get("channel"), "posted": True}


def slack_tools(approval_default: bool = True) -> list[Tool]:
    return [
        Tool(list_channels, name="slack_list_channels", takes_ctx=True, max_retries=1),
        Tool(
            post_message,
            name="slack_post_message",
            takes_ctx=True,
            requires_approval=approval_default,
            max_retries=1,
        ),
    ]
