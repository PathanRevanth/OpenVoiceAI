"""Shared runtime dependencies injected into every agent run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from voicefirst.control_plane.auth import Identity
from voicefirst.control_plane.policy import PolicyContext, Role

if TYPE_CHECKING:
    pass


@dataclass
class AgentDeps:
    """Everything a tool or capability may need during a run.

    Kept deliberately small: identity/policy for authorization, the runtime
    for cross-service access (audit, events, usage), and per-run context.
    """

    tenant_id: str
    user_id: str
    role: str = Role.VIEWER
    conversation_id: str | None = None
    run_id: str | None = None
    channel: str = "chat"  # chat | voice
    # Plan-approval gate: while True, the orchestrator's `write_plan` call is
    # deferred for human approval before any work executes. Released on the
    # resume that approves the plan (denials keep it raised so a revised plan
    # re-presents).
    plan_gate: bool = True
    runtime: Any = None  # AgentRuntime; typed as Any to avoid a circular import
    collector: Any = None  # SpanCollector for the active run (set per request)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def policy(self) -> PolicyContext:
        return PolicyContext(tenant_id=self.tenant_id, user_id=self.user_id, role=self.role)

    @classmethod
    def from_identity(cls, identity: Identity, **kwargs: Any) -> "AgentDeps":
        return cls(
            tenant_id=identity.policy.tenant_id,
            user_id=identity.policy.user_id,
            role=identity.policy.role,
            **kwargs,
        )
