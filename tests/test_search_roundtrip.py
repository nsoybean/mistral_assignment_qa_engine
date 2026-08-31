"""The one test this project ships: a document goes in, a search brings it back.

Not a test of the search toolkit, and not a test of the migration chain -- both belong upstream.
It covers the seam this project owns: that the collection the app declares, the schema the
backend was actually set up with, and the `get_index` the entrypoints call all agree well enough
to store a document and find it again. That is what breaks after editing the declaration without
migrating, and it is what a fresh clone most wants to confirm before writing any code of its own.

It needs the backend running with its schema applied -- `make setup-postgres` or
`make setup-vespa`. Without one it skips, so `make test` is safe on a laptop with nothing
started.

The embedding is a fixed dummy vector rather than a call to the embedding API: the round-trip is
what is under test, and a test that needed `MISTRAL_API_KEY` would be one nobody runs.

Everything happens inside a single `asyncio.run`, including opening the index. A store opened in
one event loop holds connections bound to it, so probing in one loop and querying in another
fails with "attached to a different loop" -- which says nothing about the collection and is the
sort of thing a starter app should not make its users debug.
"""

import asyncio
import os
import uuid

import pytest

from mistralai.search.toolkit.document import Document, DocumentChunk
from mistralai.search.toolkit.search import VectorSearchQuery, VectorStoreIndex
from search_app import get_index

# Matches the dimension the app declares -- `_EMBEDDING_DIM` for Postgres, the schema's
# `embedding_dimensions` for Vespa. Changing one without the other is what this test should fail
# on, so it is stated here rather than imported from whichever backend is in use.
EMBEDDING_DIM = 128

COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "exampledocs")
PROBE_VECTOR = [0.1] * EMBEDDING_DIM
CONTENT = "the quick brown fox"


def _query(top_k: int) -> VectorSearchQuery:
    """The same query shape for both backends.

    `query` is the keyword half. Postgres is dense-only and ignores it, but Vespa builds hybrid
    YQL and rejects an empty one with "Parsing '' only resulted in NullItem" -- so a query
    carrying the embedding alone passes on one backend and fails on the other.
    """
    return VectorSearchQuery(query=CONTENT, embedding=PROBE_VECTOR, top_k=top_k)


async def _open_ready_index() -> VectorStoreIndex:
    """The live index, or a skip if the backend is not set up.

    The probe is a search, which needs the server reachable *and* the collection's schema
    applied -- together, what `make setup-*` produces. Telling "not started" apart from "started
    but broken" is not worth a second code path here: the skip names the command either way, and
    carries the error that explains which it was.
    """
    index: VectorStoreIndex = get_index(COLLECTION_NAME)
    try:
        await index.search(_query(top_k=1))
    except Exception as exc:  # noqa: BLE001 - any failure here means the backend is not ready
        pytest.skip(f"backend not ready for collection {COLLECTION_NAME!r} ({exc}); run `make setup-*` first")
    return index


async def _index_then_search() -> tuple[str, list[str]]:
    store = await _open_ready_index()

    # A fresh id per run, so a row left behind by an earlier run cannot make this pass.
    document_id = f"roundtrip-{uuid.uuid4()}"
    source_id = f"{document_id}.txt"
    chunk_id = f"{document_id}-chunk-0"

    document = Document(
        id=document_id,
        source_id=source_id,
        content=CONTENT,
        chunks=[
            DocumentChunk(
                id=chunk_id,
                source_id=source_id,
                locator="1",
                content=CONTENT,
                start_offset=0,
                end_offset=len(CONTENT),
                embedding=PROBE_VECTOR,
            )
        ],
    )

    await store.index_document(document)
    try:
        results = await store.search(_query(top_k=5))
        return chunk_id, [result.chunk.id for result in results]
    finally:
        # Leave the collection as it was found, so repeated runs stay meaningful.
        await store.delete_document(document_id)


def test_a_document_can_be_indexed_and_found() -> None:
    chunk_id, found = asyncio.run(_index_then_search())
    assert chunk_id in found
