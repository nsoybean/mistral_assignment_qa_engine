"""Search an indexed collection.

Usage:
    python -m entrypoints.search <query> [--top-k 5] [--query-profile hybrid-search]
"""

import argparse
import asyncio

from dotenv import load_dotenv

from search_app import DEFAULT_QUERY_PROFILE
from search_app.query import get_collection_name, search

load_dotenv(override=True)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Search an indexed collection.")
    parser.add_argument("query", help="Search query")
    parser.add_argument(
        "--top-k", type=int, default=5, help="Number of results (default: 5)"
    )
    parser.add_argument(
        "--query-profile",
        default=DEFAULT_QUERY_PROFILE,
        help=f"Vespa query profile to rank with (default: {DEFAULT_QUERY_PROFILE})",
    )
    args = parser.parse_args()

    collection_name = get_collection_name()

    print(f"Query: {args.query!r}")
    print(f"Collection: {collection_name}")
    print(f"Top-K: {args.top_k}")
    print(f"Query profile: {args.query_profile}")

    try:
        result = await search(
            args.query,
            top_k=args.top_k,
            query_profile=args.query_profile,
            collection=collection_name,
        )
    except ValueError as exc:
        raise SystemExit(f"Error: {exc}") from exc

    print(f"\nResults ({len(result.results)}):")
    for i, hit in enumerate(result.results, 1):
        preview = hit.chunk.content
        print(f"\n--- Result {i} ---")
        print(f"Score: {hit.score}")
        print(f"Content: {preview}")
        print(f"Metadata: {hit.chunk.metadata}")


if __name__ == "__main__":
    asyncio.run(main())
