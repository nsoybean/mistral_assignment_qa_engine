import os
from pathlib import Path

from mistralai.search.toolkit.plugins.vespa import VespaApp, VespaClientConfig

# Default query profile applied by the search entrypoint (tuned BM25 + vector
# weights). Defined in ``migrations/001_vespa_create_index_schema.py``.
DEFAULT_QUERY_PROFILE = "hybrid-search"

app = VespaApp(Path(__file__).parent)


def vespa_endpoint() -> str:
    """Query API URL from VESPA_QUERY_PORT (or optional VESPA_ENDPOINT override)."""
    if url := os.environ.get("VESPA_ENDPOINT"):
        return url
    port = os.environ.get("VESPA_QUERY_PORT", "18080")
    return f"http://localhost:{port}"


def get_index(collection_name: str, query_profile: str = DEFAULT_QUERY_PROFILE):
    """Return a live store index for ``collection_name`` on the configured backend."""
    return app.get_search_index(
        VespaClientConfig(endpoint=vespa_endpoint()),
        collection_name=collection_name,
        query_profile=query_profile,
    )
