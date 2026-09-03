"""Offline checks for Studio docs HTML → heading chunks."""

import asyncio
from pathlib import Path

from mistralai.search.toolkit.document import DocumentChunk

from search_app.docs_html import (
    _enrich_chunks,
    _leading_section_heading,
    citation_url,
    docs_paths_for_url,
    is_preprocessed_docs_html,
    isolate_article,
    load_docs_meta,
    parse_docs_page,
    parse_saved_docs_page,
    preprocess_docs_page,
    slugify,
    validate_docs_url,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "chat_completion_snippet.html"
_PAGE_URL = "https://docs.mistral.ai/studio/conversations/chat-completion"


def test_citation_url_uses_html_anchor_id_not_slugify() -> None:
    url = "https://docs.mistral.ai/studio/conversations/chat-completion/prompting"
    anchors = {
        "what to avoid": "avoid",
        "avoid subjective and blurry words": "subjective-and-blurry",
    }
    assert (
        citation_url(
            url,
            "What to Avoid",
            is_page_title=False,
            heading_anchors=anchors,
        )
        == f"{url}#avoid"
    )
    assert (
        citation_url(
            url,
            "Avoid Subjective and Blurry Words",
            is_page_title=False,
            heading_anchors=anchors,
        )
        == f"{url}#subjective-and-blurry"
    )
    # h3 without id inherits parent section link
    assert (
        citation_url(
            url,
            "What you should Avoid",
            is_page_title=False,
            heading_anchors=anchors,
            parent_citation_url=f"{url}#avoid",
        )
        == f"{url}#avoid"
    )


def test_slugify_matches_studio_anchors() -> None:
    assert slugify("Chat completion") == "chat-completion"
    assert slugify("Multi-turn") == "multi-turn"
    assert slugify("Chat messages") == "chat-messages"


def test_isolate_article_strips_section_tabs_and_keeps_headings() -> None:
    html = _FIXTURE.read_text()
    title, breadcrumb, article, anchors = isolate_article(html, _PAGE_URL)
    assert title == "Chat completions"
    assert breadcrumb == ("Studio", "Conversations", "Chat Completions")
    assert "<main>" in article
    assert "data-table-of-contents" not in article
    assert 'data-slot="section-tab"' not in article
    assert "Copy section link" not in article
    assert "<h2" in article and "Multi-turn" in article
    assert 'id="multi-turn"' in article
    assert anchors["multi-turn"] == "multi-turn"
    # Dark-theme twin dropped; light variant kept.
    assert "print(\"dark\")" not in article
    assert "print(\"light\")" in article
    assert "dark:block" not in article


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
    title_chunk = next(
        c for c in page.chunks if c.metadata.get("heading") == "Chat completions"
    )
    assert title_chunk.metadata.get("citation_url") == _PAGE_URL


def test_parse_docs_page_default_config_merges_small_pages() -> None:
    html = _FIXTURE.read_text()
    page = asyncio.run(parse_docs_page(_PAGE_URL, html=html))
    assert len(page.chunks) == 1


def test_leading_section_heading_ignores_hash_comments_in_code() -> None:
    assert (
        _leading_section_heading("# get api key\nimport os", "Chat completions") is None
    )
    assert (
        _leading_section_heading("## Multi-turn\n\nMore text.", "Chat completions")
        == "Multi-turn"
    )


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
        heading_anchors={
            "chat completion": "chat-completion",
            "multi-turn": "multi-turn",
        },
    )
    assert enriched[1].metadata.get("heading") == "Chat completion"
    assert enriched[1].metadata.get("citation_url") == f"{_PAGE_URL}#chat-completion"
    assert enriched[3].metadata.get("heading") == "Multi-turn"
    assert enriched[3].metadata.get("citation_url") == f"{_PAGE_URL}#multi-turn"


def test_validate_docs_url_rejects_non_docs_host() -> None:
    try:
        validate_docs_url("https://example.com/page")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "docs.mistral.ai" in str(exc)


def test_preprocess_docs_page_writes_html_and_meta(tmp_path: Path) -> None:
    html = _FIXTURE.read_text()
    html_path, meta_path = asyncio.run(
        preprocess_docs_page(_PAGE_URL, tmp_path, html=html)
    )
    assert html_path == tmp_path / "studio/conversations/chat-completion.html"
    assert meta_path == tmp_path / "studio/conversations/chat-completion.meta.json"
    assert is_preprocessed_docs_html(html_path)
    meta = load_docs_meta(html_path)
    assert meta.url == _PAGE_URL
    assert meta.title == "Chat completions"
    assert meta.heading_anchors["multi-turn"] == "multi-turn"


def test_parse_saved_docs_page_matches_live_pipeline(tmp_path: Path) -> None:
    html = _FIXTURE.read_text()
    html_path, _ = asyncio.run(preprocess_docs_page(_PAGE_URL, tmp_path, html=html))
    saved = asyncio.run(parse_saved_docs_page(html_path, chunk_size=1))
    live = asyncio.run(parse_docs_page(_PAGE_URL, html=html, chunk_size=1))
    assert saved.title == live.title
    assert [c.metadata.get("citation_url") for c in saved.chunks] == [
        c.metadata.get("citation_url") for c in live.chunks
    ]


def test_docs_paths_for_url() -> None:
    html_path, meta_path = docs_paths_for_url(_PAGE_URL)
    assert html_path.name == "chat-completion.html"
    assert meta_path.name == "chat-completion.meta.json"
