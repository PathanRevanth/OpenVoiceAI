"""AgentRuntime: the composition root of the control plane.

Holds all services (identity, policy, events, audit, usage, approvals,
registries) and the agent instances (orchestrator + specialists). Built once
at app startup; `AgentDeps.runtime` references it so tools can audit/publish.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import Agent

from voicefirst.agents.deps import AgentDeps
from voicefirst.agents.factory import (
    build_orchestrator,
    build_specialist,
    build_voice_companion,
    resolve_model,
)
from voicefirst.control_plane.agent_access import AgentAccessService
from voicefirst.control_plane.approvals import ApprovalService
from voicefirst.control_plane.audit import AuditService
from voicefirst.control_plane.auth import IdentityService
from voicefirst.control_plane.connections import ConnectionService, KeyVault
from voicefirst.control_plane.events import EventBus
from voicefirst.control_plane.policy import PolicyEngine
from voicefirst.control_plane.registry import AgentRegistry, MCPRegistry, SkillRegistry
from voicefirst.control_plane.tenant_settings import TenantSettingService
from voicefirst.control_plane.usage import UsageService
from voicefirst.control_plane.user_tokens import UserTokenService
from voicefirst.control_plane.voice_analytics import VoiceAnalyticsService
from voicefirst.db.session import Database
from voicefirst.settings import Settings, get_settings


@dataclass
class AgentRuntime:
    settings: Settings
    db: Database
    events: EventBus
    identity: IdentityService
    policy_engine: PolicyEngine
    audit: AuditService
    usage: UsageService
    approvals: ApprovalService
    tenant_settings: TenantSettingService
    agent_access: AgentAccessService
    user_tokens: UserTokenService
    connections: ConnectionService
    voice_analytics: VoiceAnalyticsService
    vault: KeyVault
    agents_registry: AgentRegistry
    skills_registry: SkillRegistry
    mcp_registry: MCPRegistry
    orchestrator: Agent[AgentDeps, Any]
    realtime_orchestrator: Agent[AgentDeps, Any] | None = None
    voice_companion: Agent[AgentDeps, Any] | None = None
    specialists: dict[str, Agent[AgentDeps, Any]] = field(default_factory=dict)

    @classmethod
    async def create(cls, settings: Settings | None = None) -> "AgentRuntime":
        settings = settings or get_settings()
        db = Database(settings.database_url)
        await db.create_all()

        events = EventBus(redis_url=settings.redis_url)
        audit = AuditService(db, events)
        usage = UsageService(db, events)
        tenant_settings = TenantSettingService(db)
        approvals = ApprovalService(db, events, audit, tenant_settings)
        agent_access = AgentAccessService(db)
        user_tokens = UserTokenService(db)
        vault = KeyVault(settings.secret_key)
        connections = ConnectionService(db, vault)
        voice_analytics = VoiceAnalyticsService(db, events)
        policy_engine = PolicyEngine(
            mutating_min_role=settings.mutating_min_role,
            require_approval_for_mutations=settings.require_approval_for_mutations,
            redact_pii=settings.redact_pii,
        )
        identity = IdentityService(settings)
        identity.set_pat_verifier(user_tokens.verify)
        skills_registry = SkillRegistry(db, settings.skills_dir)
        mcp_registry = MCPRegistry(db)
        agents_registry = AgentRegistry(db, [])

        runtime = cls(
            settings=settings,
            db=db,
            events=events,
            identity=identity,
            policy_engine=policy_engine,
            audit=audit,
            usage=usage,
            approvals=approvals,
            tenant_settings=tenant_settings,
            agent_access=agent_access,
            user_tokens=user_tokens,
            connections=connections,
            voice_analytics=voice_analytics,
            vault=vault,
            agents_registry=agents_registry,
            skills_registry=skills_registry,
            mcp_registry=mcp_registry,
            orchestrator=None,
            specialists={},
        )
        runtime.rebuild_agents()

        # Seed registries now that the runtime exists (descriptors need tool lists).

        await runtime._seed_registries()
        await events.publish("platform.started", {"model": settings.default_model, "demo": not settings.has_chat_key})
        return runtime

    async def _seed_registries(self) -> None:
        from voicefirst.control_plane.registry import AgentDescriptor

        descriptors = [
            AgentDescriptor(
                slug="orchestrator",
                name="Orchestrator",
                description=self.orchestrator.description or "Main orchestrator",
                kind="orchestrator",
                skills=["orchestrator"],
                mcp_servers=[],
            )
        ]
        for slug, agent in self.specialists.items():
            descriptors.append(
                AgentDescriptor(
                    slug=slug,
                    name=slug.title(),
                    description=agent.description or slug,
                    kind="specialist",
                    skills=[slug],
                    mcp_servers=[slug],
                )
            )
        self.agents_registry._descriptors = {d.slug: d for d in descriptors}  # noqa: SLF001
        await self.agents_registry.seed()
        await self.mcp_registry.seed_from_settings(self.settings)

    def rebuild_agents(self) -> None:
        """(Re)construct orchestrator + specialists. Used at startup and by
        Agent Studio after a SKILL.md edit so behavior changes apply live."""
        model = resolve_model(self.settings)
        # Shared provider-pool protection: cap concurrent model requests across
        # every agent (pydantic-ai ConcurrencyLimitedModel).
        if self.settings.model_concurrency_limit > 0:
            from pydantic_ai import limit_model_concurrency

            model = limit_model_concurrency(model, self.settings.model_concurrency_limit)
        self.specialists = {
            slug: build_specialist(slug, model, self.policy_engine, self.settings)
            for slug in ("github", "slack", "mongodb", "research", "marketing")
        }
        self.orchestrator = build_orchestrator(
            model, self.policy_engine, self.settings, specialists=self.specialists
        )
        # Realtime (voice) variant: no deferred tools (Skills) and no plan
        # approval gate — realtime sessions can't reveal tools mid-session
        # or pause for human decisions.
        self.realtime_orchestrator = build_orchestrator(
            model,
            self.policy_engine,
            self.settings,
            specialists=self.specialists,
            include_skills=False,
            plan_gate=False,
            spoken=True,
        )
        # Live speech only — Gemini/OpenAI realtime. Work is handed to the
        # chat orchestrator in the background (see api/voice.py).
        self.voice_companion = build_voice_companion(self.settings)

    async def close(self) -> None:
        await self.identity.close()
        await self.events.close()
        await self.db.close()
