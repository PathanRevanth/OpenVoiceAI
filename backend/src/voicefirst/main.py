"""Voice-First AI control plane â€” FastAPI application factory.

Production posture (all toggleable via `VOICEFIRST_*` env):

* Middleware stack, outermost first: CORS â†’ request context (X-Request-ID +
  access logs + metrics) â†’ security headers â†’ rate limit â†’ routes.
* `GET /health` â€” liveness (process up). `GET /ready` â€” readiness (DB ping,
  scheduler alive). `GET /metrics` â€” Prometheus exposition.
* Unhandled exceptions return a JSON 500 carrying the request id; details
  stay in the server log, never in the response body.
* Startup validates safety-critical configuration and logs loud warnings
  (default secret key, disabled auth) without blocking demo mode.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from voicefirst.agents.runtime import AgentRuntime
from voicefirst.api import routes
from voicefirst.api.agent_access_routes import router as agent_access_router
from voicefirst.api.connections_routes import router as connections_router
from voicefirst.api.github_oauth_routes import router as github_oauth_router
from voicefirst.api.model_routes import router as model_router
from voicefirst.api.middleware import (
    Metrics,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from voicefirst.api.platform_routes import router as platform_router
from voicefirst.api.studio_routes import router as studio_router
from voicefirst.api.triggers_routes import router as triggers_router
from voicefirst.api.voice import router as voice_router
from voicefirst.api.voice_twilio import router as twilio_router
from voicefirst.settings import get_settings

if TYPE_CHECKING:
    from voicefirst.control_plane.event_subscriptions import EventDispatcher
    from voicefirst.control_plane.triggers import TriggerScheduler

logger = logging.getLogger("voicefirst")


def _configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level)
    logging.getLogger("voicefirst").setLevel(level)
    if settings.log_format == "json":
        # Plain-format access logs are emitted by RequestContextMiddleware; in
        # json mode it writes single-line JSON, so silence its plain handler.
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def _log_config_warnings(settings) -> None:  # noqa: ANN001 - Settings import cycle avoided
    if settings.secret_key == "dev-insecure-change-me":
        logger.warning(
            "VOICEFIRST_SECRET_KEY is the default value â€” provider API keys stored "
            "in LLM Connections are NOT protected. Set a strong secret in production."
        )
    if settings.auth_mode != "betterauth":
        logger.warning(
            "VOICEFIRST_AUTH_MODE=%s â€” the API trusts X-VF-User/X-VF-Role headers. "
            "Set VOICEFIRST_AUTH_MODE=betterauth in production.",
            settings.auth_mode,
        )
    if settings.database_url.startswith("sqlite"):
        logger.info("Using SQLite at %s â€” switch to PostgreSQL for production.", settings.database_url)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    settings = get_settings()
    _log_config_warnings(settings)

    # Observability: Logfire (OpenTelemetry-backed) when a token is present.
    if settings.logfire_token:
        import logfire

        logfire.configure(
            token=settings.logfire_token,
            environment=settings.logfire_environment,
            service_name="voicefirst-control-plane",
        )
        # Native pydantic-ai OTel instrumentation: spans for every model
        # request, tool call, and delegation â€” visible in Logfire automatically.
        try:
            logfire.instrument_pydantic_ai()
        except Exception as exc:  # noqa: BLE001 - tracing must never block boot
            logger.warning("pydantic-ai instrumentation unavailable: %s", exc)

    runtime = await AgentRuntime.create()
    app.state.runtime = runtime
    if getattr(app.state, "metrics", None) is None:
        app.state.metrics = Metrics()

    # Restore a previously-connected GitHub OAuth token so the specialist
    # keeps working across restarts without re-authorizing.
    stored_token = await runtime.tenant_settings.get(settings.default_tenant_id, "github_oauth_token")
    if stored_token and not settings.github_token:
        settings.github_token = stored_token

    from voicefirst.control_plane.event_subscriptions import EventDispatcher
    from voicefirst.control_plane.triggers import TriggerScheduler

    trigger_scheduler = TriggerScheduler(runtime)
    app.state.trigger_scheduler = trigger_scheduler
    await trigger_scheduler.start()
    event_dispatcher = EventDispatcher(runtime.db, runtime.events, runtime.vault, runtime.audit)
    app.state.event_dispatcher = event_dispatcher
    await event_dispatcher.start()
    app.state.db = runtime.db
    app.state.identity = runtime.identity
    await runtime.audit.record(
        actor="system", action="platform.boot", resource="control-plane",
        tenant_id=settings.default_tenant_id,
        detail={"model": settings.default_model, "demo": not settings.has_chat_key},
    )
    yield
    await app.state.trigger_scheduler.stop()
    await app.state.event_dispatcher.stop()
    await runtime.audit.record(actor="system", action="platform.shutdown", resource="control-plane")
    await runtime.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Voice-first agentic control plane built on Pydantic AI v2",
        lifespan=lifespan,
    )
    app_metrics = Metrics()
    app.state.metrics = app_metrics

    # Outermost â†’ innermost (the last add_middleware call sees requests first).
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware, metrics=app_metrics)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.include_router(routes.router)
    app.include_router(connections_router)
    app.include_router(agent_access_router)
    app.include_router(triggers_router)
    app.include_router(studio_router)
    app.include_router(github_oauth_router)
    app.include_router(model_router)
    app.include_router(voice_router)
    app.include_router(twilio_router)
    app.include_router(platform_router)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None) or ""
        logger.exception("Unhandled error on %s %s [request_id=%s]", request.method, request.url.path, request_id)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request_id},
        )

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        runtime: AgentRuntime | None = getattr(app.state, "runtime", None)
        agents = sorted(runtime.specialists.keys()) + ["orchestrator"] if runtime else []
        return {
            "status": "ok",
            "app": settings.app_name,
            "version": app.version,
            "model": settings.default_model,
            "demo_mode": not settings.has_chat_key,
            "agents": agents,
        }

    @app.get("/ready", tags=["meta"])
    async def ready() -> JSONResponse:
        """Readiness: DB reachable and the trigger scheduler loop alive."""
        runtime: AgentRuntime | None = getattr(app.state, "runtime", None)
        if runtime is None:
            return JSONResponse(status_code=503, content={"ready": False, "reason": "runtime not started"})
        checks: dict[str, bool] = {}
        try:
            started = time.perf_counter()
            async with runtime.db.session() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = True
            db_ms = round((time.perf_counter() - started) * 1000, 1)
        except Exception:  # noqa: BLE001
            checks["database"] = False
            db_ms = None
        scheduler: TriggerScheduler | None = getattr(app.state, "trigger_scheduler", None)
        checks["trigger_scheduler"] = bool(scheduler and scheduler.alive)
        dispatcher: EventDispatcher | None = getattr(app.state, "event_dispatcher", None)
        checks["event_dispatcher"] = bool(dispatcher and dispatcher.alive)
        ready = all(checks.values())
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"ready": ready, "checks": checks, "database_latency_ms": db_ms},
        )

    @app.get("/metrics", tags=["meta"])
    async def metrics() -> JSONResponse:
        from starlette.responses import Response

        app_metrics: Metrics | None = getattr(app.state, "metrics", None)
        if not settings.metrics_enabled or app_metrics is None:
            return Response(status_code=404)
        return Response(
            content=app_metrics.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app


app = create_app()
