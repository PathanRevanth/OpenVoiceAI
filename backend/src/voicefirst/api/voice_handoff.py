"""Decide when a spoken turn should go to the chat orchestrator.

The Gemini Live session is the conversational layer. The Anthropic (or other
chat) orchestrator does the actual work in the background — same conversation,
live corrections included.
"""

from __future__ import annotations

import re

_TASK_HINT = re.compile(
    r"\b(orchestrator|design|create|make|build|write|change|update|fix|search|"
    r"find|show|open|draft|artifact|colour|color|white|black|marketing|"
    r"github|slack|research|mongodb|look\s+up|look\s+into|check|send|email|"
    r"schedule|remind|summari[sz]e|compare|analyz|analys|fetch|download|"
    r"upload|deploy|review|plan|slides?|spreadsheet|dataset|chart|graph|"
    r"report|investigate)\b",
    re.IGNORECASE,
)

# A bare work word in a factual question ("what does this report say?") is not a
# request to act. Only treat hints as work when the caller is asking for action.
_REQUEST = re.compile(
    r"\b(please|can you|could you|would you|will you|i need you to|"
    r"i want you to|help me|go ahead|let'?s)\b",
    re.IGNORECASE,
)

_QUESTION_START = re.compile(
    r"^\s*(what|who|whom|whose|when|where|why|how|which|is|are|was|were|do|"
    r"does|did|can|could|should|would|will|have|has|had)\b",
    re.IGNORECASE,
)


def should_handoff_to_orchestrator(text: str) -> bool:
    """True when this utterance is a request for work, not just conversation."""
    cleaned = " ".join((text or "").strip().split())
    if len(cleaned) < 4:
        return False
    if not _TASK_HINT.search(cleaned):
        # No action word: the voice model answers conversation and facts itself.
        return False
    if _REQUEST.search(cleaned):
        return True
    # "How do I fix X?" is a question the companion can talk through.
    return not _QUESTION_START.search(cleaned)


def spoken_control_cue(event_type: str, data: dict) -> str | None:
    """A short instruction for the voice model — not shown as a user turn."""
    if event_type == "run.started":
        return (
            "The control plane just started the real work in the background. "
            "Acknowledge in one short spoken sentence that you are on it. "
            "Do not invent the result."
        )
    if event_type == "tool.started":
        tool = str((data or {}).get("tool") or "a specialist")
        return (
            f"Briefly tell the caller you are handling {tool.replace('_', ' ')} now. "
            "One sentence. Keep listening."
        )
    if event_type == "run.finished":
        status = str((data or {}).get("status") or "")
        if status == "cancelled":
            return "The previous request was updated. Stay with the caller."
        if status == "awaiting_approval":
            return "Tell the caller a step needs their approval in the console."
        summary = str((data or {}).get("summary") or "").strip()
        if summary:
            return (
                "The orchestrator finished. Speak this result naturally, in a few "
                f"sentences, then offer to change it: {summary[:800]}"
            )
        return "The orchestrator finished. Tell the caller the result is on screen."
    if event_type == "run.failed":
        return "The background run hit a problem. Apologize briefly and ask how to continue."
    return None
