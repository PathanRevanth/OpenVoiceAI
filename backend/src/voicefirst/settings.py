"""Platform settings, loaded from environment variables / `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent
SKILLS_DIR = PACKAGE_DIR / "skills"


class Settings(BaseSettings):
    """Voice-First AI control-plane configuration.

    Every value can be overridden with a `VOICEFIRST_`-prefixed environment variable.
    """

    model_config = SettingsConfigDict(
        env_prefix="VOICEFIRST_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    app_name: str = "Voice-First AI Control Plane"
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    # "plain" for human-readable access logs; "json" for log shippers (OTel, Loki).
    log_format: str = "plain"  # plain | json
    log_level: str = "INFO"

    # --- Production hardening ---
    # Token-bucket rate limit applied per caller (bearer token hash or client IP)
    # to /api/* routes. Set rate_limit_enabled=false or a very high rate to disable.
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 120
    # Expose Prometheus metrics at /metrics (counters + latency histograms).
    metrics_enabled: bool = True
    # When set, outbound webhooks (approvals + trigger results) are signed with
    # HMAC-SHA256 in the X-VoiceFirst-Signature: sha256=<hex> header.
    webhook_secret: str | None = None

    # --- Persistence ---
    # SQLite for zero-config local dev; use a postgres DSN in production:
    #   postgresql+asyncpg://voicefirst:voicefirst@db:5432/voicefirst
    database_url: str = "sqlite+aiosqlite:///./voicefirst.db"

    # --- Cache / event bus (optional) ---
    redis_url: str | None = None

    # --- Observability (optional) ---
    logfire_token: str | None = None
    logfire_environment: str = "local"
    instrument_otel: bool = True

    # --- Model gateway ---
    # Any pydantic-ai model string, e.g. openai:gpt-5.2, anthropic:claude-..., google:gemini-2.5-flash
    # Special value "demo" runs a local echo model so the platform works without API keys.
    default_model: str = "anthropic:claude-haiku-5-5"
    realtime_model: str = "google:gemini-3.1-flash-live-preview"

    # --- Provider keys (also read natively by pydantic-ai) ---
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None

    # --- Safety / governance ---
    # Roles: viewer < ops < admin. Mutating tools require at least this role.
    mutating_min_role: str = "ops"
    # When True (default) mutating tools require explicit human approval instead of
    # merely a role check.
    require_approval_for_mutations: bool = True
    # When True (default) the orchestrator's plan is surfaced to the operator and
    # the run pauses until the plan is approved before any delegation executes.
    require_plan_approval: bool = True
    # Platform-wide budgets (harness SpendLimits).
    daily_spend_limit_usd: float = 25.0
    # A deep-research delegation (plan + several searches/fetches + synthesis)
    # routinely needs more than 200k total tokens across the orchestrator and
    # specialist, so the ceiling leaves room for a real research pass while
    # still stopping runaway loops.
    per_run_token_limit: int = 500_000
    # Compliance: redact PII (emails, phone numbers, SSNs, Luhn-valid card
    # numbers, IP addresses) from agent output. Toggle at runtime via
    # PATCH /api/policies (admin) without a restart.
    redact_pii: bool = False
    # Max concurrent provider model requests (pydantic-ai ConcurrencyLimitedModel)
    # shared across all agents — protects against provider rate limits.
    model_concurrency_limit: int = 16
    # Per-agent ceiling on concurrent runs (pydantic-ai Agent max_concurrency).
    agent_max_concurrency: int = 8
    # Model thinking/reasoning effort: off | minimal | low | medium | high.
    # Reasoning tokens cost money — enable deliberately.
    thinking_level: str = "off"
    # Provider-native web search/fetch upgrade for the Research agent
    # (OpenAI/Google). Models without support fall back to local tools
    # automatically; set false to always use local-only tools.
    native_web_tools: bool = True
    # Scan TOOL RESULTS for injected instructions (StackOne defender).
    injection_defender_enabled: bool = True
    # Durable per-step event log + continuable snapshots (voicefirst_steps.db).
    step_persistence_enabled: bool = True
    step_store_path: str = "voicefirst_steps.db"
    # Monitor that warns when the prompt-cache prefix collapses.
    cache_bust_monitor_enabled: bool = True
    # Offload attachment parts >= this size from stored conversation history
    # into the media store (bytes leave the per-turn model request).
    media_offload_enabled: bool = True
    media_offload_threshold_bytes: int = 512_000
    media_store_path: str = "voicefirst_media"
    # Sandbox-Python upgrades: code_mode on data-heavy specialists, and the
    # orchestrator's scripted multi-agent workflows (run_workflow tool).
    code_mode_enabled: bool = True
    # Second-model advisor for the orchestrator (max 2 uses/run). Cost note:
    # advisor calls bill tokens on the same provider account.
    advisor_enabled: bool = True
    advisor_model: str | None = None  # defaults to the platform default model
    # Agent-authored capabilities (Agent Studio). Admin-gated at runtime via
    # PATCH /api/studio/capability-creation; drafts land in this directory and
    # mount only after an admin re-enables/rebuilds.
    capability_creation_enabled: bool = False
    authored_capabilities_path: str = "voicefirst_authored_capabilities"

    # --- Integration credentials (optional; enable real tool calls) ---
    github_token: str | None = None
    slack_bot_token: str | None = None
    mongodb_uri: str | None = None

    # --- GitHub OAuth app (connect flow for the GitHub agent) ---
    github_client_id: str | None = None
    github_client_secret: str | None = None
    # Where to send the browser after GitHub authorizes (frontend route).
    github_oauth_return_url: str = "http://localhost:3000/settings?github=connected"

    # --- MCP registry (optional remote MCP servers; streamable-http) ---
    github_mcp_url: str | None = None
    slack_mcp_url: str | None = None
    mongodb_mcp_url: str | None = None

    # --- Multi-tenancy ---
    default_tenant_id: str = "tenant-default"

    # --- Authentication (Better Auth in the Next.js experience layer) ---
    # disabled  : dev mode — trust X-VF-User / X-VF-Role headers (default)
    # betterauth: verify Bearer JWTs against Better Auth JWKS, fall back to
    #             session introspection via the Better Auth base URL.
    auth_mode: str = "disabled"  # disabled | betterauth
    auth_jwks_url: str | None = None  # e.g. http://localhost:3000/api/auth/jwks
    auth_base_url: str | None = None  # e.g. http://localhost:3000/api/auth
    dev_role: str = "admin"  # role granted in `disabled` mode when no header is present

    # --- Telephony (Twilio Media Streams) ---
    # Set to require and verify Twilio's X-Twilio-Signature on the bridge.
    twilio_auth_token: str | None = None

    # --- Secrets ---
    # Master key for encrypting user-supplied provider API keys at rest
    # (AES-128-CBC via Fernet, key derived with SHA-256). Set a strong value
    # in production; rotating it invalidates stored connections.
    secret_key: str = "dev-insecure-change-me"

    @property
    def skills_dir(self) -> Path:
        """Directory containing per-agent SKILL.md folders."""
        return SKILLS_DIR

    @property
    def has_realtime_key(self) -> bool:
        """Whether a provider key exists for the configured realtime model."""
        model = self.realtime_model.lower()
        if model.startswith("openai") or model.startswith("azure"):
            return bool(self.openai_api_key)
        if model.startswith("google") or model.startswith("gemini"):
            return bool(self.google_api_key)
        if model.startswith("xai"):
            return bool(self.openai_api_key or self.anthropic_api_key or self.google_api_key)
        return False

    @property
    def has_chat_key(self) -> bool:
        if self.default_model == "demo":
            return True
        if self.default_model.startswith("openai"):
            return bool(self.openai_api_key)
        if self.default_model.startswith("anthropic"):
            return bool(self.anthropic_api_key)
        if self.default_model.startswith("google"):
            return bool(self.google_api_key)
        # Unknown provider prefix — assume the user configured their environment.
        return True


@lru_cache
def get_settings() -> Settings:
    return Settings()
