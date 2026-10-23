"""GitHub OAuth connect flow for the GitHub specialist agent.

The agent's tools read their token from `settings.github_token`. This router
lets an operator authorize the platform's GitHub OAuth app in the browser:

    GET  /api/github/oauth/start    → 302 to GitHub's authorize URL (state-protected)
    GET  /api/github/oauth/callback → exchanges ?code for a user access token
    GET  /api/github/oauth/status   → {connected, login, token_prefix}
    DELETE /api/github/oauth        → disconnect (drops the stored token)

The exchanged token is stored in tenant settings (DB) so it survives restarts,
and applied to the live `settings` object immediately so the specialist can use
it without a rebuild.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from voicefirst.control_plane.auth import Identity, require_identity, require_role_ops

router = APIRouter(prefix="/api/github/oauth")

# Short-lived CSRF states: state -> user_id (monotonic purge on insert).
_PENDING_STATES: dict[str, tuple[str, float]] = {}
_STATE_TTL_SECONDS = 600

_GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
_GITHUB_USER = "https://api.github.com/user"
# Read/write repo access + profile: enough for the specialist's tools.
_SCOPES = "repo read:user"


def _credentials(request: Request) -> tuple[str, str]:
    settings = request.app.state.runtime.settings
    if not settings.github_client_id or not settings.github_client_secret:
        raise HTTPException(400, "GitHub OAuth is not configured (set VOICEFIRST_GITHUB_CLIENT_ID/SECRET)")
    return settings.github_client_id, settings.github_client_secret


async def _exchange_code(client_id: str, client_secret: str, code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            _GITHUB_TOKEN,
            json={"client_id": client_id, "client_secret": client_secret, "code": code},
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()


async def _github_login(token: str) -> str | None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(_GITHUB_USER, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code != 200:
            return None
        return resp.json().get("login")


async def _store_token(request: Request, user_id: str, token: str, login: str | None) -> None:
    runtime = request.app.state.runtime
    await runtime.tenant_settings.set(
        runtime.settings.default_tenant_id, "github_oauth_token", token
    )
    await runtime.tenant_settings.set(
        runtime.settings.default_tenant_id, "github_oauth_login", login or ""
    )
    # Live-apply so the running agents pick it up without a rebuild.
    runtime.settings.github_token = token


def _authorize_url(request: Request, user_id: str) -> str:
    """Build the GitHub authorize URL and remember a CSRF state for it."""
    client_id, _ = _credentials(request)
    # Purge expired states, then remember this one.
    now = time.time()
    for key in [k for k, (_, ts) in _PENDING_STATES.items() if now - ts > _STATE_TTL_SECONDS]:
        _PENDING_STATES.pop(key, None)
    state = secrets.token_urlsafe(24)
    _PENDING_STATES[state] = (user_id, now)
    redirect = httpx.URL(_GITHUB_AUTHORIZE, params={
        "client_id": client_id,
        "redirect_uri": str(request.base_url).rstrip("/") + "/api/github/oauth/callback",
        "scope": _SCOPES,
        "state": state,
    })
    return str(redirect)


@router.get("/authorize-url")
async def authorize_url(request: Request, identity: Identity = Depends(require_role_ops)):
    """JSON variant for the UI: return the consent-page URL to navigate to."""
    return {"authorize_url": _authorize_url(request, identity.policy.user_id)}


@router.get("/start")
async def start(request: Request, identity: Identity = Depends(require_role_ops)):
    """Kick off the connect flow: redirect the browser to GitHub's consent page."""
    return RedirectResponse(_authorize_url(request, identity.policy.user_id))


@router.get("/callback")
async def callback(request: Request, code: str | None = None, state: str | None = None):
    """Exchange the authorization code and store the resulting token."""
    if not code or not state:
        raise HTTPException(400, "Missing code or state")
    pending = _PENDING_STATES.pop(state, None)
    if pending is None or time.time() - pending[1] > _STATE_TTL_SECONDS:
        raise HTTPException(400, "Unknown or expired OAuth state")
    user_id, _ = pending

    client_id, client_secret = _credentials(request)
    token_resp = await _exchange_code(client_id, client_secret, code)
    token = token_resp.get("access_token")
    if not token:
        detail = token_resp.get("error_description") or token_resp.get("error") or "token exchange failed"
        raise HTTPException(400, f"GitHub OAuth failed: {detail}")

    login = await _github_login(token)
    runtime = request.app.state.runtime
    await _store_token(request, user_id, token, login)
    await runtime.audit.record(
        actor=user_id,
        action="github.oauth.connected",
        resource=f"github:{login or 'unknown'}",
        tenant_id=runtime.settings.default_tenant_id,
    )

    return_url = request.app.state.runtime.settings.github_oauth_return_url
    return RedirectResponse(return_url)


@router.get("/status")
async def status(request: Request, identity: Identity = Depends(require_identity)):
    runtime = request.app.state.runtime
    token = runtime.settings.github_token
    if not token:
        stored = await runtime.tenant_settings.get(
            runtime.settings.default_tenant_id, "github_oauth_token"
        )
        token = stored or None
        if token:
            runtime.settings.github_token = token
    login = await runtime.tenant_settings.get(
        runtime.settings.default_tenant_id, "github_oauth_login"
    )
    return {
        "configured": bool(runtime.settings.github_client_id and runtime.settings.github_client_secret),
        "connected": bool(token),
        "login": login or None,
        "token_prefix": (token[:4] + "…") if token else None,
    }


@router.delete("")
async def disconnect(request: Request, identity: Identity = Depends(require_role_ops)):
    runtime = request.app.state.runtime
    await runtime.tenant_settings.set(runtime.settings.default_tenant_id, "github_oauth_token", "")
    await runtime.tenant_settings.set(runtime.settings.default_tenant_id, "github_oauth_login", "")
    runtime.settings.github_token = None
    await runtime.audit.record(
        actor=identity.policy.user_id,
        action="github.oauth.disconnected",
        resource="github",
        tenant_id=identity.policy.tenant_id,
    )
    return {"connected": False}
