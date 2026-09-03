"""Offline tests for citation-URL eval matching and dataset loading."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from entrypoints.eval_retrieval import (
    citation_satisfies,
    load_citation_dataset,
    score_query,
)


def test_citation_satisfies_exact_and_parent_page() -> None:
    page = "https://docs.mistral.ai/studio/conversations/chat-completion/prompt-registry"
    section = f"{page}#template-variables"
    other = f"{page}#pinning-a-version"
    elsewhere = "https://docs.mistral.ai/studio/conversations/reasoning#usage"

    assert citation_satisfies(section, section)
    assert citation_satisfies(page, section)  # coarse enrich → still a hit
    assert citation_satisfies(section, page)  # page gold accepts any section
    assert not citation_satisfies(other, section)  # wrong section
    assert not citation_satisfies(elsewhere, section)


def test_score_query_parent_page_counts_as_hit() -> None:
    page = "https://docs.mistral.ai/studio/conversations/chat-completion/prompt-registry"
    section = f"{page}#template-variables"

    def _hit(url: str):
        return SimpleNamespace(
            chunk=SimpleNamespace(metadata={"citation_url": url})
        )

    score = score_query(
        query="template variable in prompt",
        gold=(section,),
        results=[_hit(page)],  # type: ignore[list-item]
        k=5,
    )
    assert score.hit is True
    assert score.recall_at_k == 1.0
    assert score.reciprocal_rank == 1.0
    assert score.first_hit_rank == 1


def test_load_citation_dataset(tmp_path: Path) -> None:
    path = tmp_path / "eval.jsonl"
    path.write_text(
        '{"query": "q1", "citation_urls": ["https://docs.mistral.ai/a#b"]}\n',
        encoding="utf-8",
    )
    examples = load_citation_dataset(path)
    assert len(examples) == 1
    assert examples[0].citation_urls == ("https://docs.mistral.ai/a#b",)


def test_load_citation_dataset_rejects_empty_citations(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"query": "q", "citation_urls": []}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="citation_urls"):
        load_citation_dataset(path)


def test_sample_eval_queries_file_loads() -> None:
    path = Path("sample_data/eval_queries.jsonl")
    assert path.is_file()
    examples = load_citation_dataset(path)
    assert len(examples) >= 1
    for ex in examples:
        assert ex.query
        assert ex.citation_urls
