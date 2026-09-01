"""Parse a Mistral docs HTML page into markdown chunks with citation metadata.

Studio pages do not use ``<h2>`` for visible section titles. Each section is a
``div[data-slot=section-tab][id=...]`` (the "Copy section link" target) plus a
screen-reader ``<h2>``/``<h3>``. We drop the tab chrome, keep the sr-only
headings, convert with the toolkit HTMLExtractor, then split on markdown
headers.

This module does not embed or index — use it to inspect extraction before
wiring ingest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag
from mistralai.search.toolkit.document import Document, DocumentChunk
from mistralai.search.toolkit.ingestion import File
from mistralai.search.toolkit.ingestion.extractors import HTMLExtractor
from mistralai.search.toolkit.ingestion.text_splitters import (
    MarkdownTextSplitter,
    MarkdownTextSplitterConfig,
)

_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SLUG_STRIP_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_HYPHEN_RE = re.compile(r"[-\s]+")

# Split at h1–h3 boundaries; keep header lines in chunk text for retrieval context.
HEADERS_TO_SPLIT_ON: list[tuple[str, str]] = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
]

# Match starter-app ingest defaults;
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_MAX_SIZE = 4096
DEFAULT_CHUNK_OVERLAP = 200


def markdown_splitter_config(
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_max_size: int | None = DEFAULT_CHUNK_MAX_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> MarkdownTextSplitterConfig:
    """Build a header-aware splitter config per Search Toolkit docs."""
    overlap = min(chunk_overlap, chunk_size - 1) if chunk_size > 1 else 0
    return MarkdownTextSplitterConfig(
        headers_to_split_on=HEADERS_TO_SPLIT_ON,
        strip_headers=False,
        chunk_size=chunk_size,
        chunk_max_size=chunk_max_size,
        chunk_overlap=overlap,
    )


@dataclass(frozen=True)
class DocsPage:
    url: str
    title: str
    breadcrumb: tuple[str, ...]
    html: str
    markdown: str
    document: Document
    chunks: tuple[DocumentChunk, ...] = field(default_factory=tuple)

    @property
    def breadcrumb_text(self) -> str:
        return " > ".join(self.breadcrumb)


def slugify(text: str) -> str:
    """Match the hashes used by Studio 'Copy section link' (e.g. ``#multi-turn``)."""
    slug = _SLUG_STRIP_RE.sub("", text.lower().strip())
    return _SLUG_HYPHEN_RE.sub("-", slug).strip("-")


def citation_url(
    page_url: str,
    heading: str | None,
    *,
    is_page_title: bool,
    heading_anchors: dict[str, str] | None = None,
    parent_citation_url: str | None = None,
) -> str:
    canonical, _ = urldefrag(page_url)
    if not heading or is_page_title:
        return canonical
    if heading_anchors:
        anchor_id = heading_anchors.get(heading.casefold())
        if anchor_id:
            return f"{canonical}#{anchor_id}"
    if parent_citation_url and parent_citation_url != canonical:
        return parent_citation_url
    return f"{canonical}#{slugify(heading)}"


def _heading_anchors(root: Tag) -> dict[str, str]:
    """Map heading text (casefold) to Studio ``sectionId`` / ``id`` from isolated HTML."""
    anchors: dict[str, str] = {}
    for tag in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        anchor_id = tag.get("id")
        if not anchor_id:
            continue
        text = tag.get_text(" ", strip=True)
        if text:
            anchors[text.casefold()] = anchor_id
    return anchors


async def fetch_html(url: str, *, timeout: float = 30.0) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        response = await client.get(
            url, headers={"User-Agent": "mistral-docs-preview/0.1"}
        )
        response.raise_for_status()
        return response.text


def _lift_section_headings(root: Tag) -> None:
    """Pull sr-only section headings out of section-tab chrome, then drop the tab div.

    Studio renders each section as a ``div[data-slot=section-tab][id=…]`` (visible
    tab label + "Copy section link") with an ``h2``/``h3`` inside for a11y. If we
    only delete the tab div, we either lose the heading (live site) or leave a
    duplicate plain-text label in the markdown (minimal isolate). Lifting the
    heading out first keeps ``## Multi-turn`` and the ``id`` used for ``#multi-turn``.
    """
    for tab in list(root.select('[data-slot="section-tab"]')):
        if not isinstance(tab, Tag):
            continue
        section_id = tab.get("id")
        heading = tab.find(["h2", "h3"])
        if isinstance(heading, Tag):
            if section_id:
                heading["id"] = section_id
            tab.insert_before(heading.extract())
        tab.decompose()


def isolate_article(html: str, page_url: str) -> tuple[str, tuple[str, ...], str, dict[str, str]]:
    """Return ``(title, breadcrumb, article HTML, heading_anchors)`` for ``HTMLExtractor``.

    Steps:
    1. Scope to ``<main>`` — drops outer page chrome (header, sidebar shell).
    2. Read breadcrumb + page title before mutating the tree.
    3. Remove on-page TOC (duplicates heading list).
    4. Lift sr-only headings out of section-tab divs, then remove those divs —
       stops "Chat messages" plain-text chunks that sit beside ``### Chat messages``.
    5. Hand the result to ``HTMLExtractor`` (MarkdownifyConverter strips nav,
       script, footer, etc.).
    """
    soup = BeautifulSoup(html, "html.parser")
    breadcrumb = _breadcrumb(soup)
    main = soup.find("main")
    root: Tag = main if isinstance(main, Tag) else soup

    for node in root.select("[data-table-of-contents]"):
        node.decompose()

    _lift_section_headings(root)

    h1 = root.find("h1")
    title = (
        h1.get_text(" ", strip=True)
        if isinstance(h1, Tag)
        else _title_from_url(page_url)
    )
    anchors = _heading_anchors(root)
    return title, breadcrumb, str(root), anchors


def _breadcrumb(soup: BeautifulSoup) -> tuple[str, ...]:
    nav = soup.find("nav", attrs={"aria-label": "breadcrumb"})
    if not isinstance(nav, Tag):
        return ()
    crumbs: list[str] = []
    for link in nav.find_all("a"):
        text = link.get_text(" ", strip=True)
        if text:
            crumbs.append(text)
    return tuple(crumbs)


def _title_from_url(page_url: str) -> str:
    path = urlparse(page_url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else page_url


def _leading_section_heading(content: str, page_title: str) -> str | None:
    """Return a section heading only when it leads the chunk.

    Uses the first non-empty line so ``#`` comments inside code blocks do not
    advance the carry-forward state. Single-``#`` lines count only when they
    match the page ``h1`` title; section tabs use ``##`` / ``###``.
    """
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _HEADING_LINE_RE.match(stripped)
        if not match:
            return None
        level = len(match.group(1))
        heading = match.group(2).strip()
        if level >= 2:
            return heading
        if heading.casefold() == page_title.casefold():
            return heading
        return None
    return None


def _enrich_chunks(
    chunks: list[DocumentChunk],
    *,
    page_url: str,
    title: str,
    heading_anchors: dict[str, str],
) -> tuple[DocumentChunk, ...]:
    """Stamp citation metadata and inherit the last section for heading-less chunks."""
    canonical_url = urljoin(page_url, urlparse(page_url).path)
    last_heading: str | None = title
    last_citation_url = citation_url(
        page_url, title, is_page_title=True, heading_anchors=heading_anchors
    )

    enriched: list[DocumentChunk] = []
    for chunk in chunks:
        section_heading = _leading_section_heading(chunk.content, title)
        if section_heading is not None:
            is_page_title = section_heading.casefold() == title.casefold()
            last_heading = section_heading
            last_citation_url = citation_url(
                page_url,
                section_heading,
                is_page_title=is_page_title,
                heading_anchors=heading_anchors,
                parent_citation_url=last_citation_url,
            )
            chunk_heading = section_heading
            chunk_citation_url = last_citation_url
        else:
            chunk_heading = last_heading
            chunk_citation_url = last_citation_url

        meta = chunk.metadata.model_copy(
            update={
                "url": canonical_url,
                "title": title,
                "heading": chunk_heading,
                "citation_url": chunk_citation_url,
            }
        )
        enriched.append(chunk.model_copy(update={"metadata": meta}))
    return tuple(enriched)


async def parse_docs_page(
    url: str,
    html: str | None = None,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_max_size: int | None = DEFAULT_CHUNK_MAX_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> DocsPage:
    """Fetch (unless ``html`` is given), extract, split, and stamp citation metadata."""
    raw_html = html if html is not None else await fetch_html(url)
    title, breadcrumb, article_html, heading_anchors = isolate_article(raw_html, url)

    extractor = HTMLExtractor()
    file = File(
        path=url, name="page.html", raw=article_html.encode("utf-8"), source_id=url
    )
    document = await extractor.extract(file)

    splitter = MarkdownTextSplitter(
        markdown_splitter_config(
            chunk_size=chunk_size,
            chunk_max_size=chunk_max_size,
            chunk_overlap=chunk_overlap,
        )
    )
    document = await splitter.process(document)

    chunks = _enrich_chunks(
        list(document.chunks),
        page_url=url,
        title=title,
        heading_anchors=heading_anchors,
    )
    document = document.model_copy(update={"chunks": list(chunks)})
    return DocsPage(
        url=url,
        title=title,
        breadcrumb=breadcrumb,
        html=article_html,
        markdown=document.content or "",
        document=document,
        chunks=chunks,
    )
