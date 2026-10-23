"""Marketing specialist tools: pure analysis functions, the fetch path (mocked
transport, no network), and the registered toolset."""

from __future__ import annotations

import httpx
import pytest

from voicefirst.agents.tools import _http
from voicefirst.agents.tools.marketing import (
    analyze_html,
    build_utm,
    classify_intent,
    competitor_snapshot,
    content_calendar,
    marketing_tools,
    score_copy,
    seo_audit,
)

GOOD_PAGE = """<!doctype html>
<html lang="en"><head>
<title>Acme Analytics — Customer Revenue Dashboards for Growing Teams</title>
<meta name="description" content="Acme turns raw billing data into live revenue dashboards your whole team can trust, with no SQL required.">
<link rel="canonical" href="https://acme.test/">
<meta property="og:image" content="https://acme.test/card.png">
</head><body>
<h1>Revenue clarity in minutes</h1>
<h2>Why teams switch</h2>
<img src="a.png" alt="dashboard"><img src="b.png">
<a href="/pricing">Pricing</a><a href="https://other.test/x">Partner</a>
<script>var hidden = "should not count";</script>
<p>""" + ("Acme gives operators clear, shared numbers every morning. " * 60) + """</p>
<a href="/signup">Get started free</a>
</body></html>"""


def test_analyze_html_extracts_on_page_signals():
    report = analyze_html(GOOD_PAGE, "https://acme.test/")
    assert report["lang"] == "en"
    assert report["title"].startswith("Acme Analytics")
    assert report["h1"] == ["Revenue clarity in minutes"]
    assert report["h2_count"] == 1
    assert report["images"] == 2 and report["images_missing_alt"] == 1
    assert report["links_internal"] == 2 and report["links_external"] == 1
    assert report["canonical"] == "https://acme.test/"
    assert "og:image" in report["open_graph"]
    assert report["word_count"] >= 300


def test_analyze_html_flags_missing_essentials_and_scores_lower():
    bare = "<html><body><p>Hi there.</p></body></html>"
    report = analyze_html(bare, "https://bare.test/")
    messages = " | ".join(i["message"] for i in report["issues"])
    assert "Missing <title>" in messages
    assert "Missing meta description" in messages
    assert "No <h1>" in messages
    assert "Thin content" in messages
    assert report["score"] < analyze_html(GOOD_PAGE, "https://acme.test/")["score"]


def test_analyze_html_noindex_is_high_severity():
    page = '<html><head><meta name="robots" content="noindex"></head><body></body></html>'
    report = analyze_html(page, "https://x.test/")
    assert any(i["severity"] == "high" and "noindex" in i["message"] for i in report["issues"])


def test_score_copy_reports_readability_cta_and_density():
    copy = (
        "Get started free. Ship faster with simple dashboards. "
        "Dashboards help teams see revenue. Start your trial today."
    )
    result = score_copy(copy, ["dashboards", "revenue"])
    assert result["sentence_count"] == 4
    assert result["reading_level"] in ("easy", "moderate")
    assert "get started" in result["calls_to_action"]
    assert result["keyword_density_pct"]["dashboards"] > 0


def test_score_copy_flags_missing_cta_and_stuffing():
    stuffed = "dashboards " * 40 + "are great."
    result = score_copy(stuffed, ["dashboards", "pricing"])
    issues = " ".join(result["issues"])
    assert "No clear call to action" in issues
    assert "looks like stuffing" in issues
    assert "'pricing' does not appear" in issues


def test_score_copy_rejects_empty():
    assert score_copy("   ")["error"] == "empty_text"


def test_build_utm_normalizes_and_preserves_existing_query():
    out = build_utm(
        "https://acme.test/launch?ref=hn",
        source="LinkedIn",
        medium="Social Post",
        campaign="Spring Launch 2026!",
        content="Hero CTA",
    )
    assert out["params"] == {
        "utm_source": "linkedin",
        "utm_medium": "social-post",
        "utm_campaign": "spring-launch-2026",
        "utm_content": "hero-cta",
    }
    assert "ref=hn" in out["url"] and "utm_campaign=spring-launch-2026" in out["url"]


def test_build_utm_rejects_bad_input():
    assert build_utm("ftp://x.test", "a", "b", "c")["error"] == "invalid_url"
    assert build_utm("https://x.test", "", "b", "c")["error"] == "missing_utm"


def test_classify_intent_buckets():
    assert classify_intent("buy running shoes") == "transactional"
    assert classify_intent("best crm software") == "commercial"
    assert classify_intent("how to write a press release") == "informational"
    assert classify_intent("acme corp") == "navigational/mixed"


def test_content_calendar_is_deterministic_and_rotates_channels():
    plan = content_calendar("Q3 launch", "2026-10-05", weeks=2, channels=["linkedin", "x"], posts_per_week=3)
    rows = plan["calendar"]
    assert len(rows) == 6
    assert rows[0]["date"] == "2026-10-05" and rows[0]["weekday"] == "Monday"
    assert {r["channel"] for r in rows} == {"linkedin", "x"}
    assert content_calendar("Q3 launch", "2026-10-05", weeks=2, channels=["linkedin", "x"], posts_per_week=3) == plan


def test_content_calendar_rejects_bad_date_and_unknown_channels_fall_back():
    assert content_calendar("t", "not-a-date")["error"] == "invalid_date"
    plan = content_calendar("t", "2026-01-01", weeks=1, channels=["myspace"])
    assert plan["channels"] == ["linkedin", "blog", "email"]


def test_marketing_toolset_is_read_only_and_named():
    names = {t.name for t in marketing_tools()}
    assert names == {
        "marketing_seo_audit",
        "marketing_competitor_snapshot",
        "marketing_serp_research",
        "marketing_score_copy",
        "marketing_build_utm",
        "marketing_content_calendar",
    }


@pytest.fixture
def page_transport(monkeypatch):
    routes: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        return routes.get(key, httpx.Response(404, text="not found"))

    monkeypatch.setattr(_http, "transport", httpx.MockTransport(handler))
    return routes


async def test_seo_audit_fetches_and_analyzes(page_transport):
    page_transport["GET /"] = httpx.Response(200, text=GOOD_PAGE, headers={"content-type": "text/html; charset=utf-8"})
    report = await seo_audit("https://acme.test/")
    assert report["title"].startswith("Acme Analytics")
    assert report["score"] >= 80


async def test_seo_audit_rejects_non_html_and_errors(page_transport):
    page_transport["GET /doc"] = httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
    assert (await seo_audit("https://acme.test/doc"))["error"] == "not_html"
    assert (await seo_audit("https://acme.test/missing"))["error"] == "http_404"
    assert (await seo_audit("javascript:alert(1)"))["error"] == "invalid_url"


async def test_competitor_snapshot_ranks_pages(page_transport):
    page_transport["GET /a"] = httpx.Response(200, text=GOOD_PAGE, headers={"content-type": "text/html"})
    page_transport["GET /b"] = httpx.Response(200, text="<html><body>x</body></html>", headers={"content-type": "text/html"})
    result = await competitor_snapshot(["https://acme.test/a", "https://rival.test/b"])
    assert len(result["competitors"]) == 2
    assert result["best_on_page"] == "https://acme.test/a"


async def test_competitor_snapshot_requires_urls():
    assert (await competitor_snapshot([]))["error"] == "no_urls"
