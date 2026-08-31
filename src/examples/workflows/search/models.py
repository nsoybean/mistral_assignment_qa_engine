"""Pydantic models for the document ingestion workflow."""

from pydantic import BaseModel, Field


class IngestionInput(BaseModel):
    """Input accepted by the document-ingestion workflow."""

    file_path: str = Field(description="Local path to a file or directory to ingest")
    collection_name: str = Field(
        default="exampledocs",
        description="Vespa collection to index documents into",
    )


class IngestionResult(BaseModel):
    """Output returned by the document-ingestion workflow."""

    status: str = Field(description="'success' or 'error'")
    total_chunks: int = Field(description="Total number of chunks indexed")
    file_count: int = Field(description="Number of files processed")
    collection_name: str = Field(description="Vespa collection that was used")
