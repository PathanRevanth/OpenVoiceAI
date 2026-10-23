"""In-process event bus with fan-out to subscribers.

Every meaningful control-plane occurrence (run started/finished, tool call,
approval requested, spend threshold crossed) is published here so the
experience layer can stream live activity to browsers over SSE/WebSocket.
Optionally mirrored to Redis Streams when `redis_url` is configured.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


class EventBus:
    """Async pub/sub. Each subscriber gets its own bounded queue."""

    def __init__(self, redis_url: str | None = None, queue_size: int = 512) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._queue_size = queue_size
        self._lock = asyncio.Lock()
        self.redis_url = redis_url
        self._redis = None  # lazily connected; mirroring is best-effort

    async def publish(self, event_type: str, payload: dict[str, Any], **envelope: Any) -> None:
        event: dict[str, Any] = {
            "type": event_type,
            "ts": _now(),
            "payload": payload,
            **envelope,
        }
        async with self._lock:
            queues = list(self._subscribers)
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - slow consumer
                pass
        await self._mirror(event)

    async def _mirror(self, event: dict[str, Any]) -> None:
        if not self.redis_url:
            return
        try:
            if self._redis is None:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
            await self._redis.xadd("voicefirst:events", {"event": json.dumps(event, default=str)})
        except Exception:  # noqa: BLE001 - mirroring must never break the caller
            pass

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:  # noqa: BLE001
                pass
