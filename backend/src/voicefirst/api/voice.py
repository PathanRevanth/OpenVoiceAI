"""Voice: browser WebSocket bridge into a two-layer spoken conversation.

Layer 1 — voice model (Gemini Live / OpenAI Realtime):
    Mic PCM in, companion speech out. This model only talks.

Layer 2 — chat orchestrator (Anthropic / whatever the composer selected):
    Final user transcripts are handed off in the background. Specialists,
    artifacts, and approvals run there. Spoken cues are injected back into
    the live session so the caller hears progress like a human colleague.

Protocol (JSON frames over `/api/voice/ws`):

Browser -> Server:
    {"type": "start", "data": {model, voice, chat_model, conversation_id}}
    {"type": "pcm", "data": "<base64>"}
    {"type": "text", "data": "..."}
    {"type": "stop"}

Server -> Browser:
    {"type": "state", "data": {...}}
    {"type": "transcript", "data": {...}}
    {"type": "speech", "data": "<base64>"}
    plus the same run.* / text.delta / tool.* events as /api/chat SSE.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from array import array
from contextlib import AsyncExitStack
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic_ai.realtime import (
    RealtimeResponseInterruptedEvent,
    RealtimeSessionErrorEvent,
    RealtimeTurnCompleteEvent,
)

from voicefirst.agents.deps import AgentDeps
from voicefirst.api.chat_service import ChatService
from voicefirst.api.voice_handoff import should_handoff_to_orchestrator, spoken_control_cue
from voicefirst.control_plane.auth import IdentityService
from voicefirst.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/voice/catalog", tags=["voice"])
async def voice_catalog() -> dict:
    """Voice + chat options per provider for the composer selectors.

    `available` reflects whether this deployment holds a key for the provider's
    realtime API; Anthropic ships no realtime speech-to-speech API at all, so it
    is never voice-capable (its chat models still work — voice turns fall back
    to browser speech).
    """
    settings = get_settings()
    from voicefirst.control_plane.connections import (
        CHAT_MODELS,
        PROVIDERS,
        VOICE_CATALOG,
        VOICE_MODELS,
    )

    key_for = {
        "openai": settings.openai_api_key,
        "google": settings.google_api_key,
        "anthropic": settings.anthropic_api_key,
    }
    providers: dict[str, Any] = {}
    for pid, meta in PROVIDERS.items():
        voice_capable = pid in VOICE_CATALOG
        providers[pid] = {
            "label": meta["label"],
            "tile": meta.get("tile"),
            "key_present": bool(key_for.get(pid)),
            "voice_capable": voice_capable,
            "available": voice_capable and bool(key_for.get(pid)),
            "voices": VOICE_CATALOG.get(pid, []),
            "voice_models": VOICE_MODELS.get(pid, []),
            "reason": None
            if voice_capable
            else "Anthropic has no realtime speech-to-speech API — voice turns use browser speech.",
        }
    return {
        "default_realtime_model": settings.realtime_model,
        "default_chat_model": settings.default_model,
        "providers": providers,
        "chat_models": CHAT_MODELS,
    }


def _peak_pcm16(data: bytes) -> int:
    """Peak amplitude (0-32768) of a little-endian PCM16 chunk, for local VAD."""
    usable = len(data) & ~1
    if not usable:
        return 0
    samples = array("h")
    samples.frombytes(data[:usable])
    return max(max(samples), -min(samples))


@router.websocket("/api/voice/ws")
async def voice_ws(ws: WebSocket) -> None:
    try:
        identity_svc: IdentityService = ws.app.state.identity
        identity = await identity_svc.from_websocket(ws)
    except Exception:  # noqa: BLE001 - auth failure closes below
        await ws.close(code=4401, reason="Unauthorized")
        return

    await ws.accept()
    runtime = ws.app.state.runtime
    settings = runtime.settings
    deps = AgentDeps.from_identity(identity, runtime=runtime, channel="voice")

    stack = AsyncExitStack()
    session: Any = None
    tasks: list[asyncio.Task] = []
    mic_queue: asyncio.Queue[bytes | None] | None = None
    # Voice-quality metrics for this connection (see VoiceAnalyticsService).
    voice_session: Any = None
    voice_started_mono: float | None = None
    voice_metrics: dict[str, Any] = {
        "turns": 0,
        "barge_ins": 0,
        "ttfw_ms": None,
        "last_speaker": None,
        "last_user_at": None,
        "last_voice_at": None,
        "turn_voice_at": None,
        "awaiting_audio": False,
    }

    async def end_voice_session(status: str = "ended", error: str | None = None) -> None:
        nonlocal voice_session
        if voice_session is None:
            return
        (
            int((asyncio.get_running_loop().time() - (voice_started_mono or 0)) * 1000)
            if voice_started_mono is not None
            else None
        )
        await runtime.voice_analytics.update_metrics(
            voice_session.id,
            turns=voice_metrics["turns"],
            ttfw_ms=voice_metrics["ttfw_ms"],
            barge_ins=voice_metrics["barge_ins"],
        )
        await runtime.voice_analytics.end_session(voice_session.id, status=status, error=error)
        voice_session = None

    async def send(frame: dict[str, Any]) -> None:
        try:
            await ws.send_text(json.dumps(frame, default=str))
        except Exception:  # noqa: BLE001 - client gone
            pass

    conversation_id: str | None = None
    chat_model_override: str | None = None
    background_run_id: str | None = None
    background_task: asyncio.Task | None = None
    debounce_task: asyncio.Task | None = None
    pending_utterances: list[str] = []

    async def cue_voice(event_type: str, data: dict[str, Any]) -> None:
        if session is None:
            return
        note = spoken_control_cue(event_type, data)
        if not note:
            return
        try:
            await session.send(note)
        except Exception:  # noqa: BLE001
            pass

    async def run_background(text: str) -> None:
        nonlocal conversation_id, background_run_id
        chat = ChatService(runtime.db, runtime)
        if background_run_id:
            try:
                await chat.cancel_run(background_run_id)
            except Exception:  # noqa: BLE001
                pass
            background_run_id = None
        conv = await chat.get_or_create_conversation(
            conversation_id,
            tenant_id=deps.tenant_id,
            user_id=deps.user_id,
            channel="voice",
            title_from=text,
        )
        conversation_id = conv.id
        await send({"type": "conversation", "data": {"id": conv.id}})
        summary = ""
        try:
            async for event in chat.stream_run(
                message=text,
                conversation=conv,
                deps=deps,
                model_override=chat_model_override,
            ):
                etype = str(event.get("type") or "")
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                await send({"type": etype, "data": data})
                if etype == "run.started":
                    background_run_id = str(data.get("run_id") or "") or None
                elif etype == "text.done":
                    summary = str(data.get("text") or summary)
                elif etype in ("run.finished", "run.failed"):
                    payload = {**data, "summary": summary}
                    # Only the result is spoken. Injecting "I'm on it" / tool
                    # progress as user-role content makes the live model take a
                    # *second* turn over its own answer — the caller hears a
                    # hesitation and a restart. The voice model already
                    # acknowledged the request in its own reply.
                    await cue_voice(etype, payload)
                    if etype == "run.finished":
                        background_run_id = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            await send({"type": "state", "data": {"error": f"background run failed: {exc}"}})
            await cue_voice("run.failed", {"error": str(exc)})

    def schedule_handoff(text: str) -> None:
        nonlocal debounce_task, background_task
        pending_utterances.append(text)

        async def flush() -> None:
            nonlocal background_task
            await asyncio.sleep(0.7)
            combined = " ".join(pending_utterances).strip()
            pending_utterances.clear()
            if not should_handoff_to_orchestrator(combined):
                return
            if background_task is not None and not background_task.done():
                background_task.cancel()
            background_task = asyncio.create_task(run_background(combined))

        if debounce_task is not None:
            debounce_task.cancel()
        debounce_task = asyncio.create_task(flush())

    async def pump_transcripts(parts: Any) -> None:
        """Live captions: stream cumulative partials so the thread keeps up."""
        try:
            async for transcript in parts:
                speaker = getattr(transcript, "speaker", "assistant")
                text = getattr(transcript, "transcript", "") or getattr(transcript, "delta", "") or ""
                if not text:
                    continue
                if speaker == "user":
                    voice_metrics["last_user_at"] = asyncio.get_running_loop().time()
                    # Freeze the last voiced mic frame for this turn: that is the
                    # caller's real speech end, which is what latency feels like.
                    voice_metrics["turn_voice_at"] = voice_metrics.get("last_voice_at")
                    voice_metrics["awaiting_audio"] = True
                    voice_metrics["last_speaker"] = "user"
                else:
                    voice_metrics["last_speaker"] = "assistant"
                await send(
                    {
                        "type": "transcript",
                        "data": {
                            "speaker": speaker,
                            "text": text,
                            "final": False,
                            "index": getattr(transcript, "index", None),
                        },
                    }
                )
        except Exception as exc:  # noqa: BLE001
            await send({"type": "state", "data": {"error": f"transcript stream ended: {exc}"}})

    async def pump_final_transcripts(parts: Any) -> None:
        """Completed caller turns: hand the finished utterance to the orchestrator once."""
        try:
            async for transcript in parts:
                if getattr(transcript, "speaker", "") != "user":
                    continue
                text = (
                    getattr(transcript, "transcript", None)
                    or getattr(transcript, "content", None)
                    or getattr(transcript, "text", None)
                    or ""
                )
                if text:
                    schedule_handoff(str(text))
        except Exception:  # noqa: BLE001 - final transcript view closing is not fatal
            pass

    async def pump_audio(chunks: Any) -> None:
        try:
            async for chunk in chunks:
                if not chunk:
                    continue
                new_turn = False
                if voice_metrics.get("awaiting_audio"):
                    new_turn = True
                    last_user = voice_metrics.get("turn_voice_at") or voice_metrics.get("last_user_at")
                    if last_user is not None:
                        latency = max(0, int((asyncio.get_running_loop().time() - last_user) * 1000))
                        voice_metrics["ttfw_ms"] = latency
                        logger.info(
                            "voice first audio %sms after caller stopped speaking (model=%s)",
                            latency,
                            voice_metrics.get("model") or "?",
                        )
                    voice_metrics["awaiting_audio"] = False
                    voice_metrics["turns"] = int(voice_metrics.get("turns") or 0) + 1
                    if voice_session is not None:
                        await runtime.voice_analytics.update_metrics(
                            voice_session.id,
                            turns=voice_metrics["turns"],
                            ttfw_ms=voice_metrics["ttfw_ms"],
                            barge_ins=voice_metrics["barge_ins"],
                        )
                if new_turn:
                    await send({"type": "turn", "data": {"state": "speaking"}})
                await send({"type": "speech", "data": base64.b64encode(chunk).decode()})
        except Exception as exc:  # noqa: BLE001
            await send({"type": "state", "data": {"error": f"speech stream ended: {exc}"}})

    async def drain_session(sess: Any) -> None:
        """Drive the realtime event pump (required alongside stream_audio)."""
        try:
            async for event in sess:
                if isinstance(event, RealtimeResponseInterruptedEvent):
                    # Barge-in: the provider cut the reply the instant the caller
                    # spoke. Tell the browser to drop queued audio now.
                    voice_metrics["barge_ins"] = int(voice_metrics.get("barge_ins") or 0) + 1
                    logger.info("voice barge-in (model=%s)", voice_metrics.get("model") or "?")
                    await send({"type": "interrupted", "data": {}})
                    await send({"type": "turn", "data": {"state": "listening"}})
                elif isinstance(event, RealtimeTurnCompleteEvent):
                    await send({"type": "turn", "data": {"state": "done"}})
                elif isinstance(event, RealtimeSessionErrorEvent):
                    await send({"type": "state", "data": {"error": str(event)}})
        except Exception as exc:  # noqa: BLE001
            await send({"type": "state", "data": {"error": f"realtime session ended: {exc}"}})

    await send(
        {
            "type": "state",
            "data": {
                "mode": "realtime" if settings.has_realtime_key else "browser-fallback",
                "realtime_model": settings.realtime_model,
                "user": {"id": identity.policy.user_id, "role": identity.policy.role},
            },
        }
    )

    try:
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break
            except RuntimeError:
                # Starlette raises RuntimeError (not WebSocketDisconnect) when a
                # tab or network drops without sending a close frame.
                break
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                await send({"type": "state", "data": {"error": "invalid JSON frame"}})
                continue

            ftype = frame.get("type")

            if ftype == "start":
                if session is not None:
                    await send({"type": "state", "data": {"info": "session already active"}})
                    continue
                start_data = frame.get("data") or {}
                requested_model = str(start_data.get("model") or "").strip() or None
                requested_voice = str(start_data.get("voice") or "").strip() or None
                chat_model_override = str(start_data.get("chat_model") or "").strip() or None
                incoming_conv = str(start_data.get("conversation_id") or "").strip() or None
                if incoming_conv:
                    conversation_id = incoming_conv

                try:
                    resolved = await runtime.connections.resolve_session_realtime(
                        deps.user_id, requested_model, settings
                    )
                except Exception:  # noqa: BLE001 - decryption issues fall back
                    resolved = None

                if resolved is None:
                    await send(
                        {
                            "type": "state",
                            "data": {
                                "mode": "browser-fallback",
                                "info": "No voice connection or realtime provider key; using browser speech. "
                                "Add a Gemini or OpenAI connection on Connections for server speech-to-speech.",
                            },
                        }
                    )
                    continue

                resolved_model, model_str = resolved
                from voicefirst.control_plane.connections import realtime_model_settings

                model_settings = realtime_model_settings(model_str, requested_voice)
                try:
                    # Voice companion talks. The chat orchestrator works off-session.
                    rt_agent = (
                        getattr(runtime, "voice_companion", None)
                        or getattr(runtime, "realtime_orchestrator", None)
                        or runtime.orchestrator
                    )
                    realtime_agent = rt_agent.realtime(
                        resolved_model, deps=deps, model_settings=model_settings or None
                    )
                    session = await stack.enter_async_context(realtime_agent.session())
                    mic_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=64)
                    mic_queue = mic_q

                    async def microphone() -> Any:
                        while True:
                            chunk = await mic_q.get()
                            if chunk is None:
                                return
                            yield chunk

                    voice_metrics.update(
                        {
                            "turns": 0,
                            "barge_ins": 0,
                            "ttfw_ms": None,
                            "last_speaker": None,
                            "last_user_at": None,
                            "last_voice_at": None,
                            "turn_voice_at": None,
                            "awaiting_audio": False,
                            "model": model_str,
                        }
                    )
                    # Subscribe to views at task-create time (pydantic-ai realtime audio docs).
                    tasks = [
                        asyncio.create_task(pump_transcripts(session.stream_transcripts(delta=True))),
                        asyncio.create_task(pump_final_transcripts(session.stream_transcripts())),
                        asyncio.create_task(pump_audio(session.stream_audio())),
                        asyncio.create_task(drain_session(session)),
                        asyncio.create_task(session.send_audio(microphone())),
                    ]
                    provider_name = (model_str or "").split(":", 1)[0] or None
                    voice_session = await runtime.voice_analytics.start_session(
                        tenant_id=deps.tenant_id,
                        user_id=deps.user_id,
                        channel="browser",
                        provider=provider_name,
                        model=model_str,
                        voice=requested_voice or (model_settings.get("google_voice") if model_settings else None),
                    )
                    voice_started_mono = asyncio.get_running_loop().time()
                    logger.info(
                        "voice session ready model=%s in=%sHz out=%sHz voice=%s",
                        model_str,
                        session.audio_input_sample_rate,
                        session.audio_output_sample_rate,
                        requested_voice or (model_settings.get("google_voice") if model_settings else None),
                    )
                    await send(
                        {
                            "type": "state",
                            "data": {
                                "mode": "realtime",
                                "input_rate": session.audio_input_sample_rate,
                                "output_rate": session.audio_output_sample_rate,
                                "model": model_str,
                                "voice": requested_voice or model_settings.get("google_voice"),
                                "session_id": voice_session.id,
                                "chat_model": chat_model_override or settings.default_model,
                                "conversation_id": conversation_id,
                                "provider_settings": sorted(model_settings.keys()) if model_settings else [],
                            },
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    session = None
                    await send({"type": "state", "data": {"error": f"realtime unavailable: {exc}"}})

            elif ftype == "text":
                text = str(frame.get("data") or "").strip()
                if not text:
                    continue
                await runtime.events.publish(
                    "voice.text_turn", {"user_id": deps.user_id, "chars": len(text)}, tenant_id=deps.tenant_id
                )
                try:
                    if session is not None:
                        await session.send(text)
                        schedule_handoff(text)
                        continue
                    result = await runtime.orchestrator.run(text, deps=deps)
                    output = result.output
                    output = output if isinstance(output, str) else str(output)
                    await send({"type": "transcript", "data": {"speaker": "assistant", "text": output, "final": True}})
                    await send({"type": "turn.end", "data": {}})
                except Exception as exc:  # noqa: BLE001
                    await send({"type": "state", "data": {"error": f"run failed: {exc}"}})

            elif ftype == "pcm":
                if session is not None and mic_queue is not None:
                    try:
                        audio = base64.b64decode(frame.get("data") or "")
                        if audio:
                            if _peak_pcm16(audio) >= 700:
                                voice_metrics["last_voice_at"] = asyncio.get_running_loop().time()
                            mic_queue.put_nowait(audio)
                    except asyncio.QueueFull:
                        pass
                    except Exception as exc:  # noqa: BLE001
                        await send({"type": "state", "data": {"error": f"audio send failed: {exc}"}})

            elif ftype == "stop":
                await end_voice_session()
                if debounce_task is not None:
                    debounce_task.cancel()
                if session is not None:
                    if mic_queue is not None:
                        try:
                            mic_queue.put_nowait(None)
                        except Exception:  # noqa: BLE001
                            pass
                    session = None
                    mic_queue = None
                    for task in tasks:
                        task.cancel()
                    tasks = []
                    await send({"type": "state", "data": {"mode": "idle", "info": "session closed"}})
                else:
                    await send({"type": "state", "data": {"mode": "idle"}})

    except WebSocketDisconnect:
        pass
    finally:
        if debounce_task is not None:
            debounce_task.cancel()
        if background_task is not None:
            background_task.cancel()
        for task in tasks:
            task.cancel()
        await end_voice_session()
        await stack.aclose()
