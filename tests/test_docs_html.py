"""Offline checks for Studio docs HTML → heading chunks."""

import asyncio
from pathlib import Path

from search_app.docs_html import isolate_article, parse_docs_page, slugify

_FIXTURE = Path(__file__).parent / "fixtures" / "chat_completion_snippet.html"
_PAGE_URL = "https://docs.mistral.ai/studio/conversations/chat-completion"


def test_slugify_matches_studio_anchors() -> None:
    assert slugify("Chat completion") == "chat-completion"
    assert slugify("Multi-turn") == "multi-turn"
    assert slugify("Chat messages") == "chat-messages"


def test_isolate_article_strips_section_tabs_and_keeps_headings() -> None:
    html = _FIXTURE.read_text()
    title, breadcrumb, article = isolate_article(html, _PAGE_URL)
    assert title == "Chat completions"
    assert breadcrumb == ("Studio", "Conversations", "Chat Completions")
    assert "<main>" in article
    assert 'data-table-of-contents' not in article
    assert 'data-slot="section-tab"' not in article
    assert "Copy section link" not in article
    assert "<h2" in article and "Multi-turn" in article
    assert 'id="multi-turn"' in article


def test_parse_docs_page_splits_on_headings_and_stamps_citation_urls() -> None:
    html = _FIXTURE.read_text()
    page = asyncio.run(parse_docs_page(_PAGE_URL, html=html))
    headings = [chunk.metadata.get("heading") for chunk in page.chunks]
    citations = [chunk.metadata.get("citation_url") for chunk in page.chunks]

    assert page.title == "Chat completions"
    assert "Chat completion" in headings
    assert "Multi-turn" in headings
    assert f"{_PAGE_URL}#multi-turn" in citations
    assert f"{_PAGE_URL}#chat-completion" in citations
    # Page title chunk cites the canonical URL, not a hash of the h1.
    title_chunk = next(c for c in page.chunks if c.metadata.get("heading") == "Chat completions")
    assert title_chunk.metadata.get("citation_url") == _PAGE_URL
