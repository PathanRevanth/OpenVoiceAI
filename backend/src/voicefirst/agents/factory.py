"""Agent factory: builds the specialist agents and the orchestrator.

Design (per the control-plane architecture):

* Every specialist is a Pydantic AI `Agent` bound to its domain tools, loaded
  from the SKILL.md of the same name, wrapped in platform guardrails.
* The orchestrator is a Pydantic AI `Agent` whose ONLY tool is `delegate_task`
  (via the harness `SubAgents` capability) — it plans and routes, specialists act.
* Cross-cutting concerns are capabilities: guardrails (input/output/tool),
  spend limits, memory, planning, compaction, and on-demand skills.

Demo mode: with `VOICEFIRST_DEFAULT_MODEL=demo` (or no provider key), a
`FunctionModel` echoes a scripted response so the full API/UI loop works with
zero credentials. Tests use `TestModel` to exercise real tool flows.
"""

from __future__ import annotations

import hashlib
import logging
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai_harness.compaction import ClearToolResults, SlidingWindowCompaction
from pydantic_ai_harness.memory import Memory, SqliteMemoryStore
from pydantic_ai_harness.planning import Planning
from pydantic_ai_harness.skills import Skills
from pydantic_ai_harness.spend import Budget, SpendLimits
from pydantic_ai_harness.subagents import SubAgent, SubAgents
from pydantic_ai_skills import discover_skills

from voicefirst.agents.deps import AgentDeps
from voicefirst.agents.tools.github import github_tools
from voicefirst.agents.tools.marketing import marketing_tools
from voicefirst.agents.tools.mongodb import mongodb_tools
from voicefirst.agents.tools.research import fetch_page, research_tools, search_web
from voicefirst.agents.tools.slack import slack_tools
from voicefirst.control_plane.registry import AgentRegistry
from voicefirst.pricing import price_model_response
from voicefirst.settings import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


async def _demo_generate(_messages: list[Any], _info: Any) -> Any:  # pragma: no cover - offline mode
    """Scripted model response used when no provider key is configured."""
    from pydantic_ai.messages import ModelResponse, TextPart

    return ModelResponse(parts=[TextPart(content=_demo_text(_messages))])


