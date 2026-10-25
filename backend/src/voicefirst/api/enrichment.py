"""Chat enrichment: Perplexity-grade sources and follow-ups.

Pure helpers so they are cheap to unit test and safe to run on every turn:

* `extract_sources` pulls linked references out of the final answer text
  (markdown links plus bare URLs) and merges them with URLs the run actually
  fetched via web tools. Results are deduped by normalized URL, capped, and
  numbered in first-seen order so the UI can render [1] [2] chips.
* `build_followups` produces three contextual follow-up prompts from the user
  message and the answer. Deterministic and keyless: no extra model call, so
  it works in demo mode and never adds latency or cost.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

MAX_SOURCES = 8

_MD_LINK = re.compile(r"\[([^\]]{1,120})\]\((https?://[^\s)]+)\)")
_BARE_URL = re.compile(r"(?<![(\['\"])(https?://[^\s)>\]]+)")
_TRAILING = ".,;:!?)'\""

WEB_TOOLS = frozenset(
    {
        "research_web_search",
        "research_search_web",
        "research_fetch_page",
        "web_search",
        "fetch_url",
        "fetch_page",
        "duckduckgo_search",
    }
)


def _clean_url(raw: str) -> str | None:
    url = raw.strip().rstrip(_TRAILING)
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if len(url) > 500:
        return None
    return url


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return url
    return host[4:] if host.startswith("www.") else host


def _title_for(url: str, label: str | None = None) -> str:
    if label and label.strip() and len(label.strip()) <= 80:
        return label.strip()
    return _domain(url)


_NATIVE_WEB_TOOLS = frozenset({"web_search", "web_fetch"})


def native_web_evidence(messages: list | None) -> tuple[bool, list[dict[str, str]]]:
    """Detect server-side web search/fetch and return the result pages.

    Providers with native web tools (e.g. Anthropic's `web_search`) run the
    search inside the model request, so no function tool call ever reaches the
    run loop. The evidence lives in `NativeToolCallPart`/`NativeToolReturnPart`
    parts instead; this helper turns those into the same source shape the UI
    already renders.
    """
    used = False
    results: list[dict[str, str]] = []
    for message in messages or []:
        for part in getattr(message, "parts", ()) or ():
            name = getattr(part, "tool_name", "")
            if name not in _NATIVE_WEB_TOOLS:
                continue
            kind = getattr(part, "part_kind", "")
            if kind not in ("builtin-tool-call", "builtin-tool-return"):
                continue
            used = True
            if kind != "builtin-tool-return":
                continue
            content = getattr(part, "content", None)
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                url = item.get("url")
                if not isinstance(url, str) or not url.startswith("http"):
                    continue
                title = item.get("title")
                results.append({"url": url, "title": title if isinstance(title, str) else None})
    return used, results


def extract_sources(
    final_text: str,
    tool_calls: list[dict] | None = None,
    native_results: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Merge answer links with fetched URLs into a numbered source list."""
    seen: dict[str, dict[str, str]] = {}

    def add(url: str | None, title: str | None = None) -> None:
        clean = _clean_url(url) if url else None
        if not clean or clean in seen:
            return
        if len(seen) >= MAX_SOURCES:
            return
        seen[clean] = {"title": _title_for(clean, title), "url": clean}

    for match in _MD_LINK.finditer(final_text or ""):
        add(match.group(2), match.group(1))
    for match in _BARE_URL.finditer(final_text or ""):
        add(match.group(1))

    for call in tool_calls or []:
        args = call.get("args") if isinstance(call, dict) else None
        if not isinstance(args, dict):
            continue
        tool = str(call.get("tool", ""))
        candidates: list[str] = []
        for key in ("url", "link", "href"):
            value = args.get(key)
            if isinstance(value, str) and value.startswith("http"):
                candidates.append(value)
        results = args.get("results") if isinstance(args.get("results"), list) else None
        if results:
            for item in results[:5]:
                if isinstance(item, dict):
                    for key in ("url", "link"):
                        value = item.get(key)
                        if isinstance(value, str) and value.startswith("http"):
                            candidates.append(value)
                    if isinstance(item.get("title"), str):
                        add(item.get("url") if isinstance(item.get("url"), str) else None, item.get("title"))
        for candidate in candidates:
            add(candidate)
        # A search query that produced no fetchable URL still proves the run
        # consulted the web; the answer text links remain the source list.
        _ = tool

    # Native (server-side) web tools return their result pages as structured
    # content; append them after the answer's own citations.
    for item in native_results or []:
        if isinstance(item, dict):
            add(item.get("url"), item.get("title"))

    return [{"id": str(i + 1), **entry} for i, entry in enumerate(seen.values())]


def _topic(user_message: str) -> str:
    words = re.sub(r"\s+", " ", (user_message or "").strip()).split(" ")
    words = [w for w in words if w]
    # Strip leading interrogatives so "What is ..." becomes "is ...".
    while words and words[0].lower().rstrip("?") in {"what", "who", "when", "where", "why", "how", "which", "please", "can", "could", "would", "should", "do", "does", "is", "are", "tell", "show", "give", "find"}:
        words.pop(0)
    topic = " ".join(words[:6]).strip(" ?.,;:")
    return topic[:60] if topic else "this topic"


def build_followups(
    user_message: str,
    final_text: str = "",
    used_web: bool = False,
) -> list[str]:
    """Three follow-up prompts. Deterministic, always safe to show."""
    topic = _topic(user_message)
    followups = [
        f"Explain {topic} in more detail",
        f"Give me a concrete example of {topic}",
        f"What should I do next with {topic}?",
    ]
    text = final_text or ""
    # Prefer a code follow-up when the answer contains code, and a sources
    # follow-up when the run consulted the web.
    if "```" in text or "artifact" in text.lower():
        followups[1] = f"Show me starter code for {topic}"
    if used_web:
        followups[2] = f"What are the latest updates on {topic}?"
    return followups[:3]


def apply_instructions(message: str, instructions: str | None) -> str:
    """Prepend saved custom instructions to the model input.

    The display text stays exactly what the user typed; only the tokens sent
    to the model carry the operator block. Blank instructions pass through.
    """
    clean = (instructions or "").strip()
    if not clean:
        return message
    return (
        "Operator instructions (follow these on every answer unless they "
        f"conflict with safety policy):\n{clean[:4000]}\n\n---\n\n{message}"
    )
