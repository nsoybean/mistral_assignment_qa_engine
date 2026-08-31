# Search Toolkit — API reference (agent)

## Top-level exports (`from mistralai.search.toolkit import ...`)

`BaseSearchQuery`, `Document`, `DocumentChunk`, `Image`, `Page`, `IngestContext`, `RetrievalContext`, `MistralClientContext`, `MistralClientOptions`, `MistralOCRContext`, `MistralOCRExtractorRuntimeOptions`, `SearchResult`, `SearchToolkitException`, `VectorSearchQuery`, `merge_mistral_http_headers`

## Module map

| Module | Key symbols |
|--------|-------------|
| `document` | `Document`, `DocumentChunk`, `DocumentMetadata`, `DocumentChunkMetadata`, `Page`, `Image`, `ExtractorType` |
| `indices` | `VectorStoreIndex`, `KeywordStoreIndex`, `StoreIndex`, `BaseSearchQuery`, `VectorSearchQuery`, `KeywordSearchQuery`, `SearchResult`, `SearchResultChunk`, `SearchResultGroup` |
| `indices.errors` | `DocumentNotFoundError`, `IndexingError`, `SearchError` |
| `embedders` | `Embedder`, `MistralEmbedder`, `EmbeddingResult`, `MODEL_128/256/1024_EMBEDDING` |
| `ingestion` | `File` |
| `ingestion.loaders` | `FileLoader`, `FilesystemFileLoader` (+ cloud loaders in storage plugins) |
| `ingestion.extractors` | `DocumentExtractor`, `MistralOCRExtractor`, `HTMLExtractor`, `PlainTextExtractor`, `SpreadsheetExtractor`, `EmailExtractor`, `PyMuPDFExtractor`, ... |
| `ingestion.text_splitters` | `TextSplitter`, `MarkdownTextSplitter`, `TokenTextSplitter`, `CharacterTextSplitter`, `SeparatorTextSplitter` |
| `ingestion.pipelines` | `Pipeline`, `RoutedPipeline` |
| `ingestion.enrichment` | `ChunkEnricher` implementations |
| `retrieval` | `QueryEngine`, `QueryEngineResult`, `VectorRetriever`, `KeywordRetriever`, `Retriever`, `LLMQueryRewriter`, `LLMQueryExtension`, `LLMReRanker`, `ReRanker`, `RRFRanker`, `GroupedRanker`, `CachedQueryEngine` |
| `llm` | `MistralChat`, `LLMConfig`, ... |
| `storage` | `ObjectStorage`, `ObjectStorageFactory`, `FilesystemStorage` |
| `evals` | evaluation helpers (see module) |
| `plugins.vespa` | `VespaSearchIndex`, `VespaClientConfig`, `VespaApp`, `VespaAppDefinition`, evaluators, migration |

## PyPI optional extras (`mistralai-search-toolkit`)

| Extra | Pulls in |
|-------|----------|
| `vespa` | vespa plugin |
| `postgres` | postgres (pgvector) plugin |
| `storage-gcs` | GCS plugin |
| `storage-azure` | azure plugin |
| `extractor-pymupdf` | pymupdfpro |
| `extractor-email` | eml/msg + markdownify |
| `extractor-spreadsheet` | pandas, calamine, numbers-parser |
| `html-converter-markdownify` | markdownify |
| `text-splitter-langchain` | langchain splitters |
| `all` | bundles common extras |

Separate wheels (install explicitly): `mistralai-search-toolkit-storage-s3`, `mistralai-search-toolkit-plugins-vespa`, `mistralai-search-toolkit-plugins-postgres`, etc.

## Exceptions (domain → import)

| Exception | Module |
|-----------|--------|
| `SearchToolkitException` | `errors` |
| `DocumentExtractorException`, `HtmlConversionError`, `FileLoaderException`, `FileSizeLimitExceededException`, `TextSplitterException` | `ingestion.errors` |
| `RetrieverException`, `RerankerException`, `CacheException` | `retrieval.errors` |
| `EmbedderException`, `TooManyTokensException` | `embedders.errors` |
| `LLMException` | `llm.errors` |
| `VespaIndexException` | `plugins.vespa` |

## Ragnarok → toolkit renames

