---
name: mistralai-search-toolkit
description: >-
  Build ingestion and retrieval pipelines with mistralai-search-toolkit (PyPI).
  Covers Document/DocumentChunk models, Pipeline, QueryEngine, embedders,
  retrievers, rerankers, context propagation, and Vespa/Postgres/S3/GCS/Azure plugins.
  Use when working with mistralai-search-toolkit, mistralai.search.toolkit,
  search toolkit SDK, IR pipelines, document ingestion, vector/keyword search,
  search-starter-app, local Vespa or Postgres via docker compose, or migrating from ragnarok.
---

# Mistral Search Toolkit SDK

Modular, backend-agnostic Python framework for IR: ingest → chunk → embed → index → retrieve → rerank.

**Namespace:** `mistralai.search.toolkit`
**PyPI:** `mistralai-search-toolkit` (Python ≥3.12)

This project was generated with the **Vespa** backend.

## Install

Use **uv** for dependencies and running scripts — not `pip`.

```bash
# In a project (preferred)
uv add mistralai-search-toolkit
uv add "mistralai-search-toolkit[vespa]"
uv add "mistralai-search-toolkit[all]"   # all optional integrations

# One-off script without adding to pyproject
uv run --with mistralai-search-toolkit python your_script.py
```

Monorepo / workspace: `cd search && uv sync --all-packages`, then `uv run python ...` or `uv run invoke tests`.

| Need | Extra / plugin package |
|------|------------------------|
| Vespa backend | `[vespa]` → `mistralai-search-toolkit-plugins-vespa` |
| Postgres backend | `[postgres]` → `mistralai-search-toolkit-plugins-postgres` |
| S3 loader/storage | `mistralai-search-toolkit-storage-s3` |
| GCS | `[storage-gcs]` |
| Azure | `[storage-azure]` |
| PyMuPDF extractor | `[extractor-pymupdf]` |
| LangChain splitters | `[text-splitter-langchain]` |
| HTML → Markdown | `[html-converter-markdownify]` |

Import plugins from `mistralai.search.toolkit.plugins.<name>` (PEP 420 namespace — install the plugin wheel).

This Copier template ships the skill at **`.agents/skills/search/`** (`SKILL.md` + `reference.md`). No copy step needed in generated projects.

## Search starter app (this project)

**Layout:**

```text
src/entrypoints/ingest.py   # Pipeline ingest (plain-text vs OCR by suffix)
src/entrypoints/search.py   # QueryEngine + VectorRetriever
src/search_app/             # VespaApp + schema migrations
sample_data/                # Example documents
```

**Local Vespa (Docker):**

```bash
uv sync
# Set MISTRAL_API_KEY in .env (from copier copy or edit manually)
make setup-vespa          # start container + migrate schema
make ingest path=sample_data/hello.txt
make search query="hello world"
make bruno                # optional: API files under vespa/bruno/vespa/
make reset-vespa            # wipe data volume; then setup-vespa again
```

**Environment (`.env`):** `VESPA_QUERY_PORT` and `VESPA_CONFIG_PORT` are the source of truth (defaults `18080` / `19072`). URLs are `http://localhost:{port}` unless `VESPA_ENDPOINT` / `VESPA_CONFIG_URL` override. Also: `MISTRAL_API_KEY`, `COLLECTION_NAME`, `WORKSPACE_ROOT=.` (Bruno output path). See [reference.md](reference.md) for the full table and Make targets.

**Store index in entrypoints:** all three call sites (`ingest.py`, `search.py`, workflow activity) build the index through one neutral seam:

```python
import os
from dotenv import load_dotenv
from search_app import get_index

load_dotenv(override=True)
collection_name = os.environ.get("COLLECTION_NAME", "exampledocs")
index = get_index(collection_name)   # builds VespaClientConfig internally
```

`search.py` also accepts `get_index(collection_name, query_profile=...)` to rank with a named Vespa query profile (default `hybrid-search`).

`ingest.py` uses two shared `Pipeline` instances: `PlainTextExtractor` for `.txt`/`.md`/… and `MistralOCRExtractor` for other files (e.g. PDFs). Prefer extending those entrypoints or `src/search_app/migrations/` rather than ad-hoc scripts.

**Port alignment:** `docker-compose.yaml` host ports, `.env` ports, and Makefile defaults must match — mismatches cause network errors on ingest/search.

## Architecture

