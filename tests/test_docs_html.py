"""Offline checks for Studio docs HTML → heading chunks."""

import asyncio
from pathlib import Path

from mistralai.search.toolkit.document import DocumentChunk

from search_app.docs_html import (
    _enrich_chunks,
    _leading_section_heading,
    isolate_article,
    parse_docs_page,
    slugify,
)

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
    page = asyncio.run(parse_docs_page(_PAGE_URL, html=html, chunk_size=1))
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


def test_parse_docs_page_default_config_merges_small_pages() -> None:
    html = _FIXTURE.read_text()
    page = asyncio.run(parse_docs_page(_PAGE_URL, html=html))
    assert len(page.chunks) == 1


def test_leading_section_heading_ignores_hash_comments_in_code() -> None:
    assert _leading_section_heading("# get api key\nimport os", "Chat completions") is None
    assert _leading_section_heading("## Multi-turn\n\nMore text.", "Chat completions") == "Multi-turn"


def test_enrich_chunks_inherits_last_section_for_code_blocks() -> None:
    chunks = [
        DocumentChunk(
            source_id="page",
            locator="0",
            content="## Chat completion\n\nThe API accepts messages.",
            start_offset=0,
            end_offset=40,
        ),
        DocumentChunk(
            source_id="page",
            locator="1",
            content="import os\n# get api key\nfrom mistralai.client import Mistral",
            start_offset=40,
            end_offset=90,
        ),
        DocumentChunk(
            source_id="page",
            locator="2",
            content="## Multi-turn\n\nSend multiple messages.",
            start_offset=90,
            end_offset=130,
        ),
        DocumentChunk(
            source_id="page",
            locator="3",
            content="async function main() {}",
            start_offset=130,
            end_offset=155,
        ),
    ]
    enriched = _enrich_chunks(
        chunks,
        page_url=_PAGE_URL,
        title="Chat completions",
    )
    assert enriched[1].metadata.get("heading") == "Chat completion"
    assert enriched[1].metadata.get("citation_url") == f"{_PAGE_URL}#chat-completion"
    assert enriched[3].metadata.get("heading") == "Multi-turn"
    assert enriched[3].metadata.get("citation_url") == f"{_PAGE_URL}#multi-turn"
