"""Parity batch 4: thinking-part streaming into SSE events."""

from __future__ import annotations

from pydantic_ai.messages import (
    NativeToolCallPart,
    PartDeltaEvent,
    PartStartEvent,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
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


def test_function_tool_start_deferred_to_call_tools_node():
    # The model-response part has no assembled args yet; the call-tools node
    # emits the single `tool.started` with complete args.
    part = ToolCallPart(tool_name="web_search", args={})
    assert _translate_event(PartStartEvent(index=0, part=part), [], []) == []


def test_native_tool_start_is_still_streamed():
    part = NativeToolCallPart(tool_name="web_search", args={"query": "x"})
    out = _translate_event(PartStartEvent(index=0, part=part), [], [])
    assert out == [{"type": "tool.started", "data": {"tool": "web_search", "args": {"query": "x"}}}]


def test_final_thinking_caps_with_marker():
    assert _final_thinking(None) == ""
    assert _final_thinking(["a", "b"]) == "ab"
    long = _final_thinking(["x" * 20_000])
    assert len(long) < 20_000 and long.endswith("[truncated]")
