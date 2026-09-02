"""Shared search wiring for CLI and MCP entrypoints."""

from __future__ import annotations

import os

from mistralai.client import Mistral
from mistralai.search.toolkit.embedders import MistralEmbedder
from mistralai.search.toolkit.retrieval import QueryEngine, VectorRetriever
from mistralai.search.toolkit.retrieval.query_engine import QueryEngineResult
from mistralai.search.toolkit.search import StoreIndex

from search_app import DEFAULT_QUERY_PROFILE, get_index


def get_collection_name() -> str:
    return os.environ.get("COLLECTION_NAME", "exampledocs")


def get_mistral_client() -> Mistral:
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        raise ValueError("MISTRAL_API_KEY is not set. Check your .env file.")
    return Mistral(
        api_key=api_key,
        server_url=os.getenv("MISTRAL_API_URL", "https://api.mistral.ai"),
    )


def create_query_engine(
    *,
    collection: str | None = None,
    query_profile: str = DEFAULT_QUERY_PROFILE,
    client: Mistral | None = None,
    store: StoreIndex | None = None,
) -> tuple[QueryEngine, StoreIndex]:
    """Build a ``QueryEngine`` and the backing store (re-use ``store`` when already open)."""
    coll = collection or get_collection_name()
    vector_store = store or get_index(coll, query_profile=query_profile)
    embedder = MistralEmbedder(client=client or get_mistral_client())
    query_engine = QueryEngine(
        retriever=[VectorRetriever(client=vector_store, embedder=embedder)],
    )
    return query_engine, vector_store


async def search(
    query: str,
    *,
    top_k: int = 5,
    query_profile: str = DEFAULT_QUERY_PROFILE,
    collection: str | None = None,
    query_engine: QueryEngine | None = None,
) -> QueryEngineResult:
    """Run hybrid retrieval with content and metadata included."""
    engine = query_engine
    if engine is None:
        engine, _ = create_query_engine(
            collection=collection,
            query_profile=query_profile,
        )
    return await engine.search(
        query=query,
        top_k=top_k,
        include_metadata=True,
        include_content=True,
    )