async def _demo_stream(messages: list[Any], info: Any) -> Any:  # pragma: no cover - offline mode
    """Streaming variant: yields text deltas (raw str per FunctionModel contract)."""
    text = _demo_text(messages)
    step = max(1, len(text) // 4)
    for i in range(0, len(text), step):
        yield text[i : i + step]


def _demo_text(messages: list[Any]) -> str:
    last_user = ""
    for message in reversed(messages):
        for part in getattr(message, "parts", []):
            if getattr(part, "part_kind", "") == "user-prompt":
                content = getattr(part, "content", "")
                last_user = content if isinstance(content, str) else str(content)
                break
        if last_user:
            break
    return (
        "**Demo mode** — no model provider key is configured, so this is a scripted reply.\n\n"
        f"You said: \"{last_user[:300]}\"\n\n"
        "The control plane is fully operational: set `VOICEFIRST_DEFAULT_MODEL` (e.g. "
        "`openai:gpt-5.2`) plus the matching provider key to enable real delegation to the "
        "GitHub, Slack, MongoDB, and Research specialists."
    )


def _expose_provider_keys(settings: Settings) -> None:
    """Mirror VOICEFIRST_-prefixed provider keys onto the standard env vars.

    pydantic-ai's provider inference reads OPENAI_API_KEY / ANTHROPIC_API_KEY /
    GEMINI_API_KEY when building a model from a string like `openai:gpt-5.2`.
    Without this, a platform-default model string can never resolve a key
    configured via backend/.env and every run fails at startup.
    """
    import os

    for settings_key, env_var in (
        ("openai_api_key", "OPENAI_API_KEY"),
        ("anthropic_api_key", "ANTHROPIC_API_KEY"),
        ("google_api_key", "GEMINI_API_KEY"),
        ("google_api_key", "GOOGLE_API_KEY"),
    ):
        value = getattr(settings, settings_key, None)
        if value and not os.getenv(env_var):
            os.environ[env_var] = value


def resolve_model(settings: Settings) -> Any:
    """Return the model instance/string for agent construction."""
    if settings.default_model == "demo" or not settings.has_chat_key:
        return FunctionModel(_demo_generate, stream_function=_demo_stream)
    _expose_provider_keys(settings)
    return settings.default_model


def test_model() -> TestModel:
    """Model used in tests: auto-calls tools then returns their output."""
    return TestModel()


# ---------------------------------------------------------------------------
# Instructions from SKILL.md
# ---------------------------------------------------------------------------


def skill_instructions(skills_dir: Path, name: str, fallback: str) -> str:
    """Load a specialist's SKILL.md content as its instructions."""
    for skill in discover_skills(skills_dir, validate=False):
        if skill.name == name:
            return skill.content
    return fallback


# ---------------------------------------------------------------------------
# Specialist factory
# ---------------------------------------------------------------------------

_SPECIALIST_TOOL_BUILDERS = {
    "github": github_tools,
    "slack": slack_tools,
    "mongodb": mongodb_tools,
    "marketing": marketing_tools,
}

_SPECIALIST_FALLBACK_INSTRUCTIONS = {
    "github": "You are the GitHub specialist: repositories, PRs, issues, commits.",
    "slack": "You are the Slack specialist: channels, threads, notifications.",
    "mongodb": "You are the MongoDB specialist: schemas, queries, aggregations.",
    "research": "You are the Research specialist: web search and source synthesis.",
    "marketing": "You are the Marketing specialist: campaigns, content, SEO, competitors, and analytics-minded growth.",
}


# ---------------------------------------------------------------------------
# Shared capability helpers
# ---------------------------------------------------------------------------


# Room for complete deliverables (HTML pages, reports, generated code).
# Providers otherwise fall back to ~4k output tokens, which silently cuts a
# streamed artifact off mid-tag. 16k was still too low for a 35k-character
# HTML/SVG page (token-dense markup hit the cap before </html>), so the normal
# path gets 32k and thinking runs get the model's full 64k window.
DEFAULT_MAX_OUTPUT_TOKENS = 32_768
# Anthropic requires max_tokens above the thinking budget (16k at "high").
THINKING_MAX_OUTPUT_TOKENS = 65_536

# Sub-agent deliverables must survive verbatim: they are the artifact body the
# orchestrator re-emits, so truncating them (even head+tail) breaks the result.
_DELEGATION_TOOL_NAMES = ("delegate_task", "run_workflow")


def _output_token_settings(settings: Settings) -> dict[str, Any]:
    """Output cap for agents that are not driven by a run-level override."""
    level = (getattr(settings, "thinking_level", "off") or "off").lower()
    cap = THINKING_MAX_OUTPUT_TOKENS if level != "off" else DEFAULT_MAX_OUTPUT_TOKENS
    return {"max_tokens": cap}


def _tool_output_limits_capability(*, preserve_delegation: bool = False) -> Any:
    """Truncate oversized tool returns (head+tail, 20k → 4k chars) so one huge
    payload can't dominate history and get re-sent on every later request.

    `preserve_delegation` exempts the orchestrator's `delegate_task` and
    `run_workflow` returns: those carry specialist deliverables (often a whole
    HTML document), and truncation leaves an unusable, half-finished artifact.
    """
    from pydantic_ai_harness.tool_output_limits import (
        Band,
        Passthrough,
        ToolOutputLimits,
        Truncate,
    )

    return ToolOutputLimits(
        bands=[Band(over=20_000, action=Truncate(max_chars=4_000))],
        per_tool=(
            {name: [Band(over=0, action=Passthrough())] for name in _DELEGATION_TOOL_NAMES}
            if preserve_delegation
            else {}
        ),
    )


_WEB_RESULT_TOOLS = frozenset(
    {
        "web_search",
        "web_fetch",
        "research_search_web",
        "research_fetch_page",
        "duckduckgo_search",
        "search_web",
        "fetch_page",
    }
)

# The harness default ("for multi-step work") is easy for the model to skip.
# Research requests that compare products or span several sub-questions should
# always get an explicit plan; it also gives the UI live task events.
_RESEARCH_PLANNING_GUIDANCE = (
    "Before the first search, call `write_plan` whenever the request compares two or more "
    "things, asks for a report or memo, or contains multiple sub-questions. Use one task per "
    "sub-question plus a final synthesis task, keep exactly one task in_progress, and mark "
    "tasks completed as their evidence is gathered. The plan is internal: never mention it, "
    "its tasks, or its statuses in the final answer."
)


def _prompt_injection_defender_capabilities(settings: Settings) -> list[Any]:
    """Scan TOOL RESULTS for injected instructions (the input guardrail only
    covers user input — a poisoned GitHub README or Slack message reaches the
    model as tool output).

    Two scopes, because the right failure mode differs by tool:

    * Tools that can drive actions (GitHub/Slack/MongoDB) keep hard blocking:
      a high-risk result is replaced so its instructions never reach the model.
    * Read-only web tools only report. Web pages are untrusted by nature, the
      provider-native web tools bypass this hook entirely, and the pattern
      scanner false-positives on ordinary prose (PyPI trips
      `confusable_homoglyphs` on phonetic characters), which would withhold a
      whole source without removing the attack surface. The research skill
      frames fetched text as data, never instructions, and Tier 1 findings are
      still logged for operators.
    """
    if not getattr(settings, "injection_defender_enabled", True):
        return []
    try:
        from pydantic_ai_harness.prompt_injection_defender import PromptInjectionDefender
    except ImportError:  # pragma: no cover - extra missing; input guardrail still active
        return []

    def _is_web_tool(_ctx: Any, tool_def: Any) -> bool:
        return tool_def.name in _WEB_RESULT_TOOLS

    def _is_not_web_tool(_ctx: Any, tool_def: Any) -> bool:
        return tool_def.name not in _WEB_RESULT_TOOLS

    def _log_web_finding(_ctx: Any, call: Any, verdict: Any) -> None:
        detections = ", ".join(verdict.detections) or "no named detections"
        if verdict.risk_level in ("high", "critical"):
            logger.warning(
                "read-only %s result flagged as %s (%s); passing it through as untrusted data",
                call.tool_name,
                verdict.risk_level,
                detections,
            )
        else:
            logger.debug(
                "read-only %s result flagged as %s (%s)",
                call.tool_name,
                verdict.risk_level,
                detections,
            )

    return [
        PromptInjectionDefender(block_high_risk=True, tool_filter=_is_not_web_tool),
        # Report-only scan: `block_high_risk=False` keeps the page intact while
        # `on_detection` preserves the audit signal.
        PromptInjectionDefender(
            block_high_risk=False,
            tool_filter=_is_web_tool,
            on_detection=_log_web_finding,
        ),
    ]


def _step_persistence_capability(agent_name: str, settings: Settings) -> Any:
    """Durable per-step event log: continuable snapshots + tool-effect ledger
    for every run of this agent (SQLite store alongside the memory store)."""
    if not getattr(settings, "step_persistence_enabled", True):
        return None
    try:
        from pydantic_ai_harness.step_persistence import SqliteStepStore, StepPersistence

        store_path = Path(
            getattr(settings, "step_store_path", None) or "voicefirst_steps.db"
        )
        return StepPersistence(
            store=SqliteStepStore(database=store_path),
            agent_name=agent_name,
        )
    except ImportError:  # pragma: no cover
        return None


def _warn_on_cache_busts_capability(settings: Settings) -> Any:
    """Observational monitor: warns when the prompt cache prefix collapses
    (a silent token-cost multiplier on long agent runs)."""
    if not getattr(settings, "cache_bust_monitor_enabled", True):
        return None
    from pydantic_ai_harness.warn_on_cache_busts import WarnOnCacheBusts

    return WarnOnCacheBusts()


def _system_reminders_capability(settings: Settings, reminders: list[Any]) -> Any:
    """Cache-safe periodic re-injection of behavioral guidance."""
    if not reminders:
        return None
    from pydantic_ai_harness.system_reminders import SystemReminders

    return SystemReminders(reminders=reminders, cache_ttl="1h")


def _code_mode_capability(slug: str, settings: Settings) -> Any:
    """Code mode for the data-heavy specialists: the model can route tool
    results through sandboxed Python (monty) — analysis, reshaping, and math
    happen in one step instead of many model round-trips.

    Research is deliberately excluded: code mode folds every function tool
    (including `write_plan` and the local web tools) into `run_code`, which
    contradicts the planning guidance and makes direct research calls fail.
    """
    if slug not in ("mongodb", "github") or not getattr(settings, "code_mode_enabled", True):
        return None
    try:
        from pydantic_ai_harness.code_mode import CodeMode

        return CodeMode(max_retries=2, max_tool_calls=100)
    except ImportError:  # pragma: no cover - pydantic-monty missing
        return None


def _advisor_capability(settings: Settings) -> Any:
    """Optional second-model advisor: the orchestrator can request a critique
    before committing to a risky plan. Provider-adaptive (native when the
    provider offers it, local sub-request otherwise)."""
    if not getattr(settings, "advisor_enabled", True):
        return None
    from pydantic_ai_harness.advisor import Advisor

    return Advisor(
        model=getattr(settings, "advisor_model", None) or settings.default_model,
        mode="auto",
        max_uses=2,
        caching="1h",
    )


def _capability_creation_capability(settings: Settings) -> Any:
    """Agent-authored capabilities (Agent Studio integration): when enabled by
    an admin, the orchestrator may draft new capability files into the
    authored directory; they mount on the next studio rebuild."""
    if not getattr(settings, "capability_creation_enabled", False):
        return None
    from pydantic_ai_harness.capability_creation import CapabilityCreation

    directory = Path(getattr(settings, "authored_capabilities_path", None) or "voicefirst_authored_capabilities")
    directory.mkdir(parents=True, exist_ok=True)
    return CapabilityCreation(
        directory=directory,
        guidance=(
            "Draft capabilities for governed automation. Every file is reviewed "
            "by an admin in Agent Studio before it is mounted."
        ),
    )


def _thinking_capability(settings: Settings) -> Any:
    """Optional model thinking/reasoning control (provider-portable).

    Off by default — `VOICEFIRST_THINKING_LEVEL` accepts off/minimal/low/
    medium/high (reasoning tokens cost money; enable deliberately)."""
    level = (getattr(settings, "thinking_level", "off") or "off").lower()
    if level in ("", "off", "none", "false"):
        return None
    from pydantic_ai.capabilities import Thinking

    return Thinking(effort=level)  # type: ignore[arg-type]


def _spend_limits(settings: Settings) -> SpendLimits:
    return SpendLimits(
        budgets=[
            Budget(usd=Decimal(str(settings.daily_spend_limit_usd)), window="day", name="platform-daily"),
            Budget(tokens=settings.per_run_token_limit, window="run", name="per-run-tokens"),
        ],
        price=price_model_response,
    )


def build_specialist(
    slug: str,
    model: Any,
    policy_engine: Any,
    settings: Settings,
    *,
    description: str | None = None,
    approval_default: bool = True,
) -> Agent[AgentDeps, Any]:
    """Build one specialist agent with guardrails + domain tools."""
    from pydantic_ai.capabilities import WebFetch, WebSearch

    extra_capabilities: list[Any] = []
    if slug == "research" and settings.native_web_tools:
        # Provider-adaptive search & fetch: use the provider's native web tools
        # (OpenAI/Google) when supported, falling back to our keyless local
        # tools otherwise. `optional=True` drops the native tool silently on
        # models that can't honor it (demo/Test models) so the local function
        # takes over — the pydantic-ai sanctioned hybrid pattern.
        from pydantic_ai import WebFetchTool, WebSearchTool

        extra_capabilities = [
            WebSearch(native=WebSearchTool(optional=True, search_context_size="medium"), local=search_web),
            WebFetch(native=WebFetchTool(optional=True), local=fetch_page),
        ]
    if slug == "research":
        # The local search/fetch tools are owned by the WebSearch/WebFetch
        # capabilities when enabled; keep any remaining tool (the ddgs-based
        # search) as a third path.
        owned = {"search_web", "fetch_page"} if settings.native_web_tools else set()
        tools = [t for t in research_tools() if t.name not in owned]
    elif slug in _SPECIALIST_TOOL_BUILDERS:
        tools = list(_SPECIALIST_TOOL_BUILDERS[slug](approval_default=approval_default))
    else:
        tools = []
    instructions = skill_instructions(settings.skills_dir, slug, _SPECIALIST_FALLBACK_INSTRUCTIONS.get(slug, ""))
    capabilities = [
        policy_engine.input_guardrail(),
        policy_engine.output_guardrail(),
        policy_engine.tool_guardrail(),
        *_prompt_injection_defender_capabilities(settings),
        _tool_output_limits_capability(),
        _step_persistence_capability(slug, settings),
        _warn_on_cache_busts_capability(settings),
        SlidingWindowCompaction(max_messages=80),
        # Deep research is multi-step and long-running: give the specialist its
        # own task plan so it can decompose a question, track source gathering,
        # and resume coherently after compaction. The 1h TTL matches
        # SystemReminders: Anthropic rejects a 1h cache block that follows a 5m
        # one in the same request.
        Planning(
            enable_subtasks=True,
            guidance=_RESEARCH_PLANNING_GUIDANCE,
            cache_ttl="1h",
        )
        if slug == "research"
        else None,
        _thinking_capability(settings),
        _code_mode_capability(slug, settings),
        _spend_limits(settings),
        *extra_capabilities,
    ]
    if slug == "research":
        # Citation discipline for the research specialist, re-injected cache-safely.
        from pydantic_ai_harness.system_reminders import Reminder

        capabilities.append(_system_reminders_capability(settings, [
            Reminder(
                content="Cite the source URL for every factual claim you report from the web.",
                interval=8, max_fires=3, tag="citation-discipline",
            ),
        ]))
    return Agent(
        model,
        deps_type=AgentDeps,
        name=slug,
        description=description or _SPECIALIST_FALLBACK_INSTRUCTIONS.get(slug, slug),
        instructions=instructions,
        tools=tools,
        output_type=[str, DeferredToolRequests],  # approval pauses surface as output
        retries=2,  # transient tool failures become retry prompts, not run failures
        tool_timeout=60,
        max_concurrency=settings.agent_max_concurrency,
        model_settings=_output_token_settings(settings),
        capabilities=[c for c in capabilities if c is not None],
    )


# ---------------------------------------------------------------------------
# Orchestrator factory
# ---------------------------------------------------------------------------


class _PlanApprovalGate(AbstractToolset[AgentDeps]):
    """Marks `write_plan` as approval-required while `deps.plan_gate` is raised.

    A `'unapproved'` tool definition defers the call into `DeferredToolRequests`,
    which the control plane already renders as a human-approval pause — so the
    operator sees the plan (from the deferred call's args) and approves or denies
    it before the orchestrator delegates any work.
    """

    def __init__(self, wrapped: AbstractToolset[AgentDeps]) -> None:
        self._wrapped = wrapped

    @property
    def id(self) -> str | None:  # noqa: D102 - mirrors WrapperToolset
        return None

    @property
    def label(self) -> str:
        return f"PlanApprovalGate({self._wrapped.label})"

    async def for_run(self, ctx: Any) -> AbstractToolset[AgentDeps]:
        return self

    async def __aenter__(self) -> "_PlanApprovalGate":
        await self._wrapped.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> bool | None:
        return await self._wrapped.__aexit__(*args)

    async def get_tools(self, ctx: Any) -> dict[str, Any]:
        tools = await self._wrapped.get_tools(ctx)
        if getattr(getattr(ctx, "deps", None), "plan_gate", True):
            tool = tools.get("write_plan")
            if tool is not None:
                tool.tool_def.kind = "unapproved"
        return tools

    async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: Any, tool: Any) -> Any:
        return await self._wrapped.call_tool(name, tool_args, ctx, tool)

    def apply(self, visitor: Any) -> None:
        self._wrapped.apply(visitor)

    def visit_and_replace(self, visitor: Any) -> AbstractToolset[AgentDeps]:
        return self


