"""Outbound webhook delivery with optional HMAC-SHA256 signing.

When `VOICEFIRST_WEBHOOK_SECRET` is configured, every webhook (approval
requests, trigger results, notifications) carries an
`X-VoiceFirst-Signature: sha256=<hex>` header computed over the raw body —
the same scheme as GitHub/Stripe webhooks — so receivers can verify
authenticity and reject replayed/tampered payloads.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from voicefirst.settings import get_settings

SIGNATURE_HEADER = "X-VoiceFirst-Signature"


def sign_payload(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Constant-time verification helper for webhook receivers."""
    expected = sign_payload(body, secret)
    return hmac.compare_digest(expected, signature)


async def deliver_webhook(
    url: str,
    payload: dict[str, Any],
    *,
    event: str,
    timeout: float = 10.0,
    secret: str | None = None,
) -> tuple[bool, int | None]:
    """POST a webhook, signed with the per-subscription secret when given,
    else the platform `VOICEFIRST_WEBHOOK_SECRET`. Never raises; returns
    (delivered, status_code)."""
    body = json.dumps(payload, default=str).encode()
    headers = {
        "Content-Type": "application/json",
        "X-VoiceFirst-Event": event,
    }
    signing_secret = secret or get_settings().webhook_secret
    if signing_secret:
        headers[SIGNATURE_HEADER] = sign_payload(body, signing_secret)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, content=body, headers=headers)
            return resp.status_code < 400, resp.status_code
    except Exception:  # noqa: BLE001 - delivery is best-effort by contract
        return False, None
