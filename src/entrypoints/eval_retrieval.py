"""Evaluate retrieval against a golden citation-URL dataset.

Ground truth is section ``citation_url`` values (stable across re-chunking).
Add lines to ``sample_data/eval_queries.jsonl``:

    {"query": "how do i handle thinking chunk",
     "citation_urls": ["https://docs.mistral.ai/studio/conversations/reasoning#handling-thinking-chunks"]}

Multi-hop queries list several URLs. Page-level answers may omit the ``#anchor``.

Usage:
    python -m entrypoints.eval_retrieval
    python -m entrypoints.eval_retrieval sample_data/eval_queries.jsonl --top-k 10
    make eval-retrieval
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from dotenv import load_dotenv
from mistralai.search.toolkit.evals import (
    EvaluationDataset,
    EvaluationQuery,
    EvaluationSummary,
    RetrieverEvaluator,
    RetrievalStepResult,
    save_evaluation_summary_to_json,
)
from mistralai.search.toolkit.evals.models import RetrievalMetrics, generate_proxy

from search_app import DEFAULT_QUERY_PROFILE
from search_app.query import create_query_engine, get_collection_name, search

load_dotenv(override=True)

_DEFAULT_DATASET = Path("sample_data/eval_queries.jsonl")
_CITATION_METADATA_KEY = "citation_url"
# Demo metrics only — enough to compare chunking configs without IR noise.
_PRIMARY_K = 5
_DEFAULT_K_VALUES = [_PRIMARY_K]


def _gold_urls(eval_result) -> list[str]:
    gold = eval_result.relevant_reference_ids or eval_result.relevant_ids
    prefix = f"{_CITATION_METADATA_KEY}_"
    return [g.removeprefix(prefix) if g.startswith(prefix) else g for g in gold]


def _print_focused_summary(summary: EvaluationSummary, *, k: int = _PRIMARY_K) -> None:
    metrics = summary.workflow_metrics_avg.get("hybrid")
    print("\n" + "=" * 60)
    print("RETRIEVAL EVAL (Hit rate / Recall@k / MRR)")
    print("=" * 60)
    print(f"Dataset: {summary.dataset_name}")
    print(f"Queries: {summary.total_queries}")
    if metrics is None:
        print("No hybrid metrics.")
        return
    recall = metrics.recall_at_k.get(k)
    print(f"Hit rate:  {metrics.hit_rate:.3f}" if metrics.hit_rate is not None else "Hit rate:  n/a")
    print(f"Recall@{k}: {recall:.3f}" if recall is not None else f"Recall@{k}: n/a")
    print(f"MRR:       {metrics.mrr:.3f}" if metrics.mrr is not None else "MRR:       n/a")
    print("=" * 60)


def _print_per_query(per_query, *, k: int = _PRIMARY_K) -> None:
    print(f"\nPer-query (gold citation_url in top results):")
    for eval_result in per_query:
        metrics: RetrievalMetrics | None = eval_result.workflow_metrics.get("hybrid")
        hit = metrics.hit_rate if metrics else None
        mrr = metrics.mrr if metrics else None
        recall = metrics.recall_at_k.get(k) if metrics else None
        status = "HIT" if hit == 1.0 else "MISS"
        recall_s = f"{recall:.2f}" if recall is not None else "n/a"
        mrr_s = f"{mrr:.2f}" if mrr is not None else "n/a"
        print(f"  [{status}] recall@{k}={recall_s}  mrr={mrr_s}  {eval_result.query!r}")
        print(f"         gold={_gold_urls(eval_result)}")


def _proxy_for_url(url: str) -> str:
    """Match ``generate_proxy(..., metadata_keys=['citation_url'])`` format."""

    class _Chunk:
        metadata = {_CITATION_METADATA_KEY: url}

    return generate_proxy(_Chunk(), [_CITATION_METADATA_KEY])  # type: ignore[arg-type]


def load_citation_dataset(path: Path) -> EvaluationDataset:
    """Load JSONL with ``query`` + ``citation_urls`` (or toolkit-native fields)."""
    if not path.is_file():
        raise FileNotFoundError(f"Dataset not found: {path}")

    queries: list[EvaluationQuery] = []
    with path.open(encoding="utf-8") as handle:
        for line_num, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_num}: {exc}") from exc

            query_text = data.get("query")
            if not query_text:
                raise ValueError(f"Missing 'query' on line {line_num}")

            citation_urls = data.get("citation_urls")
            if citation_urls is not None:
                if not isinstance(citation_urls, list) or not citation_urls:
                    raise ValueError(
                        f"'citation_urls' must be a non-empty list on line {line_num}"
                    )
                relevant_reference_ids = [_proxy_for_url(str(u)) for u in citation_urls]
                relevant_ids: list[str] = []
            elif data.get("relevant_reference_ids") or data.get("relevant_ids"):
                relevant_reference_ids = data.get("relevant_reference_ids")
                relevant_ids = data.get("relevant_ids") or []
            else:
                raise ValueError(
                    f"Line {line_num}: provide 'citation_urls' "
                    "(preferred) or toolkit 'relevant_ids' / 'relevant_reference_ids'"
                )

            queries.append(
                EvaluationQuery(
                    query=query_text,
                    relevant_ids=relevant_ids,
                    relevant_reference_ids=relevant_reference_ids,
                    metadata=data.get("metadata") or {},
                )
            )

    if not queries:
        raise ValueError(f"No queries in {path}")

    return EvaluationDataset(
        queries=queries,
        name=path.stem,
        description="Citation-URL retrieval eval",
    )


async def _run(args: argparse.Namespace) -> None:
    dataset_path = Path(args.dataset)
    dataset = load_citation_dataset(dataset_path)
    collection = get_collection_name()

    print(f"Dataset: {dataset_path} ({len(dataset.queries)} queries)")
    print(f"Collection: {collection}")
    print(f"Top-K: {args.top_k}")
    print(f"Query profile: {args.query_profile}")
    print(f"Match on metadata key: {_CITATION_METADATA_KEY}")

    query_engine, _ = create_query_engine(query_profile=args.query_profile)

    async def workflow(query: str) -> list[RetrievalStepResult]:
        started = time.perf_counter()
        result = await search(
            query,
            top_k=args.top_k,
            query_profile=args.query_profile,
            query_engine=query_engine,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        return [
            RetrievalStepResult(
                step_name="hybrid",
                results=list(result.results),
                top_k=args.top_k,
                execution_time_ms=elapsed_ms,
                k_values=args.k_values,
            )
        ]

    evaluator = RetrieverEvaluator(
        k_values=args.k_values,
        metadata_keys=[_CITATION_METADATA_KEY],
    )
    summary, per_query = await evaluator.evaluate_workflow_dataset_batch_with_results(
        dataset=dataset,
        workflow=workflow,
        batch_size=args.batch_size,
        max_concurrent_batches=1,
    )

    primary_k = args.k_values[0] if args.k_values else _PRIMARY_K
    _print_focused_summary(summary, k=primary_k)
    _print_per_query(per_query, k=primary_k)

    if args.output:
        out = Path(args.output)
        save_evaluation_summary_to_json(summary, out)
        print(f"\nWrote summary JSON to {out}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate hybrid retrieval against citation_url ground truth "
            "(see sample_data/eval_queries.jsonl)."
        )
    )
    parser.add_argument(
        "dataset",
        nargs="?",
        type=Path,
        default=_DEFAULT_DATASET,
        help=f"JSONL eval set (default: {_DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Retrieve this many hits per query (default: 10)",
    )
    parser.add_argument(
        "--k-values",
        type=int,
        nargs="+",
        default=_DEFAULT_K_VALUES,
        help=f"k for Recall@k (default: {_DEFAULT_K_VALUES}; first value is the reported Recall@k)",
    )
    parser.add_argument(
        "--query-profile",
        default=DEFAULT_QUERY_PROFILE,
        help=f"Vespa query profile (default: {DEFAULT_QUERY_PROFILE})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Queries per eval batch (default: 5)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write EvaluationSummary JSON",
    )
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except Exception as exc:
        raise SystemExit(f"Error: {exc}") from exc


if __name__ == "__main__":
    main()
