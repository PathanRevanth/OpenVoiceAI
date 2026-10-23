"""Identity service: resolves every request to an authenticated principal.

Two modes, driven by `VOICEFIRST_AUTH_MODE`:

`disabled` (dev default)
    Trusts `X-VF-User` / `X-VF-Role` headers so the API is usable without the
    frontend (curl, tests). Falls back to `dev_role`.

`betterauth` (production)
    The Next.js experience layer owns authentication via Better Auth
    (sessions + admin plugin roles). Two credential forms are accepted:

    * `Authorization: Bearer <JWT>` — verified against Better Auth's JWKS
      (`auth_jwks_url`), with the `role` claim mapped to RBAC.
    * `Authorization: Bearer <session-token>` — introspected via
      `/get-session` on `auth_base_url`; slower but always available.

The resolved `Identity.policy` (`PolicyContext`) drives RBAC, approvals, and
audit attribution everywhere downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, WebSocket
from jwt import PyJWKClient

from voicefirst.control_plane.policy import PolicyContext, Role
from voicefirst.settings import Settings, get_settings

_BETTER_AUTH_ROLE_MAP = {
    "admin": Role.ADMIN,
    "ops": Role.OPS,
    "operator": Role.OPS,
    "viewer": Role.VIEWER,
    "user": Role.VIEWER,
    "member": Role.VIEWER,
}


@dataclass
class Identity:
    policy: PolicyContext
    name: str = ""
    email: str = ""
    auth_mode: str = "dev"


class IdentityService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._jwks_client: PyJWKClient | None = None
        self._http = None  # lazily created httpx client for session introspection
        self._pat_verifier: Any = None  # async callable set by the runtime

    def set_pat_verifier(self, verifier: Any) -> None:
        """Runtime injects UserTokenService.verify here at startup."""
        self._pat_verifier = verifier

    @property
    def dev_mode(self) -> bool:
        return self.settings.auth_mode != "betterauth"

    # -- Better Auth JWT --------------------------------------------------

    def _jwks(self) -> PyJWKClient:
        if self._jwks_client is None:
            if not self.settings.auth_jwks_url:
                raise HTTPException(status_code=500, detail="auth_jwks_url is not configured")
            self._jwks_client = PyJWKClient(self.settings.auth_jwks_url, cache_keys=True, lifespan=3600)
        return self._jwks_client

    def _verify_jwt(self, token: str) -> dict[str, Any]:
        try:
            key = self._jwks().get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                key.key,
                # Better Auth defaults to EdDSA (Ed25519); ES256/RS256 cover
                # deployments with a custom keyPairConfig.
                algorithms=["EdDSA", "ES256", "RS256"],
                options={"verify_aud": False},  # Better Auth omits `aud` by default
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=401, detail=f"Invalid auth token: {exc}") from exc

    @staticmethod
    def _claims_to_identity(claims: dict[str, Any], tenant: str) -> Identity:
        sub = str(claims.get("sub") or claims.get("userId") or "unknown")
        email = str(claims.get("email") or "")
        name = str(claims.get("name") or email.split("@")[0] or sub)
        role_raw = claims.get("role", "viewer")
        if isinstance(role_raw, dict):
            role_raw = role_raw.get("role", "viewer")
        role = _BETTER_AUTH_ROLE_MAP.get(str(role_raw).lower(), Role.VIEWER)
        return Identity(
            policy=PolicyContext(tenant_id=tenant, user_id=sub, role=role),
            name=name,
            email=email,
            auth_mode="betterauth",
        )

    # -- Better Auth session introspection ---------------------------------

    async def _introspect_session(self, token: str) -> Identity:
        import httpx

        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10.0)
        base = (self.settings.auth_base_url or "").rstrip("/")
        if not base:
            raise HTTPException(status_code=500, detail="auth_base_url is not configured")
        try:
            resp = await self._http.get(
                f"{base}/get-session",
                cookies={"better-auth.session_token": token},
                headers={"authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Session introspection failed") from exc
        user = (data or {}).get("user") or {}
        if not user.get("id"):
            raise HTTPException(status_code=401, detail="Invalid session")
        role_raw = user.get("role", "viewer")
        if isinstance(role_raw, dict):
            role_raw = role_raw.get("role", "viewer")
        role = _BETTER_AUTH_ROLE_MAP.get(str(role_raw).lower(), Role.VIEWER)
        return Identity(
            policy=PolicyContext(tenant_id=self.settings.default_tenant_id, user_id=user["id"], role=role),
            name=str(user.get("name") or ""),
            email=str(user.get("email") or ""),
            auth_mode="betterauth",
        )

    # -- Dev mode ------------------------------------------------------------

    def _dev_identity(self, user: str | None, role: str | None) -> Identity:
        from voicefirst.control_plane.policy import ROLE_LEVELS

        if role not in ROLE_LEVELS:
            role = self.settings.dev_role
        return Identity(
            policy=PolicyContext(
                tenant_id=self.settings.default_tenant_id,
                user_id=user or "dev-user",
                role=role,
            ),
            name=user or "Developer",
            email=f"{user or 'dev'}@localhost",
            auth_mode="dev",
        )

    # -- Public resolution -----------------------------------------------------

    async def verify_bearer(self, token: str) -> Identity:
        # Personal access tokens work in every mode: scripts call the API.
        if token.startswith("vf_pat_") and self._pat_verifier is not None:
            record = await self._pat_verifier(token)
            if record is None:
                raise HTTPException(status_code=401, detail="Invalid access token")
            return Identity(
                policy=PolicyContext(
                    tenant_id=record.tenant_id, user_id=record.user_id, role=record.role
                ),
                name=f"PAT:{record.label}",
                email=f"{record.user_id}@pat",
                auth_mode="pat",
            )
        if self.dev_mode:
            return self._dev_identity(None, None)
        # A JWT has three dot-separated base64 segments; session tokens don't.
        if token.count(".") == 2:
            claims = self._verify_jwt(token)
            return self._claims_to_identity(claims, self.settings.default_tenant_id)
        return await self._introspect_session(token)

    def _headers(self, source: Request | WebSocket) -> tuple[str | None, str | None]:
        h = source.headers
        user = h.get("x-vf-user") or h.get("x-dev-user")
        role = h.get("x-vf-role") or h.get("x-dev-role")
        return user, role

    async def from_request(self, request: Request) -> Identity:
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            return await self.verify_bearer(auth[7:].strip())
        if self.dev_mode:
            user, role = self._headers(request)
            return self._dev_identity(user, role)
        raise HTTPException(status_code=401, detail="Missing bearer token")

    async def from_websocket(self, ws: WebSocket) -> Identity:
        token = ws.query_params.get("token") or ""
        auth = ws.headers.get("authorization") or ""
        if not token and auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if token and not self.dev_mode:
            return await self.verify_bearer(token)
        if self.dev_mode:
            user, role = self._headers(ws)
            return self._dev_identity(user or ws.query_params.get("dev_user"), role)
        raise HTTPException(status_code=401, detail="Missing auth token")

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()


# --- FastAPI dependencies -----------------------------------------------------


async def require_identity(request: Request) -> Identity:
    service: IdentityService = request.app.state.identity
    return await service.from_request(request)


async def require_role_ops(identity: Identity = Depends(require_identity)) -> Identity:
    if not Role.at_least(identity.policy.role, Role.OPS):
        raise HTTPException(status_code=403, detail="Ops role required")
    return identity


async def require_role_admin(identity: Identity = Depends(require_identity)) -> Identity:
    if identity.policy.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required")
    return identity
