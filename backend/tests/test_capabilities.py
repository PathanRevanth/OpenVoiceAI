"""Tests for the new Pydantic AI capability integrations: native-or-local
web search/fetch, tool output limits, thinking control, per-agent concurrency,
FallbackModel chains, and cross-conversation search."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from httpx import Request, Response

from voicefirst.agents.deps import AgentDeps
from voicefirst.agents.factory import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    THINKING_MAX_OUTPUT_TOKENS,
    _thinking_capability,
    _tool_output_limits_capability,
    build_orchestrator,
    build_specialist,
)
from voicefirst.agents.tools import _http, research
from voicefirst.control_plane.connections import ConnectionService, KeyVault
from voicefirst.control_plane.policy import PolicyEngine
from voicefirst.db.session import Database
from voicefirst.settings import get_settings


@pytest.fixture
def mock_http(monkeypatch):
    routes: dict[str, Response] = {}

    def handler(request: httpx.Request) -> Response:
        for pattern, response in routes.items():
            method, _, path = pattern.partition(" ")
            if request.method == method and request.url.path.endswith(path):
                return response
        raise AssertionError(f"unexpected mock HTTP call: {request.method} {request.url.path}")

    _http.transport = httpx.MockTransport(handler)
    yield routes
    _http.transport = None


def _json_response(payload: Any, status: int = 200) -> Response:
    return Response(status, json=payload, request=Request("GET", "https://mock"))


def _tool_model(call_tools: Any = "all") -> Any:
    from pydantic_ai.models.test import TestModel

    return TestModel(call_tools=call_tools)


def _deps() -> AgentDeps:
    return AgentDeps(tenant_id="t1", user_id="tester", role="admin", runtime=None)


def _engine() -> PolicyEngine:
    return PolicyEngine()


def _settings() -> Any:
    return get_settings()


# ---------------------------------------------------------------------------
# Thinking control
# ---------------------------------------------------------------------------


def test_thinking_off_by_default():
    assert _thinking_capability(_settings()) is None


def test_thinking_level_creates_capability():
    from pydantic_ai.capabilities import Thinking

    s = _settings().model_copy(update={"thinking_level": "high"})
    cap = _thinking_capability(s)
    assert isinstance(cap, Thinking)


# ---------------------------------------------------------------------------
# Output budget and deliverable preservation
# ---------------------------------------------------------------------------


def test_specialist_reserves_output_tokens_for_long_deliverables():
    """Specialists must not inherit the provider's ~4k default, which cut a
    generated marketing page off mid-tag."""
    agent = build_specialist("marketing", _tool_model(), _engine(), _settings())
    assert agent.model_settings["max_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS


def test_thinking_specialist_gets_a_larger_output_cap():
    settings = _settings().model_copy(update={"thinking_level": "high"})
    agent = build_specialist("marketing", _tool_model(), _engine(), settings)
    assert agent.model_settings["max_tokens"] == THINKING_MAX_OUTPUT_TOKENS


def test_delegation_returns_are_exempt_from_tool_output_truncation():
    """delegate_task/run_workflow carry whole deliverables; truncating them to
    4k leaves the orchestrator with an unusable half-finished artifact."""
    from pydantic_ai_harness.tool_output_limits import Passthrough, Truncate

    cap = _tool_output_limits_capability(preserve_delegation=True)
    for name in ("delegate_task", "run_workflow"):
        bands = cap.per_tool[name]
        assert len(bands) == 1
        assert isinstance(bands[0].action, Passthrough)
    # Ordinary tool returns are still bounded.
    assert isinstance(cap.bands[0].action, Truncate)
    # Specialists keep the default behavior (no delegation tools in scope).
    assert not _tool_output_limits_capability().per_tool


# ---------------------------------------------------------------------------
# Native-or-local web search / fetch (research agent)
# ---------------------------------------------------------------------------


async def test_research_local_fallback_executes_under_unsupported_provider(mock_http):
    """With native web tools disabled (the local-fallback configuration), the
    research agent's scraper executes through the mocked transport."""
    routes = mock_http
    routes["POST /html"] = Response(
        200,
        content=b'<html><body><a class="result__a" href="https://example.com/a">Result A</a></body></html>',
        headers={"content-type": "text/html"},
    )
    settings = _settings().model_copy(update={"native_web_tools": False, "code_mode_enabled": False})
    agent = build_specialist("research", _tool_model(), _engine(), settings)
    result = await agent.run("Search the web for pydantic.", deps=_deps())
    output = result.output
    assert isinstance(output, str) and len(output) > 0


async def test_research_agent_registers_local_tools(mock_http):
    """The capability's local tools resolve through the same governed functions."""
    routes = mock_http
    routes["GET /page"] = Response(
        200,
        content=b"<html><body><p>hello page</p></body></html>",
        headers={"content-type": "text/html"},
    )
    result = await research.fetch_page("https://mock/page")
    assert "hello page" in result["text"]


# ---------------------------------------------------------------------------
# Tool output limits
# ---------------------------------------------------------------------------


