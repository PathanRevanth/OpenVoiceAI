"""Chat runs: streaming (SSE) orchestration with deferred-approval pause/resume.

The run loop is the heart of the experience layer:

1. Persist the user message, create a run row.
2. Stream the orchestrator via `run_stream_events`, forwarding text deltas,
   tool activity, and usage to the client as SSE events.
3. If the run ends with `DeferredToolRequests` (mutating tool awaiting human
   approval), record approvals, persist serialized state (history + deferred
   requests), and emit `approval.requested`.
4. On resume, rebuild `DeferredToolResults` from approval decisions and
   continue the same run.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from pydantic_ai import (
    CancellationToken,
    DeferredToolRequests,
    DeferredToolResults,
    RunCancelled,
    UsageLimits,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from sqlalchemy import select

from voicefirst.agents.deps import AgentDeps
from voicefirst.agents.factory import DEFAULT_MAX_OUTPUT_TOKENS, THINKING_MAX_OUTPUT_TOKENS
from voicefirst.api.attachments import extract_attachment_text, wrap_attachment_text
from voicefirst.api.enrichment import (
    WEB_TOOLS,
    apply_instructions,
    build_followups,
    extract_sources,
    native_web_evidence,
)
from voicefirst.control_plane.model_catalog import CATALOG

# Image previews persisted on the user message (chars of base64); bigger
# uploads stay metadata-only so message rows stay small.
ATTACHMENT_PREVIEW_CHARS = 400_000
# Thinking text persisted on the assistant message (chars); the tail is cut
# with a marker so the stored trace stays small.
THINKING_PERSIST_CHARS = 12_000
from voicefirst.api.spans import SpanCollector

# In-flight streaming runs, keyed by run id, for cancellation: the task
# consuming the generator plus the pydantic-ai CancellationToken that cancels
# the run graph cleanly (raising RunCancelled inside the model loop).
ACTIVE_RUNS: dict[str, Any] = {}
ACTIVE_TOKENS: dict[str, Any] = {}
from voicefirst.db.models import ConversationRecord, MessageRecord, RunRecord, new_id
from voicefirst.db.session import Database


@dataclass
class RunOutcome:
    status: str  # completed | awaiting_approval | failed
    output: str = ""
    deferred: dict[str, Any] | None = None
    error: str | None = None


class ChatService:
    def __init__(self, db: Database, runtime: Any) -> None:
        self._db = db
        self._rt = runtime

    # -- Persistence helpers -------------------------------------------------

    async def get_or_create_conversation(
        self,
        conversation_id: str | None,
        *,
        tenant_id: str,
        user_id: str,
        channel: str,
        title_from: str,
    ) -> ConversationRecord:
        async with self._db.session() as session:
            if conversation_id:
                conv = await session.get(ConversationRecord, conversation_id)
                if conv is not None:
                    return conv
            conv = ConversationRecord(
                id=new_id("conv"),
                title=title_from[:80] or "New conversation",
                tenant_id=tenant_id,
                user_id=user_id,
                channel=channel,
            )
            session.add(conv)
        return conv

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: dict[str, Any],
        *,
        run_id: str | None = None,
        agent_slug: str | None = None,
    ) -> None:
        async with self._db.session() as session:
            session.add(
                MessageRecord(
                    id=new_id("msg"),
                    conversation_id=conversation_id,
                    run_id=run_id,
                    role=role,
                    agent_slug=agent_slug,
                    content=content,
                )
            )

    async def history_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        async with self._db.session() as session:
            rows = (
                await session.execute(
                    select(MessageRecord)
                    .where(MessageRecord.conversation_id == conversation_id)
                    .order_by(MessageRecord.created_at, MessageRecord.id)
                )
            ).scalars()
            return [r.content for r in rows]

    # -- Run persistence (for approval pause/resume) --------------------------

    async def create_run(
        self, conversation_id: str, *, tenant_id: str, user_id: str, model: str | None,
        agent_slug: str = "orchestrator",
    ) -> RunRecord:
        run = RunRecord(
            id=new_id("run"),
            conversation_id=conversation_id,
            agent_slug=agent_slug,
            status="running",
            model=model,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        async with self._db.session() as session:
            session.add(run)
        return run

    async def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        usage: Any = None,
        error: str | None = None,
        deferred: dict[str, Any] | None = None,
        history: list[ModelMessage] | None = None,
        spans: list[dict[str, Any]] | None = None,
        ttft_ms: int | None = None,
    ) -> None:
        async with self._db.session() as session:
            run = await session.get(RunRecord, run_id)
            if run is None:
                return
            run.status = status
            run.duration_ms = int((time.time() - _run_start(run)) * 1000) if _run_start(run) else None
            if spans is not None:
                import json as _json

                run.spans_json = _json.dumps(spans)
            if ttft_ms is not None:
                run.ttft_ms = ttft_ms
            if usage is not None:
                run.usage_json = {
                    "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                    "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                    "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
                    "requests": int(getattr(usage, "requests", 0) or 0),
                    "tool_calls": int(getattr(usage, "tool_calls", 0) or 0),
                    "cost_usd": float(getattr(usage, "cost", 0.0) or 0.0),
                }
            run.error = error
            if deferred is not None:
                run.deferred_requests = deferred
            if history is not None:
                run.history_json = ModelMessagesTypeAdapter.dump_json(history).decode()

    async def load_run_state(self, run_id: str) -> tuple[list[ModelMessage], dict[str, Any], str | None] | None:
        async with self._db.session() as session:
            run = await session.get(RunRecord, run_id)
            if run is None or not run.history_json or not run.deferred_requests:
                return None
            history = ModelMessagesTypeAdapter.validate_json(run.history_json)
            return history, run.deferred_requests, run.agent_slug

    async def store_deferred_mapping(self, run_id: str, mapping: dict[str, str]) -> None:
        """Persist tool_call_id -> approval_id so resume can map decisions."""
        async with self._db.session() as session:
            run = await session.get(RunRecord, run_id)
            if run is not None and run.deferred_requests:
                run.deferred_requests = {**run.deferred_requests, "approval_map": mapping}

    # -- The streaming run -----------------------------------------------------

    async def stream_run(
        self,
        *,
        message: str,
        conversation: ConversationRecord,
        deps: AgentDeps,
        model_override: str | None = None,
        attachments: list[dict[str, str]] | None = None,
        agent_slug: str | None = None,
        effort: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Register the in-flight run for cancellation, then delegate.

        The task consuming this generator (the SSE response task) is keyed by
        run id in `ACTIVE_RUNS` so `POST /api/runs/{id}/cancel` can abort it;
        the `finally` block guarantees deregistration on every exit path,
        including client disconnects.
        """
        run_id: str | None = None
        cancellation_token = CancellationToken()
        try:
            async for event in self._stream_run(
                message=message,
                conversation=conversation,
                deps=deps,
                model_override=model_override,
                attachments=attachments,
                agent_slug=agent_slug,
                cancellation_token=cancellation_token,
                effort=effort,
            ):
                if run_id is None and event.get("type") == "run.started":
                    run_id = (event.get("data") or {}).get("run_id")
                    if run_id:
                        ACTIVE_RUNS[run_id] = asyncio.current_task()
                        ACTIVE_TOKENS[run_id] = cancellation_token
                yield event
        finally:
            if run_id is not None:
                ACTIVE_RUNS.pop(run_id, None)
                ACTIVE_TOKENS.pop(run_id, None)

    async def _stream_run(
        self,
        *,
        message: str,
        conversation: ConversationRecord,
        deps: AgentDeps,
        model_override: str | None = None,
        attachments: list[dict[str, str]] | None = None,
        agent_slug: str | None = None,
        cancellation_token: Any | None = None,
        effort: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute an orchestrator (or directly-addressed specialist) run, yielding SSE events.

        Model resolution: the user's active connection (agents purpose) wins,
        then explicit override, then platform default (env/demo). Agent
        resolution: an explicit `agent_slug` matching an enabled specialist
        runs that specialist directly; anything else routes via the orchestrator.
        """
        specialist = self._rt.specialists.get(agent_slug) if agent_slug else None
        agent = specialist if specialist is not None else self._rt.orchestrator
        run_agent_slug = agent_slug if specialist is not None else "orchestrator"
        # Resolution order: the composer's selection (`provider:model`, bound to the
        # caller's key for that provider) → the saved connection → platform default.
        # A selection the user cannot run (no key for its provider) falls through
        # and is reported, so the picker never silently pretends to switch models.
        selected_model_id: str | None = None
        model = None
        retired = bool(model_override) and CATALOG.is_listed(model_override or "") is False
        if model_override and not retired:
            model = await self._rt.connections.model_for_selection(deps.user_id, model_override)
            if model is not None:
                selected_model_id = model_override
        if model_override and selected_model_id is None:
            await self._rt.events.publish(
                "run.model_fallback",
                {
                    "requested": model_override,
                    "error": "the provider no longer offers this model" if retired else "no usable key for this provider",
                },
                tenant_id=deps.tenant_id,
            )
        if model is None:
            model = await self._rt.connections.resolve_chat_model(deps.user_id)
        # Cap output high enough for complete artifacts. Providers otherwise
        # default to a low cap (Anthropic: 4096) and long HTML/SVG deliverables
        # are cut off mid-tag with their fence left open.
        model_settings: dict[str, Any] = {"max_tokens": DEFAULT_MAX_OUTPUT_TOKENS}
        # Reasoning effort applies only to a selection the provider lists with that level.
        if effort and selected_model_id and effort in CATALOG.efforts_for(selected_model_id):
            # Anthropic requires max_tokens above the thinking budget (16k at "high").
            model_settings["thinking"] = effort
            model_settings["max_tokens"] = THINKING_MAX_OUTPUT_TOKENS
        elif effort:
            await self._rt.events.publish(
                "run.effort_ignored",
                {"requested": effort, "model": selected_model_id or "saved connection"},
                tenant_id=deps.tenant_id,
            )
        model_label = (
            f"{type(model).__name__}:{getattr(model, 'model_name', '')}"
            if model is not None
            else (selected_model_id or self._rt.settings.default_model)
        )
        # Image previews stay with the message (bounded) so the thread can
        # show thumbnails; anything bigger rides along as metadata only.
        attach_meta: list[dict[str, str]] = []
        for _att in attachments or []:
            _meta: dict[str, str] = {"name": _att["name"], "media_type": _att["media_type"]}
            _preview = (_att.get("preview") or "").strip()
            if _preview:
                _meta["preview"] = _preview
            elif (
                _att["media_type"].startswith("image/")
                and len(_att.get("data", "")) <= ATTACHMENT_PREVIEW_CHARS
            ):
                _meta["preview"] = f"data:{_att['media_type']};base64,{_att['data']}"
            attach_meta.append(_meta)
        run = await self.create_run(
            conversation.id,
            tenant_id=deps.tenant_id,
            user_id=deps.user_id,
            model=model_label,
            agent_slug=run_agent_slug,
        )
        deps.run_id = run.id
        deps.conversation_id = conversation.id
        await self.add_message(
            conversation.id,
            "user",
            {
                "text": message,
                "attachments": attach_meta,
            },
            run_id=run.id,
        )
        await self._rt.events.publish(
            "run.started",
            {"run_id": run.id, "conversation_id": conversation.id, "agent": run_agent_slug},
            tenant_id=deps.tenant_id,
        )
        yield {
            "type": "run.started",
            "data": {"run_id": run.id, "conversation_id": conversation.id, "agent": run_agent_slug},
        }

        # Prefer the serialized pydantic-ai history (full tool-call structure);
        # fall back to reconstructing from display text for legacy conversations.
        async with self._db.session() as session:
            fresh = await session.get(ConversationRecord, conversation.id)
            raw_history = fresh.history_json if fresh else None
        if raw_history:
            history_models = await self._load_conversation_history(raw_history)
        else:
            history_models = _to_model_messages(await self.history_messages(conversation.id))
        text_parts: list[str] = []
        final_text_parts: list[str] = []
        thinking_parts: list[str] = []
        deferred: DeferredToolRequests | None = None
        usage = None
        error: str | None = None
        paused_messages: list[ModelMessage] | None = None
        collector = SpanCollector()
        run_t0 = time.time()
        first_token_ms: int | None = None

        user_content: str | list = message
        try:
            saved_instructions = await self._rt.tenant_settings.get(
                deps.tenant_id, f"custom_instructions:{deps.user_id}"
            )
        except Exception:  # noqa: BLE001 - instructions are best-effort
            saved_instructions = None
        try:
            project_instructions: str | None = None
            async with self._db.session() as _session:
                _conv = await _session.get(ConversationRecord, conversation.id)
                _project_id = _conv.project_id if _conv else None
                if _project_id:
                    from voicefirst.db.models import ProjectRecord as _ProjectRecord

                    _project = await _session.get(_ProjectRecord, _project_id)
                    if _project is not None and (_project.instructions or "").strip():
                        project_instructions = _project.instructions.strip()
        except Exception:  # noqa: BLE001 - project instructions are best-effort
            project_instructions = None
        # Project instructions scope the run; personal instructions refine it.
        model_input = apply_instructions(apply_instructions(message, saved_instructions), project_instructions)
        if attachments:
            import base64

            from pydantic_ai import BinaryContent

            # Text-like and Office documents are flattened to text so every
            # model can read them. Images, PDFs, audio, and video stay native.
            extracted: list[str] = []
            media_parts: list = []
            for att in attachments:
                try:
                    raw = base64.b64decode(att["data"])
                except Exception:  # noqa: BLE001 - schema already validated base64
                    continue
                text = extract_attachment_text(att["name"], att["media_type"], raw)
                if text is not None:
                    if text.strip():
                        extracted.append(wrap_attachment_text(att["name"], att["media_type"], text))
                    continue
                try:
                    media_parts.append(BinaryContent(data=raw, media_type=att["media_type"]))
                except Exception:  # noqa: BLE001 - unsupported native media type
                    continue
            if extracted:
                model_input = (
                    f"{model_input}\n\n"
                    "The user attached the following files. Treat their contents as "
                    "part of the request:\n\n" + "\n\n".join(extracted)
                )
            user_content = [model_input, *media_parts] if media_parts else model_input
        else:
            user_content = model_input

        try:
            usage_limits = UsageLimits(
                total_tokens_limit=self._rt.settings.per_run_token_limit or None
            )
            async with agent.iter(
                user_content,
                deps=deps,
                message_history=history_models or None,
                model=model,
                model_settings=model_settings,
                usage_limits=usage_limits,
                cancellation_token=cancellation_token,
            ) as agent_run:
                tool_calls: list[dict[str, Any]] = []
                async for out in _stream_nodes(
                    agent, agent_run, text_parts, collector, thinking_parts, final_text_parts
                ):
                    if out.get("type") == "tool.started":
                        data = out.get("data") or {}
                        tool_calls.append({"tool": data.get("tool"), "args": data.get("args") or {}})
                    yield out
                    if first_token_ms is None and out.get("type") == "text.delta":
                        first_token_ms = int((time.time() - run_t0) * 1000)
                usage = agent_run.usage
                _apply_usage_cost(usage, model, self._rt.settings.default_model)
                result = agent_run.result
                if result is not None and not isinstance(result.output, DeferredToolRequests):
                    await self._save_conversation_history(conversation.id, agent_run.all_messages())
                if result is not None and isinstance(result.output, DeferredToolRequests):
                    deferred = result.output
                    paused_messages = list(agent_run.all_messages())
        except RunCancelled:
            await self.finish_run(run.id, status="cancelled", usage=usage, spans=collector.normalize(), ttft_ms=first_token_ms)
            await self._rt.events.publish("run.cancelled", {"run_id": run.id}, tenant_id=deps.tenant_id)
            yield {"type": "run.finished", "data": {"run_id": run.id, "status": "cancelled"}}
            return
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            await self._rt.audit.record(
                actor=deps.user_id, action="run.failed", resource=f"run:{run.id}",
                tenant_id=deps.tenant_id, detail={"error": error},
            )

        final_text = "".join(final_text_parts)
        if error is not None:
            await self.finish_run(run.id, status="failed", usage=usage, error=error, spans=collector.normalize(), ttft_ms=first_token_ms)
            await self._rt.events.publish("run.failed", {"run_id": run.id, "error": error}, tenant_id=deps.tenant_id)
            yield {"type": "run.failed", "data": {"run_id": run.id, "error": error}}
            return

        native_web_used, native_results = native_web_evidence(agent_run.all_messages())
        used_web = native_web_used or any(str(c.get("tool", "")) in WEB_TOOLS for c in tool_calls)
        sources = extract_sources(final_text, tool_calls, native_results)
        followups = build_followups(message, final_text, used_web=used_web)
        thinking = _final_thinking(thinking_parts)
        await self.add_message(
            conversation.id, "assistant",
            {
                "text": final_text, "sources": sources, "followups": followups,
                "model": model_label, **({"thinking": thinking} if thinking else {}),
            },
            run_id=run.id, agent_slug=run_agent_slug,
        )

        if deferred is not None and deferred.approvals:
            approval_map: dict[str, str] = {}
            records = await self._rt.approvals.create_for_deferred_requests(
                run_id=run.id,
                conversation_id=conversation.id,
                deferred=deferred,
                tenant_id=deps.tenant_id,
            )
            for record in records:
                approval_map[record.tool_call_id] = record.id
            await self.finish_run(
                run.id,
                status="awaiting_approval",
                usage=usage,
                deferred={
                    "tool_call_ids": list(approval_map.keys()),
                    "approval_map": approval_map,
                },
                history=paused_messages,
                spans=collector.normalize(),
                ttft_ms=first_token_ms,
            )
            await self._rt.events.publish(
                "approval.requested",
                {"run_id": run.id, "approvals": [r.id for r in records]},
                tenant_id=deps.tenant_id,
            )
            yield {
                "type": "approval.requested",
                "data": {
                    "run_id": run.id,
                    "approvals": [
                        {
                            "approval_id": r.id,
                            "tool_name": r.tool_name,
                            "args": r.args_json,
                        }
                        for r in records
                    ],
                },
            }
            yield {"type": "run.finished", "data": {"run_id": run.id, "status": "awaiting_approval"}}
            return

        await self.finish_run(run.id, status="completed", usage=usage, spans=collector.normalize(), ttft_ms=first_token_ms)
        await self._rt.usage.record_run_usage(
            run_id=run.id,
            agent_slug=run_agent_slug,
            model=model_label,
            tenant_id=deps.tenant_id,
            usage=usage,
            user_id=deps.user_id,
        )
        yield {"type": "text.done", "data": {"text": final_text}}
        yield {
            "type": "usage",
            "data": _usage_dict(usage),
        }
        if thinking:
            yield {"type": "thinking.done", "data": {"run_id": run.id, "thinking": thinking}}
        if sources:
            yield {"type": "sources", "data": {"run_id": run.id, "sources": sources}}
        yield {"type": "followups", "data": {"run_id": run.id, "followups": followups}}
        yield {"type": "run.finished", "data": {"run_id": run.id, "status": "completed"}}

    async def _save_conversation_history(self, conversation_id: str, messages: Any) -> None:
        """Snapshot the full pydantic-ai message list for true multi-turn context.

        Oversized binary/text parts are offloaded into the content-addressed
        media store and stored as reference markers (`media+sha256://…`), so a
        huge upload doesn't bloat the stored history. `_load_conversation_history`
        restores the bytes before the messages go back to the model.
        """
        import json as _json

        from pydantic_ai.messages import ModelMessagesTypeAdapter

        raw_messages: Any = _json.loads(ModelMessagesTypeAdapter.dump_json(list(messages)))
        if self._rt.settings.media_offload_enabled:
            try:
                from pydantic_ai_harness.media import DiskMediaStore, externalize_media

                store = DiskMediaStore(self._rt.settings.media_store_path)
                raw_messages = await externalize_media(
                    raw_messages,
                    media_store=store,
                    threshold_bytes=self._rt.settings.media_offload_threshold_bytes,
                )
            except Exception:  # noqa: BLE001 - offloading is best-effort
                pass
        raw = _json.dumps(raw_messages)
        async with self._db.session() as session:
            conv = await session.get(ConversationRecord, conversation_id)
            if conv is not None:
                conv.history_json = raw

    async def _load_conversation_history(self, raw_history: str) -> Any:
        """Validate stored history, restoring offloaded media bytes first so
        the model adapter sees well-formed message parts."""
        import json as _json

        from pydantic_ai.messages import ModelMessagesTypeAdapter

        node: Any = _json.loads(raw_history)
        if self._rt.settings.media_offload_enabled:
            try:
                from pydantic_ai_harness.media import DiskMediaStore, restore_media

                node = await restore_media(
                    node, media_store=DiskMediaStore(self._rt.settings.media_store_path)
                )
            except Exception:  # noqa: BLE001 - restore is best-effort
                pass
        return ModelMessagesTypeAdapter.validate_json(_json.dumps(node))

    async def cancel_run(self, run_id: str) -> bool:
        handle = ACTIVE_RUNS.get(run_id)
        if handle is None:
            return False
        handle.cancel()
        # Also cancel through pydantic-ai so the run graph unwinds cleanly.
        token = ACTIVE_TOKENS.get(run_id)
        if token is not None:
            token.cancel()
        ACTIVE_RUNS.pop(run_id, None)
        ACTIVE_TOKENS.pop(run_id, None)
        await self.finish_run(run_id, status="cancelled", error="Cancelled by operator")
        return True

    # -- Trace replay -----------------------------------------------------------

    async def replay_run(
        self,
        run_id: str,
        deps: AgentDeps,
        *,
        model_override: str | None = None,
        agent_slug: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Regenerate a stored run's answer from its recorded message history.

        Replay = quality loop: pick any run in Observability, optionally swap
        the model, and re-run the exact recorded context. The trailing model
        response is stripped so the agent produces a fresh answer; the result
        is stored as a new run on the same conversation.
        """
        async with self._db.session() as session:
            original = await session.get(RunRecord, run_id)
        if original is None:
            yield {"type": "run.failed", "data": {"run_id": run_id, "error": "Run not found"}}
            return

        run_agent_slug = agent_slug or original.agent_slug or "orchestrator"
        agent = self._rt.specialists.get(run_agent_slug) or self._rt.orchestrator

        history_models: list[ModelMessage] = []
        user_fallback: str | None = None
        if original.history_json:
            history_models = list(ModelMessagesTypeAdapter.validate_json(original.history_json))
            # Strip trailing model response(s) so the agent regenerates the answer.
            while history_models and isinstance(history_models[-1], ModelResponse):
                history_models.pop()
        if not history_models:
            # No usable history (e.g. a run that failed early): fall back to the
            # original user input as a fresh prompt.
            async with self._db.session() as session:
                row = (
                    await session.execute(
                        select(MessageRecord)
                        .where(MessageRecord.run_id == run_id, MessageRecord.role == "user")
                        .order_by(MessageRecord.created_at)
                        .limit(1)
                    )
                ).scalars()
                first = row.first()
            user_fallback = (first.content or {}).get("text") if first else None
            if not user_fallback:
                yield {"type": "run.failed", "data": {"run_id": run_id, "error": "Nothing to replay (no history or input)"}}
                return

        # Model resolution identical to a normal run.
        model = await self._rt.connections.resolve_chat_model(deps.user_id)
        if model is None and model_override:
            from pydantic_ai.models import infer_model

            try:
                model = infer_model(model_override)
            except Exception as exc:  # noqa: BLE001 - unknown model falls back
                await self._rt.events.publish(
                    "run.model_fallback", {"requested": model_override, "error": str(exc)},
                    tenant_id=deps.tenant_id,
                )
                model = None
        model_label = (
            f"{type(model).__name__}:{getattr(model, 'model_name', '')}"
            if model is not None
            else (model_override or self._rt.settings.default_model)
        ) + " (replay)"

        run = await self.create_run(
            original.conversation_id or "",
            tenant_id=deps.tenant_id,
            user_id=deps.user_id,
            model=model_label,
            agent_slug=run_agent_slug,
        )
        deps.run_id = run.id
        deps.conversation_id = original.conversation_id

        cancellation_token = CancellationToken()
        ACTIVE_RUNS[run.id] = asyncio.current_task()
        ACTIVE_TOKENS[run.id] = cancellation_token
        await self._rt.events.publish(
            "run.replayed",
            {"run_id": run.id, "replayed_run_id": run_id, "model": model_label, "agent": run_agent_slug},
            tenant_id=deps.tenant_id,
        )
        yield {
            "type": "run.started",
            "data": {
                "run_id": run.id,
                "conversation_id": original.conversation_id,
                "agent": run_agent_slug,
                "replay_of": run_id,
            },
        }
        text_parts: list[str] = []
        final_text_parts: list[str] = []
        thinking_parts: list[str] = []
        usage = None
        error: str | None = None
        collector = SpanCollector()
        first_token_ms: int | None = None
        run_t0 = time.time()
        try:
            async with agent.iter(
                user_fallback if user_fallback is not None else None,
                deps=deps,
                message_history=history_models or None,
                model=model,
                usage_limits=UsageLimits(total_tokens_limit=self._rt.settings.per_run_token_limit or None),
                cancellation_token=cancellation_token,
            ) as agent_run:
                tool_calls: list[dict[str, Any]] = []
                async for out in _stream_nodes(
                    agent, agent_run, text_parts, collector, thinking_parts, final_text_parts
                ):
                    if out.get("type") == "tool.started":
                        data = out.get("data") or {}
                        tool_calls.append({"tool": data.get("tool"), "args": data.get("args") or {}})
                    yield out
                    if first_token_ms is None and out.get("type") == "text.delta":
                        first_token_ms = int((time.time() - run_t0) * 1000)
                usage = agent_run.usage
                _apply_usage_cost(usage, model, self._rt.settings.default_model)
                if original.conversation_id and agent_run.result is not None:
                    await self._save_conversation_history(original.conversation_id, agent_run.all_messages())
        except RunCancelled:
            await self.finish_run(run.id, status="cancelled", usage=usage, spans=collector.normalize(), ttft_ms=first_token_ms)
            yield {"type": "run.finished", "data": {"run_id": run.id, "status": "cancelled"}}
            return
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        finally:
            ACTIVE_RUNS.pop(run.id, None)
            ACTIVE_TOKENS.pop(run.id, None)

        final_text = "".join(final_text_parts)
        if error is not None:
            await self.finish_run(run.id, status="failed", usage=usage, error=error, spans=collector.normalize(), ttft_ms=first_token_ms)
            yield {"type": "run.failed", "data": {"run_id": run.id, "error": error}}
            return
        native_web_used, native_results = native_web_evidence(agent_run.all_messages())
        used_web = native_web_used or any(str(c.get("tool", "")) in WEB_TOOLS for c in tool_calls)
        sources = extract_sources(final_text, tool_calls, native_results)
        followups = build_followups(user_fallback or "", final_text, used_web=used_web)
        thinking = _final_thinking(thinking_parts)
        if original.conversation_id:
            await self.add_message(
                original.conversation_id, "assistant",
                {
                    "text": final_text, "sources": sources, "followups": followups,
                    **({"thinking": thinking} if thinking else {}),
                },
                run_id=run.id, agent_slug=run_agent_slug,
            )
        await self.finish_run(run.id, status="completed", usage=usage, spans=collector.normalize(), ttft_ms=first_token_ms)
        await self._rt.usage.record_run_usage(
            run_id=run.id, agent_slug=run_agent_slug, model=model_label,
            tenant_id=deps.tenant_id, usage=usage, user_id=deps.user_id,
        )
        yield {"type": "text.done", "data": {"text": final_text}}
        yield {"type": "usage", "data": _usage_dict(usage)}
        if thinking:
            yield {"type": "thinking.done", "data": {"run_id": run.id, "thinking": thinking}}
        if sources:
            yield {"type": "sources", "data": {"run_id": run.id, "sources": sources}}
        yield {"type": "followups", "data": {"run_id": run.id, "followups": followups}}
        yield {"type": "run.finished", "data": {"run_id": run.id, "status": "completed", "replay_of": run_id}}

    # -- Approval resume --------------------------------------------------------

    async def resume_run(
        self, run_id: str, deps: AgentDeps, *, model_override: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Continue a run paused for approval, with the recorded decisions."""
        state = await self.load_run_state(run_id)
        if state is None:
            yield {"type": "run.failed", "data": {"run_id": run_id, "error": "No paused run found"}}
            return
        history, deferred_info, run_agent_slug = state
        # Re-register for cancellation while the resumed run is in flight.
        cancellation_token = CancellationToken()
        ACTIVE_RUNS[run_id] = asyncio.current_task()
        ACTIVE_TOKENS[run_id] = cancellation_token
        try:
            async for event in self._resume_run(
                run_id, deps, history, deferred_info, run_agent_slug, cancellation_token,
                model_override=model_override,
            ):
                yield event
        finally:
            ACTIVE_RUNS.pop(run_id, None)
            ACTIVE_TOKENS.pop(run_id, None)

    async def _resume_run(
        self,
        run_id: str,
        deps: AgentDeps,
        history: list[ModelMessage],
        deferred_info: dict[str, Any],
        run_agent_slug: str | None,
        cancellation_token: Any | None = None,
        model_override: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        agent = (
            self._rt.specialists[run_agent_slug]
            if run_agent_slug in self._rt.specialists
            else self._rt.orchestrator
        )
        # Same model resolution as a fresh turn, so a resumed run cannot silently
        # fall back to a platform default the user did not choose.
        model = await self._rt.connections.resolve_chat_model(deps.user_id)
        if model is None and model_override:
            from pydantic_ai.models import infer_model

            try:
                model = infer_model(model_override)
            except Exception:  # noqa: BLE001 - unknown model falls back to default
                model = None
        conversation_id = deps.conversation_id or ""
        approval_map: dict[str, str] = deferred_info.get("approval_map", {})
        decisions: dict[str, Any] = {}
        for tool_call_id, approval_id in approval_map.items():
            record = await self._rt.approvals.get(approval_id)
            if record is not None and record.status in ("approved", "denied"):
                decisions[tool_call_id] = record.status == "approved"
                # An approved plan releases the gate for the rest of the run; a
                # denial keeps it raised so the revised plan re-presents.
                if record.status == "approved" and record.tool_name == "write_plan":
                    deps.plan_gate = False

        results = DeferredToolResults()
        results.approvals = decisions
        yield {"type": "run.started", "data": {"run_id": run_id, "resumed": True, "agent": run_agent_slug}}

        text_parts: list[str] = []
        final_text_parts: list[str] = []
        thinking_parts: list[str] = []
        usage = None
        error: str | None = None
        try:
            async with agent.iter(
                deferred_tool_results=results,
                message_history=history,
                deps=deps,
                model=model,
                usage_limits=UsageLimits(total_tokens_limit=self._rt.settings.per_run_token_limit or None),
                cancellation_token=cancellation_token,
            ) as agent_run:
                tool_calls: list[dict[str, Any]] = []
                async for out in _stream_nodes(
                    agent, agent_run, text_parts, None, thinking_parts, final_text_parts
                ):
                    if out.get("type") == "tool.started":
                        data = out.get("data") or {}
                        tool_calls.append({"tool": data.get("tool"), "args": data.get("args") or {}})
                    yield out
                usage = agent_run.usage
                _apply_usage_cost(usage, model, self._rt.settings.default_model)
                if conversation_id:
                    await self._save_conversation_history(conversation_id, agent_run.all_messages())
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"

        final_text = "".join(final_text_parts)
        if error is not None:
            await self.finish_run(run_id, status="failed", usage=usage, error=error)
            yield {"type": "run.failed", "data": {"run_id": run_id, "error": error}}
            return
        native_web_used, native_results = native_web_evidence(agent_run.all_messages())
        used_web = native_web_used or any(str(c.get("tool", "")) in WEB_TOOLS for c in tool_calls)
        sources = extract_sources(final_text, tool_calls, native_results)
        followups = build_followups("", final_text, used_web=used_web)
        thinking = _final_thinking(thinking_parts)
        if conversation_id:
            await self.add_message(
                conversation_id, "assistant",
                {
                    "text": final_text, "sources": sources, "followups": followups,
                    **({"thinking": thinking} if thinking else {}),
                },
                run_id=run_id, agent_slug=run_agent_slug or "orchestrator",
            )
        await self.finish_run(run_id, status="completed", usage=usage)
        await self._rt.usage.record_run_usage(
            run_id=run_id, agent_slug=run_agent_slug or "orchestrator",
            model=self._rt.settings.default_model, tenant_id=deps.tenant_id, usage=usage, user_id=deps.user_id,
        )
        yield {"type": "text.done", "data": {"text": final_text}}
        yield {"type": "usage", "data": _usage_dict(usage)}
        if thinking:
            yield {"type": "thinking.done", "data": {"run_id": run_id, "thinking": thinking}}
        if sources:
            yield {"type": "sources", "data": {"run_id": run_id, "sources": sources}}
        yield {"type": "followups", "data": {"run_id": run_id, "followups": followups}}
        yield {"type": "run.finished", "data": {"run_id": run_id, "status": "completed"}}


# --- helpers -------------------------------------------------------------------


def _final_thinking(thinking_parts: list[str] | None) -> str:
    """Join streamed thinking content, capped for persistence."""
    text = "".join(thinking_parts or "").strip()
    if len(text) > THINKING_PERSIST_CHARS:
        text = text[:THINKING_PERSIST_CHARS].rstrip() + "\n…[truncated]"
    return text


def _run_start(run: RunRecord) -> float | None:
    created = getattr(run, "created_at", None)
    if created is None:
        return None
    if created.tzinfo is None:
        # SQLite drops tzinfo on read-back; stored values are UTC.
        created = created.replace(tzinfo=UTC)
    return created.timestamp()


def _to_model_messages(rows: list[dict[str, Any]]) -> list[ModelMessage]:
    """Best-effort conversion of stored message content into ModelMessages."""
    messages: list[ModelMessage] = []
    for row in rows:
        text = row.get("text")
        role = row.get("role")
        if not text:
            continue
        if role == "user":
            from pydantic_ai.messages import ModelRequest, UserPromptPart

            messages.append(ModelRequest(parts=[UserPromptPart(content=text)]))
        elif role == "assistant":
            from pydantic_ai.messages import ModelResponse

            messages.append(ModelResponse(parts=[TextPart(content=text)]))
    return messages


def _apply_usage_cost(usage: Any, model: Any, fallback_model: str | None = None) -> None:
    """Fill `usage.cost` when the registry has no price for the model.

    `SpendLimits` already enforces the USD ceiling through the same fallback;
    this keeps the persisted run cost and the usage screen consistent with it.
    When `model` is None the agent ran on its platform-default model string.
    """
    if usage is None or getattr(usage, "cost", None):
        return
    model_name = getattr(model, "model_name", None)
    provider_name = getattr(model, "system", None)
    if not model_name and fallback_model:
        provider_name, _, model_name = fallback_model.partition(":")
    if not model_name:
        return
    from voicefirst.pricing import price_usage

    cost = price_usage(usage, model_name=model_name, provider_name=provider_name)
    if cost is not None:
        usage.cost = cost


def _usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    return {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        "requests": int(getattr(usage, "requests", 0) or 0),
        "tool_calls": int(getattr(usage, "tool_calls", 0) or 0),
        "cost_usd": float(getattr(usage, "cost", 0.0) or 0.0),
    }


def _translate_event(
    event: Any,
    text_parts: list[str],
    thinking_parts: list[str] | None = None,
    final_text_parts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Convert pydantic-ai graph events into SSE-ready dicts."""
    out: list[dict[str, Any]] = []
    if isinstance(event, PartStartEvent):
        part = event.part
        if isinstance(part, TextPart):
            out.append({"type": "text.delta", "data": {"delta": part.content}})
            text_parts.append(part.content)
            if final_text_parts is not None:
                final_text_parts.append(part.content)
        elif isinstance(part, ThinkingPart):
            if part.content:
                out.append({"type": "thinking.delta", "data": {"delta": part.content}})
                if thinking_parts is not None:
                    thinking_parts.append(part.content)
        # Function tool calls are surfaced from the call-tools node, where the
        # arguments are fully assembled. Emitting here too duplicated every
        # `tool.started` event and usually reported empty args.
        elif isinstance(part, NativeToolCallPart):
            # Server-side tools (Anthropic web search, etc.) never reach the
            # tool-call node, so surface them here as normal activity.
            out.append(
                {
                    "type": "tool.started",
                    "data": {"tool": part.tool_name, "args": _safe_args(part.args)},
                }
            )
        elif isinstance(part, NativeToolReturnPart):
            out.append({"type": "tool.finished", "data": {"tool": part.tool_name}})
    elif isinstance(event, PartDeltaEvent):
        delta = event.delta
        if isinstance(delta, TextPartDelta):
            content = delta.content_delta or ""
            if content:
                out.append({"type": "text.delta", "data": {"delta": content}})
                text_parts.append(content)
                if final_text_parts is not None:
                    final_text_parts.append(content)
        elif isinstance(delta, ThinkingPartDelta):
            content = delta.content_delta or ""
            if content:
                out.append({"type": "thinking.delta", "data": {"delta": content}})
                if thinking_parts is not None:
                    thinking_parts.append(content)
    return out


async def _stream_nodes(
    agent: Any,
    agent_run: Any,
    text_parts: list[str],
    collector: SpanCollector | None = None,
    thinking_parts: list[str] | None = None,
    final_text_parts: list[str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield SSE events from a running agent run by streaming its nodes."""
    async for node in agent_run:
        if agent.is_model_request_node(node):
            # Only the last model request is the user-facing answer. Earlier
            # requests may contain "let me search..." commentary before a tool
            # call; that text must not be concatenated into the deliverable.
            if final_text_parts is not None:
                final_text_parts.clear()
            native_open: dict[str, int] = {}
            model_span = collector.start("model", "model") if collector else None
            async with node.stream(agent_run.ctx) as node_stream:
                async for ev in node_stream:
                    for out in _translate_event(ev, text_parts, thinking_parts, final_text_parts):
                        if out.get("type") == "tool.started":
                            name = out.get("data", {}).get("tool")
                            if name:
                                native_open[name] = native_open.get(name, 0) + 1
                        elif out.get("type") == "tool.finished":
                            name = out.get("data", {}).get("tool")
                            if name and native_open.get(name):
                                native_open[name] -= 1
                        yield out
            # Anthropic returns built-in tool results inline instead of
            # streaming a NativeToolReturnPart. The request is finished either
            # way, so close anything still open instead of leaving the UI with
            # a spinner that never stops.
            for name, count in native_open.items():
                for _ in range(count):
                    yield {"type": "tool.finished", "data": {"tool": name}}
            if collector and model_span:
                collector.end(model_span)
        if agent.is_call_tools_node(node):
            tools_span = collector.start("tools", "tools") if collector else None
            open_tools: dict[str, str] = {}
            async with node.stream(agent_run.ctx) as node_stream:
                async for tool_event in node_stream:
                    part = getattr(tool_event, "part", None)
                    call_id = getattr(part, "tool_call_id", None)
                    # `part` is the ToolCallPart for call events and the
                    # ToolReturnPart/RetryPromptPart for result events; both
                    # carry `tool_name`. ToolCallEvent itself has none.
                    name = getattr(part, "tool_name", None)
                    event_kind = getattr(tool_event, "event_kind", "")
                    if event_kind == "function_tool_result":  # FunctionToolResultEvent
                        if collector and call_id and call_id in open_tools:
                            collector.end(open_tools.pop(call_id))
                        if name:
                            yield {"type": "tool.finished", "data": {"tool": name}}
                    elif event_kind == "function_tool_call":  # FunctionToolCallEvent
                        if collector and call_id:
                            open_tools[call_id] = collector.start(
                                name or "tool", "tool", _safe_args(getattr(part, "args", {}) or {})
                            )
                        if name:
                            args = _safe_args(getattr(part, "args", {}) or {})
                            yield {
                                "type": "tool.started",
                                "data": {"tool": name, "args": args},
                            }
                            # Structured plan events so the UI can render the
                            # orchestrator's live plan card.
                            if name == "write_plan" and isinstance(args, dict) and args.get("items"):
                                yield {"type": "plan.proposed", "data": {"items": args["items"]}}
                            elif name == "update_task_status" and isinstance(args, dict):
                                yield {
                                    "type": "plan.task",
                                    "data": {"task_id": args.get("task_id"), "status": args.get("status")},
                                }
                            elif name == "update_task_statuses" and isinstance(args, dict):
                                for update in args.get("updates", []) or []:
                                    if isinstance(update, dict):
                                        yield {
                                            "type": "plan.task",
                                            "data": {
                                                "task_id": update.get("task_id"),
                                                "status": update.get("status"),
                                            },
                                        }
            if collector and tools_span:
                collector.end(tools_span)


def _safe_args(args: Any) -> dict[str, Any]:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {"raw": args}
        except json.JSONDecodeError:
            return {"raw": args[:200]}
    return {}