class PlanGatedPlanning(Planning):
    """`Planning` whose `write_plan` is deferred for operator approval.

    Everything else (statuses, granular edits, reminders) flows without a gate,
    so a run pauses exactly once per new plan until it is approved.
    """

    def get_toolset(self) -> AbstractToolset[AgentDeps] | None:
        inner = super().get_toolset()
        if inner is None:
            return None
        return _PlanApprovalGate(inner)


async def _sub_agent_span_handler(ctx: Any, events: Any) -> None:
    """Record nested spans for specialist execution under delegate_task.

    The orchestrator's per-run SpanCollector rides on deps; sub-agents share
    the same deps object, so their streams append nested agent spans."""
    collector = getattr(getattr(ctx, "deps", None), "collector", None)
    if collector is None:
        async for _ in events:
            pass
        return
    agent_name = getattr(getattr(ctx, "agent", None), "name", None) or "specialist"
    key = collector.start(f"agent:{agent_name}", "agent")
    try:
        async for _event in events:
            pass
    finally:
        collector.end(key)


def build_orchestrator(
    model: Any,
    policy_engine: Any,
    settings: Settings,
    agents_registry: AgentRegistry | None = None,
    *,
    specialists: dict[str, Agent[AgentDeps, Any]] | None = None,
    demo: bool = False,
    include_skills: bool = True,
    plan_gate: bool = True,
    spoken: bool = False,
) -> Agent[AgentDeps, Any]:
    """Build the main orchestrator with delegation, planning, and governance.

    `include_skills=False` and `plan_gate=False` produce the realtime (voice)
    variant: realtime sessions cannot reveal deferred tools mid-session and
    cannot pause for approvals, so on-demand skill loading and the plan gate
    are omitted there. Delegation, planning, guardrails, spend limits, memory,
    and compaction are kept.
    """
    if specialists is None:
        specialists = {
            slug: build_specialist(slug, model, policy_engine, settings) for slug in _SPECIALIST_TOOL_BUILDERS
        }

    instructions = skill_instructions(
        settings.skills_dir,
        "orchestrator",
        "You are the orchestrator of an agentic control plane. Plan, delegate to specialists, synthesize.",
    )
    specialist_names = ", ".join(sorted(specialists.keys()))
    instructions = (
        f"{instructions}\n\n## Available specialists\n\n{specialist_names}\n\n"
        "Delegate with `delegate_task` to the matching specialist. Never call domain "
        "tools yourself — that is the specialists' job."
    )
    if spoken:
        instructions = (
            f"{instructions}\n\n## Voice session\n\n"
            "You are speaking out loud over Gemini Live. Sound like a capable colleague, "
            "not a document.\n"
            "- Answer first in one or two short sentences, then add only the detail the "
            "caller asked for.\n"
            "- No markdown, bullets, code fences, tables, or URLs unless the caller "
            "explicitly wants them read.\n"
            "- Spell out numbers, names, and tool results so they are easy to hear.\n"
            "- When you delegate, say what you are doing in a clause: "
            "\"I'll check GitHub for that.\"\n"
            "- If you need a clarification, ask one spoken question and wait.\n"
            "- Keep turns under about 20 seconds unless the caller asked for a briefing."
        )

    sub_agents = [
        SubAgent(
            agent,
            name=slug,
            description=agent.description or _SPECIALIST_FALLBACK_INSTRUCTIONS.get(slug, slug),
            timeout_seconds=120,
        )
        for slug, agent in specialists.items()
    ]

    from pydantic_ai_harness.dynamic_workflow import DynamicWorkflow

    capabilities: list[Any] = [
        policy_engine.input_guardrail(),
        policy_engine.output_guardrail(),
        policy_engine.tool_guardrail(),
        *_prompt_injection_defender_capabilities(settings),
        SubAgents(
            agents=sub_agents,
            forward_usage=True,
            contain_errors=True,
            event_stream_handler=_sub_agent_span_handler,
        ),
        Planning(enable_subtasks=True, cache_ttl="1h")
        if (not plan_gate) or (not settings.require_plan_approval)
        else PlanGatedPlanning(enable_subtasks=True, cache_ttl="1h"),
        _spend_limits(settings),
        SlidingWindowCompaction(max_messages=120),
        ClearToolResults(max_fraction=0.7, keep_pairs=3),
        build_memory_capability(settings),
        _tool_output_limits_capability(preserve_delegation=True),
        _thinking_capability(settings),
        _step_persistence_capability("orchestrator", settings),
        _warn_on_cache_busts_capability(settings),
        _advisor_capability(settings),
        _capability_creation_capability(settings),
    ]
    if specialists:
        # Scripted multi-agent workflows (fan-out, chaining, voting) over the
        # same governed specialists — requires at least one sub-agent.
        capabilities.append(
            DynamicWorkflow(
                agents=list(specialists.values()),
                tool_name="run_workflow",
                max_agent_calls=20,
            )
        )
    if plan_gate and settings.require_plan_approval:
        # Plan discipline, re-injected cache-safely during long delegations.
        from pydantic_ai_harness.system_reminders import Reminder

        capabilities.append(
            _system_reminders_capability(settings, [
                Reminder(
                    content=(
                        "Governance reminder: delegate domain work via delegate_task; "
                        "never call specialist tools yourself; keep plan task statuses "
                        "current via update_task_status."
                    ),
                    interval=10, max_fires=3, tag="orchestration-discipline",
                ),
            ])
        )
    capabilities = [c for c in capabilities if c is not None]

    if include_skills and settings.skills_dir.is_dir():
        capabilities.append(Skills(directories=[settings.skills_dir]))

    return Agent(
        model,
        deps_type=AgentDeps,
        name="orchestrator",
        instructions=instructions,
        tools=[conversation_search_tool()],
        output_type=[str, DeferredToolRequests],  # approval pauses surface as output
        retries=2,
        tool_timeout=120,
        max_concurrency=settings.agent_max_concurrency,
        model_settings=_output_token_settings(settings),
        capabilities=capabilities,
    )


