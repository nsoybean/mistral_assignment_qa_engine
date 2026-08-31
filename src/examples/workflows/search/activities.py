"""Activities for the document ingestion workflow.

All I/O lives here: file system access, embedding calls, and writes to the
configured search backend. The workflow body in workflow.py calls these as
durable, retryable units.

Stateless pipeline components and API clients are provided via Depends() so
the SDK initialises them once at worker startup and reuses them across every
activity execution. The only per-call objects are the store index and the
Pipeline wrappers, because they are keyed to the collection_name input.
"""

import os
from datetime import timedelta
from functools import cache
from pathlib import Path
from typing import Any

import mistralai.workflows as workflows
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
from mistralai.workflows import Depends
from search_app import get_index

from .models import IngestionResult

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json"}


# ---------------------------------------------------------------------------
# Dependency providers — each called once at worker startup
# ---------------------------------------------------------------------------


@cache
def get_mistral_client() -> Mistral:
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        raise ValueError("MISTRAL_API_KEY is not set. Check your .env file.")
    return Mistral(
        api_key=api_key,
        server_url=os.getenv("MISTRAL_API_URL", "https://api.mistral.ai"),
    )


def get_embedder() -> MistralEmbedder:
    return MistralEmbedder(client=get_mistral_client())


def get_ocr_extractor() -> MistralOCRExtractor:
    return MistralOCRExtractor(client=get_mistral_client())


def get_loader() -> FilesystemFileLoader:
    return FilesystemFileLoader()


def get_text_splitter() -> MarkdownTextSplitter:
    return MarkdownTextSplitter(
        MarkdownTextSplitterConfig(chunk_size=4096, chunk_overlap=50)
    )


# ---------------------------------------------------------------------------
# Per-call pipeline factory
# ---------------------------------------------------------------------------


def _build_pipelines(
    loader: FilesystemFileLoader,
    text_splitter: MarkdownTextSplitter,
    embedder: MistralEmbedder,
    ocr_extractor: MistralOCRExtractor,
    vector_store: Any,
) -> tuple[Pipeline, Pipeline]:
    """Assemble plain-text and OCR pipelines from shared components.

    The store index (vector_store) is the only per-call argument because
    it is keyed to the collection_name supplied at runtime.
    """
    shared = dict(
        loader=loader,
        text_splitter=text_splitter,
        embedder=embedder,
        stores=vector_store,
    )
    return (
        Pipeline(extractor=PlainTextExtractor(), **shared),
        Pipeline(extractor=ocr_extractor, **shared),
    )


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------


@workflows.activity(
    name="collect_document_paths",
    start_to_close_timeout=timedelta(seconds=30),
    retry_policy_max_attempts=1,
)
async def collect_document_paths(file_path: str) -> list[str]:
    """Collect all file paths to ingest from a file or directory.

    Returns string paths (Path objects are not serialisable across the
    activity boundary) already split into two lists: text files and OCR files.
    """
    root = Path(file_path)
    if root.is_file():
        documents = [root]
    elif root.is_dir():
        documents = sorted(p for p in root.rglob("*") if p.is_file())
    else:
        raise ValueError(f"Path not found: {file_path}")

    if not documents:
        raise ValueError(f"No files found at: {file_path}")

    return [str(p) for p in documents]


@workflows.activity(
    name="ingest_documents",
    # Ingestion can be slow for large files or directories with many PDFs.
    # 30 minutes is a generous upper bound; raise if processing very large corpora.
    start_to_close_timeout=timedelta(minutes=30),
    retry_policy_max_attempts=2,
)
async def ingest_documents(
    paths: list[str],
    collection_name: str,
    embedder: MistralEmbedder = Depends(get_embedder),
    ocr_extractor: MistralOCRExtractor = Depends(get_ocr_extractor),
    loader: FilesystemFileLoader = Depends(get_loader),
    text_splitter: MarkdownTextSplitter = Depends(get_text_splitter),
) -> IngestionResult:
    """Run the ingestion pipelines on a pre-collected list of file paths."""
    vector_store = get_index(collection_name)
    plain_text_pipeline, ocr_pipeline = _build_pipelines(
        loader, text_splitter, embedder, ocr_extractor, vector_store
    )

    documents = [Path(p) for p in paths]
    text_docs = [p for p in documents if p.suffix.lower() in _TEXT_SUFFIXES]
    ocr_docs = [p for p in documents if p.suffix.lower() not in _TEXT_SUFFIXES]

    total_chunks = 0
    if text_docs:
        total_chunks += await plain_text_pipeline.run(documents=text_docs, use_checkpoint=False)
    if ocr_docs:
        total_chunks += await ocr_pipeline.run(documents=ocr_docs, use_checkpoint=False)

    return IngestionResult(
        status="success",
        total_chunks=total_chunks,
        file_count=len(documents),
        collection_name=collection_name,
    )
