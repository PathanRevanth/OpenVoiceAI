"""Live model catalog: capability rules, provider fetchers (mocked transport),
removal from the list, invalid keys, and effort validation on chat requests."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

from voicefirst.agents.tools import _http
from voicefirst.api.schemas import ChatRequest
from voicefirst.control_plane.model_catalog import (
    ModelCatalogService,
    anthropic_efforts,
    fetch_anthropic,
    fetch_google,
    fetch_openai,
    google_efforts,
    openai_efforts,
)


def test_openai_reasoning_levels_follow_model_family():
    assert openai_efforts("gpt-5.5") == ["minimal", "low", "medium", "high"]
    assert openai_efforts("o3-mini") == ["low", "medium", "high"]
    assert openai_efforts("o1-mini") == []
    assert openai_efforts("gpt-5.5-pro") == []  # fixed effort, no choice
    assert openai_efforts("gpt-5-chat-latest") == []
    assert openai_efforts("gpt-4o") == []
    assert openai_efforts("text-embedding-3-large") == []


def test_anthropic_thinking_on_claude_3_7_and_4_plus_only():
    assert anthropic_efforts("claude-haiku-4-5-20251001") == ["low", "medium", "high"]
    assert anthropic_efforts("claude-3-7-sonnet-20250219") == ["low", "medium", "high"]
    assert anthropic_efforts("claude-opus-5") == ["low", "medium", "high"]
    assert anthropic_efforts("claude-3-haiku-20240307") == []
    assert anthropic_efforts("claude-3-5-haiku-20241022") == []


def test_google_thinking_flag_from_models_api():
    assert google_efforts({"thinking": True}) == ["low", "medium", "high"]
    assert google_efforts({}) == []


@pytest.fixture
def provider_transport(monkeypatch):
    routes: dict[str, list] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.host
        if key not in routes:
            return httpx.Response(404, json={})
        return routes[key].pop(0) if len(routes[key]) > 1 else routes[key][0]

    monkeypatch.setattr(_http, "transport", httpx.MockTransport(handler))
    return routes


async def test_anthropic_pagination_and_removed_models(provider_transport):
    provider_transport["api.anthropic.com"] = [
        httpx.Response(
            200,
            json={
                "data": [{"id": "claude-haiku-4-5-20251001", "display_name": "Claude Haiku 4.5"}],
                "has_more": True,
                "last_id": "claude-haiku-4-5-20251001",
            },
        ),
        httpx.Response(
            200,
            json={"data": [{"id": "claude-sonnet-4-5", "display_name": "Claude Sonnet 4.5"}], "has_more": False},
        ),
    ]
    models = await fetch_anthropic("sk-ant-test")
    assert [m.id for m in models] == ["anthropic:claude-haiku-4-5-20251001", "anthropic:claude-sonnet-4-5"]
    assert models[0].name == "Claude Haiku 4.5" and models[0].efforts


async def test_removed_model_disappears_on_refresh(provider_transport):
    service = ModelCatalogService()
    provider_transport["api.anthropic.com"] = [
        httpx.Response(200, json={"data": [{"id": "claude-old-4-0", "display_name": "Old"}], "has_more": False}),
    ]
    before = await service.provider_catalog("anthropic", "sk-ant-test")
    assert [m["id"] for m in before.to_dict()["models"]] == ["anthropic:claude-old-4-0"]

    provider_transport["api.anthropic.com"] = [
        httpx.Response(200, json={"data": [{"id": "claude-new-5", "display_name": "New"}], "has_more": False}),
    ]
    after = await service.provider_catalog("anthropic", "sk-ant-test", refresh=True)
    assert [m["id"] for m in after.to_dict()["models"]] == ["anthropic:claude-new-5"]


async def test_invalid_key_is_reported_not_hidden(provider_transport):
    provider_transport["api.openai.com"] = [httpx.Response(401, json={"error": "bad key"})]
    result = await ModelCatalogService().provider_catalog("openai", "sk-bad")
    assert result.status == "invalid_key"
    assert result.models == []


async def test_missing_key_reports_no_key():
    result = await ModelCatalogService().provider_catalog("google", None)
    assert result.status == "no_key"


async def test_openai_filters_to_chat_models(provider_transport):
    provider_transport["api.openai.com"] = [
        httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-5.5"},
                    {"id": "text-embedding-3-large"},
                    {"id": "whisper-1"},
                    {"id": "o3-mini"},
                ]
            },
        )
    ]
    ids = [m.id for m in await fetch_openai("sk-test")]
    assert "openai:gpt-5.5" in ids and "openai:o3-mini" in ids
    assert all("embedding" not in i and "whisper" not in i for i in ids)


async def test_google_keeps_generate_content_gemini_models(provider_transport):
    provider_transport["generativelanguage.googleapis.com"] = [
        httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-2.5-flash",
                        "displayName": "Gemini 2.5 Flash",
                        "supportedGenerationMethods": ["generateContent"],
                        "thinking": True,
                    },
                    {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
                ]
            },
        )
    ]
    models = await fetch_google("key")
    assert [m.id for m in models] == ["google:gemini-2.5-flash"]
    assert models[0].efforts == ["low", "medium", "high"]


def test_chat_request_rejects_unknown_effort():
    assert ChatRequest(message="hi", effort="high").effort == "high"
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", effort="turbo")
