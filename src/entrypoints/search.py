"""Search an indexed collection.

Usage:
    python -m entrypoints.search <query> [--top-k 5] [--query-profile hybrid-search]
"""

import argparse
import asyncio
import os

from dotenv import load_dotenv
from mistralai.client import Mistral
from mistralai.search.toolkit.embedders import MistralEmbedder
from mistralai.search.toolkit.retrieval import QueryEngine, VectorRetriever
from search_app import get_index

load_dotenv(override=True)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Search an indexed collection.")
    parser.add_argument("query", help="Search query")
    parser.add_argument(
        "--top-k", type=int, default=5, help="Number of results (default: 5)"
    )
    parser.add_argument(
        "--query-profile",
        default="hybrid-search",
        help="Vespa query profile to rank with (default: hybrid-search)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        raise SystemExit("Error: MISTRAL_API_KEY is not set. Check your .env file.")

    collection_name = os.environ.get("COLLECTION_NAME", "exampledocs")
    mistral_client = Mistral(
        api_key=api_key,
        server_url=os.getenv("MISTRAL_API_URL", "https://api.mistral.ai"),
    )
    vector_store = get_index(collection_name, query_profile=args.query_profile)
    embedder = MistralEmbedder(client=mistral_client)
    query_engine = QueryEngine(
        retriever=[VectorRetriever(client=vector_store, embedder=embedder)],
    )

    print(f"Query: {args.query!r}")
    print(f"Collection: {collection_name}")
    print(f"Top-K: {args.top_k}")
    print(f"Query profile: {args.query_profile}")

    result = await query_engine.search(
        query=args.query,
        top_k=args.top_k,
        include_metadata=True,
        include_content=True,
    )

    print(f"\nResults ({len(result.results)}):")
    for i, hit in enumerate(result.results, 1):
        preview = hit.chunk.content[:500]
        if len(hit.chunk.content) > 500:
            preview += "..."
        print(f"\n--- Result {i} ---")
        print(f"Score: {hit.score}")
        print(f"Content: {preview}")
        print(f"Metadata: {hit.chunk.metadata}")


if __name__ == "__main__":
    asyncio.run(main())
