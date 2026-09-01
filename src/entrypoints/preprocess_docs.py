"""Fetch docs.mistral.ai pages and save isolated HTML under sample_data/.

The cleaned HTML is committed to the repo so reviewers can ingest offline with
``make ingest path=sample_data/mistral_docs`` (FileLoader + HTMLExtractor).

Usage:
    python -m entrypoints.preprocess_docs --url https://docs.mistral.ai/studio/...
    python -m entrypoints.preprocess_docs --urls-file sample_data/urls.txt
    python -m entrypoints.preprocess_docs --urls-file sample_data/urls.txt --output sample_data/mistral_docs
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from search_app.docs_html import (
    DEFAULT_SAMPLE_DIR,
    preprocess_docs_page,
    validate_docs_url,
)


def _load_urls_from_file(path: Path) -> list[str]:
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        validate_docs_url(line)
        urls.append(line)
    if not urls:
        raise SystemExit(f"Error: no URLs found in {path}")
    return urls


async def _run(urls: list[str], output_dir: Path) -> None:
    failed: list[str] = []
    for url in urls:
        print(f"Fetching: {url}")
        try:
            html_path, meta_path = await preprocess_docs_page(url, output_dir)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            print(f"  FAILED: {exc}")
            failed.append(url)
            continue
        print(f"  wrote {html_path}")
        print(f"  wrote {meta_path}")
    if failed:
        raise SystemExit(
            f"Failed to preprocess {len(failed)} URL(s): {', '.join(failed)}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and pre-process docs.mistral.ai pages to local HTML files."
    )
    parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        default=[],
        metavar="URL",
        help="Docs page URL (repeatable).",
    )
    parser.add_argument(
        "--urls-file",
        type=Path,
        help="Text file with one docs.mistral.ai URL per line (# comments allowed).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_SAMPLE_DIR,
        help=f"Output directory (default: {DEFAULT_SAMPLE_DIR}).",
    )
    args = parser.parse_args()

    urls = list(args.urls)
    if args.urls_file:
        urls.extend(_load_urls_from_file(args.urls_file))
    if not urls:
        parser.error("Provide at least one --url or --urls-file")

    try:
        asyncio.run(_run(urls, args.output))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
