"""SQLAlchemy 2.0 async models for the control plane.

Tables cover the platform's governance surface: registries (agents, skills, MCP
servers), conversations/runs/messages, human approvals, hash-chained audit log,
and per-run token/cost usage.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Base(DeclarativeBase):
    pass


def JSONType() -> Any:  # noqa: N802
    """JSON column that uses JSONB on PostgreSQL for indexability."""
    return JSON().with_variant(JSONB(), "postgresql")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------


class AgentRecord(Base, TimestampMixin):
    """An agent registered in the control plane."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(32), default="specialist")  # orchestrator | specialist
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    status: Mapped[str] = mapped_column(String(32), default="enabled")  # enabled | disabled
    allowed_models: Mapped[list | None] = mapped_column(JSONType(), nullable=True)
    mcp_servers: Mapped[list | None] = mapped_column(JSONType(), nullable=True)
    skills: Mapped[list | None] = mapped_column(JSONType(), nullable=True)
    owner: Mapped[str] = mapped_column(String(128), default="platform")
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONType(), nullable=True)


class SkillRecord(Base, TimestampMixin):
    """A SKILL.md entry registered in the skill registry."""

    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    path: Mapped[str] = mapped_column(String(512))
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    status: Mapped[str] = mapped_column(String(32), default="enabled")
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONType(), nullable=True)


class MCPServerRecord(Base, TimestampMixin):
    """An MCP server registered in the MCP registry."""

    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    transport: Mapped[str] = mapped_column(String(32), default="streamable-http")  # streamable-http | sse | stdio
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    command: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    headers: Mapped[dict | None] = mapped_column(JSONType(), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="enabled")  # enabled | disabled
    health: Mapped[str] = mapped_column(String(32), default="unknown")  # unknown | healthy | unreachable
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONType(), nullable=True)


# ---------------------------------------------------------------------------
# Conversations, runs, messages
# ---------------------------------------------------------------------------


class ConversationRecord(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(256), default="New conversation")
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(32), default="chat")  # chat | voice
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    archived: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    history_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class RunRecord(Base, TimestampMixin):
    """One agent run (orchestrator or delegated)."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("conversations.id"), index=True)
    agent_slug: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running")  # running | completed | failed | awaiting_approval | cancelled
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_json: Mapped[dict | None] = mapped_column(JSONType(), nullable=True)
    deferred_requests: Mapped[dict | None] = mapped_column(JSONType(), nullable=True)
    spans_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Serialized pydantic-ai message history (ModelMessagesTypeAdapter.dump_json) so a
    # run can be resumed exactly after an approval decision.
    history_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)


class MessageRecord(Base, TimestampMixin):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), ForeignKey("conversations.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(32))  # user | assistant | system | tool
    agent_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[dict] = mapped_column(JSONType())
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)


Index("ix_messages_conversation_created", MessageRecord.conversation_id, MessageRecord.created_at)


# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------


class ApprovalRecord(Base, TimestampMixin):
    """A human-in-the-loop tool-approval request."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    tool_name: Mapped[str] = mapped_column(String(256))
    tool_call_id: Mapped[str] = mapped_column(String(128))
    args_json: Mapped[dict] = mapped_column(JSONType())
    risk: Mapped[str] = mapped_column(String(32), default="mutating")  # mutating | destructive
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending | approved | denied | expired
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)