def _memory_user_segment(user_id: str) -> str:
    """Map a user id to a Memory-store path segment.

    Memory paths only accept ``[A-Za-z0-9_.-]``, so OIDC-style ids containing
    ``@`` (or other punctuation) would otherwise fail every orchestrator run
    with ``invalid memory path``. Already-valid ids pass through unchanged so
    existing memories keep their paths; invalid ones are sanitized and get a
    short digest suffix so two ids cannot collide after sanitization.
    """
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", user_id) and ".." not in user_id:
        return user_id
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", user_id)[:160].replace("..", "__") or "anon"
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
    return f"{safe}-{digest}"


def build_memory_capability(settings: Settings) -> Any:
    """Per-user persistent memory for the orchestrator (SQLite store).

    The harness Memory capability injects relevant memories into context and
    exposes write/search tools, namespaced by user id so tenants never mix.
    """
    store_path = Path(getattr(settings, "memory_db_path", None) or "voicefirst_memory.db")
    return Memory(
        store=SqliteMemoryStore(database=store_path),
        agent_name="orchestrator",
        namespace=lambda ctx: "users/"
        + _memory_user_segment(str(getattr(getattr(ctx, "deps", None), "user_id", "anon"))),
        max_tokens=1200,
        max_memory_size=32768,
    )


