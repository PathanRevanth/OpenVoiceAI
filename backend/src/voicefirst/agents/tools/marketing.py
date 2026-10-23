"""Marketing tools — keyless, read-only growth utilities for the Marketing agent.

* `marketing_seo_audit`          — on-page SEO audit of one URL (title, meta, headings,
                                   images, canonical, Open Graph, content depth).
* `marketing_competitor_snapshot`— the same on-page signals for up to 5 competitor URLs,
                                   side by side.
* `marketing_serp_research`      — search results for a keyword with a coarse intent
                                   read (informational / commercial / transactional).
* `marketing_score_copy`         — readability (Flesch), sentence stats, CTA presence,
                                   and keyword density for a piece of copy.
* `marketing_build_utm`          — tracked campaign URL with normalized UTM parameters.
* `marketing_content_calendar`   — deterministic multi-channel publishing calendar.

Nothing here publishes, sends, or spends: writing the copy itself is the model's job;
these tools supply measurements and structure so claims about a page or a plan are
grounded rather than guessed.
"""

from __future__ import annotations

import datetime as dt
import re
import urllib.parse
from html.parser import HTMLParser
from typing import Any

from pydantic_ai import Tool

from voicefirst.agents.tools import _http
from voicefirst.agents.tools.research import search_web

_MAX_HTML_BYTES = 512_000
_MAX_COMPETITORS = 5
_CTA_PATTERN = re.compile(
    r"\b(get started|sign up|signup|try (it|free|for free)|start (your|a) |book (a|your|now)|"
    r"buy now|shop now|download|subscribe|join|request (a )?demo|learn more|contact us|"
    r"get (a )?quote|claim|register|apply now)\b",
    re.IGNORECASE,
)
_INTENT_TERMS = {
    "transactional": ("buy", "price", "pricing", "discount", "coupon", "order", "deal", "cheap", "hire"),
    "commercial": ("best", "top", "vs", "versus", "review", "alternative", "compare", "comparison"),
    "informational": ("how", "what", "why", "guide", "tutorial", "examples", "meaning", "tips", "ideas"),
}


# ---------------------------------------------------------------------------
# On-page HTML analysis (stdlib parser — no extra dependency)
# ---------------------------------------------------------------------------


