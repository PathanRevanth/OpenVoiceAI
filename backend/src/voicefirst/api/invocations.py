"""Shared agent invocation core used by the session API, agent webhooks,
and scheduled triggers. One path means one governance story:
runs, usage, approvals, and audit all behave identically everywhere."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import HTTPException
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ModelMessagesTypeAdapter

from voicefirst.agents.deps import AgentDeps
from voicefirst.api.spans import SpanCollector
from voicefirst.db.models import RunRecord, new_id
from voicefirst.db.session import Database


async def run_agent_invocation(
    runtime: Any,
    slug: str,
    message: str,
    deps: AgentDeps,
    db: Database,
) -> dict[str, Any]:
    """Run one agent end to end. Returns a serializable payload dict.

    Raises HTTPException(404/409) for unknown/disabled agents and
    HTTPException(502) when the run itself fails.
    """
    if slug == "orchestrator":
        agent = runtime.orchestrator
    else:
        agent = runtime.specialists.get(slug)
    if agent is None:
        raise HTTPException(404, f"Unknown agent: {slug}")
    for descriptor in runtime.agents_registry._descriptors.values():  # noqa: SLF001
        if descriptor.slug == slug and descriptor.status != "enabled":
            raise HTTPException(409, f"Agent {slug} is disabled")

    run = RunRecord(
        id=new_id("run"),
        agent_slug=slug,
        status="running",
        model=runtime.settings.default_model,
        tenant_id=deps.tenant_id,
        user_id=deps.user_id,
    )
    async with db.session() as session:
        session.add(run)

    started = time.time()
    collector = SpanCollector()
    ttft_ms: int | None = None
    from pydantic_ai import UsageLimits

    try:
        async with agent.iter(
            message,
            deps=deps,
            usage_limits=UsageLimits(total_tokens_limit=runtime.settings.per_run_token_limit or None),
        ) as agent_run:
            async for node in agent_run:
                if agent.is_model_request_node(node):
                    model_span = collector.start("model", "model")
                    async with node.stream(agent_run.ctx) as node_stream:
                        async for ev in node_stream:
                            if ttft_ms is None:
                                from pydantic_ai.messages import PartDeltaEvent, TextPartDelta

                                if (
                                    isinstance(ev, PartDeltaEvent)
                                    and isinstance(ev.delta, TextPartDelta)
                                    and ev.delta.content_delta
                                ):
                                    ttft_ms = int((time.time() - started) * 1000)
                    if collector:
                        collector.end(model_span)
                if agent.is_call_tools_node(node):
                    collector.start("tools", "tools")
                    open_tools: dict[str, str] = {}
                    async with node.stream(agent_run.ctx) as node_stream:
                        async for tool_event in node_stream:
                            part = getattr(tool_event, "part", None)
                            call_id = getattr(part, "tool_call_id", None)
                            name = getattr(part, "tool_name", None)
                            if hasattr(tool_event, "result"):
                                if collector and call_id and call_id in open_tools:
                                    collector.end(open_tools.pop(call_id))
                            elif collector and call_id:
                                open_tools[call_id] = collector.start(
                                    name or "tool", "tool", getattr(part, "args", {})
                                )
        result = agent_run.result
    except Exception as exc:  # noqa: BLE001
        async with db.session() as session:
            stored = await session.get(RunRecord, run.id)
            if stored is not None:
                stored.status = "failed"
                stored.error = f"{type(exc).__name__}: {exc}"
                stored.duration_ms = int((time.time() - started) * 1000)
        raise HTTPException(502, f"Agent run failed: {exc}") from exc

    usage = result.usage
    duration_ms = int((time.time() - started) * 1000)
    spans = collector.normalize()

    if isinstance(result.output, DeferredToolRequests):
        status = "awaiting_approval"
        output = ""
        approvals = await runtime.approvals.create_for_deferred_requests(
            run_id=run.id,
            conversation_id=None,
            deferred=result.output,
            tenant_id=deps.tenant_id,
        )
        approval_payload = [
            {"approval_id": a.id, "tool_name": a.tool_name, "args": a.args_json}
            for a in approvals
        ]
        history = list(result.all_messages())
    else:
        status = "completed"
        output = result.output if isinstance(result.output, str) else str(result.output)
        approval_payload = []
        history = list(result.all_messages())

    usage_json = {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        "requests": int(getattr(usage, "requests", 0) or 0),
        "tool_calls": int(getattr(usage, "tool_calls", 0) or 0),
        "cost_usd": float(getattr(usage, "cost", 0.0) or 0.0),
    }

    async with db.session() as session:
        stored = await session.get(RunRecord, run.id)
        if stored is not None:
            stored.status = status
            stored.duration_ms = duration_ms
            stored.usage_json = usage_json
            stored.spans_json = json.dumps(spans)
            if ttft_ms is not None:
                stored.ttft_ms = ttft_ms
            if status == "awaiting_approval":
                stored.history_json = ModelMessagesTypeAdapter.dump_json(history).decode()
                stored.deferred_requests = {
                    "tool_call_ids": [a.tool_call_id for a in approvals],
                    "approval_map": {a.tool_call_id: a.id for a in approvals},
                }

    await runtime.usage.record_run_usage(
        run_id=run.id,
        agent_slug=slug,
        model=runtime.settings.default_model,
        tenant_id=deps.tenant_id,
        usage=usage,
        user_id=deps.user_id,
    )

    return {
        "run_id": run.id,
        "agent": slug,
        "status": status,
        "spans": spans,
        "ttft_ms": ttft_ms,
        "output": output,
        "approvals": approval_payload,
        "usage": {**usage_json, "duration_ms": duration_ms},
    }
