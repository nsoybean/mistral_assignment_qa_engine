# Workflow Examples

This folder contains examples that integrate the search-starter-app with the [Mistral Workflows](https://docs.mistral.ai/capabilities/workflows/) framework.

Workflow-based examples live under `examples/workflows/`. Other examples that do not use the workflows framework (standalone scripts, direct API calls, etc.) live directly under `examples/`.

## Available example

| Example | Workflow name | Makefile target | Description |
| --- | --- | --- | --- |
| [search/](search/) | `document-ingestion` | `make execute-ingestion` | Ingestion workflow with small Temporal I/O |

The example ingests local files into Vespa using the same Search Toolkit components as `make ingest`, wrapped in a durable workflow.

## Prerequisites

The workflows framework is an **optional** dependency — the core search project works without it.

> Run these commands from your **generated project root** (the folder created by `copier copy`), not from the `search-starter-app` template repo itself.

```bash
make install-workflows   # uv sync --extra workflows
make setup-vespa
```

Ensure these are set in your `.env` file:

- `MISTRAL_API_KEY`
- `DEPLOYMENT_NAME` — a stable identifier for this worker (defaults to your project name when generated via `copier copy`)

## Workflow input

```json
{
  "file_path": "sample_data/hello.txt",
  "collection_name": "exampledocs"
}
```

| Field | Required | Default | Description |
| --- | --- | --- | --- |
| `file_path` | yes | — | Path to a file or directory to ingest |
| `collection_name` | no | `"exampledocs"` | Vespa collection name |

## How to run

> **Important:** execution commands call the Mistral Workflows API. The workflow must be registered first by a running worker. If you see `Workflow not found`, start the worker below and retry.

**Terminal 1 — start the examples worker (leave this running):**

```bash
make start-examples
```

Wait until the worker is listening for execution requests.

**Terminal 2 — trigger the workflow:**

```bash
make execute-ingestion
make execute-ingestion input='{"file_path": "sample_data/hello.txt"}'
make execute-ingestion input='{"file_path": "sample_data", "collection_name": "mydocs"}'
```

You can also trigger the workflow from the [Mistral Console](https://console.mistral.ai/build/workflows): select `document-ingestion`, click **Start Workflow**, and provide the input JSON.

After ingestion, search directly (no workflow):

```bash
make search query="hello world"
```

## Folder layout

```text
examples/workflows/
├── README.md              # This file
├── worker.py              # Registers and runs all workflow examples
├── start.py               # CLI to trigger a workflow execution
└── search/                # Document ingestion workflow
    ├── models.py
    ├── activities.py
    ├── workflow.py
    └── README.md
```

## Design principles

- **Workflows for ingestion** — document ingestion is long-running and benefits from durability, retries, and observability in the Mistral Console.
- **Search stays direct** — search queries use `make search` / `entrypoints/search.py` for low latency. Workflows are not needed for queries.
- **Small activity I/O** — the workflow passes file paths and collection names only. The full Search Toolkit pipeline runs inside a single `ingest_documents` activity; the activity returns a small result (`total_chunks`, `file_count`, …).
- **Activities own all I/O** — filesystem access, API calls, and Vespa writes live in `activities.py`. The workflow body in `workflow.py` only orchestrates.

## Designing ingestion workflows at scale

Temporal stores every activity input and output in Postgres. **Do not split ingestion into one activity per pipeline stage** if each step passes document bytes, text, chunks, or embeddings — that duplicates large payloads in workflow history and does not scale.

| Step | Cost | Where results should live |
| --- | --- | --- |
| Load from remote source | Medium | Object storage (S3) keyed by source URI + version |
| Extract plain text, split | Cheap | Recompute inside the activity on retry — do not cache in Temporal |
| OCR, embeddings | Expensive | External cache (Redis) keyed by `hash(file_bytes)`, not the raw content — implement outside this starter example |

**Anti-pattern:** one `@workflows.activity` per pipeline stage with serialized `File` / `Document` dicts passed between them. That pattern only works for tiny demo files and fills Temporal storage quickly.

### Per-stage retries

This starter example runs the full pipeline inside a single activity so Temporal only stores small inputs and outputs (paths in, chunk counts out).

If you need to **split the pipeline into separate activities** — for example, independent retry policies on OCR or embedding — you must still keep activity arguments and return values small. Pass references (file paths, S3 URIs, content hashes) between steps, and store intermediate or expensive-step results in external storage rather than in workflow history.

## Adding a new workflow example

1. Create a subdirectory under `examples/workflows/` (e.g. `my_example/`).
2. Add `models.py`, `activities.py`, `workflow.py`, and a `README.md`.
3. Export the workflow class from the package `__init__.py`.
4. Register it in `EXAMPLE_WORKFLOWS` in `worker.py`.
5. Add a Makefile target that calls `examples.workflows.start --workflow <name>`.

## Troubleshooting

| Issue | Fix |
| --- | --- |
| `Workflow not found` | Start `make start-examples` in a separate terminal, then retry |
| `DEPLOYMENT_NAME is required` | Add `DEPLOYMENT_NAME=<your-project>` to `.env` |
| Worker fails on startup | Ensure `imports_passed_through()` wraps activity imports in `workflow.py` when activities use `mistralai.client` |
| Vespa errors during indexing | Run `make setup-vespa` before triggering ingestion |

Enable verbose logging:

```bash
LOG_LEVEL=DEBUG make start-examples
```
