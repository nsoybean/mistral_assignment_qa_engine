"""Inspect markdown and chunks for preprocessed docs HTML (no embed, no Vespa).

Runs the same path as ingest up to chunk enrichment: local ``.html`` +
``.meta.json`` → ``HTMLExtractor`` → ``MarkdownTextSplitter`` → citation metadata.

Usage:
    python -m entrypoints.inspect_docs sample_data/mistral_docs/studio/conversations/chat-completion.html
    python -m entrypoints.inspect_docs sample_data/mistral_docs --content
    python -m entrypoints.inspect_docs sample_data/mistral_docs --chunk-size 1
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from search_app.docs_html import (
    DEFAULT_CHUNK_MAX_SIZE,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_SAMPLE_DIR,
    DocsPage,
    citation_url,
    is_preprocessed_docs_html,
    parse_saved_docs_page,
    slugify,
)

_DEFAULT_PATH = DEFAULT_SAMPLE_DIR / "studio/conversations/chat-completion.html"
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_INSPECT_CHUNK_SIZE = 1


def _collect_html_paths(path: Path) -> list[Path]:
    if path.is_file():
        if not is_preprocessed_docs_html(path):
            raise SystemExit(
                f"Error: {path} is not a preprocessed docs page "
                f"(expected sibling {path.with_suffix('.meta.json')})"
            )
        return [path]
    if path.is_dir():
        files = sorted(
            p for p in path.rglob("*.html") if is_preprocessed_docs_html(p)
        )
        if not files:
            raise SystemExit(f"Error: no preprocessed .html files found under {path}")
        return files
    raise SystemExit(f"Error: path not found: {path}")


def _print_meta(label: str, value: object) -> None:
    print(f"{label:<18}{value}")


def _print_headings(markdown: str, page_url: str, *, page_title: str) -> None:
    print("\n=== markdown headings → anchors ===")
    for match in _HEADING_RE.finditer(markdown):
        level = len(match.group(1))
        heading = match.group(2).strip()
        is_title = heading.casefold() == page_title.casefold()
        anchor = "" if is_title else f"#{slugify(heading)}"
        cite = citation_url(page_url, heading, is_page_title=is_title)
        print(f"  h{level}  {heading!r}  anchor={anchor!r}  citation={cite}")


def _print_page(
    page: DocsPage,
    *,
    show_content: bool,
    max_chunks: int | None,
) -> None:
    _print_meta("URL:", page.url)
    _print_meta("title:", page.title)
    _print_meta("breadcrumb:", page.breadcrumb_text or "(none)")
    _print_meta("html chars:", len(page.html))
    _print_meta("markdown chars:", len(page.markdown))
    if show_content:
        print("\n=== markdown ===\n")
        print(page.markdown)
    _print_headings(page.markdown, page.url, page_title=page.title)
    _print_meta("chunks:", len(page.chunks))

    shown = list(page.chunks) if max_chunks is None else list(page.chunks)[:max_chunks]
    print(f"\n=== chunks ({len(page.chunks)} total, showing {len(shown)}) ===")
    for i, chunk in enumerate(shown, 1):
        meta = chunk.metadata
        print(f"\n--- chunk {i}/{len(page.chunks)} ---")
        print(f"heading:       {meta.get('heading')}")
        print(f"citation_url:  {meta.get('citation_url')}")
        print(
            f"offsets:       {chunk.start_offset}-{chunk.end_offset} "
            f"({len(chunk.content)} chars)"
        )
        print(f"metadata:      {dict(meta)}")
        print("content:")
        print(chunk.content)


async def _run(args: argparse.Namespace) -> None:
    paths = _collect_html_paths(args.path)
    for html_path in paths:
        if len(paths) > 1:
            print(f"\n{'=' * 72}\n{html_path}\n{'=' * 72}")
        page = await parse_saved_docs_page(
            html_path,
            chunk_size=args.chunk_size,
            chunk_max_size=args.chunk_max_size,
            chunk_overlap=args.chunk_overlap,
        )
        _print_page(
            page,
            show_content=args.content,
            max_chunks=args.max_chunks,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect markdown and chunks for preprocessed docs HTML (no Vespa)."
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=_DEFAULT_PATH,
        help=f"Preprocessed .html file or directory (default: {_DEFAULT_PATH})",
    )
    parser.add_argument(
        "--content",
        action="store_true",
        help="Print converted markdown.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=_INSPECT_CHUNK_SIZE,
        help=(
            f"Max merged chars per header group (default {_INSPECT_CHUNK_SIZE} for "
            f"per-section view; ingest uses {DEFAULT_CHUNK_SIZE})."
        ),
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=0,
        help=(
            f"Overlap between separator sub-chunks (default 0 here; "
            f"ingest uses {DEFAULT_CHUNK_OVERLAP})."
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
        help="Only print the first N chunks per page.",
    )
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
