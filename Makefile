.PHONY: installdeps install-workflows ingest search mcp preprocess-docs inspect-docs test start-examples execute-ingestion
.PHONY: setup-vespa start-vespa verify-vespa stop-vespa reset-vespa migrate-vespa bruno generate-vespa-lock

ifneq (,$(wildcard .env))
include .env
export
endif

MCP_HOST := $(or $(host),127.0.0.1)
MCP_PORT := $(or $(port),8000)
VESPA_CONTAINER := my-search-project-vespa
VESPA_QUERY_PORT := $(or $(VESPA_QUERY_PORT),18080)
VESPA_CONFIG_PORT := $(or $(VESPA_CONFIG_PORT),19072)
VESPA_ENDPOINT := $(or $(VESPA_ENDPOINT),http://localhost:$(VESPA_QUERY_PORT))
VESPA_CONFIG_URL := $(or $(VESPA_CONFIG_URL),http://localhost:$(VESPA_CONFIG_PORT))

## Install dependencies
installdeps:
	uv sync

## Start Vespa and apply schema migrations
setup-vespa: start-vespa migrate-vespa

start-vespa:
	docker compose up -d --wait vespa
	@$(MAKE) verify-vespa

verify-vespa:
	@docker inspect -f '{{.State.Running}}' $(VESPA_CONTAINER) 2>/dev/null | grep -q true \
		|| { echo "error: $(VESPA_CONTAINER) is not running. Run: make start-vespa"; exit 1; }
	@curl -sf $(VESPA_CONFIG_URL)/state/v1/health >/dev/null \
		|| { echo "error: config server not reachable at $(VESPA_CONFIG_URL)"; exit 1; }
	@echo "Vespa OK: container=$(VESPA_CONTAINER) config=$(VESPA_CONFIG_URL) query=http://localhost:$(VESPA_QUERY_PORT)"

stop-vespa:
	docker compose stop vespa

## Stop Vespa and remove this project's data volume (wipes indexed documents; run `make setup-vespa` to start fresh)
reset-vespa:
	docker compose down -v --remove-orphans
	@echo "Vespa stopped and project volume removed. Run: make setup-vespa"

migrate-vespa: verify-vespa
	uv run mistral-vespa migrate --app-dir src/search_app \
		--config-server $(VESPA_CONFIG_URL) \
		--query-port $(VESPA_QUERY_PORT)

## Ingest a file or directory (Search Toolkit Pipeline)
## Usage: make ingest path=sample_data/hello.txt
##        make ingest path=sample_data
##        make ingest path=sample_data/mistral_docs
ingest:
	uv run python -m entrypoints.ingest $(path)

## Fetch docs.mistral.ai pages and save isolated HTML under sample_data/mistral_docs/
## Usage: make preprocess-docs
##        make preprocess-docs url="https://docs.mistral.ai/studio/conversations/reasoning"
preprocess-docs:
	uv run python -m entrypoints.preprocess_docs \
		$(if $(url),--url $(url),--urls-file sample_data/urls.txt) \
		$(if $(output),--output $(output),)

## Inspect markdown/chunks for preprocessed HTML (no Vespa, no API key)
## Usage: make inspect-docs
##        make inspect-docs path=sample_data/mistral_docs/studio/conversations/reasoning.html
##        make inspect-docs content=1
##        make inspect-docs chunk_size=1   # per-section debug view
inspect-docs:
	uv run python -m entrypoints.inspect_docs \
		$(or $(path),sample_data/mistral_docs/studio/conversations/chat-completion.html) \
		$(if $(content),--content,) \
		$(if $(chunk_size),--chunk-size $(chunk_size),) \
		$(if $(chunk_overlap),--chunk-overlap $(chunk_overlap),) \
		$(if $(max_chunks),--max-chunks $(max_chunks),)

## Search the indexed collection (Search Toolkit QueryEngine)
## Usage: make search query="hello world" [top_k=5] [query_profile=hybrid-search]
search:
	uv run python -m entrypoints.search "$(query)" $(if $(top_k),--top-k $(top_k),) $(if $(query_profile),--query-profile $(query_profile),)

## Start the MCP server in HTTP mode
## Usage: make mcp [host=0.0.0.0] [port=8000]
mcp:
	uv run python -m entrypoints.mcp_server --http --host $(MCP_HOST) --port $(MCP_PORT)

## Round-trip a document through the configured backend (skips unless it is set up)
test:
	uv run pytest tests/ -q

## Generate Bruno API files under vespa/bruno/vespa/ (requires WORKSPACE_ROOT in .env)
bruno:
	uv run mistral-vespa bruno \
		--app-dir src/search_app \
		--query-url $(VESPA_ENDPOINT) \
		--document-url $(VESPA_ENDPOINT)

## Optional: write a vespa.lock snapshot for inspection or CI
generate-vespa-lock:
	uv run mistral-vespa generate \
		--app-dir src/search_app \
		--path ./vespa.lock

## Install optional workflows dependency (required for examples/workflows/)
install-workflows:
	uv sync --extra workflows

## Start a worker that registers the example workflows (requires install-workflows)
start-examples: install-workflows
	uv run python -m examples.workflows.worker

## Execute the ingestion workflow via the Mistral Workflows API
## Usage: make execute-ingestion input='{"file_path": "sample_data/hello.txt", "collection_name": "mydocs"}'
execute-ingestion: install-workflows
	uv run python -m examples.workflows.start --workflow document-ingestion $(if $(input),--input '$(input)',--input '{"file_path":"sample_data/hello.txt"}')
