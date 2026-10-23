"""Shared httpx client factory for agent tools.

Central place for production HTTP hygiene (explicit User-Agent, timeouts) and
the seam tests use to inject `httpx.MockTransport` so tool behavior is
verifiable end-to-end without hitting real APIs.
"""

from __future__ import annotations

import httpx

USER_AGENT = "VoiceFirstAI-ControlPlane/1.0 (+https://github.com/voicefirst-ai)"

# Tests may inject a transport (e.g. httpx.MockTransport); None means normal
# direct networking.
transport: httpx.AsyncBaseTransport | None = None


def make_client(timeout: float = 20.0, follow_redirects: bool = False) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=follow_redirects,
        headers={"User-Agent": USER_AGENT},
        transport=transport,
    )