def capability_catalog() -> dict[str, str]:
    """Human-readable index of the orchestrator's capability stack (for the UI)."""
    return {
        "subagents": "delegate_task routing to specialist agents (harness SubAgents)",
        "planning": "structured, self-updating task plans (harness Planning)",
        "skills": "on-demand SKILL.md loading (harness Skills)",
        "spend_limits": "per-run token and per-day USD budgets (harness SpendLimits)",
        "guardrails": "input injection defense, output redaction, tool RBAC (harness Guardrails)",
        "compaction": "sliding-window + tool-result clearing (harness Compaction)",
        "memory": "persistent per-user memory (harness Memory, optional)",
        "tool_output_limits": "oversized tool returns truncated to protect context (harness ToolOutputLimits)",
        "thinking": "optional model reasoning control (pydantic-ai Thinking)",
        "conversation_search": "search the operator's past conversations for cross-session context",
        "injection_defender": "tool-result prompt-injection scanning (blocks action-tool results; reports web-page findings)",
        "step_persistence": "durable per-step event log, continuable snapshots, tool-effect ledger (harness StepPersistence)",
        "cache_bust_monitor": "warns when the prompt-cache prefix collapses (harness WarnOnCacheBusts)",
        "media_offload": "oversized attachment parts offloaded to a content-addressed store (harness media)",
        "code_mode": "route tool results through sandboxed Python (harness CodeMode + monty)",
        "dynamic_workflow": "model-scripted multi-agent fan-out/chaining via run_workflow (harness DynamicWorkflow)",
        "advisor": "second-model critique before risky decisions (harness Advisor)",
        "capability_creation": "agent-drafted capabilities, admin-gated in Agent Studio (harness CapabilityCreation)",
    }


