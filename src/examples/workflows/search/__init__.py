"""Workflows × Search integration example.

Demonstrates wrapping the search-starter-app ingestion pipeline in a
Mistral Workflow for durability, observability, and HITL support.
"""

from .workflow import IngestionWorkflow

__all__ = ["IngestionWorkflow"]
