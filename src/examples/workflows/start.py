"""Trigger a workflow execution from the command line.

Requires the optional workflows extra:
    uv sync --extra workflows
"""
# ruff: noqa: E402

import argparse
import asyncio
import json
import os

from dotenv import load_dotenv

load_dotenv(override=True)

from mistralai.extra.workflows import WorkflowEncodingConfig, configure_workflow_encoding
from mistralai.workflows.client import get_mistral_client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trigger a workflow execution.")
    parser.add_argument(
        "--workflow",
        default="document-ingestion",
        help="Workflow name (default: document-ingestion)",
    )
    parser.add_argument(
        "--input",
        default=r"{}",
        help=r'Input data as a JSON string (e.g. \'{"file_path": "sample_data/hello.txt"}\')',
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    try:
        raw_input = json.loads(args.input)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Error: invalid JSON for --input: {exc.args[0]}\n"
            f"  Received: {args.input!r}\n"
            f'  Example:  --input \'{{"file_path": "sample_data/hello.txt"}}\''
        ) from exc

    raw_input = raw_input or {}
    if not isinstance(raw_input, dict):
        raise SystemExit(
            f"Error: --input must be a JSON object, got {type(raw_input).__name__}"
        )

    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        raise SystemExit("Error: MISTRAL_API_KEY is not set. Check your .env file.")

    deployment_name = os.environ.get("DEPLOYMENT_NAME")
    if not deployment_name:
        raise SystemExit(
            "Error: DEPLOYMENT_NAME is not set. Add it to your .env file, e.g.:\n"
            "  DEPLOYMENT_NAME=my-search-project"
        )

    client = get_mistral_client(
        api_key=api_key,
        server_url=os.environ.get("MISTRAL_API_URL", "https://api.mistral.ai"),
    )

    await configure_workflow_encoding(WorkflowEncodingConfig(), client=client)

    try:
        result = await client.workflows.execute_workflow_and_wait_async(
            workflow_identifier=args.workflow,
            input=raw_input,
            deployment_name=deployment_name,
        )
    except Exception as exc:
        if "Workflow not found" in str(exc) or "404" in str(exc):
            raise SystemExit(
                f"Error: workflow '{args.workflow}' not found.\n"
                "Start the examples worker first (separate terminal):\n"
                "  make start-examples\n"
                "Then retry this command."
            ) from exc
        raise

    print(f"Result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
