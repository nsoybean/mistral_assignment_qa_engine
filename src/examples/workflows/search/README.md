# Document Ingestion Workflow Example

This example wraps the search-starter-app ingestion pipeline in a [Mistral Workflow](https://docs.mistral.ai/capabilities/workflows/), adding durability, observability, and retry support.

It follows the recommended pattern for ingestion at scale: **small activity I/O**, with the full Search Toolkit pipeline running inside a single activity.

## What It Demonstrates

| Primitive | Where |
| --- | --- |
| `@workflows.workflow.define` | `workflow.py` — workflow registered in the Mistral Console |
| `@workflows.workflow.entrypoint` | `workflow.py` — orchestration only; passes paths, not document content |
| `@workflows.activity` | `activities.py` — all file I/O, embedding calls, and Vespa writes |
| Pydantic input/output models | `models.py` — typed boundaries; workflow output is a small summary |
| `workflows.run_worker()` | `worker.py` — registers the workflow with Temporal |

## Architecture

```
IngestionWorkflow.run(IngestionInput)
├── collect_document_paths activity   → list[str] paths only
└── ingest_documents activity         → IngestionResult (counts + status)
    └── Search Toolkit Pipeline:
        load → extract → split → embed → index
```

The workflow never sees file bytes, extracted text, chunks, or embeddings — only paths and the final chunk count.

## Prerequisites

1. Install the workflows extra:
   ```bash
   make install-workflows
   ```

2. Vespa must be running:
   ```bash
   make setup-vespa
   ```

3. Set `MISTRAL_API_KEY` and `DEPLOYMENT_NAME` in your `.env` file.

## Running the Example

> The worker must be running before you trigger the workflow, otherwise the API returns `Workflow not found`.

**Terminal 1 — start the worker (leave this running):**
```bash
make start-examples
```

**Terminal 2 — trigger ingestion:**
```bash
make execute-ingestion input='{"file_path": "sample_data/hello.txt"}'
make execute-ingestion input='{"file_path": "sample_data"}'
make execute-ingestion input='{"file_path": "sample_data/hello.txt", "collection_name": "mydocs"}'
```

You can also trigger the workflow from the [Mistral Console](https://console.mistral.ai/build/workflows) by selecting `document-ingestion`.

## What not to do

Do **not** split ingestion into one activity per pipeline stage while passing serialized documents between them. Temporal persists every activity input/output in Postgres; that pattern duplicates large payloads and does not scale. See the parent [workflows README](../README.md#designing-ingestion-workflows-at-scale).

If you want to split the pipeline to enable retries for some stages, follow the [Handling Large Data](https://docs.mistral.ai/capabilities/workflows/guides/handling-large-data/) guide instead of passing document content between activities.

## Key Difference from Direct Ingestion

`make ingest` runs the same Search Toolkit pipeline synchronously. The workflow adds:

- **Durability** — the worker can restart mid-ingestion and resume from the last completed activity
- **Observability** — executions appear in the Mistral Console
- **Retries** — the activity retries on transient errors without bloating Temporal with per-step document payloads

Search queries remain direct (`make search`) — they do not need workflow orchestration.
