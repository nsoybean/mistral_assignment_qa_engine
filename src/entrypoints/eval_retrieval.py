"""Evaluate retrieval against a golden citation-URL dataset.

Ground truth is section ``citation_url`` values (ideal deep links). Matching is
**hierarchical**: a retrieved page-level cite (no ``#anchor``) counts as a hit
for a gold section on that same page. That matters when chunk merging leaves
``## Section`` mid-chunk so enrichment inherits the parent page URL — content
is correct, deep-link metadata is coarser.

Label the *ideal* section URL in the dataset; do not downgrade labels to the
page URL just because enrichment is currently coarse.

    {"query": "template variable in prompt",
     "citation_urls": ["https://docs.mistral.ai/studio/conversations/chat-completion/prompt-registry#template-variables"]}

Usage:
    python -m entrypoints.eval_retrieval
    make eval-retrieval
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urldefrag

from dotenv import load_dotenv
from mistralai.search.toolkit.search import SearchResult

from search_app import DEFAULT_QUERY_PROFILE
from search_app.query import create_query_engine, get_collection_name, search

load_dotenv(override=True)

_DEFAULT_DATASET = Path("sample_data/eval_queries.jsonl")
_PRIMARY_K = 5


@dataclass(frozen=True)
class EvalExample:
    query: str
    citation_urls: tuple[str, ...]


@dataclass(frozen=True)
class QueryScore:
    query: str
    gold: tuple[str, ...]
    hit: bool
    recall_at_k: float
    reciprocal_rank: float
    first_hit_rank: int | None
    retrieved_citations: tuple[str, ...]


@dataclass(frozen=True)
class EvalSummary:
    dataset_name: str
    total_queries: int
    hit_rate: float
    recall_at_k: float
    mrr: float
    k: int
    per_query: tuple[QueryScore, ...]


def citation_satisfies(retrieved: str, gold: str) -> bool:
    """Exact match, or page-level cite covering a section gold on the same page.

    Examples that return True:
      retrieved == gold
      gold=.../page#section, retrieved=.../page
      gold=.../page, retrieved=.../page#anything
    """
    if retrieved == gold:
        return True
    gold_base, gold_frag = urldefrag(gold)
    ret_base, ret_frag = urldefrag(retrieved)
    if gold_base.rstrip("/") != ret_base.rstrip("/"):
        return False
    # Parent page cite satisfies a more specific section label.
    if gold_frag and not ret_frag:
        return True
    # Page-level gold accepts any section deep-link on that page.
    if not gold_frag:
        return True
    return False


def _retrieved_citations(results: list[SearchResult]) -> list[str]:
    citations: list[str] = []
    for hit in results:
        url = hit.chunk.metadata.get("citation_url")
        if url is None:
            citations.append("")
        else:
            citations.append(str(url))
    return citations


def score_query(
    *,
    query: str,
    gold: tuple[str, ...],
    results: list[SearchResult],
    k: int,
) -> QueryScore:
    retrieved = _retrieved_citations(results)
    top = retrieved[:k]

    matched_golds = {
        g for g in gold if any(citation_satisfies(r, g) for r in top if r)
    }
    recall = len(matched_golds) / len(gold) if gold else 0.0
    hit = len(matched_golds) > 0

    first_rank: int | None = None
    for i, r in enumerate(retrieved, start=1):
        if r and any(citation_satisfies(r, g) for g in gold):
            first_rank = i
            break
    rr = (1.0 / first_rank) if first_rank is not None else 0.0

    return QueryScore(
        query=query,
        gold=gold,
        hit=hit,
        recall_at_k=recall,
        reciprocal_rank=rr,
        first_hit_rank=first_rank,
        retrieved_citations=tuple(retrieved[:k]),
    )


def load_citation_dataset(path: Path) -> list[EvalExample]:
    """Load JSONL with ``query`` + ``citation_urls``."""
    if not path.is_file():
        raise FileNotFoundError(f"Dataset not found: {path}")

    examples: list[EvalExample] = []
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
            if not isinstance(citation_urls, list) or not citation_urls:
                raise ValueError(
                    f"'citation_urls' must be a non-empty list on line {line_num}"
                )
            examples.append(
                EvalExample(
                    query=query_text,
                    citation_urls=tuple(str(u) for u in citation_urls),
                )
            )

    if not examples:
        raise ValueError(f"No queries in {path}")
    return examples


def _print_summary(summary: EvalSummary) -> None:
    print("\n" + "=" * 60)
    print("RETRIEVAL EVAL (Hit rate / Recall@k / MRR)")
    print("=" * 60)
    print(f"Dataset: {summary.dataset_name}")
    print(f"Queries: {summary.total_queries}")
    print("Match:   exact citation_url, or parent page covers section gold")
    print(f"Hit rate:  {summary.hit_rate:.3f}")
    print(f"Recall@{summary.k}: {summary.recall_at_k:.3f}")
    print(f"MRR:       {summary.mrr:.3f}")
    print("=" * 60)


def _print_per_query(summary: EvalSummary) -> None:
    print("\nPer-query:")
    for q in summary.per_query:
        status = "HIT" if q.hit else "MISS"
        rank_s = str(q.first_hit_rank) if q.first_hit_rank is not None else "-"
        print(
            f"  [{status}] recall@{summary.k}={q.recall_at_k:.2f}  "
            f"mrr={q.reciprocal_rank:.2f}  first_rank={rank_s}  {q.query!r}"
        )
        print(f"         gold={list(q.gold)}")
        print(f"         top{summary.k} citations={list(q.retrieved_citations)}")


async def _run(args: argparse.Namespace) -> None:
    dataset_path = Path(args.dataset)
    examples = load_citation_dataset(dataset_path)
    collection = get_collection_name()
    k = args.k

    print(f"Dataset: {dataset_path} ({len(examples)} queries)")
    print(f"Collection: {collection}")
    print(f"Retrieve top_k: {args.top_k}  |  score Recall@{k} / Hit / MRR")
    print(f"Query profile: {args.query_profile}")

    query_engine, _ = create_query_engine(query_profile=args.query_profile)

    scores: list[QueryScore] = []
    for example in examples:
        started = time.perf_counter()
        result = await search(
            example.query,
            top_k=args.top_k,
            query_profile=args.query_profile,
            query_engine=query_engine,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        score = score_query(
            query=example.query,
            gold=example.citation_urls,
            results=list(result.results),
            k=k,
        )
        scores.append(score)
        print(f"  scored {example.query!r} in {elapsed_ms:.0f}ms → {'HIT' if score.hit else 'MISS'}")

    summary = EvalSummary(
        dataset_name=dataset_path.stem,
        total_queries=len(scores),
        hit_rate=sum(1 for s in scores if s.hit) / len(scores),
        recall_at_k=sum(s.recall_at_k for s in scores) / len(scores),
        mrr=sum(s.reciprocal_rank for s in scores) / len(scores),
        k=k,
        per_query=tuple(scores),
    )
    _print_summary(summary)
    _print_per_query(summary)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "dataset_name": summary.dataset_name,
            "total_queries": summary.total_queries,
            "hit_rate": summary.hit_rate,
            f"recall_at_{summary.k}": summary.recall_at_k,
            "mrr": summary.mrr,
            "match": "exact_or_parent_page",
            "per_query": [
                {
                    "query": q.query,
                    "gold": list(q.gold),
                    "hit": q.hit,
                    "recall_at_k": q.recall_at_k,
                    "reciprocal_rank": q.reciprocal_rank,
                    "first_hit_rank": q.first_hit_rank,
                    "retrieved_citations": list(q.retrieved_citations),
                }
                for q in summary.per_query
            ],
        }
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote summary JSON to {out}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate hybrid retrieval against citation_url ground truth "
            "(parent-page cites satisfy section golds)."
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
        "--k",
        type=int,
        default=_PRIMARY_K,
        help=f"Cutoff for Recall@k / Hit window (default: {_PRIMARY_K})",
    )
    parser.add_argument(
        "--query-profile",
        default=DEFAULT_QUERY_PROFILE,
        help=f"Vespa query profile (default: {DEFAULT_QUERY_PROFILE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write summary JSON",
    )
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except Exception as exc:
        raise SystemExit(f"Error: {exc}") from exc


if __name__ == "__main__":
    main()
