"""The MCP navigation tools keep the shape the agent loop depends on.

The round-trip test covers storing and finding a document; this one covers the seam the
MCP server owns on top of that: that `open` and `read` expose the *distinct* contracts the
agentic search loop is built around, and that `open` actually resolves a chunk id to a
position before it windows around it.

- `open(chunk_id, window)`   -- "I have a chunk from search, show me context around it."
  The caller passes only the opaque chunk `id`; the server resolves its position via
  `NavigableIndex.get_chunk` and pulls in neighbours. If this silently reverts to taking
  raw offsets, the tool becomes indistinguishable from `read` and the distinction the docs
  teach stops being true.
- `read(source_id, start_offset, end_offset)` -- "I know the exact range, give me those
  chunks." Offset-addressed, no expansion.

No backend and no API key are needed: `open`'s logic is exercised against a fake store, and
the schema is read off the registered tools. `mcp_server` fails fast at import without
`MISTRAL_API_KEY` and loads `.env` with `override=True`, so the fixture neutralises the
dotenv load and sets a placeholder key -- it is never used to make a call.
"""

import asyncio
import importlib

import pytest

from mistralai.search.toolkit.document import ChunkType
from mistralai.search.toolkit.search import NavigationDirection
from mistralai.search.toolkit.search.models import SearchResult, SearchResultChunk


@pytest.fixture
def mcp_server(monkeypatch: pytest.MonkeyPatch):
    """Import `entrypoints.mcp_server` offline, with a placeholder key and dotenv disabled.

    The module reads `.env` with `override=True` at import; the generated `.env` ships an
    empty `MISTRAL_API_KEY`, which would clobber a value set here. Neutralising `load_dotenv`
    before (re)import lets the placeholder stand so the import-time fail-fast passes.
    """
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key-not-used")

    import entrypoints.mcp_server as module

    return importlib.reload(module)


def _result(chunk_id: str, start: int, end: int, content: str) -> SearchResult:
    chunk = SearchResultChunk(
        id=chunk_id,
        source_id="doc-1",
        locator=f"chunk_idx:{start}",
        content=content,
        start_offset=start,
        end_offset=end,
        chunk_type=ChunkType.CONTENT,
    )
    return SearchResult(score=0.0, chunk=chunk)


class _FakeStore:
    """Minimal `NavigableIndex` stand-in: knows one chunk and one neighbour in each direction."""

    async def get_chunk(self, chunk_id: str, **_: object) -> SearchResult | None:
        return _result("c2", 10, 20, "anchor") if chunk_id == "c2" else None

    async def navigate(
        self, source_id: str, start: int, end: int, direction: NavigationDirection, *, top_k: int = 1, **_: object
    ) -> list[SearchResult]:
        if direction == NavigationDirection.PREVIOUS:
            return [_result("c1", 0, 10, "before")]
        return [_result("c3", 20, 30, "after")]


def _open_fn(mcp_server):
    # `open` shadows the builtin; @mcp.tool() wraps it, so reach the coroutine through `.fn`.
    return getattr(mcp_server.open, "fn", mcp_server.open)


def test_open_takes_a_chunk_id_and_read_stays_offset_addressed(mcp_server) -> None:
    open_tool = asyncio.run(mcp_server.mcp.get_tool("open"))
    read_tool = asyncio.run(mcp_server.mcp.get_tool("read"))

    assert set(open_tool.parameters["properties"]) == {"chunk_id", "window"}
    assert open_tool.parameters.get("required") == ["chunk_id"]

    read_props = read_tool.parameters["properties"]
    assert "start_offset" in read_props and "end_offset" in read_props
    assert "chunk_id" not in read_props


def test_open_resolves_the_chunk_and_windows_around_it(mcp_server, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server, "_navigable_store", _FakeStore())

    out = asyncio.run(_open_fn(mcp_server)("c2", window=1))

    # Previous neighbour, the resolved anchor, then the next neighbour -- in reading order.
    assert [r["id"] for r in out] == ["c1", "c2", "c3"]
    assert [r["content"] for r in out] == ["before", "anchor", "after"]


def test_open_raises_when_the_chunk_id_is_unknown(mcp_server, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp.exceptions import ToolError

    monkeypatch.setattr(mcp_server, "_navigable_store", _FakeStore())

    with pytest.raises(ToolError):
        asyncio.run(_open_fn(mcp_server)("does-not-exist"))