class _PageParser(HTMLParser):
    """Collects the on-page SEO signals in a single pass."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang: str | None = None
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.og: dict[str, str] = {}
        self.canonical: str | None = None
        self.h1: list[str] = []
        self.h2_count = 0
        self.images = 0
        self.images_missing_alt = 0
        self.links_internal = 0
        self.links_external = 0
        self.text_parts: list[str] = []
        self._in_title = False
        self._in_heading: str | None = None
        self._heading_buf: list[str] = []
        self._skip_depth = 0
        self._host = ""

    def set_host(self, host: str) -> None:
        self._host = host.lower().removeprefix("www.")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang") or None
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name.startswith("og:"):
                self.og[name] = a.get("content", "")
            elif name:
                self.meta[name] = a.get("content", "")
        elif tag == "link" and a.get("rel", "").lower() == "canonical":
            self.canonical = a.get("href") or None
        elif tag in ("h1", "h2"):
            self._in_heading = tag
            self._heading_buf = []
            if tag == "h2":
                self.h2_count += 1
        elif tag == "img":
            self.images += 1
            if "alt" not in a:
                self.images_missing_alt += 1
        elif tag == "a":
            href = a.get("href", "")
            if href.startswith(("http://", "https://")):
                host = urllib.parse.urlsplit(href).netloc.lower().removeprefix("www.")
                if host == self._host:
                    self.links_internal += 1
                else:
                    self.links_external += 1
            elif href.startswith("/"):
                self.links_internal += 1
        elif tag in ("script", "style", "noscript", "svg"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style", "noscript", "svg") and self._skip_depth:
            self._skip_depth -= 1
        elif tag == self._in_heading:
            text = _squash(" ".join(self._heading_buf))
            if tag == "h1" and text:
                self.h1.append(text[:200])
            self._in_heading = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if self._in_heading:
            self._heading_buf.append(data)
        if not self._skip_depth:
            self.text_parts.append(data)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", text))


def analyze_html(html: str, page_url: str) -> dict[str, Any]:
    """Pure function: on-page SEO signals + graded issues for one HTML document."""
    parser = _PageParser()
    parser.set_host(urllib.parse.urlsplit(page_url).netloc)
    parser.feed(html)
    parser.close()

    title = _squash("".join(parser.title_parts))
    description = _squash(parser.meta.get("description", ""))
    body_text = _squash(" ".join(parser.text_parts))
    words = _word_count(body_text)

    issues: list[dict[str, str]] = []
    penalty = 0

    def flag(severity: str, message: str, points: int) -> None:
        nonlocal penalty
        penalty += points
        issues.append({"severity": severity, "message": message})

    if not title:
        flag("high", "Missing <title> tag.", 15)
    elif len(title) < 30:
        flag("medium", f"Title is short ({len(title)} chars); aim for 30–60.", 5)
    elif len(title) > 60:
        flag("low", f"Title is long ({len(title)} chars); it may truncate in search results.", 2)

    if not description:
        flag("high", "Missing meta description.", 10)
    elif not 70 <= len(description) <= 160:
        flag("low", f"Meta description is {len(description)} chars; aim for 70–160.", 3)

    if len(parser.h1) == 0:
        flag("high", "No <h1> heading found.", 10)
    elif len(parser.h1) > 1:
        flag("medium", f"{len(parser.h1)} <h1> headings; use exactly one.", 4)

    if parser.images_missing_alt:
        flag(
            "medium",
            f"{parser.images_missing_alt} of {parser.images} images have no alt attribute.",
            min(8, parser.images_missing_alt),
        )

    if words < 300:
        flag("medium", f"Thin content: about {words} words of visible text.", 8)

    if not parser.canonical:
        flag("low", "No canonical link; duplicate URLs can dilute ranking signals.", 3)
    if not parser.lang:
        flag("low", "<html> has no lang attribute.", 2)
    if "og:image" not in parser.og:
        flag("low", "No og:image; social shares will render without a preview image.", 3)
    if "robots" in parser.meta and "noindex" in parser.meta["robots"].lower():
        flag("high", "Page is marked noindex and will not appear in search.", 25)

    return {
        "url": page_url,
        "score": max(0, 100 - penalty),
        "title": title,
        "title_length": len(title),
        "meta_description": description,
        "description_length": len(description),
        "h1": parser.h1,
        "h2_count": parser.h2_count,
        "canonical": parser.canonical,
        "lang": parser.lang,
        "open_graph": {k: v[:200] for k, v in parser.og.items()},
        "images": parser.images,
        "images_missing_alt": parser.images_missing_alt,
        "links_internal": parser.links_internal,
        "links_external": parser.links_external,
        "word_count": words,
        "issues": issues,
    }


async def _fetch_html(url: str) -> tuple[str | None, dict[str, Any] | None]:
    """Fetch raw HTML with a byte cap. Returns (html, error_dict)."""
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return None, {"error": "invalid_url", "detail": "Only http(s) URLs can be audited."}
    try:
        async with _http.make_client(timeout=20.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers={"Accept": "text/html"}) as resp:
                if resp.status_code >= 400:
                    return None, {"error": f"http_{resp.status_code}", "detail": f"Could not load {url}."}
                ctype = resp.headers.get("content-type", "")
                if ctype and "html" not in ctype and "xml" not in ctype:
                    return None, {"error": "not_html", "detail": f"{url} is {ctype or 'not HTML'}."}
                chunks: list[bytes] = []
                received = 0
                async for chunk in resp.aiter_bytes(65_536):
                    chunks.append(chunk)
                    received += len(chunk)
                    if received >= _MAX_HTML_BYTES:
                        break
                final_url = str(resp.url)
    except Exception as exc:  # noqa: BLE001 - surface as a structured tool result
        return None, {"error": "fetch_failed", "detail": f"{url}: {str(exc)[:160]}"}
    return b"".join(chunks).decode("utf-8", errors="replace"), {"final_url": final_url}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def seo_audit(url: str) -> dict[str, Any]:
    """On-page SEO audit of one public web page: title, meta description, H1, images,
    canonical, Open Graph, and content depth, with graded issues. Read-only."""
    html, meta = await _fetch_html(url)
    if html is None:
        return meta or {"error": "fetch_failed", "detail": "No content."}
    report = analyze_html(html, meta.get("final_url") or url)
    report["fetched_as"] = meta.get("final_url")
    return report


async def competitor_snapshot(urls: list[str]) -> dict[str, Any]:
    """Compare on-page SEO signals across up to 5 competitor URLs. Read-only."""
    cleaned = [u for u in (urls or []) if isinstance(u, str) and u.strip()][:_MAX_COMPETITORS]
    if not cleaned:
        return {"error": "no_urls", "detail": "Provide at least one competitor URL."}
    rows: list[dict[str, Any]] = []
    for url in cleaned:
        html, meta = await _fetch_html(url)
        if html is None:
            rows.append({"url": url, **(meta or {})})
            continue
        report = analyze_html(html, meta.get("final_url") or url)
        rows.append(
            {
                "url": url,
                "score": report["score"],
                "title": report["title"],
                "meta_description": report["meta_description"],
                "h1": report["h1"][:1],
                "word_count": report["word_count"],
                "top_issue": report["issues"][0]["message"] if report["issues"] else None,
            }
        )
    ranked = sorted((r for r in rows if "score" in r), key=lambda r: r["score"], reverse=True)
    return {"competitors": rows, "best_on_page": ranked[0]["url"] if ranked else None}


def classify_intent(keyword: str) -> str:
    """Coarse search-intent read from the keyword wording."""
    words = set(re.findall(r"[a-z]+", keyword.lower()))
    for intent in ("transactional", "commercial", "informational"):
        if words & set(_INTENT_TERMS[intent]):
            return intent
    return "navigational/mixed"


async def serp_research(keyword: str, limit: int = 8) -> dict[str, Any]:
    """Top web results for a keyword plus a coarse intent classification. Read-only."""
    keyword = (keyword or "").strip()
    if not keyword:
        return {"error": "empty_keyword", "detail": "Provide a keyword or phrase."}
    results = await search_web(keyword, limit=max(1, min(int(limit), 10)))
    return {"keyword": keyword, "intent": classify_intent(keyword), "results": results}


def _syllables(word: str) -> int:
    word = word.lower()
    groups = re.findall(r"[aeiouy]+", word)
    count = len(groups)
    if word.endswith("e") and count > 1 and not word.endswith(("le", "ee")):
        count -= 1
    return max(1, count)


def score_copy(text: str, keywords: list[str] | None = None) -> dict[str, Any]:
    """Pure function: readability, structure, CTA and keyword metrics for copy."""
    text = (text or "").strip()
    if not text:
        return {"error": "empty_text", "detail": "Provide the copy to score."}
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", text)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if re.search(r"[A-Za-z]", s)]
    word_total = len(words)
    sentence_total = max(1, len(sentences))
    syllable_total = sum(_syllables(w) for w in words) or 1
    avg_sentence = word_total / sentence_total
    flesch = 206.835 - 1.015 * avg_sentence - 84.6 * (syllable_total / max(1, word_total))
    if flesch >= 60:
        reading = "easy"
    elif flesch >= 40:
        reading = "moderate"
    else:
        reading = "dense"

    lowered = text.lower()
    keyword_density: dict[str, float] = {}
    for kw in keywords or []:
        kw = (kw or "").strip().lower()
        if kw:
            hits = lowered.count(kw)
            keyword_density[kw] = round(100.0 * hits * len(kw.split()) / max(1, word_total), 2)

    ctas = sorted({m.group(0).lower() for m in _CTA_PATTERN.finditer(text)})
    issues: list[str] = []
    if avg_sentence > 22:
        issues.append("Sentences average over 22 words; split long sentences.")
    if reading == "dense":
        issues.append("Reading level is dense; use shorter words and sentences.")
    if not ctas:
        issues.append("No clear call to action detected.")
    for kw, density in keyword_density.items():
        if density > 3.0:
            issues.append(f"Keyword '{kw}' density {density}% looks like stuffing (aim under 3%).")
        elif density == 0:
            issues.append(f"Keyword '{kw}' does not appear in the copy.")

    return {
        "word_count": word_total,
        "sentence_count": sentence_total,
        "avg_sentence_words": round(avg_sentence, 1),
        "flesch_reading_ease": round(flesch, 1),
        "reading_level": reading,
        "calls_to_action": ctas,
        "keyword_density_pct": keyword_density,
        "issues": issues,
    }


_UTM_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    return _UTM_SLUG.sub("-", value.strip().lower()).strip("-")[:80]


def build_utm(
    url: str,
    source: str,
    medium: str,
    campaign: str,
    content: str | None = None,
    term: str | None = None,
) -> dict[str, Any]:
    """Pure function: append normalized UTM parameters to a URL."""
    url = (url or "").strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return {"error": "invalid_url", "detail": "Provide an absolute http(s) URL."}
    params = {
        "utm_source": _slug(source),
        "utm_medium": _slug(medium),
        "utm_campaign": _slug(campaign),
    }
    if not all(params.values()):
        return {"error": "missing_utm", "detail": "source, medium, and campaign are required."}
    if content:
        params["utm_content"] = _slug(content)
    if term:
        params["utm_term"] = _slug(term)
    existing = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if not k.startswith("utm_")]
    query = urllib.parse.urlencode(existing + list(params.items()))
    return {"url": urllib.parse.urlunsplit(parts._replace(query=query)), "params": params}


_CHANNEL_FORMATS: dict[str, list[str]] = {
    "blog": ["how-to guide", "customer story", "industry analysis"],
    "linkedin": ["carousel post", "founder POV", "short update"],
    "x": ["thread", "single insight", "poll"],
    "email": ["newsletter", "product update", "nurture email"],
    "youtube": ["tutorial video", "short clip"],
}


def content_calendar(
    topic: str,
    start_date: str,
    weeks: int = 4,
    channels: list[str] | None = None,
    posts_per_week: int = 3,
) -> dict[str, Any]:
    """Pure function: a deterministic publishing calendar rotating channels and formats."""
    try:
        start = dt.date.fromisoformat(start_date)
    except (TypeError, ValueError):
        return {"error": "invalid_date", "detail": "start_date must be YYYY-MM-DD."}
    weeks = max(1, min(int(weeks), 12))
    posts_per_week = max(1, min(int(posts_per_week), 7))
    chosen = [c.lower() for c in (channels or []) if c.lower() in _CHANNEL_FORMATS] or ["linkedin", "blog", "email"]
    slots = [start + dt.timedelta(days=d) for d in range(weeks * 7)]
    cadence = [0, 2, 4, 1, 5, 3, 6][:posts_per_week]
    calendar: list[dict[str, Any]] = []
    index = 0
    for week in range(weeks):
        for offset in sorted(cadence):
            day = slots[week * 7 + offset]
            channel = chosen[index % len(chosen)]
            formats = _CHANNEL_FORMATS[channel]
            calendar.append(
                {
                    "date": day.isoformat(),
                    "weekday": day.strftime("%A"),
                    "channel": channel,
                    "format": formats[(index // len(chosen)) % len(formats)],
                    "theme": f"{topic} — week {week + 1}, post {index + 1}",
                }
            )
            index += 1
    return {"topic": topic, "start": start.isoformat(), "weeks": weeks, "channels": chosen, "calendar": calendar}


def marketing_tools(approval_default: bool = True) -> list[Tool]:
    """The Marketing specialist's toolset. All tools are read-only, so the approval
    default does not apply; the parameter keeps the builder signature uniform."""
    return [
        Tool(seo_audit, name="marketing_seo_audit", max_retries=1),
        Tool(competitor_snapshot, name="marketing_competitor_snapshot", max_retries=1),
        Tool(serp_research, name="marketing_serp_research", max_retries=1),
        Tool(score_copy, name="marketing_score_copy", max_retries=1),
        Tool(build_utm, name="marketing_build_utm", max_retries=1),
        Tool(content_calendar, name="marketing_content_calendar", max_retries=1),
    ]
