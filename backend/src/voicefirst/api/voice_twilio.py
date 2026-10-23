"""Twilio Media Streams WebSocket: ``/api/voice/twilio``.

Configure a Twilio phone number with a Media Stream pointing at
``wss://<host>/api/voice/twilio``. Frames follow Twilio's connected/start/
media/stop protocol (base64 8kHz mu-law). Audio is converted and fed to the
same governed realtime voice agent used by the browser; assistant audio is
encoded back to mu-law and streamed into the call.

Set VOICEFIRST_TWILIO_AUTH_TOKEN to require Twilio's X-Twilio-Signature
header validation; when unset the socket accepts calls unauthenticated.
"""

from __future__ import annotations

import base64
import hashlib
import json
from contextlib import AsyncExitStack
from typing import Any

from fastapi import APIRouter, WebSocket

from voicefirst.agents.deps import AgentDeps
from voicefirst.control_plane.telephony import provider_pcm_to_twilio, twilio_to_provider_pcm

router = APIRouter()


def _valid_twilio_signature(request_uri: str, params: dict[str, Any], signature: str, token: str) -> bool:
    import hmac

    data = request_uri + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    expected = base64.b64encode(hashlib.sha1(data.encode(), token.encode()).digest()).decode()
    return hmac.compare_digest(expected, signature)


@router.websocket("/api/voice/twilio")
async def twilio_media_stream(ws: WebSocket) -> None:
    runtime = ws.app.state.runtime
    settings = runtime.settings
    identity = await runtime.identity.from_websocket(ws)

    token = settings.twilio_auth_token
    if token:
        signature = ws.query_params.get("signature") or ""
        call_sid = ws.query_params.get("callid") or ""
        if not _valid_twilio_signature(str(ws.url), {"CallSid": call_sid}, signature, token):
            await ws.close(code=4403, reason="Invalid Twilio signature")
            return

    await ws.accept()
    deps = AgentDeps.from_identity(identity, runtime=runtime, channel="voice")
    stack = AsyncExitStack()
    session: Any = None
    stream_sid: str | None = None
    voice_session: Any = None
    voice_started_mono: float | None = None
    voice_metrics: dict[str, Any] = {"turns": 0, "barge_ins": 0, "ttfw_ms": None, "last_speaker": None}

    async def end_voice_session(status: str = "ended", error: str | None = None) -> None:
        nonlocal voice_session
        if voice_session is None:
            return
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
            await ws.send_text(json.dumps(frame))
        except Exception:  # noqa: BLE001
            pass

    async def pump_audio(sess: Any) -> None:
        """Provider speech -> call audio."""
        buffer = b""
        try:
            async for chunk in sess.stream_audio():
                buffer += chunk
                # Twilio wants ~20ms (160 sample) mulaw frames at 8kHz
                frame_bytes = 320  # 160 samples * 2 bytes pcm16 @24k in
                while len(buffer) >= frame_bytes:
                    piece, buffer = buffer[:frame_bytes], buffer[frame_bytes:]
                    await send(
                        {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": provider_pcm_to_twilio(piece)},
                        }
                    )
        except Exception:  # noqa: BLE001
            pass

    async def pump_transcripts(sess: Any) -> None:
        try:
            import asyncio

            async for transcript in sess.stream_transcripts():
                speaker = getattr(transcript, "speaker", "assistant")
                text = getattr(transcript, "content", "") or ""
                if not text:
                    continue
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
                    {"event": "transcript", "transcript": {"speaker": speaker, "text": text}}
                )
        except Exception:  # noqa: BLE001
            pass

    await send(
        {
            "type": "state",
            "data": {"mode": "twilio-bridge", "user": identity.policy.user_id},
        }
    )

    try:
        while True:
            raw = await ws.receive_text()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            event = frame.get("event")

            if event == "start":
                stream_sid = (frame.get("start") or {}).get("streamSid")
                if settings.has_realtime_key:
                    try:
                        voice_model = await runtime.connections.resolve_voice_model(deps.user_id)
                        realtime_agent = (
                            runtime.orchestrator.realtime(voice_model, deps=deps)
                            if voice_model is not None
                            else runtime.orchestrator.realtime(settings.realtime_model, deps=deps)
                        )
                        session = await stack.enter_async_context(realtime_agent.session())
                        import asyncio

                        for coro in (pump_transcripts(session), pump_audio(session)):
                            asyncio.create_task(coro)
                        voice_session = await runtime.voice_analytics.start_session(
                            tenant_id=deps.tenant_id,
                            user_id=deps.user_id,
                            channel="twilio",
                            provider="twilio",
                            model=settings.realtime_model,
                        )
                        voice_started_mono = asyncio.get_running_loop().time()
                        voice_metrics.update({"turns": 0, "barge_ins": 0, "ttfw_ms": None, "last_speaker": None})
                        await send({"event": "state", "data": {"mode": "realtime", "session_id": voice_session.id}})
                    except Exception as exc:  # noqa: BLE001
                        session = None
                        await send({"event": "error", "error": str(exc)[:200]})
                else:
                    await send({"event": "error", "error": "No realtime voice connection configured"})

            elif event == "media" and session is not None:
                payload = (frame.get("media") or {}).get("payload") or ""
                if payload:
                    try:
                        session.send_audio(twilio_to_provider_pcm(base64.b64decode(payload)))
                    except Exception as exc:  # noqa: BLE001
                        await send({"event": "error", "error": f"audio: {exc}"})

            elif event == "stop":
                await end_voice_session()
                if session is not None:
                    session = None
                await send({"event": "state", "data": {"mode": "idle"}})

    except Exception:  # noqa: BLE001 - socket closed
        pass
    finally:
        await end_voice_session()
        await stack.aclose()
