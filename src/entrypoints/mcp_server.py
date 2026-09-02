"""MCP server for searching preprocessed docs.mistral.ai pages."""

import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mistralai.search.toolkit.embedders import MistralEmbedder
from mistralai.search.toolkit.document import compute_id
from mistralai.search.toolkit.ingestion import File
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
from mistralai.search.toolkit.search import (
    GrepMode,
    NavigableIndex,
    NavigationDirection,
)
from mistralai.search.toolkit.search.errors import DocumentNotFoundError
from search_app.query import (
    create_query_engine,
    get_collection_name,
    get_mistral_client,
    search as run_search,
)

load_dotenv(override=True)

_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json"}

# ---------------------------------------------------------------------------
# Startup — fail fast if the environment is misconfigured
# ---------------------------------------------------------------------------

_api_key = os.environ.get("MISTRAL_API_KEY", "")
if not _api_key:
    raise RuntimeError("MISTRAL_API_KEY is not set. Check your .env file.")

_collection_name = get_collection_name()
_mistral_client = get_mistral_client()
_embedder = MistralEmbedder(client=_mistral_client)
_query_engine, _vector_store = create_query_engine(
    collection=_collection_name,
    client=_mistral_client,
)
if not isinstance(_vector_store, NavigableIndex):
    raise RuntimeError(
        "The search index does not support agentic navigation. "
        "Ensure IndexingMode.DOCUMENT_PER_CHUNK is used in the schema migration."
    )
_navigable_store: NavigableIndex = _vector_store

_loader = FilesystemFileLoader()
_text_splitter = MarkdownTextSplitter(
    MarkdownTextSplitterConfig(chunk_size=4096, chunk_overlap=50)
)
_plain_text_pipeline = Pipeline(
    loader=_loader,
    extractor=PlainTextExtractor(),
    text_splitter=_text_splitter,
    embedder=_embedder,
    stores=_vector_store,
)
_ocr_pipeline = Pipeline(
    loader=_loader,
    extractor=MistralOCRExtractor(client=_mistral_client),
    text_splitter=_text_splitter,
    embedder=_embedder,
    stores=_vector_store,
)

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

MCP_SERVER_NAME = "Mistral Documentation"

mcp = FastMCP(
    MCP_SERVER_NAME,
    instructions="""\
Search a local index of preprocessed docs.mistral.ai pages.
Each chunk carries citation metadata — when citing, prefer
metadata.citation_url (section anchor deep-link when available); if absent
or only the bare page URL, fall back to source_id (canonical page URL).
metadata.heading is the section title.

Retrieval loop:
1. `search` — find relevant sections across the indexed docs
2. `open` — expand context around a hit (pass chunk `id` from search)
3. `grep` — exact term or phrase within one page (pass source_id from search)
4. `navigate` / `read` — step through or fetch chunks within one page
5. Search again with refined queries to connect topics across pages

Prefer retrieved chunks over general knowledge for API parameters, model
names, and code examples. Known index gaps: collapsed FAQ answers and
inactive code-tab languages (TypeScript, curl) may be missing.

`ingest` adds files to the index; `delete` removes a document by source_id.""",
)


def _format_chunks(results: list) -> list[dict]:
    """Serialise SearchResult objects into a consistent dict shape.

    Includes the chunk `id` (pass to open()) and start_offset / end_offset
    (pass to navigate() / read()) so the model can drive the agentic
    navigation tools directly.
    """
    return [
        {
            "id": hit.chunk.id,
            "score": hit.score,
            "content": hit.chunk.content,
            "source_id": hit.chunk.source_id,
            "locator": hit.chunk.locator,
            "start_offset": hit.chunk.start_offset,
            "end_offset": hit.chunk.end_offset,
            "metadata": hit.chunk.metadata,
        }
        for hit in results
    ]


@mcp.tool()
async def search(query: str, top_k: int = 5) -> list[dict]:
    """Search indexed docs.mistral.ai pages by natural language.

    Returns the most relevant chunks. Each result includes metadata.citation_url
    (deep link to the section), metadata.heading, metadata.title, and source_id
    (canonical page URL). Cite metadata.citation_url in answers.

    Args:
        query: Natural-language search query (e.g. "reasoning_effort parameter").
        top_k: Maximum number of results to return (default 5).
    """
    result = await run_search(
        query,
        top_k=top_k,
        query_engine=_query_engine,
    )
    return _format_chunks(result.results)


def _pipeline_for_name(name: str) -> Pipeline:
    """Return the plain-text or OCR pipeline based on the file extension."""
    return (
        _plain_text_pipeline
        if Path(name).suffix.lower() in _TEXT_SUFFIXES
        else _ocr_pipeline
    )


def _filename_from_url(url: str, headers: httpx.Headers) -> str:
    """Derive a filename from a Content-Disposition header or the URL path."""
    cd = headers.get("content-disposition", "")
    if cd:
        for part in cd.split(";"):
            part = part.strip()
            if part.lower().startswith("filename="):
                return part.split("=", 1)[1].strip().strip('"')
    name = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return name or "download"


