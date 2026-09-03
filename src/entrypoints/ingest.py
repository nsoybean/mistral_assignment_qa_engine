"""Ingest local documents into the configured search backend.

Usage:
    python -m entrypoints.ingest <file_path>
    python -m entrypoints.ingest sample_data/mistral_docs
"""

import argparse
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from mistralai.client import Mistral
from mistralai.search.toolkit.embedders import MistralEmbedder
from mistralai.search.toolkit.ingestion.extractors import (
    MistralOCRExtractor,
    PlainTextExtractor,
)
from mistralai.search.toolkit.ingestion.loaders import FilesystemFileLoader
from mistralai.search.toolkit.ingestion.pipelines import Pipeline
from mistralai.search.toolkit.ingestion.text_splitters import (
    MarkdownTextSplitter,
    MarkdownTextSplitterConfig,
)
from search_app import get_index
from search_app.docs_html import build_docs_html_pipeline, is_preprocessed_docs_html

load_dotenv(override=True)

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json"}


def _is_docs_meta_sidecar(path: Path) -> bool:
    """True for ``*.meta.json`` sidecars (not plain ``.json`` files).

    ``Path.suffix`` on ``page.meta.json`` is ``.json``, so a suffix check alone
    would still ingest metadata sidecars into Vespa.
    """
    return path.name.endswith(".meta.json")


def _collect_documents(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.is_file())
        if not files:
            raise SystemExit(f"Error: no files found under {path}")
        return files
    raise SystemExit(f"Error: path not found: {path}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest a document into the configured search backend."
    )
    parser.add_argument("file_path", help="Path to the file or directory to ingest")
    args = parser.parse_args()

    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        raise SystemExit("Error: MISTRAL_API_KEY is not set. Check your .env file.")

    root = Path(args.file_path)
    if not root.exists():
        raise SystemExit(f"Error: path not found: {root}")

    collection_name = os.environ.get("COLLECTION_NAME", "exampledocs")
    mistral_client = Mistral(
        api_key=api_key,
        server_url=os.getenv("MISTRAL_API_URL", "https://api.mistral.ai"),
    )

    loader = FilesystemFileLoader()
    text_splitter = MarkdownTextSplitter(
        MarkdownTextSplitterConfig(chunk_size=4096, chunk_overlap=50)
    )
    embedder = MistralEmbedder(client=mistral_client)
    vector_store = get_index(collection_name)

    plain_text_pipeline = Pipeline(
        loader=loader,
        extractor=PlainTextExtractor(),
        text_splitter=text_splitter,
        embedder=embedder,
        stores=vector_store,
    )

    ocr_pipeline = Pipeline(
        loader=loader,
        extractor=MistralOCRExtractor(client=mistral_client),
        text_splitter=text_splitter,
        embedder=embedder,
        stores=vector_store,
    )

    documents = [
        p for p in _collect_documents(root) if not _is_docs_meta_sidecar(p)
    ]

    total_chunks = 0
    for doc_path in documents:
        if is_preprocessed_docs_html(doc_path):
            pipeline = build_docs_html_pipeline(
                doc_path, embedder=embedder, stores=vector_store
            )
            kind = "docs-html"
        elif doc_path.suffix.lower() in _TEXT_SUFFIXES:
            pipeline = plain_text_pipeline
            kind = doc_path.suffix
        else:
            pipeline = ocr_pipeline
            kind = doc_path.suffix
        print(f"Processing ({kind}): {doc_path}")
        total_chunks += await pipeline.run(documents=[doc_path], use_checkpoint=False)

    print(
        f"Successfully indexed {total_chunks} chunks from "
        f"{len(documents)} file(s) into '{collection_name}'."
    )


if __name__ == "__main__":
    asyncio.run(main())