| Old | New |
|-----|-----|
| `Chunk` | `DocumentChunk` |
| `SearchQuery` | `BaseSearchQuery` |
| `VectorStoreClient` | `VectorStoreIndex` |
| `KeywordStoreClient` | `KeywordStoreIndex` |
| `upsert()` | `index_document()` |
| `ragnarok.pipelines` | `ingestion.pipelines` |
| `filter` / `group_id` on queries | `RetrievalContext` / backend-specific queries |

## Immutable update patterns

```python
# Chunk embedding after embed step (pipeline does this internally)
chunk = chunk.model_copy(update={"embedding": vec, "embedder_name": name})

# Metadata
new_meta = doc.metadata.model_copy(update={"summary": "..."})
doc = doc.model_copy(update={"metadata": new_meta})

# Replace chunk list
doc = doc.model_copy(update={"chunks": new_chunks})
```

## QueryEngine composition

```python
QueryEngine(
    retriever=[vector_retriever, keyword_retriever],  # or single
    query_rewriter=rewriter,      # optional, configured at init
    query_extension=extender,     # optional
    rerankers=[rrf, llm_reranker],  # applied in order
)
```

`search()` returns `QueryEngineResult` with `original_query`, `search_query`, `results`, optional `rewrite_result` / `extension_result`.

## Pipeline entry points

| Method | When |
|--------|------|
| `run(documents=[paths], ...)` | Local paths; uses `loader` |
| `run_file(file, ...)` | Already have `File` with content/URL; `loader` can be `None` |
| `run_dir(directory, ...)` | Batch directory ingest |

Returns chunk count (int). Pass `context=IngestContext()` for custom ingest scope.

## Environment variables

### Mistral (required for embed/OCR/LLM in this template)

| Variable | Default | Role |
|----------|---------|------|
| `MISTRAL_API_KEY` | from `copier copy` | Set during `copier copy` (secret prompt) or edit `.env` |
| `MISTRAL_API_URL` | `https://api.mistral.ai` | Optional custom API base URL |

### Vespa (search-starter-app `.env`)

Host ports are the source of truth. URLs are derived as `http://localhost:{port}` unless overridden.

| Variable | Default (Copier) | Role |
|----------|------------------|------|
| `VESPA_QUERY_PORT` | `18080` | Query/document API host port (`docker-compose` maps to container `:8080`) |
| `VESPA_CONFIG_PORT` | `19072` | Config server host port (maps to container `:19071`; used by `make migrate-vespa`) |
| `COLLECTION_NAME` | `exampledocs` | Vespa schema collection; must match `src/search_app/migrations/` |
| `WORKSPACE_ROOT` | `.` | Root for Bruno output: `{WORKSPACE_ROOT}/vespa/bruno/vespa/` |

Optional overrides (rare; full URL wins over port):

| Variable | Role |
|----------|------|
| `VESPA_ENDPOINT` | Query/document URL for `VespaClientConfig` and `make bruno` (else `search_app.vespa_endpoint()` builds from `VESPA_QUERY_PORT`) |
| `VESPA_CONFIG_URL` | Config server URL for `make verify-vespa` / `migrate-vespa` (else `http://localhost:{VESPA_CONFIG_PORT}` in Makefile) |

Port defaults are hardcoded (`18080` / `19072`) but can be overridden via `VESPA_QUERY_PORT` / `VESPA_CONFIG_PORT` in `.env` or by editing `docker-compose.yaml`.

```python
from search_app import get_index

# Builds VespaClientConfig(endpoint=vespa_endpoint()) internally.
index = get_index(os.environ["COLLECTION_NAME"])
```

### Make targets (local Vespa workflow)

| Target | Purpose |
|--------|---------|
| `make setup-vespa` | `docker compose up` + schema migrate |
| `make reset-vespa` | Stop container and remove `vespa-data` volume |
| `make ingest path=...` | `python -m entrypoints.ingest` (file or directory) |
| `make search query="..."` | `python -m entrypoints.search` |
| `make bruno` | `mistral-vespa bruno` (needs `WORKSPACE_ROOT=.` in `.env`) |
| `make generate-vespa-lock` | Optional `vespa.lock` snapshot |

Port mismatch (compose host ports ≠ `.env` ports) causes ingest/search network errors — keep all three aligned.

## Dev workspace (monorepo only)

```bash
cd search && uv sync --all-packages
cd toolkit/core && make test   # or make lint / typecheck
```

Not required for PyPI consumers — in their own project use `uv add mistralai-search-toolkit` and `uv run pytest`.