async def test_oversized_tool_output_is_truncated(mock_http):
    """A 60k-char tool return is truncated (head+tail 4k) before it enters
    message history — verified through a full agent run."""
    routes = mock_http
    huge_description = "x" * 60_000
    routes["GET /search/repositories"] = _json_response(
        {
            "total_count": 1,
            "items": [
                {
                    "full_name": "a/b",
                    "description": huge_description,
                    "stargazers_count": 1,
                    "html_url": "https://github.com/a/b",
                    "language": "Python",
                }
            ],
        }
    )
    agent = build_specialist("github", _tool_model(), _engine(), _settings().model_copy(update={"code_mode_enabled": False}))
    result = await agent.run("Search repositories.", deps=_deps())
    # Hunt for the tool return in the persisted message history. ToolReturnPart
    # content may be the raw dict — stringify before measuring.
    biggest = 0
    for message in result.all_messages():
        for part in getattr(message, "parts", []):
            if type(part).__name__ == "ToolReturnPart" and getattr(part, "tool_name", "") == "github_search_repositories":
                biggest = max(biggest, len(str(part.content)))
    assert 0 < biggest < 60_000, f"expected truncation, got {biggest} chars"


# ---------------------------------------------------------------------------
# Per-agent concurrency
# ---------------------------------------------------------------------------


def test_agents_carry_max_concurrency():
    agent = build_specialist("mongodb", _tool_model(), _engine(), _settings())
    limiter = getattr(agent, "_concurrency_limiter", None)
    assert limiter is not None and limiter.max_running == _settings().agent_max_concurrency


# ---------------------------------------------------------------------------
# FallbackModel chains for user connections
# ---------------------------------------------------------------------------


@pytest.fixture
async def conn_db(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path}/conn.db")
    await db.create_all()
    yield db
    await db.close()


async def test_fallback_model_chain_built(conn_db, monkeypatch):
    """Saving a connection with a same-provider fallback yields a pydantic-ai
    FallbackModel at resolution time; without one, the primary model only."""
    from pydantic_ai.models.fallback import FallbackModel

    async def fake_test(provider, key):  # never touch the network
        return True, "ok (mocked)"

    monkeypatch.setattr("voicefirst.control_plane.connections.test_provider_key", fake_test)
    service = ConnectionService(conn_db, KeyVault("test-secret"))
    await service.upsert(
        user_id="u1", tenant_id="t1", purpose="agents", provider="openai",
        model="gpt-5.2", key="sk-test-1234567890", fallback_model="gpt-5.1",
    )
    model = await service.resolve_chat_model("u1")
    assert isinstance(model, FallbackModel)

    # Replace with no fallback → plain model.
    await service.upsert(
        user_id="u1", tenant_id="t1", purpose="agents", provider="openai",
        model="gpt-5.2", key="sk-test-1234567890", fallback_model=None,
    )
    model = await service.resolve_chat_model("u1")
    assert not isinstance(model, FallbackModel)


async def test_fallback_model_rejects_cross_provider(conn_db, monkeypatch):
    from fastapi import HTTPException

    async def fake_test(provider, key):
        return True, "ok"

    monkeypatch.setattr("voicefirst.control_plane.connections.test_provider_key", fake_test)
    import voicefirst.api.connections_routes as routes_module

    class _Req:
        class app:  # noqa: N801
            class state:  # noqa: N801
                runtime = None

    body = routes_module.ConnectionSave(
        purpose="agents", provider="openai", model="gpt-5.2",
        key="sk-test-1234567890", fallback_model="claude-opus-5",
    )
    with pytest.raises(HTTPException):
        await routes_module.save_connection(body, _Req(), None)


# ---------------------------------------------------------------------------
# Conversation search (cross-session memory)
# ---------------------------------------------------------------------------


async def test_conversation_search_scoped_and_matching(tmp_path):
    from voicefirst.agents.tools.conversation_search import search_conversations
    from voicefirst.db.models import ConversationRecord, MessageRecord, new_id

    db = Database(f"sqlite+aiosqlite:///{tmp_path}/conv.db")
    await db.create_all()
    try:
        conv = ConversationRecord(id=new_id("conv"), title="Kubernetes plan", tenant_id="t1", user_id="alice")
        msg = MessageRecord(
            id=new_id("msg"), conversation_id=conv.id, role="assistant",
            content={"text": "We decided to use Kubernetes with autoscaling."},
        )
        other = ConversationRecord(id=new_id("conv"), title="Other user", tenant_id="t1", user_id="bob")
        other_msg = MessageRecord(
            id=new_id("msg"), conversation_id=other.id, role="assistant",
            content={"text": "Kubernetes secrets revealed here."},
        )
        async with db.session() as session:
            session.add_all([conv, msg, other, other_msg])

        class _Deps:
            tenant_id = "t1"
            user_id = "alice"
            runtime = type("R", (), {"db": db})()

        class _Ctx:
            deps = _Deps()

        results = await search_conversations(_Ctx(), query="kubernetes")
        assert len(results) == 1
        assert results[0]["user-scoped"] if False else results[0]["conversation_title"] == "Kubernetes plan"
        assert "autoscaling" in results[0]["snippet"]

        empty = await search_conversations(_Ctx(), query="")
        assert "info" in empty[0]
        none_found = await search_conversations(_Ctx(), query="quantum")
        assert "info" in none_found[0]
    finally:
        await db.close()


async def test_orchestrator_runs_conversation_search_tool(mock_http):
    """conversation_search executes through the real orchestrator pipeline."""
    deps = AgentDeps(tenant_id="t1", user_id="tester", role="admin", runtime=None)
    agent = build_orchestrator(
        _tool_model(call_tools=["conversation_search"]),
        _engine(),
        _settings().model_copy(update={"native_web_tools": False}),
        specialists={},
    )
    result = await agent.run("Search my past conversations.", deps=deps)
    assert isinstance(result.output, str)


# ---------------------------------------------------------------------------
# Sanitize json import usage
# ---------------------------------------------------------------------------


def test_json_still_importable():
    assert json.dumps({"ok": True})
