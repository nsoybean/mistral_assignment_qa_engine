"""Start a worker that registers all example workflows for search-starter-app."""
# ruff: noqa: E402

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv(override=True)

import mistralai.workflows as workflows

from examples.workflows.search import IngestionWorkflow

EXAMPLE_WORKFLOWS = [IngestionWorkflow]


async def main() -> None:
    if not os.environ.get("DEPLOYMENT_NAME"):
        print(
            "Error: DEPLOYMENT_NAME is not set. Add it to your .env file, e.g.:\n"
            "  DEPLOYMENT_NAME=my-search-project",
            file=sys.stderr,
        )
        raise SystemExit(1)

    names = [wf.__name__ for wf in EXAMPLE_WORKFLOWS]
    print(
        f"Starting worker with {len(EXAMPLE_WORKFLOWS)} example(s): {', '.join(names)}"
    )
    await workflows.run_worker(EXAMPLE_WORKFLOWS)


if __name__ == "__main__":
    asyncio.run(main())
