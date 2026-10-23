"""Outbound event subscriptions: tenant webhooks for platform events.

Replaces ad-hoc webhook calls with a first-class subscription model:

* Subscribers register a URL + an event pattern list (`run.failed`,
  `budget.*`, `approval.*`, `*` for everything) and an optional per-subscription
  signing secret (stored encrypted).
* The dispatcher task consumes the in-process EventBus and fans matching
  events out to every enabled subscription as a signed POST
  (`X-VoiceFirst-Signature: sha256=<hmac>` when a secret exists).
* Delivery retries up to 3 attempts (1s / 4s backoff) on network errors or
  5xx/429; after 10 consecutive failed deliveries the subscription is
  dead-lettered (disabled + flagged) so one dead endpoint never throttles
  the platform.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from voicefirst.control_plane.connections import KeyVault
from voicefirst.control_plane.webhooks import SIGNATURE_HEADER, sign_payload
from voicefirst.db.models import EventSubscriptionRecord, new_id
from voicefirst.db.session import Database

MAX_CONSECUTIVE_FAILURES = 10
RETRY_DELAYS = (1.0, 4.0)


def event_matches(patterns: list[str], event_type: str) -> bool:
    """Shell-style match: `run.*` matches `run.failed`; `*` matches all."""
    for pattern in patterns or []:
        if pattern == "*" or fnmatch.fnmatchcase(event_type, pattern):
            return True
    return False


class EventSubscriptionService:
    def __init__(self, db: Database, vault: KeyVault) -> None:
        self._db = db
        self._vault = vault

    async def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        url: str,
        events: list[str],
        secret: str | None = None,
    ) -> EventSubscriptionRecord:
        record = EventSubscriptionRecord(
            id=new_id("sub"),
            tenant_id=tenant_id,
            user_id=user_id,
            url=url[:1024],
            events=[e for e in events if e][:32] or ["*"],
            secret_encrypted=self._vault.encrypt(secret) if secret else None,
            enabled=True,
        )
        async with self._db.session() as session:
            session.add(record)
        return record

    async def list_for_tenant(self, tenant_id: str) -> list[EventSubscriptionRecord]:
        async with self._db.session() as session:
            rows = (
                (
                    await session.execute(
                        select(EventSubscriptionRecord)
                        .where(EventSubscriptionRecord.tenant_id == tenant_id)
                        .order_by(EventSubscriptionRecord.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    async def get(self, subscription_id: str, tenant_id: str) -> EventSubscriptionRecord | None:
        async with self._db.session() as session:
            record = await session.get(EventSubscriptionRecord, subscription_id)
            if record is None or record.tenant_id != tenant_id:
                return None
            return record

    async def set_enabled(self, subscription_id: str, tenant_id: str, enabled: bool) -> EventSubscriptionRecord | None:
        async with self._db.session() as session:
            record = await session.get(EventSubscriptionRecord, subscription_id)
            if record is None or record.tenant_id != tenant_id:
                return None
            record.enabled = enabled
            if enabled:
                record.dead_lettered = False
                record.failure_count = 0
        return await self.get(subscription_id, tenant_id)

    async def delete(self, subscription_id: str, tenant_id: str) -> bool:
        async with self._db.session() as session:
            record = await session.get(EventSubscriptionRecord, subscription_id)
            if record is None or record.tenant_id != tenant_id:
                return False
            await session.delete(record)
        return True

    async def record_delivery(
        self, subscription_id: str, *, ok: bool, status_code: int | None, detail: str | None = None
    ) -> bool:
        """Update delivery counters; returns True if the subscription was
        dead-lettered by this delivery."""
        now = datetime.now(UTC)
        async with self._db.session() as session:
            record = await session.get(EventSubscriptionRecord, subscription_id)
            if record is None:
                return False
            record.deliveries_total += 1
            record.last_delivery_at = now
            record.last_status_code = status_code
            if ok:
                record.failure_count = 0
                record.last_status = "delivered"
                return False
            record.failure_count += 1
            record.last_status = (detail or f"http {status_code}")[:200]
            if record.failure_count >= MAX_CONSECUTIVE_FAILURES:
                record.dead_lettered = True
                record.enabled = False
                return True
        return False

    @staticmethod
    def to_dict(record: EventSubscriptionRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "url": record.url,
            "events": list(record.events or []),
            "enabled": record.enabled,
            "dead_lettered": record.dead_lettered,
            "failure_count": record.failure_count,
            "deliveries_total": record.deliveries_total,
            "has_secret": bool(record.secret_encrypted),
            "last_status": record.last_status,
            "last_status_code": record.last_status_code,
            "last_delivery_at": record.last_delivery_at.isoformat() if record.last_delivery_at else None,
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }


class EventDispatcher:
    """Background task: EventBus → matching subscriptions → signed delivery."""

    def __init__(self, db: Database, events: Any, vault: KeyVault, audit: Any = None) -> None:
        self._db = db
        self._events = events
        self._vault = vault
        self._audit = audit
        self._service = EventSubscriptionService(db, vault)
        self._queue: asyncio.Queue[Any] | None = None
        self._task: asyncio.Task | None = None

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._task is None:
            self._queue = await self._events.subscribe()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._queue is not None:
            await self._events.unsubscribe(self._queue)
            self._queue = None

    async def _loop(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            try:
                await self._dispatch(event)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must survive anything
                pass

    async def _dispatch(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if not event_type:
            return
        event_tenant = event.get("tenant_id")
        rows = await self._service.list_for_tenant(event_tenant) if event_tenant else []
        if not event_tenant:
            async with self._db.session() as session:
                rows = (
                    (await session.execute(select(EventSubscriptionRecord).where(EventSubscriptionRecord.enabled.is_(True))))
                    .scalars()
                    .all()
                )
            rows = list(rows)
        for record in rows:
            if not record.enabled or record.dead_lettered:
                continue
            if not event_matches(list(record.events or []), event_type):
                continue
            secret = (
                self._vault.decrypt(record.secret_encrypted)
                if record.secret_encrypted
                else None
            )
            delivered, status_code = await self._deliver(
                record.url, event_type, event, secret
            )
            dead_lettered = await self._service.record_delivery(
                record.id, ok=delivered, status_code=status_code,
                detail=None if delivered else "delivery failed",
            )
            if dead_lettered and self._audit is not None:
                await self._audit.record(
                    actor="system",
                    action="subscription.dead_lettered",
                    resource=f"subscription:{record.id}",
                    tenant_id=record.tenant_id,
                    detail={"url": record.url, "failures": MAX_CONSECUTIVE_FAILURES},
                )

    async def _deliver(
        self, url: str, event_type: str, event: dict[str, Any], secret: str | None
    ) -> tuple[bool, int | None]:
        """POST with retries; returns (delivered, last_status_code)."""
        from voicefirst.settings import get_settings

        body = json_bytes(event)
        headers = {"Content-Type": "application/json", "X-VoiceFirst-Event": event_type}
        signing_secret = secret or get_settings().webhook_secret
        if signing_secret:
            headers[SIGNATURE_HEADER] = sign_payload(body, signing_secret)
        status_code: int | None = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, content=body, headers=headers)
                status_code = resp.status_code
                if resp.status_code < 500 and resp.status_code != 429:
                    return resp.status_code < 400, resp.status_code
            except Exception:  # noqa: BLE001 - retry, then count as failure
                status_code = None
            if attempt < len(RETRY_DELAYS):
                await asyncio.sleep(RETRY_DELAYS[attempt])
        return False, status_code


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, default=str).encode()
