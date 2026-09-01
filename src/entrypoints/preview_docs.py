"""Preview HTMLExtractor → markdown → heading chunks (no embed, no Vespa).

Uses toolkit ``HTMLExtractor`` + ``MarkdownTextSplitter``. By default runs the
``search_app.docs_html`` pipeline (isolate ``<main>``, strip section tabs, stamp
``citation_url`` from heading slugs).

Usage:
    python -m entrypoints.preview_docs
    python -m entrypoints.preview_docs --content
    python -m entrypoints.preview_docs --no-split
    python -m entrypoints.preview_docs --raw
    python -m entrypoints.preview_docs --chunk-size 4096
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys

from mistralai.search.toolkit.ingestion import File
from mistralai.search.toolkit.ingestion.extractors import HTMLExtractor
from mistralai.search.toolkit.ingestion.text_splitters import (
    MarkdownTextSplitter,
    MarkdownTextSplitterConfig,
)
from search_app.docs_html import (
    DEFAULT_CHUNK_MAX_SIZE,
    DEFAULT_CHUNK_SIZE,
    citation_url,
    fetch_html,
    isolate_article,
    parse_docs_page,
    slugify,
)

_DEFAULT_URL = "https://docs.mistral.ai/studio/conversations/chat-completion"
_STARTER_CHUNK_SIZE = 4096
_STARTER_CHUNK_OVERLAP = 50
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _print_meta(label: str, value: object) -> None:
    print(f"{label:<18}{value}")


def _truncate(text: str, limit: int) -> str:
    if not limit or len(text) <= limit:
        return text
    return text[:limit] + f"\n... [{len(text) - limit} more chars]"


def _print_headings(markdown: str, page_url: str, *, page_title: str | None) -> None:
    print("\n=== markdown headings → anchors ===")
    for match in _HEADING_RE.finditer(markdown):
        level = len(match.group(1))
        heading = match.group(2).strip()
        is_title = page_title is not None and heading.casefold() == page_title.casefold()
        anchor = "" if is_title else f"#{slugify(heading)}"
        cite = citation_url(page_url, heading, is_page_title=is_title)
        print(f"  h{level}  {heading!r}  anchor={anchor!r}  citation={cite}")


def _print_chunks(chunks: list, *, limit: int | None, preview_chars: int) -> None:
    shown = chunks if limit is None else chunks[:limit]
    print(f"\n=== chunks ({len(chunks)} total, showing {len(shown)}) ===")
    for i, chunk in enumerate(shown, 1):
        meta = chunk.metadata
        print(f"\n--- chunk {i}/{len(chunks)} ---")
        print(f"heading:       {meta.get('heading')}")
        print(f"citation_url:  {meta.get('citation_url')}")
        print(f"offsets:       {chunk.start_offset}-{chunk.end_offset} ({len(chunk.content)} chars)")
        print(f"metadata:      {dict(meta)}")
        print("content:")
        print(_truncate(chunk.content, preview_chars))


async def _run_raw(
    url: str,
    *,
    chunk_size: int,
    chunk_max_size: int,
    no_split: bool,
    show_content: bool,
    preview_chars: int,
    max_chunks: int | None,
) -> None:
    """Vanilla HTMLExtractor on the full downloaded page (no isolate_article)."""
    html = await fetch_html(url)
    print("Mode:              raw HTMLExtractor (full page, no pre-clean)")
    _print_meta("html chars:", len(html))

    document = await HTMLExtractor().extract(
        File(path=url, name="page.html", raw=html.encode("utf-8"), source_id=url)
    )
    markdown = document.content or ""
    _print_meta("markdown chars:", len(markdown))
    _print_meta("extractor chunks:", len(document.chunks))

    if show_content:
        print("\n=== markdown ===\n")
        print(_truncate(markdown, preview_chars))

    _print_headings(markdown, url, page_title=None)

    if no_split:
        _print_chunks(list(document.chunks), limit=max_chunks, preview_chars=preview_chars)
        return

    split_doc = await MarkdownTextSplitter(
        MarkdownTextSplitterConfig(
            chunk_size=chunk_size,
            chunk_max_size=chunk_max_size,
            chunk_overlap=_STARTER_CHUNK_OVERLAP,
        )
    ).process(document)
    _print_meta("after split:", len(split_doc.chunks))
    _print_chunks(list(split_doc.chunks), limit=max_chunks, preview_chars=preview_chars)


async def _run_cleaned(args: argparse.Namespace) -> None:
    """docs_html pipeline: isolate main, HTMLExtractor, optional split, citation metadata."""
    if not args.no_split:
        page = await parse_docs_page(
            args.url,
            chunk_size=args.chunk_size,
            chunk_max_size=args.chunk_max_size,
        )
        print("Mode:              HTMLExtractor + isolate_article + heading split")
        _print_meta("URL:", page.url)
        _print_meta("title:", page.title)
        _print_meta("breadcrumb:", page.breadcrumb_text or "(none)")
        _print_meta("html chars:", len(page.html))
        _print_meta("markdown chars:", len(page.markdown))
        if args.content:
            print("\n=== markdown ===\n")
            print(_truncate(page.markdown, args.preview_chars))
        _print_headings(page.markdown, page.url, page_title=page.title)
        _print_meta("chunks:", len(page.chunks))
        _print_chunks(list(page.chunks), limit=args.max_chunks, preview_chars=args.preview_chars)
        return

    html = await fetch_html(args.url)
    title, breadcrumb, article_html = isolate_article(html, args.url)
    document = await HTMLExtractor().extract(
        File(path=args.url, name="page.html", raw=article_html.encode("utf-8"), source_id=args.url)
    )
    markdown = document.content or ""
    print("Mode:              HTMLExtractor + isolate_article (no split)")
    _print_meta("URL:", args.url)
    _print_meta("title:", title)
    _print_meta("breadcrumb:", " > ".join(breadcrumb) if breadcrumb else "(none)")
    _print_meta("markdown chars:", len(markdown))
    if args.content:
        print("\n=== markdown ===\n")
        print(_truncate(markdown, args.preview_chars))
    _print_headings(markdown, args.url, page_title=title)
    _print_chunks(list(document.chunks), limit=args.max_chunks, preview_chars=args.preview_chars)


async def _run(args: argparse.Namespace) -> None:
    if args.raw:
        await _run_raw(
            args.url,
            chunk_size=args.chunk_size,
            chunk_max_size=args.chunk_max_size,
            no_split=args.no_split,
            show_content=args.content,
            preview_chars=args.preview_chars,
            max_chunks=args.max_chunks,
        )
        return
    await _run_cleaned(args)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview HTMLExtractor markdown and heading/anchor chunks for a docs URL."
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=_DEFAULT_URL,
        help=f"Docs page URL (default: {_DEFAULT_URL})",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Skip isolate_article; run HTMLExtractor on the full page HTML.",
    )
    parser.add_argument(
        "--content",
        action="store_true",
        help="Print converted markdown.",
    )
    parser.add_argument(
        "--no-split",
        action="store_true",
        help="Skip MarkdownTextSplitter (raw mode) or show pre-split state (cleaned mode).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=(
            f"MarkdownTextSplitter merge size (default {DEFAULT_CHUNK_SIZE} = one section per "
            f"heading; starter ingest uses {_STARTER_CHUNK_SIZE})."
        ),
    )
    parser.add_argument(
        "--chunk-max-size",
        type=int,
        default=DEFAULT_CHUNK_MAX_SIZE,
        help=f"Max chars before sub-splitting a section (default {DEFAULT_CHUNK_MAX_SIZE}).",
    )
    parser.add_argument(
        "--max-chunks",
        type=int,
        default=None,
        help="Only print the first N chunks.",
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=2000,
        help="Truncate printed content (0 = no limit).",
    )
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
