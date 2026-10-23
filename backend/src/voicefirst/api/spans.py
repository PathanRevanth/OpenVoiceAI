"""Span collection for agent runs: one span per model request and tool call.

Follows the observability-industry unit of work ("trace every step"): each
span records kind, name, relative start/end in milliseconds, and a small
detail payload, so the UI can render waterfall timelines.
"""

from __future__ import annotations

import time
from typing import Any


class SpanCollector:
    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []
        self._open: dict[str, dict[str, Any]] = {}
        self._seq = 0
        self._t0: float | None = None

    def start_clock(self) -> None:
        if self._t0 is None:
            self._t0 = time.time()

    def start(self, name: str, kind: str, detail: Any = None, key: str | None = None) -> str:
        self.start_clock()
        self._seq += 1
        span_key = key or f"{kind}:{self._seq}"
        self._open[span_key] = {
            "name": name,
            "kind": kind,
            "start": time.time(),
            "detail": detail,
        }
        return span_key

    def end(self, key: str, detail: Any = None) -> None:
        span = self._open.pop(key, None)
        if span is None:
            return
        span["end"] = time.time()
        if detail is not None:
            span["detail"] = detail
        self.spans.append(span)

    def normalize(self) -> list[dict[str, Any]]:
        """Convert to relative-millisecond spans sorted by start."""
        if not self.spans:
            return []
        t0 = min(s["start"] for s in self.spans)
        out = []
        for span in sorted(self.spans, key=lambda s: s["start"]):
            start_ms = int((span["start"] - t0) * 1000)
            end_ms = int((span["end"] - span["start"]) * 1000) + start_ms
            out.append(
                {
                    "name": span["name"],
                    "kind": span["kind"],
                    "start_ms": start_ms,
                    "end_ms": max(end_ms, start_ms + 1),
                    "duration_ms": max(end_ms - start_ms, 1),
                    "detail": span.get("detail"),
                }
            )
        return out