class AuditRecord(Base):
    """Immutable, hash-chained audit trail entry."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(128), index=True)
    resource: Mapped[str] = mapped_column(String(256), default="")
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    detail: Mapped[dict | None] = mapped_column(JSONType(), nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class UsageRecord(Base, TimestampMixin):
    """Token/cost usage captured per run."""

    __tablename__ = "usage"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_slug: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str | None] = mapped_column(String(128))
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class ConnectionRecord(Base, TimestampMixin):
    """A user-scoped provider connection (LLM keys), encrypted at rest.

    `purpose` splits agent (chat) models from voice (realtime) models.
    The raw key is never stored — only a Fernet token — and is never
    returned to any client, only the last 4 characters for identification.
    """

    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("user_id", "purpose", "provider", name="uq_connection_user_purpose_provider"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    purpose: Mapped[str] = mapped_column(String(16))  # agents | voice
    provider: Mapped[str] = mapped_column(String(16))  # openai | anthropic | google
    model: Mapped[str] = mapped_column(String(128))
    key_encrypted: Mapped[str] = mapped_column(Text)
    key_last4: Mapped[str] = mapped_column(String(4))
    fallback_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="untested")  # untested | valid | invalid
    test_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AgentKeyRecord(Base, TimestampMixin):
    """An access token for calling one agent programmatically.

    Webhooks and API clients authenticate with the plaintext key, which is
    shown once at creation and stored only as a SHA-256 hash.
    """

    __tablename__ = "agent_keys"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_slug: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(128), default="default")
    key_hash: Mapped[str] = mapped_column(String(64), index=True)
    key_last4: Mapped[str] = mapped_column(String(4))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TriggerRecord(Base, TimestampMixin):
    """A scheduled agent run owned by a user.

    schedule_type is "interval" (every interval_minutes) or "daily" (at
    time_of_day, UTC). Results are optionally delivered to webhook_url.
    """

    __tablename__ = "triggers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    agent_slug: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    schedule_type: Mapped[str] = mapped_column(String(16))  # interval | daily
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_of_day: Mapped[str | None] = mapped_column(String(5), nullable=True)  # HH:MM UTC
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class TenantSettingRecord(Base, TimestampMixin):
    """Tenant-scoped key/value platform settings (e.g. notification URLs)."""

    __tablename__ = "tenant_settings"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_tenant_setting"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)


class UserTokenRecord(Base, TimestampMixin):
    """A personal access token for platform API calls, hashed at rest."""

    __tablename__ = "user_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(128), default="default")
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    key_hash: Mapped[str] = mapped_column(String(64), index=True)
    key_last4: Mapped[str] = mapped_column(String(4))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SkillVersionRecord(Base, TimestampMixin):
    """Version history for SKILL.md edits made in Agent Studio."""

    __tablename__ = "skill_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64))


class VoiceSessionRecord(Base, TimestampMixin):
    """One realtime voice session (browser or telephony) with quality metrics.

    Captures the numbers voice platforms are judged on: time-to-first-word,
    turns, barge-ins (caller interrupting the agent mid-utterance), and the
    session outcome.
    """

    __tablename__ = "voice_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(16), default="browser")  # browser | twilio
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    voice: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | ended | failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    turns: Mapped[int] = mapped_column(Integer, default=0)
    ttfw_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # time to first word
    barge_ins: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class EventSubscriptionRecord(Base, TimestampMixin):
    """A tenant webhook subscription for platform events.

    Deliveries are HMAC-signed (like GitHub/Stripe) when a subscription secret
    (or the platform webhook secret) exists, retried with backoff, and
    auto-disabled after repeated consecutive failures (dead-letter).
    """
    __tablename__ = "event_subscriptions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(1024))
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    events: Mapped[list] = mapped_column(JSONType(), default=list)  # e.g. ["run.failed", "budget.*"]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)  # consecutive failures
    deliveries_total: Mapped[int] = mapped_column(Integer, default=0)
    dead_lettered: Mapped[bool] = mapped_column(Boolean, default=False)
    last_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunFeedbackRecord(Base, TimestampMixin):
    """Thumbs up/down on one assistant run, with an optional note."""

    __tablename__ = "run_feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    rating: Mapped[str] = mapped_column(String(16))  # up | down
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConversationShareRecord(Base, TimestampMixin):
    """A revocable public link for one conversation (read-only export view)."""

    __tablename__ = "conversation_shares"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class PromptTemplateRecord(Base, TimestampMixin):
    """A user-saved prompt template, insertable from the composer library."""

    __tablename__ = "prompt_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(Text)


class ProjectRecord(Base, TimestampMixin):
    """A named workspace grouping conversations with shared instructions."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    instructions: Mapped[str] = mapped_column(Text, default="")
    # Claude-style identity: folder icon key + color key (see frontend project-style).
    icon: Mapped[str] = mapped_column(String(32), default="folder")
    color: Mapped[str] = mapped_column(String(32), default="slate")
