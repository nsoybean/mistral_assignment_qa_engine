"""Ingest path filtering."""

from pathlib import Path

from entrypoints.ingest import _is_docs_meta_sidecar


def test_meta_json_sidecar_is_skipped() -> None:
    assert _is_docs_meta_sidecar(Path("prompt-registry.meta.json"))
    assert _is_docs_meta_sidecar(
        Path("sample_data/mistral_docs/studio/conversations/reasoning.meta.json")
    )
    assert not _is_docs_meta_sidecar(Path("prompt-registry.html"))
    assert not _is_docs_meta_sidecar(Path("notes.json"))
    assert not _is_docs_meta_sidecar(Path("data.jsonl"))
