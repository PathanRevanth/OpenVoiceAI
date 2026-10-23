"""Research tools — web search + page fetching that work with zero API keys.

Two search paths:
* `research_web_search` — the official pydantic-ai DuckDuckGo tool (ddgs API).
* `research_search_web` — an HTML-scraper fallback when ddgs hiccups.

`research_fetch_page` streams with a hard byte cap so a huge or binary page
can never blow the model's context window. Network failures degrade to
structured error results rather than crashing the run.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic_ai import Tool

from voicefirst.agents.tools import _http

_DDGBASE = "https://html.duckduckgo.com/html/"
_CLEAN = re.compile(r"<[^>]+>")
_MAX_DOWNLOAD_BYTES = 512_000


def _strip(html: str) -> str:
    text = _CLEAN.sub("", html)
    return re.sub(r"\s+", " ", text).strip()


async def search_web(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search the web (DuckDuckGo HTML endpoint, no API key needed). Read-only."""
    query = (query or "").strip()
    if not query:
        return [{"error": "empty_query", "detail": "Provide a search phrase."}]
    limit = max(1, min(int(limit), 10))
    try:
        async with _http.make_client(timeout=20.0, follow_redirects=True) as client:
            resp = await client.post(_DDGBASE, data={"q": query}, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:  # noqa: BLE001 - degrade gracefully; the agent has a second search tool
        return [
            {
                "error": "search_unavailable",
                "detail": f"DuckDuckGo HTML search failed ({str(exc)[:120]}). Try the research_web_search tool instead.",
            }
        ]
    results: list[dict[str, Any]] = []
    # The HTML endpoint renders results as <a class="result__a" href="...">title</a>
    for match in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html):
        url, title = match.group(1), _strip(match.group(2))
        if "uddg=" in url:  # unwrap redirect
            import urllib.parse

            url = urllib.parse.unquote(url.split("uddg=", 1)[1].split("&", 1)[0])
        if title and url.startswith("http"):
            results.append({"title": title[:140], "url": url})
        if len(results) >= limit:
            break
    return results or [{"title": "No results found", "url": ""}]


async def fetch_page(url: str, max_chars: int = 4000) -> dict[str, Any]:
    """Fetch a web page and return readable text (truncated, byte-capped
    download). Read-only."""
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return {"error": "invalid_url", "detail": "Only http(s) URLs can be fetched."}
    max_chars = max(200, min(int(max_chars), 20_000))
    try:
        async with _http.make_client(timeout=20.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status_code >= 400:
                    return {"error": f"http_{resp.status_code}", "detail": f"Fetch failed for {url}."}
                ctype = resp.headers.get("content-type", "")
                chunks: list[bytes] = []
                received = 0
                async for chunk in resp.aiter_bytes(65_536):
                    chunks.append(chunk)
                    received += len(chunk)
                    if received >= _MAX_DOWNLOAD_BYTES:
                        break
                body_bytes = b"".join(chunks)
    except Exception as exc:  # noqa: BLE001
        return {"error": "fetch_failed", "detail": f"{url}: {str(exc)[:160]}"}

    text: str
    if "html" in ctype or "xml" in ctype or not ctype:
        body = body_bytes[:_MAX_DOWNLOAD_BYTES].decode("utf-8", errors="replace")
        page = body.split("<body", 1)[-1]
        # Drop script/style content before stripping tags.
        page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.IGNORECASE | re.DOTALL)
        text = _strip(page)
    else:
        text = body_bytes[:_MAX_DOWNLOAD_BYTES].decode("utf-8", errors="replace")
    return {"url": url, "content_type": ctype, "truncated": received >= _MAX_DOWNLOAD_BYTES, "text": text[:max_chars]}


def research_tools() -> list[Any]:
    tools: list[Any] = []
    # Official pydantic-ai DuckDuckGo tool (ddgs API) — primary search path.
    # NB: common_tools.__init__ is empty in 2.35; import from the submodule.
    # The search-tool instance is passed to the Agent unwrapped (it is not a
    # plain function and must not go through the Tool() constructor).
    try:
        from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool

        tools.append(duckduckgo_search_tool(max_results=5))
    except ImportError:  # pragma: no cover - ddgs is a hard dep; surfaced in tests if broken
        pass
    tools.extend(
        [
            Tool(search_web, name="research_search_web", max_retries=1),
            Tool(fetch_page, name="research_fetch_page", max_retries=1),
        ]
    )
    return tools
