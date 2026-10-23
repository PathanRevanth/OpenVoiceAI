"""Production HTTP middleware stack.

Four orthogonal pieces, all dependency-free:

* `RequestContextMiddleware` — attaches an `X-Request-ID` to every request
  (honoring an inbound header) and emits one structured access log line per
  request. JSON format when `VOICEFIRST_LOG_FORMAT=json` for log shippers.
* `SecurityHeadersMiddleware` — the OWASP baseline headers on every response.
* `RateLimitMiddleware` — a token bucket per caller (bearer-token hash, else
  client IP) with `429` + `Retry-After`. Cheap in-process limiting for
  single-instance deployments; put a real gateway in front for horizontal scale.
* `Metrics` — Prometheus text-format counters/histograms over request rate,
  latency, and status, exported at `/metrics`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from voicefirst.settings import get_settings

logger = logging.getLogger("voicefirst.access")

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

# Paths exempted from rate limiting (probes used by orchestrators/k8s).
_RATE_LIMIT_EXEMPT = {"/health", "/ready", "/metrics"}


def _client_key(request: Request) -> str:
    """Rate-limit identity: bearer-token hash when present, else client IP."""
    auth = request.headers.get("authorization") or ""
    if auth:
        return "tok:" + hashlib.sha256(auth.encode()).hexdigest()[:24]
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
    return "ip:" + ip


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class RateLimiter:
    """In-memory token bucket: `rate_per_minute` requests, burst of 1 minute's worth."""

    def __init__(self, rate_per_minute: int) -> None:
        self.rate = float(rate_per_minute)
        self.capacity = float(rate_per_minute)
        self._buckets: dict[str, _Bucket] = {}
        self._last_sweep = time.monotonic()

    def check(self, key: str) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            self._buckets[key] = _Bucket(tokens=self.capacity - 1.0, updated_at=now)
            allowed = True
        else:
            elapsed = now - bucket.updated_at
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * (self.rate / 60.0))
            bucket.updated_at = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                allowed = True
            else:
                allowed = False
        if now - self._last_sweep > 300.0:
            cutoff = now - 600.0
            self._buckets = {k: b for k, b in self._buckets.items() if b.updated_at > cutoff}
            self._last_sweep = now
        if allowed:
            return True, 0
        return False, max(1, int(60.0 / self.rate))


class Metrics:
    """Prometheus-compatible in-process metrics registry."""

    LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

    def __init__(self) -> None:
        self.requests_total: dict[tuple[str, str, int], int] = defaultdict(int)
        self.latency_buckets: dict[tuple[str, str, float], int] = defaultdict(int)
        self.latency_count: dict[tuple[str, str], int] = defaultdict(int)
        self.latency_sum: dict[tuple[str, str], float] = defaultdict(float)
        self.started_at = time.time()

    def observe(self, method: str, path: str, status: int, duration: float) -> None:
        self.requests_total[(method, path, status)] += 1
        key = (method, path)
        self.latency_count[key] += 1
        self.latency_sum[key] += duration
        for bound in self.LATENCY_BUCKETS:
            if duration <= bound:
                self.latency_buckets[(method, path, bound)] += 1

    def render(self) -> bytes:
        lines: list[str] = [
            "# HELP voicefirst_uptime_seconds Process uptime in seconds.",
            "# TYPE voicefirst_uptime_seconds gauge",
            f"voicefirst_uptime_seconds {time.time() - self.started_at:.3f}",
            "# HELP voicefirst_http_requests_total HTTP requests processed.",
            "# TYPE voicefirst_http_requests_total counter",
        ]
        for (method, path, status), count in sorted(self.requests_total.items()):
            lines.append(
                f'voicefirst_http_requests_total{{method="{method}",path="{path}",status="{status}"}} {count}'
            )
        lines.extend(
            [
                "# HELP voicefirst_http_request_duration_seconds Request latency.",
                "# TYPE voicefirst_http_request_duration_seconds histogram",
            ]
        )
        routes = sorted({(m, p) for m, p, _ in self.latency_buckets} | set(self.latency_count))
        for method, path in routes:
            key = (method, path)
            # observe() increments every bound the duration fits under, so the
            # stored values are already cumulative — emit them directly.
            for bound in self.LATENCY_BUCKETS:
                lines.append(
                    f'voicefirst_http_request_duration_seconds_bucket{{method="{method}",path="{path}",le="{bound}"}} {self.latency_buckets[(method, path, bound)]}'
                )
            lines.append(
                f'voicefirst_http_request_duration_seconds_bucket{{method="{method}",path="{path}",le="+Inf"}} {self.latency_count[key]}'
            )
            count = self.latency_count[key]
            total = self.latency_sum[key]
            lines.append(
                f'voicefirst_http_request_duration_seconds_sum{{method="{method}",path="{path}"}} {total:.6f}'
            )
            lines.append(
                f'voicefirst_http_request_duration_seconds_count{{method="{method}",path="{path}"}} {count}'
            )
        return ("\n".join(lines) + "\n").encode()


class RequestContextMiddleware(BaseHTTPMiddleware):
    """X-Request-ID propagation + one access log per request + metrics.\\
    """

    def __init__(self, app: Any, metrics: Metrics) -> None:
        super().__init__(app)
        self.metrics = metrics
        self._json_logs = get_settings().log_format == "json"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id")
        if not request_id:
            import uuid

            request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            self._log(request, 500, time.perf_counter() - started, request_id, error=True)
            raise
        duration = time.perf_counter() - started
        response.headers["X-Request-ID"] = request_id
        route = request.scope.get("route")
        path = getattr(route, "path", None) or request.url.path
        self.metrics.observe(request.method, path, response.status_code, duration)
        self._log(request, response.status_code, duration, request_id)
        return response

    def _log(
        self, request: Request, status: int, duration: float, request_id: str, *, error: bool = False
    ) -> None:
        identity = getattr(request.state, "identity", None)
        user = getattr(identity, "policy", None)
        fields = {
            "event": "http_request",
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "duration_ms": round(duration * 1000, 1),
            "request_id": request_id,
            "user_id": getattr(user, "user_id", None),
            "tenant_id": getattr(user, "tenant_id", None),
            "client": _client_key(request).split(":", 1)[1][:16],
        }
        if self._json_logs:
            logger.info(json.dumps(fields, default=str))
        elif error:
            logger.exception("5xx %s %s", request.method, request.url.path)
        else:
            logger.info(
                '%s %s -> %s %.1fms [%s]',
                request.method,
                request.url.path,
                status,
                fields["duration_ms"],
                request_id,
            )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """OWASP baseline headers; CSP excluded for the API (docs pages need inline)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Token-bucket limiting per caller; 429 + Retry-After when exceeded."""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        settings = get_settings()
        self.enabled = settings.rate_limit_enabled and settings.rate_limit_per_minute > 0
        self.limiter = RateLimiter(settings.rate_limit_per_minute)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.enabled or request.url.path in _RATE_LIMIT_EXEMPT:
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        allowed, retry_after = self.limiter.check(_client_key(request))
        if not allowed:
            return Response(
                content=json.dumps({"detail": "Rate limit exceeded"}),
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
