"""Offline tests for citation-URL eval dataset loading."""

from pathlib import Path

import pytest

from entrypoints.eval_retrieval import load_citation_dataset


def test_load_citation_dataset_maps_urls_to_proxies(tmp_path: Path) -> None:
    path = tmp_path / "eval.jsonl"
    path.write_text(
        '{"query": "q1", "citation_urls": ["https://docs.mistral.ai/a#b"]}\n'
        '{"query": "q2", "citation_urls": ["https://docs.mistral.ai/x", "https://docs.mistral.ai/y#z"]}\n',
        encoding="utf-8",
    )
    dataset = load_citation_dataset(path)
    assert len(dataset.queries) == 2
    assert dataset.queries[0].relevant_reference_ids == [
        "citation_url_https://docs.mistral.ai/a#b"
    ]
    assert dataset.queries[1].relevant_reference_ids == [
        "citation_url_https://docs.mistral.ai/x",
        "citation_url_https://docs.mistral.ai/y#z",
    ]


def test_load_citation_dataset_rejects_empty_citations(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"query": "q", "citation_urls": []}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="citation_urls"):
        load_citation_dataset(path)


def test_sample_eval_queries_file_loads() -> None:
    path = Path("sample_data/eval_queries.jsonl")
    assert path.is_file()
    dataset = load_citation_dataset(path)
    assert len(dataset.queries) >= 1
    for q in dataset.queries:
        assert q.query
        assert q.relevant_reference_ids
