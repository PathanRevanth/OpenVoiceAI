"""Twilio Media Streams bridge: phone call audio <-> realtime voice agent.

Twilio streams 8kHz mu-law audio in base64 JSON frames; realtime providers
consume 24kHz PCM16 and emit 24kHz PCM16 back. This module owns the DSP
(mu-law decode/encode, linear resampling) and the frame plumbing so an
inbound call can talk to the same governed voice agent as the browser.
"""

from __future__ import annotations

import audioop
import base64


def mulaw_to_pcm16(data: bytes) -> bytes:
    return audioop.ulaw2lin(data, 2)


def pcm16_to_mulaw(data: bytes) -> bytes:
    return audioop.lin2ulaw(data, 2)


def resample_pcm16(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resampler for 16-bit mono PCM."""
    if src_rate == dst_rate or not data:
        return data
    samples = len(data) // 2
    if samples < 2:
        return data
    import struct

    in_samples = struct.unpack(f"<{samples}h", data)
    out_count = int(samples * dst_rate / src_rate)
    if out_count < 2:
        return data
    step = (samples - 1) / (out_count - 1)
    out: list[int] = []
    for i in range(out_count):
        pos = i * step
        i0 = int(pos)
        i1 = min(i0 + 1, samples - 1)
        frac = pos - i0
        value = int(in_samples[i0] * (1 - frac) + in_samples[i1] * frac)
        out.append(max(-32768, min(32767, value)))
    return struct.pack(f"<{len(out)}h", *out)


def twilio_to_provider_pcm(b64_mulaw: str) -> bytes:
    """8kHz mu-law (base64) frame -> 24kHz PCM16 for the realtime provider."""
    pcm8 = mulaw_to_pcm16(base64.b64decode(b64_mulaw))
    return resample_pcm16(pcm8, 8000, 24000)


def provider_pcm_to_twilio(pcm24: bytes) -> str:
    """24kHz PCM16 -> base64 mu-law at 8kHz, as Twilio expects in JSON frames."""
    pcm8 = resample_pcm16(pcm24, 24000, 8000)
    return base64.b64encode(pcm16_to_mulaw(pcm8)).decode()