def build_voice_companion(settings: Settings) -> Agent[AgentDeps, Any]:
    """Conversational speech agent for Gemini Live / OpenAI Realtime.

    No specialist tools. The voice model talks; the chat orchestrator does the
    work in the background and the control plane feeds spoken cues back in.
    """
    model = resolve_model(settings)
    return Agent(
        model,
        deps_type=AgentDeps,
        name="voice-companion",
        instructions=(
            "You are the live voice of Voice-First AI. You are in a real-time "
            "spoken conversation.\n\n"
            "Another model — the orchestrator — is doing the actual work "
            "(artifacts, GitHub, research, marketing) in the background while "
            "you talk. You do not have those tools.\n\n"
            "Sound like a capable colleague:\n"
            "- Answer first in one or two short sentences.\n"
            "- No markdown, bullets, code, or URLs unless the caller asks you "
            "to read them.\n"
            "- When they ask for work (design this, change the colour, search "
            "GitHub), acknowledge and keep the floor open. The control plane "
            "will send you a spoken update when the result is ready — then "
            "read that update naturally.\n"
            "- If they change their mind mid-task, acknowledge the new "
            "direction. Do not argue.\n"
            "- Never invent tool results, files, or artifact contents.\n"
            "- Keep turns under about 15 seconds unless they asked for a briefing."
        ),
        output_type=str,
        retries=1,
    )


def conversation_search_tool() -> Any:
    """`conversation_search`: let the orchestrator look back into the caller's
    past conversations (tenant- and user-scoped) for cross-session context."""
    from pydantic_ai import Tool

    from voicefirst.agents.tools.conversation_search import search_conversations

    return Tool(search_conversations, name="conversation_search", takes_ctx=True, max_retries=1)
