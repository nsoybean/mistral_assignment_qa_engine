"""Document ingestion workflow -- orchestration only, no I/O here."""

import mistralai.workflows as workflows
from mistralai.workflows import workflow

# Activities import the search toolkit and mistralai.client (→ httpx), which
# the Temporal sandbox blocks at workflow import time. imports_passed_through
# lets the workflow reference activity stubs without loading I/O deps in-process.
with workflow.unsafe.imports_passed_through():
    from .activities import collect_document_paths, ingest_documents

from .models import IngestionInput, IngestionResult  # noqa: E402


@workflows.workflow.define(
    name="document-ingestion",
    workflow_display_name="Document Ingestion",
    workflow_description=(
        "Ingest documents from the local filesystem into the Vespa search index "
        "using the Search Toolkit pipeline: load → extract → split → embed → index."
    ),
)
class IngestionWorkflow:
    """Workflow that wraps the search-starter-app ingestion pipeline.

    All I/O (file loading, LLM calls, Vespa writes) lives in activities.py so
    the workflow body stays deterministic and replayable by the Temporal engine.
    """

    @workflows.workflow.entrypoint
    async def run(self, params: IngestionInput) -> IngestionResult:
        paths = await collect_document_paths(file_path=params.file_path)
        return await ingest_documents(
            paths=paths,
            collection_name=params.collection_name,
        )