async def _ingest_http(url: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        name = _filename_from_url(url, r.headers)
        content = r.content

    file = File(path=url, name=name, raw=content, source_id=url)
    doc = await _pipeline_for_name(name).run_file(file)
    return f"Indexed {len(doc.chunks)} chunks from '{url}' into '{_collection_name}'."


async def _ingest_local(root: Path) -> str:
    if not root.exists():
        return f"Error: path not found: {root}"

    if root.is_file():
        documents = [root]
    elif root.is_dir():
        documents = sorted(p for p in root.rglob("*") if p.is_file())
        if not documents:
            return f"Error: no files found under {root}"
    else:
        return f"Error: {root} is neither a file nor a directory"

    total_chunks = 0
    for doc_path in documents:
        total_chunks += await _pipeline_for_name(doc_path.name).run(
            documents=[doc_path], use_checkpoint=False
        )
    return (
        f"Indexed {total_chunks} chunks from {len(documents)} file(s)"
        f" into '{_collection_name}'."
    )


@mcp.tool()
async def ingest(uri: str) -> str:
    """Add a document to the Mistral docs search index.

    For preprocessed docs HTML, prefer ingesting via the CLI
    (`make ingest path=sample_data/mistral_docs`) — the MCP path does not yet
    use the docs HTML pipeline. Accepts a local path, file:// URI, or http(s) URL.
    Text files use plain-text extraction; other formats use Mistral OCR.

    Args:
        uri: Local file/directory path, file:// URI, or http(s):// URL.

    Returns:
        A summary of how many chunks were indexed, or an error message.
    """
    parsed = urlparse(uri)

    if parsed.scheme in ("http", "https"):
        return await _ingest_http(uri)

    if parsed.scheme == "file":
        return await _ingest_local(Path(url2pathname(parsed.path)))

    # Bare local path (no scheme)
    return await _ingest_local(Path(uri))


@mcp.tool()
async def delete(source_id: str) -> str:
    """Remove a docs.mistral.ai page and all its chunks from the index.

    Use source_id from search() — for indexed docs this is the page URL
    (e.g. https://docs.mistral.ai/studio/conversations/reasoning).

    Args:
        source_id: Page URL or other source identifier of the document to delete.

    Returns:
        A confirmation message, or an error message if the document was not found.
    """
    try:
        await _vector_store.delete_document(compute_id(source_id))
        return f"Deleted document '{source_id}' from '{_collection_name}'."
    except DocumentNotFoundError:
        return f"Error: document '{source_id}' not found in '{_collection_name}'."


# ---------------------------------------------------------------------------
# Agentic navigation tools  (RFC: Agentic Search Loop)
# ---------------------------------------------------------------------------


@mcp.tool()
async def open(chunk_id: str, window: int = 2) -> list[dict]:
    """Expand context around a docs.mistral.ai search hit within the same page.

    Pass chunk `id` from search(); returns neighbouring chunks in reading order
    on the same page. Use when a search snippet is truncated or missing context.
    For a known offset range without expansion, use read() instead.

    Args:
        chunk_id: `id` from a search() result.
        window:   Adjacent chunks to include on each side (default 2).
    """
    anchor = await _navigable_store.get_chunk(chunk_id)
    if anchor is None:
        raise ToolError(f"chunk not found: {chunk_id!r}")
    source_id = anchor.chunk.source_id
    start_offset = anchor.chunk.start_offset or 0
    end_offset = anchor.chunk.end_offset or 0
    prev = await _navigable_store.navigate(
        source_id, start_offset, end_offset, NavigationDirection.PREVIOUS, top_k=window
    )
    nxt = await _navigable_store.navigate(
        source_id, start_offset, end_offset, NavigationDirection.NEXT, top_k=window
    )
    return _format_chunks(prev + [anchor] + nxt)


@mcp.tool()
async def navigate(
    source_id: str,
    start_offset: int,
    end_offset: int,
    direction: str,
    top_k: int = 1,
) -> list[dict]:
    """Step forward or backward through a document from a known position.

    Args:
        source_id:    Source identifier from a search() or open() result.
        start_offset: start_offset of the current anchor chunk.
        end_offset:   end_offset of the current anchor chunk.
        direction:    "next" to move forward, "previous" to move backward.
        top_k:        Number of chunks to retrieve in the given direction (default 1).
    """
    nav_dir = NavigationDirection(direction)
    results = await _navigable_store.navigate(
        source_id, start_offset, end_offset, nav_dir, top_k=top_k
    )
    return _format_chunks(results)


@mcp.tool()
async def read(
    source_id: str,
    start_offset: int | None = None,
    end_offset: int | None = None,
    top_k: int = 20,
) -> list[dict]:
    """Fetch chunks from a known offset range: direct access, no context expansion.

    Use when you already know the source and the exact range you want, and just
    want those chunks back as-is (unlike open(), which expands around a chunk).
    Pass None for start_offset to read from the beginning, or None for
    end_offset to read to the end of the document.

    Args:
        source_id:    Source identifier from a search() result.
        start_offset: Inclusive lower bound (None = start of document).
        end_offset:   Inclusive upper bound (None = end of document).
        top_k:        Maximum number of chunks to return (default 20).
    """
    results = await _navigable_store.read(
        source_id, start_offset, end_offset, top_k=top_k
    )
    return _format_chunks(results)


@mcp.tool()
async def grep(
    source_id: str,
    pattern: str,
    mode: str = "phrase",
    top_k: int = 5,
) -> list[dict]:
    """Find exact terms or phrases within one indexed docs page.

    Use source_id from search() (the docs.mistral.ai page URL). Helpful for model
    names, parameter names, or error strings that hybrid search may rank poorly.

    Args:
        source_id: Page URL from a search() result.
        pattern:   Text to search for (e.g. "reasoning_effort", "ThinkChunk").
        mode:      "phrase" (default) — exact order; "term" — all terms, any order.
        top_k:     Maximum matches to return (default 5).
    """
    grep_mode = GrepMode(mode)
    results = await _navigable_store.grep(
        source_id, pattern, mode=grep_mode, top_k=top_k
    )
    return _format_chunks(results)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the MCP server.")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Start in HTTP (streamable-HTTP) mode instead of the default stdio mode.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (HTTP mode only, default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port (HTTP mode only, default: 8000).",
    )
    args = parser.parse_args()

    if args.http:
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()
