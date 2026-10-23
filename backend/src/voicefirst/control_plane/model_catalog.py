"""Live model catalogs from each provider's own models API, with per-model
reasoning-effort capabilities.

The picker must reflect what the provider serves right now: a model a provider
retires disappears from the list once the short cache window passes, so nothing
is hard-coded here. Providers are queried with the caller's own key (saved
connection), falling back to the platform key.

Effort capabilities follow each provider's documented controls:
* OpenAI reasoning models (GPT-5 family, o-series): reasoning_effort levels.
  `-pro` variants reason at a fixed effort and expose no choice.
* Anthropic: extended thinking on Claude 3.7 and Claude 4+ models.
* Google Gemini: thinking models (flag reported by the models API).

Effort levels are provider-neutral (minimal/low/medium/high) and are translated
by pydantic-ai's unified `thinking` setting at run time.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from voicefirst.agents.tools import _http

CACHE_TTL_SECONDS = 120.0
MAX_PAGES = 5

OPENAI_EFFORTS_GPT5 = ["minimal", "low", "medium", "high"]
OPENAI_EFFORTS_O = ["low", "medium", "high"]
ANTHROPIC_EFFORTS = ["low", "medium", "high"]
GOOGLE_EFFORTS = ["low", "medium", "high"]

_OPENAI_NON_CHAT = (
    "embedding", "tts", "whisper", "dall-e", "image", "audio", "realtime", "transcribe",
    "moderation", "search", "computer-use", "davinci", "babbage", "instruct", "omni-moderation",
)
_GOOGLE_NON_CHAT = ("embedding", "aqa", "imagen", "veo", "tts", "image", "live", "audio", "learnlm")


@dataclass
class CatalogModel:
    id: str
    name: str
    provider: str
    efforts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "provider": self.provider, "efforts": self.efforts}


@dataclass
class ProviderCatalog:
    provider: str
    label: str
    status: str  # ok | no_key | invalid_key | unavailable
    models: list[CatalogModel]
    message: str | None
    fetched_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "label": self.label,
            "status": self.status,
            "message": self.message,
            "fetched_at": self.fetched_at,
            "models": [m.to_dict() for m in self.models],
        }


# -- capability rules -----------------------------------------------------------


def openai_efforts(model_id: str) -> list[str]:
    mid = model_id.lower()
    if any(term in mid for term in _OPENAI_NON_CHAT):
        return []
    if mid.endswith("-pro") or "-pro-" in mid:
        return []  # always-on reasoning at a fixed effort
    if re.match(r"^gpt-5", mid) and "chat" not in mid:
        return list(OPENAI_EFFORTS_GPT5)
    if re.match(r"^o[134]", mid):
        return [] if mid.startswith("o1-mini") else list(OPENAI_EFFORTS_O)
    return []


_ANTHROPIC_THINKING = re.compile(
    r"claude-(?:3-7-sonnet|(?:opus|sonnet|haiku|fable|mythos)-(?:[4-9]|\d{2}))"
)


def anthropic_efforts(model_id: str) -> list[str]:
    return list(ANTHROPIC_EFFORTS) if _ANTHROPIC_THINKING.search(model_id.lower()) else []


def google_efforts(model_entry: dict[str, Any]) -> list[str]:
    return list(GOOGLE_EFFORTS) if model_entry.get("thinking") else []


# -- provider fetchers ----------------------------------------------------------


class ProviderError(Exception):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


async def _get(url: str, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        async with _http.make_client(timeout=15.0) as client:
            resp = await client.get(url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        raise ProviderError("unavailable", f"Could not reach the provider: {type(exc).__name__}.") from exc
    if resp.status_code in (401, 403):
        raise ProviderError("invalid_key", "The provider rejected this API key.")
    if resp.status_code >= 400:
        raise ProviderError("unavailable", f"The provider returned HTTP {resp.status_code}.")
    try:
        return resp.json()
    except ValueError as exc:
        raise ProviderError("unavailable", "The provider returned a non-JSON response.") from exc


async def fetch_openai(key: str) -> list[CatalogModel]:
    data = await _get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
    models: list[CatalogModel] = []
    for entry in data.get("data", []):
        mid = str(entry.get("id", ""))
        if not re.match(r"^(gpt-|o[1345]|chatgpt-)", mid):
            continue
        models.append(CatalogModel(f"openai:{mid}", mid, "openai", openai_efforts(mid)))
    return sorted(models, key=lambda m: m.id, reverse=True)


async def fetch_anthropic(key: str) -> list[CatalogModel]:
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    models: list[CatalogModel] = []
    after: str | None = None
    for _ in range(MAX_PAGES):
        params: dict[str, Any] = {"limit": 100}
        if after:
            params["after_id"] = after
        data = await _get("https://api.anthropic.com/v1/models", headers=headers, params=params)
        for entry in data.get("data", []):
            mid = str(entry.get("id", ""))
            name = str(entry.get("display_name") or mid)
            models.append(CatalogModel(f"anthropic:{mid}", name, "anthropic", anthropic_efforts(mid)))
        if not data.get("has_more"):
            break
        after = data.get("last_id")
        if not after:
            break
    return models


async def fetch_google(key: str) -> list[CatalogModel]:
    models: list[CatalogModel] = []
    page_token: str | None = None
    for _ in range(MAX_PAGES):
        params: dict[str, Any] = {"key": key, "pageSize": 1000}
        if page_token:
            params["pageToken"] = page_token
        data = await _get("https://generativelanguage.googleapis.com/v1beta/models", params=params)
        for entry in data.get("models", []):
            name = str(entry.get("name", ""))  # "models/gemini-2.5-flash"
            mid = name.split("/", 1)[-1]
            methods = entry.get("supportedGenerationMethods", []) or []
            if "generateContent" not in methods or not mid.startswith("gemini"):
                continue
            if any(term in mid for term in _GOOGLE_NON_CHAT):
                continue
            display = str(entry.get("displayName") or mid)
            models.append(CatalogModel(f"google:{mid}", display, "google", google_efforts(entry)))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return models


FETCHERS = {"openai": fetch_openai, "anthropic": fetch_anthropic, "google": fetch_google}
LABELS = {"openai": "OpenAI", "anthropic": "Anthropic", "google": "Google"}


# -- cached service -------------------------------------------------------------


class ModelCatalogService:
    """Per-provider live catalogs, cached per (provider, key fingerprint)."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], ProviderCatalog] = {}
        self._efforts: dict[str, list[str]] = {}

    @staticmethod
    def _fingerprint(key: str | None) -> str:
        return hashlib.sha256((key or "").encode()).hexdigest()[:16]

    async def provider_catalog(self, provider: str, key: str | None, *, refresh: bool = False) -> ProviderCatalog:
        label = LABELS.get(provider, provider)
        if not key:
            return ProviderCatalog(provider, label, "no_key", [], "No API key configured for this provider.", time.time())
        cache_key = (provider, self._fingerprint(key))
        cached = self._cache.get(cache_key)
        if cached and not refresh and time.time() - cached.fetched_at < CACHE_TTL_SECONDS:
            return cached
        try:
            models = await FETCHERS[provider](key)
            result = ProviderCatalog(provider, label, "ok", models, None, time.time())
            self._efforts.update({m.id: m.efforts for m in models})
        except ProviderError as exc:
            result = ProviderCatalog(provider, label, exc.status, [], exc.message, time.time())
        self._cache[cache_key] = result
        return result

    def efforts_for(self, model_id: str) -> list[str]:
        """Supported effort levels for provider:model. Uses the efforts the
        provider reported on its last catalog fetch; otherwise the naming rules."""
        if model_id in self._efforts:
            return list(self._efforts[model_id])
        provider, _, name = model_id.partition(":")
        if provider == "openai":
            return openai_efforts(name)
        if provider == "anthropic":
            return anthropic_efforts(name)
        return []

    def is_listed(self, model_id: str) -> bool | None:
        """Whether the provider's latest catalog still lists `model_id`.

        False means the provider has retired it; None means that provider has not
        been fetched yet, so the caller cannot tell.
        """
        provider = model_id.partition(":")[0]
        fetched = [v for (p, _), v in self._cache.items() if p == provider and v.status == "ok"]
        if not fetched:
            return None
        return any(m.id == model_id for v in fetched for m in v.models)

    def invalidate(self) -> None:
        self._cache.clear()
        self._efforts.clear()


CATALOG = ModelCatalogService()