```text
Ingestion:  FileLoader → DocumentExtractor → TextSplitter → [ChunkEnricher] → Embedder → StoreIndex
Retrieval:  QueryEngine → [LLMQueryRewriter] → [LLMQueryExtension] → Retriever(s) → [ReRanker(s)]
```

- **Core** defines abstractions (`VectorStoreIndex`, `KeywordStoreIndex`, `Pipeline`, `QueryEngine`).
- **Backends** (Vespa, Postgres, cloud loaders) live in separate plugin packages.
- **Mistral APIs** use the public SDK: `from mistralai.client import Mistral`.

## Non-negotiable conventions

1. **Async I/O** — `await pipeline.run(...)`, `await query_engine.search(...)`, `await index.index_document(...)`.
2. **Frozen models** — never assign fields on `Document`, `DocumentChunk`, `SearchResult`, metadata. Use `model_copy(update={...})`.
3. **Required `Document.id`** — always set explicitly (no auto-generation).
4. **Metadata** — `DocumentMetadata` / `DocumentChunkMetadata` are frozen `extra="allow"` models. Read with `meta["k"]` / `meta.get("k")`. Update via `meta.model_copy(update={...})` then `doc.model_copy(update={"metadata": ...})`.
5. **Context, not kwargs** — pass `IngestContext` / `RetrievalContext` subclasses for tenant headers, tracing, backend options. No `filter=` or `**kwargs` on retrievers/query engine.
6. **Logging** — use `structlog` with keyword args; no `print` in library/application code meant for production.
7. **Imports at module level** — not inside functions unless breaking a circular import.
8. **Tooling** — use `uv add` / `uv sync` / `uv run`; do not suggest `pip install`.

## Core types

| Concept | Type | Import |
|---------|------|--------|
| Extracted doc | `Document` | `mistralai.search.toolkit.document` |
| Chunk | `DocumentChunk` | same |
| Search hit | `SearchResult` | `mistralai.search.toolkit.indices` |
| Vector query | `VectorSearchQuery` | same |
| Ingest scope | `IngestContext` | `mistralai.search.toolkit` |
| Retrieval scope | `RetrievalContext` | same |
| Base exception | `SearchToolkitException` | `mistralai.search.toolkit.errors` |

`DocumentChunk.processing_metadata` was renamed to **`metadata`**.

## Ingestion workflow

```python
import asyncio
from pathlib import Path

from mistralai.client import Mistral
from mistralai.search.toolkit.embedders import MistralEmbedder
from mistralai.search.toolkit.ingestion.extractors import MistralOCRExtractor
from mistralai.search.toolkit.ingestion.loaders import FilesystemFileLoader
from mistralai.search.toolkit.ingestion.pipelines import Pipeline
from mistralai.search.toolkit.ingestion.text_splitters import (
    MarkdownTextSplitter,
    MarkdownTextSplitterConfig,
)

# vector_store: VectorStoreIndex from a plugin (e.g. get_index(collection_name))

async def ingest(pdf_path: Path, vector_store, api_key: str) -> int:
    client = Mistral(api_key=api_key)
    pipeline = Pipeline(
        loader=FilesystemFileLoader(),
        extractor=MistralOCRExtractor(client=client),
        text_splitter=MarkdownTextSplitter(
            MarkdownTextSplitterConfig(chunk_size=4096, chunk_overlap=50)
        ),
        embedder=MistralEmbedder(client=client),
        stores=vector_store,
    )
    return await pipeline.run(documents=[pdf_path], use_checkpoint=False)
```

**Pipeline rules:**
- `stores` must include at least one index; if any `VectorStoreIndex`, `embedder` is required.
- `loader=None` when calling `run_file(...)` with an in-memory `File` (bytes/URL).
- `checkpoint_dir` enables JSONL OCR checkpoints for long runs.

**Pick extractors by format:** `MistralOCRExtractor`, `HTMLExtractor`, `PlainTextExtractor`, `SpreadsheetExtractor`, `EmailExtractor`, `PyMuPDFExtractor` (optional dep), etc. — see `ingestion.extractors`.

## Retrieval workflow

```python
from mistralai.client import Mistral
from mistralai.search.toolkit.embedders import MistralEmbedder
from mistralai.search.toolkit.retrieval import QueryEngine, VectorRetriever

async def search(query: str, vector_store, api_key: str, top_k: int = 10):
    client = Mistral(api_key=api_key)
    engine = QueryEngine(
        retriever=[VectorRetriever(client=vector_store, embedder=MistralEmbedder(client=client))],
    )
    return await engine.search(
        query=query,
        top_k=top_k,
        include_metadata=True,
        include_content=True,
    )
```

