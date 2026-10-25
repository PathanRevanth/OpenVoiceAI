"""Pricing fallback tests: a model missing from the registry must not be free."""

from __future__ import annotations

from decimal import Decimal

from pydantic_ai.messages import ModelResponse
from pydantic_ai.usage import RequestUsage, RunUsage

from voicefirst.pricing import price_model_response, price_usage


def _response(model_name: str) -> ModelResponse:
    return ModelResponse(
        parts=[],
        usage=RequestUsage(input_tokens=1000, output_tokens=1000),
        model_name=model_name,
        provider_name="anthropic",
    )


def test_unknown_haiku_response_is_priced_as_family_sibling() -> None:
    # 1k input at $1/M + 1k output at $5/M = $0.006.
    assert price_model_response(_response("claude-haiku-5-5")) == Decimal("0.006")


def test_known_response_is_priced_directly() -> None:
    assert price_model_response(_response("claude-haiku-4-5")) == Decimal("0.006")


def test_unrelated_unknown_model_stays_unpriced() -> None:
    assert price_model_response(_response("totally-unknown-model")) is None


def test_usage_aggregate_uses_same_fallback() -> None:
    usage = RunUsage(input_tokens=1000, output_tokens=1000)
    assert price_usage(usage, model_name="claude-haiku-5-5", provider_name="anthropic") == Decimal(
        "0.006"
    )


def test_usage_aggregate_does_not_guess_unknown_providers() -> None:
    usage = RunUsage(input_tokens=1000, output_tokens=1000)
    assert price_usage(usage, model_name="totally-unknown-model", provider_name="acme") is None
