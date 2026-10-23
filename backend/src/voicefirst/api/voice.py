"""Voice: browser WebSocket bridge into pydantic-ai realtime speech-to-speech.

Protocol (JSON frames over `/api/voice/ws`):

Browser -> Server:
    {"type": "start"}                      begin a realtime session
    {"type": "pcm", "data": "<base64>"}    16-bit 24kHz mono PCM audio chunk
    {"type": "text", "data": "..."}        text turn (typed or fallback input)
    {"type": "stop"}                       end the session

Server -> Browser:
    {"type": "state", "data": {...}}       session state / errors / info
    {"type": "transcript", "data": {...}}  user/assistant transcript updates
    {"type": "speech", "data": "<base64>"} assistant speech audio (PCM)
    {"type": "turn.end", "data": {...}}    assistant finished speaking

Realtime providers (OpenAI gpt-realtime, Google Gemini Live, xAI Grok Voice)
require an API key. Without one, `start` reports `browser-fallback` mode and
`text` turns still route through the orchestrator over this socket — so the
voice UI works everywhere (browser SpeechRecognition/SpeechSynthesis client-side,
full control-plane delegation server-side).
"""

from __future__ import annotations

import asyncio
import base64
import json
from contextlib import AsyncExitStack
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from voicefirst.agents.deps import AgentDeps
from voicefirst.control_plane.auth import IdentityService
from voicefirst.settings import get_settings

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
    # Voice-quality metrics for this connection (see VoiceAnalyticsService).
    voice_session: Any = None
    voice_started_mono: float | None = None
    voice_metrics: dict[str, Any] = {"turns": 0, "barge_ins": 0, "ttfw_ms": None, "last_speaker": None}

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

    async def pump_transcripts(sess: Any) -> None:
        try:
            async for transcript in sess.stream_transcripts():
                speaker = getattr(transcript, "speaker", "assistant")
                text = getattr(transcript, "content", "") or ""
                if not text:
                    continue
                # Barge-in proxy: the caller starting to speak right after the
                # assistant spoke means they interrupted the agent's turn.
                if speaker == "user" and voice_metrics["last_speaker"] == "assistant":
                    voice_metrics["barge_ins"] += 1
                if speaker != "user" and voice_metrics["ttfw_ms"] is None and voice_started_mono is not None:
                    voice_metrics["ttfw_ms"] = int((asyncio.get_running_loop().time() - voice_started_mono) * 1000)
                voice_metrics["turns"] += 1
                voice_metrics["last_speaker"] = speaker
                if voice_session is not None:
                    await runtime.voice_analytics.update_metrics(
                        voice_session.id,
                        turns=voice_metrics["turns"],
                        ttfw_ms=voice_metrics["ttfw_ms"],
                        barge_ins=voice_metrics["barge_ins"],
                    )
                await send(
                    {"type": "transcript", "data": {"speaker": speaker, "text": text, "final": True}}
                )
        except Exception as exc:  # noqa: BLE001
            await send({"type": "state", "data": {"error": f"transcript stream ended: {exc}"}})

    async def pump_audio(sess: Any) -> None:
        try:
            async for chunk in sess.stream_audio():
                await send({"type": "speech", "data": base64.b64encode(chunk).decode()})
        except Exception:  # noqa: BLE001
            pass

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
            raw = await ws.receive_text()
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
                # The user's saved voice connection wins over platform env keys.
                voice_model = None
                try:
                    voice_model = await runtime.connections.resolve_voice_model(deps.user_id)
                except Exception:  # noqa: BLE001 - decryption issues fall back
                    voice_model = None

                # Per-session overrides from the composer: requested realtime
                # model + voice (voices follow the model's provider). An
                # explicit pick wins over the saved voice connection.
                start_data = frame.get("data") or {}
                requested_model = str(start_data.get("model") or "").strip() or None
                requested_voice = str(start_data.get("voice") or "").strip() or None

                model_str = requested_model or settings.realtime_model
                resolved_model: Any = requested_model or voice_model or settings.realtime_model

                # Voice applies per provider: `openai_voice` / `google_voice`.
                model_settings: dict[str, Any] | None = None
                if requested_voice:
                    provider = model_str.split(":", 1)[0].lower()
                    if provider in ("openai", "azure"):
                        model_settings = {"openai_voice": requested_voice}
                    elif provider == "google":
                        model_settings = {"google_voice": requested_voice}

                if not requested_model and voice_model is None and not settings.has_realtime_key:
                    await send(
                        {
                            "type": "state",
                            "data": {
                                "mode": "browser-fallback",
                                "info": "No voice connection or realtime provider key; using browser speech. "
                                "Add a connection on the Connections page for server speech-to-speech.",
                            },
                        }
                    )
                    continue
                try:
                    # The realtime orchestrator variant excludes deferred tools
                    # (Skills) and the plan gate — both unsupported mid-session.
                    rt_agent = getattr(runtime, "realtime_orchestrator", None) or runtime.orchestrator
                    realtime_agent = rt_agent.realtime(
                        resolved_model, deps=deps, model_settings=model_settings
                    )
                    session = await stack.enter_async_context(realtime_agent.session())
                    tasks = [asyncio.create_task(pump_transcripts(session)), asyncio.create_task(pump_audio(session))]
                    # Begin a tracked voice session for analytics.
                    provider_name = (model_str or "").split(":", 1)[0] or None
                    voice_session = await runtime.voice_analytics.start_session(
                        tenant_id=deps.tenant_id,
                        user_id=deps.user_id,
                        channel="browser",
                        provider=provider_name,
                        model=model_str,
                        voice=requested_voice,
                    )
                    voice_started_mono = asyncio.get_running_loop().time()
                    voice_metrics.update({"turns": 0, "barge_ins": 0, "ttfw_ms": None, "last_speaker": None})
                    await send(
                        {
                            "type": "state",
                            "data": {
                                "mode": "realtime",
                                "input_rate": session.audio_input_sample_rate,
                                "output_rate": session.audio_output_sample_rate,
                                "model": model_str,
                                "voice": requested_voice,
                                "session_id": voice_session.id,
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
                    result = await runtime.orchestrator.run(text, deps=deps)
                    output = result.output
                    output = output if isinstance(output, str) else str(output)
                    await send({"type": "transcript", "data": {"speaker": "assistant", "text": output, "final": True}})
                    await send({"type": "turn.end", "data": {}})
                except Exception as exc:  # noqa: BLE001
                    await send({"type": "state", "data": {"error": f"run failed: {exc}"}})

            elif ftype == "pcm":
                if session is not None:
                    try:
                        audio = base64.b64decode(frame.get("data") or "")
                        if audio:
                            session.send_audio(audio)
                    except Exception as exc:  # noqa: BLE001
                        await send({"type": "state", "data": {"error": f"audio send failed: {exc}"}})

            elif ftype == "stop":
                await end_voice_session()
                if session is not None:
                    session = None
                    for task in tasks:
                        task.cancel()
                    tasks = []
                    await send({"type": "state", "data": {"mode": "idle", "info": "session closed"}})
                else:
                    await send({"type": "state", "data": {"mode": "idle"}})

    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        await end_voice_session()
        await stack.aclose()
