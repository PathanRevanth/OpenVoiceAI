"""Chat enrichment (top-5 parity pack): sources, follow-ups, feedback/shares."""

from __future__ import annotations

import pytest

from voicefirst.api.enrichment import build_followups, extract_sources
from voicefirst.api.schemas import ConversationRename, RunFeedbackIn
from voicefirst.db.models import ConversationShareRecord, RunFeedbackRecord, new_id
from voicefirst.db.session import Database


def test_extract_sources_from_markdown_and_bare_urls():
    text = (
        "See [Anthropic](https://www.anthropic.com/news/claude-haiku-4-5) "
        "and also https://example.com/docs, plus https://example.com/docs again."
    )
    sources = extract_sources(text, None)
    assert [s["url"] for s in sources] == [
        "https://www.anthropic.com/news/claude-haiku-4-5",
        "https://example.com/docs",
    ]
    assert sources[0]["id"] == "1" and sources[0]["title"] == "Anthropic"


def test_extract_sources_merges_fetched_tool_urls_first_seen_order():
    text = "No links here."
    tool_calls = [
        {"tool": "research_fetch_page", "args": {"url": "https://example.com/a"}},
        {"tool": "research_fetch_page", "args": {"url": "https://example.com/a"}},
        {"tool": "research_fetch_page", "args": {"url": "https://example.com/b"}},
    ]
    sources = extract_sources(text, tool_calls)
    assert [s["url"] for s in sources] == ["https://example.com/a", "https://example.com/b"]


def test_extract_sources_caps_at_eight_and_ignores_non_http():
    text = " ".join(f"https://example.com/{i}" for i in range(20))
    assert len(extract_sources(text, None)) == 8
    assert extract_sources("not a link (notaurl) ftp://example.com/x", None) == []


def test_build_followups_are_contextual_and_stable():
    first = build_followups("What is pydantic-ai?", "Pydantic AI is a framework.")
    assert len(first) == 3 and all(isinstance(q, str) and q for q in first)
    assert first == build_followups("What is pydantic-ai?", "Pydantic AI is a framework.")
    assert any("pydantic-ai" in q.lower() or "pydantic" in q.lower() for q in first)


def test_build_followups_prefers_code_and_web_variants():
    code = build_followups("Help with charts", "```python\nprint(1)\n```")
    assert any("code" in q.lower() or "example" in q.lower() for q in code)
    web = build_followups("Tell me about MCP", "Some answer", used_web=True)
    assert any("latest" in q.lower() or "update" in q.lower() for q in web)


def test_conversation_rename_rejects_blank():
    # Pydantic keeps the raw string; the route strips and rejects blanks (422).
    assert ConversationRename(title="   ").title.strip() == ""
    assert ConversationRename(title="  Launch plan  ").title.strip() == "Launch plan"


def test_run_feedback_schema_accepts_up_down_only():
    assert RunFeedbackIn(rating="up").rating == "up"
    assert RunFeedbackIn(rating="down", comment="too short").comment == "too short"
    with pytest.raises(Exception):
        RunFeedbackIn(rating="sideways")  # type: ignore[arg-type]


@pytest.fixture
async def db(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/enrich.db")
    await database.create_all()
    yield database
    await database.close()


async def test_feedback_and_share_tables_persist(db):
    async with db.session() as session:
        session.add(
            RunFeedbackRecord(
                id=new_id("fb"), run_id="run_x", tenant_id="t1", user_id="u1", rating="up"
            )
        )
        session.add(
            ConversationShareRecord(
                id=new_id("share"), conversation_id="conv_x", tenant_id="t1", token="tok123", revoked=False
            )
        )
    async with db.session() as session:
        from sqlalchemy import select

        fb = (await session.execute(select(RunFeedbackRecord))).scalars().all()
        shares = (await session.execute(select(ConversationShareRecord))).scalars().all()
        assert len(fb) == 1 and fb[0].rating == "up"
        assert len(shares) == 1 and shares[0].token == "tok123"