**Hybrid / keyword:** add `KeywordRetriever` alongside `VectorRetriever`; fuse with `RRFRanker` or `GroupedRanker`.

**LLM features:** configure at init, not per `search()` call:
- `LLMQueryRewriter` + `MistralChat` (`mistralai.search.toolkit.llm`)
- `LLMQueryExtension` for multi-query retrieval
- `LLMReRanker` in `rerankers=[...]`

**Caching:** wrap with `CachedQueryEngine` (`retrieval.cache`).

## Vespa plugin (typical production path)

```python
from pathlib import Path
from mistralai.search.toolkit.plugins.vespa import VespaApp, VespaClientConfig

app = VespaApp(Path("src/search_app"))
index = app.get_search_index(
    VespaClientConfig(endpoint="http://localhost:18080"),
    collection_name="exampledocs",
)
```

Index creation is backend-specific (not on `StoreIndex` base). In this template, schema lives under `src/search_app/migrations/` and is applied with:

```bash
make migrate-vespa   # mistral-vespa migrate --config-server ... --query-port ...
```

CLI also supports `mistral-vespa bruno` and `mistral-vespa generate` (see `make bruno`, `make generate-vespa-lock`).

## Custom context example

```python
from mistralai.search.toolkit import IngestContext, MistralClientOptions, RetrievalContext

class TenantContext(RetrievalContext):
    tenant_id: str
    mistral_client_options: MistralClientOptions = MistralClientOptions(
        http_headers={"X-Tenant-Id": "acme"}
    )

await engine.search("query", top_k=5, context=TenantContext(tenant_id="acme"))
```

## Store index API (implement or call)

```python
await index.index_document(document, context=IngestContext())
results = await index.search(VectorSearchQuery(embedding=[...], query="optional text"))
```

Subclasses: `VectorStoreIndex`, `KeywordStoreIndex`. Exceptions: `IndexingError`, `SearchError`, `DocumentNotFoundError` in `indices.errors`.

## Task router

| Goal | Start here |
|------|------------|
| Run this template end-to-end | `make setup-vespa` → `make ingest` → `make search` |
| Change Vespa ports | `.env` `VESPA_QUERY_PORT` / `VESPA_CONFIG_PORT` + `docker-compose.yaml` |
| Vespa schema / hybrid profile | `src/search_app/migrations/`, then `make migrate-vespa` |
| Bruno API collection | `make bruno` (`WORKSPACE_ROOT=.` in `.env`) |
| BM25 / hybrid | `KeywordRetriever` + optional `VectorRetriever` + `RRFRanker` |
| Index local files | `make ingest` or `Pipeline` + `FilesystemFileLoader` in `entrypoints/ingest.py` |
| Index from S3/GCS/Azure | storage plugin loader + same `Pipeline` |
| Semantic search | `make search` or `QueryEngine` + `VectorRetriever` in `entrypoints/search.py` |
| Better ranking | `LLMReRanker` or `GroupedRanker` |
| Query understanding | `LLMQueryRewriter` / `LLMQueryExtension` |
| Multi-tenant headers | Subclass `RetrievalContext` + `MistralClientOptions` |
| Object storage for assets | `storage.ObjectStorage` + plugin factory |
| Env vars / Make targets | [reference.md](reference.md) |
| Migrate from ragnarok | Read `toolkit/MIGRATION_GUIDE.md` in repo |

## Extending the toolkit

- **New search backend:** implement `VectorStoreIndex` / `KeywordStoreIndex`; ship as `mistralai-search-toolkit-plugins-<name>` with `pkgutil.extend_path` on `mistralai.search.toolkit`.
- **New extractor/loader:** subclass `DocumentExtractor` / `FileLoader` in core or a plugin.

## Additional resources

- Full import map, optional deps, exceptions: [reference.md](reference.md)
- Monorepo cookbooks: `search/cookbooks/` (`01-quickstart`, `02-advanced-indexing`, `03-advanced-search`)
- Ragnarok migration: `toolkit/MIGRATION_GUIDE.md`

When unsure about an API signature, prefer reading the installed package or repo source over guessing — the SDK is strict-typed and frozen-model rules are enforced at runtime.
