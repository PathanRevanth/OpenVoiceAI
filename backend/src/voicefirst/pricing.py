"""Cost estimation for models the genai-prices registry does not know yet.

New provider model ids ship before the public price registry catches up. Without
a fallback they are priced at zero, which silently disables USD spend limits and
under-reports usage. An unknown Anthropic model is priced as the closest priced
model in the same family; everything else still goes through the registry.

Both entry points return ``None`` when no price can be resolved, so callers keep
their existing "unpriced" behaviour instead of guessing a rate.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

from pydantic_ai.messages import calculate_price_for_usage

# Newest model id -> a priced sibling used only when the registry lookup fails.
_PRICED_MODEL_SIBLINGS: tuple[tuple[str, str], ...] = (
    ("claude-haiku", "claude-haiku-4-5"),
    ("claude-sonnet", "claude-sonnet-4-6"),
    ("claude-opus", "claude-opus-4-8"),
)


def _bare_model_name(model_name: str | None) -> str:
    """Normalise ``anthropic:claude-x`` / ``anthropic/claude-x`` to ``claude-x``."""
    return (model_name or "").rsplit("/", 1)[-1].split(":", 1)[-1]


def _sibling(model_name: str | None) -> str | None:
    bare = _bare_model_name(model_name)
    for prefix, sibling in _PRICED_MODEL_SIBLINGS:
        if bare.startswith(prefix):
            return sibling
    return None


def price_model_response(response: Any) -> Decimal | None:
    """Price one model response, falling back to a priced family sibling.

    Used as the ``SpendLimits.price`` callback so USD budgets keep enforcing for
    a model the registry has not listed yet.
    """
    try:
        return response.cost().total_price
    except (LookupError, ValueError):
        pass
    sibling = _sibling(getattr(response, "model_name", None))
    if sibling is None:
        return None
    try:
        return replace(response, model_name=sibling).cost().total_price
    except (LookupError, ValueError):
        return None


def price_usage(
    usage: Any,
    *,
    model_name: str | None,
    provider_name: str | None = None,
) -> Decimal | None:
    """Price an aggregate ``RunUsage``/``RequestUsage`` for display and records.

    ``pydantic-ai`` computes ``usage.cost`` from the registry; this fills the
    same number for an unknown model so the usage screen and daily meter agree
    with the spend ceiling that was actually enforced.
    """
    if not model_name:
        return None
    try:
        return calculate_price_for_usage(
            usage,
            model_name=model_name,
            provider_name=provider_name,
        ).total_price
    except (LookupError, ValueError):
        pass
    sibling = _sibling(model_name)
    if sibling is None:
        return None
    try:
        return calculate_price_for_usage(
            usage,
            model_name=sibling,
            provider_name=provider_name,
        ).total_price
    except (LookupError, ValueError):
        return None
