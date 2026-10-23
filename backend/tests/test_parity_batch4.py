"""Parity batch 4: thinking-part streaming into SSE events."""

from __future__ import annotations

from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    ThinkingPart,
    ThinkingPartDelta,
)

from voicefirst.api.chat_service import _final_thinking, _translate_event


def test_thinking_start_event_streams_delta():
    thinking: list[str] = []
    text: list[str] = []
    out = _translate_event(PartStartEvent(index=0, part=ThinkingPart(content="Let me think")), text, thinking)
    assert out == [{"type": "thinking.delta", "data": {"delta": "Let me think"}}]
    assert thinking == ["Let me think"] and text == []


def test_thinking_delta_event_appends():
    thinking: list[str] = []
    out = _translate_event(
        PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta=" more")), [], thinking
    )
    assert out == [{"type": "thinking.delta", "data": {"delta": " more"}}]
    assert thinking == [" more"]


def test_empty_thinking_is_dropped():
    assert _translate_event(PartStartEvent(index=0, part=ThinkingPart(content="")), [], []) == []
    assert _translate_event(PartDeltaEvent(index=0, delta=ThinkingPartDelta()), [], []) == []


def test_final_thinking_caps_with_marker():
    assert _final_thinking(None) == ""
    assert _final_thinking(["a", "b"]) == "ab"
    long = _final_thinking(["x" * 20_000])
    assert len(long) < 20_000 and long.endswith("[truncated]")
