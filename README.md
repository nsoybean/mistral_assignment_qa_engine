# my-search-project

A [Mistral Search Toolkit](https://pypi.org/project/mistralai-search-toolkit/) project with a Vespa backend.

SDK namespace: `mistralai.search.toolkit` — see the [toolkit docs](https://github.com/mistralai/mistral-pro/tree/main/dashboards/main/search/toolkit) for architecture and extension guides.

## Setup

```bash
make installdeps
```

`MISTRAL_API_KEY` is set in `.env` from the value you entered during `copier copy` (or edit `.env` later). Project name matches the folder you passed to `copier copy` (this directory).

## Commands

### Start Vespa and apply schema migrations

```bash
make setup-vespa
```

Expected output (success): Vespa container `Healthy`, migration `"activated": true`, then `Application is up!` / `Application ready`. Warnings about `no_query_match` and `summary_fields` are normal. First startup may take up to a minute.

You are not asked for ports during `copier copy`; defaults are set for you. If ports **18080** / **19072** are already in use, update `VESPA_QUERY_PORT` / `VESPA_CONFIG_PORT` in `.env` and rerun `make setup-vespa`.

To wipe local Vespa data and redeploy from scratch:

```bash
make reset-vespa
make setup-vespa
```

### Ingest documents

Pre-processed Mistral docs HTML lives under `sample_data/mistral_docs/` (isolated article HTML + `.meta.json` sidecars). Ingest uses `DocsHTMLFileLoader` → `HTMLExtractor` → heading-aware split → citation metadata → embed → Vespa.

```bash
make setup-vespa
make ingest path=sample_data/mistral_docs
```

Other local files still use plain-text or OCR pipelines:

```bash
make ingest path=sample_data/hello.txt
make ingest path=sample_data
```

To refresh docs HTML from the live site (requires network):

```bash
make preprocess-docs
make preprocess-docs url="https://docs.mistral.ai/studio/conversations/reasoning"
```

Inspect markdown and chunks before indexing (no Vespa, no API key):

```bash
make inspect-docs content=1
make inspect-docs path=sample_data/mistral_docs
make inspect-docs chunk_size=1   # per-section debug view
```

### Search the collection

Uses `QueryEngine` with `VectorRetriever` (hybrid BM25 + vector via Vespa):

```bash
make search query="hello world"
```

Ranking weights live in a Vespa **query profile**, not in the request. The search defaults to the `hybrid-search` profile (tuned BM25 + vector weights, defined in `src/search_app/migrations/001_vespa_create_index_schema.py`).

### Run the tests

```bash
make test
```

One round-trip: a document is indexed and searched back through the same `get_index` the
entrypoints use. It skips unless the backend is set up, so it is safe to run before
`make setup-vespa`.

### MCP server

The sample app includes an MCP server which exposes search, agentic navigation, and ingest as MCP tools so agents (Vibe, Claude Code, etc.) can query and populate the local index directly.

The server fails fast at startup with a clear error if `MISTRAL_API_KEY` is missing or if the search index does not support agentic navigation. Vespa must be running before you start the server (`make start-vespa`).

**Available tools:**

| Tool | Description |
|------|-------------|
| `search(query, top_k=5)` | Hybrid BM25 + vector search; returns ranked chunks with **id**, score, content, source_id, locator, **start_offset**, **end_offset**, and metadata |
| `ingest(uri)` | Ingest a local path/directory, `file://` URI, or `http(s)://` URL; text files use plain-text extraction, everything else uses Mistral OCR |
| `open(chunk_id, window=2)` | Expand context around a chunk from search — pass the chunk `id`, the server resolves its position and returns the anchor chunk plus `window` neighbours on each side, in reading order; `window` controls the radius |
| `navigate(source_id, start_offset, end_offset, direction, top_k=1)` | Step through a document from a known position; `direction` is `"next"` or `"previous"` |
| `read(source_id, start_offset=None, end_offset=None, top_k=20)` | Fetch a known offset range directly, no context expansion; omit either bound to read from the start or to the end |
| `grep(source_id, pattern, mode="phrase", top_k=5)` | Lexical search within a single source; `mode` is `"phrase"` (ordered) or `"term"` (any order) |

### Vibe CLI

Run `vibe` from this project directory. It automatically reads `.vibe/config.toml` and connects to the server via stdio — no manual setup needed. You can immediately ask Vibe to search or ingest documents.

> On first run, Vibe will ask you to trust this directory before loading the project config. Accept the prompt, or pass `--trust` to skip it for that session.

### Claude Code

Open this project directory in Claude Code. It automatically reads `.mcp.json` and connects to the server via stdio — no manual setup needed. You can immediately ask Claude to search or ingest documents.

### MCP Inspector

```bash
make mcp
npx @modelcontextprotocol/inspector http://127.0.0.1:8000/mcp
```

### Bruno API files (optional)

```bash
make bruno
```

Opens collection under `vespa/bruno/vespa/` (uses `WORKSPACE_ROOT=.` from `.env`).

### Vespa lock snapshot (optional)

```bash
make generate-vespa-lock
```

## Project layout

```
src/
├── entrypoints/
│   ├── ingest.py      # mistralai.search.toolkit.ingestion.pipelines.Pipeline
│   ├── preprocess_docs.py  # fetch docs.mistral.ai → sample_data/mistral_docs/
│   ├── inspect_docs.py     # preview markdown/chunks before ingest (no Vespa)
│   ├── mcp_server.py  # MCP server (search + navigation + ingest tools)
│   └── search.py      # mistralai.search.toolkit.retrieval.QueryEngine
└── search_app/
    ├── __init__.py    # VespaApp — mistralai.search.toolkit.plugins.vespa
    └── migrations/    # mistral-vespa migrate
tests/                # make test — one index-and-search round-trip
.mcp.json             # MCP server config (auto-loaded by Claude Code)
.vibe/config.toml     # MCP server config (auto-loaded by Vibe CLI)
sample_data/          # Sample documents
vespa/bruno/vespa/    # Generated by `make bruno` (optional)
```

## Development

```bash
uv run ruff format .
uv run ruff check --fix .
```
