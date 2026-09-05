# Technical walkthrough — Mistral documentation Q&A

## Project objective

Build a search-powered Q&A engine over Mistral's public documentation that returns accurate, grounded answers with links to the relevant source sections.

This implementation prioritizes retrieval quality over corpus breadth:

- 21 curated Studio documentation pages: 13 Conversations pages and 8 Agents pages
- custom extraction for the structure of `docs.mistral.ai`
- heading-aware chunks with section-level citation metadata
- hybrid lexical and semantic retrieval in Vespa
- retrieval and navigation tools exposed to an answering agent over MCP
- an 18-query citation-based retrieval evaluation

The repository owns document preparation, ingestion, retrieval, citation metadata, evaluation, and MCP tools. The final natural-language answer is synthesized by an MCP client such as Vibe or Claude Code.

## End-to-end architecture

```text
Preprocess (committed, deterministic output)

docs.mistral.ai URL
  → fetch page
  → isolate and clean the article
  → preserve headings and DOM anchor IDs
  → save HTML and a .meta.json sidecar

Ingest

local HTML
  → DocsHTMLFileLoader
  → HTMLExtractor
  → MarkdownTextSplitter
  → DocsChunkEnricher
  → MistralEmbedder
  → Vespa

Retrieve and answer

user question
  → Mistral query embedding
  → Vespa hybrid ranking
  → cited chunks
  → MCP search/navigation tools
  → agent-generated grounded answer
```

## 1. Document preparation

Mistral's documentation pages are React/Next.js pages. Ingesting their raw HTML introduces several quality problems:

- navigation and table-of-contents content can dominate the article
- some section headings are visually hidden inside tab controls
- light and dark themes can produce duplicate code blocks
- canonical section anchor IDs can be lost during generic extraction
- inactive tabs and collapsed content may not exist in the static DOM

The preprocessing step applies domain-specific cleanup before conversion to Markdown:

1. Fetch the requested documentation URL.
2. Scope extraction to the main article.
3. Remove navigation, table-of-contents, and tab chrome.
4. Lift hidden tab headings into the article and retain their IDs.
5. Remove known theme duplicates.
6. Save cleaned HTML plus metadata containing the canonical URL, title, breadcrumbs, and heading-to-anchor mapping.

The prepared corpus is committed under [`sample_data/mistral_docs/`](sample_data/mistral_docs/). This makes reviewer setup deterministic and allows extraction and chunking to be inspected without network access.

Implementation:

- [`preprocess_docs.py`](src/entrypoints/preprocess_docs.py)
- [`docs_html.py`](src/search_app/docs_html.py)
- [`urls.txt`](sample_data/urls.txt)

## 2. Chunking and citation strategy

Documentation structure is the primary chunk boundary. The Markdown splitter separates content at `#`, `##`, and `###` headings rather than relying only on fixed-size windows.

Current configuration:

| Parameter | Value | Role |
| --- | ---: | --- |
| Heading levels | `#`, `##`, `###` | Preserve page, section, and subsection boundaries |
| Target chunk size | 800 characters | Merge very small adjacent sections |
| Maximum chunk size | 2,000 characters | Split unusually long sections |
| Overlap | 100 characters | Preserve context when a long section is subdivided |
| `strip_headers` | `false` | Keep headings in text used for retrieval |

Each chunk is enriched with:

- canonical source URL
- page title
- current heading
- section-level `citation_url`

The citation URL uses the original DOM anchor when one was captured during preprocessing. Reconstructing anchors from heading text was avoided because generated slugs do not always match the live site's IDs.

Some split chunks, especially code fences, do not start with a heading. These chunks inherit the latest section heading and citation URL so that standalone code remains attributable to its surrounding documentation section.

The same pipeline can be inspected before indexing:

```bash
make inspect-docs content=1 \
  path=sample_data/mistral_docs/studio/conversations/reasoning.html
```

## 3. Ingestion and storage

The Search Toolkit pipeline is:

```text
DocsHTMLFileLoader
  → HTMLExtractor
  → MarkdownTextSplitter
  → DocsChunkEnricher
  → MistralEmbedder
  → Vespa index
```

`DocsHTMLFileLoader` reads each HTML file together with its metadata sidecar and uses the canonical documentation URL as the source identifier. Sidecar files are explicitly excluded from ingestion.

The repository retains plain-text and OCR fallbacks inherited from the starter application, but the curated documentation corpus follows the dedicated HTML path.

Implementation:

- [`ingest.py`](src/entrypoints/ingest.py)
- [`docs_html.py`](src/search_app/docs_html.py)
- [`Vespa schema migration`](src/search_app/migrations/001_vespa_create_index_schema.py)

## 4. Hybrid retrieval

Real documentation queries mix lexical and semantic requirements:

- exact names such as model IDs, parameters, and API methods benefit from BM25
- paraphrases such as “how do I handle thinking chunks?” benefit from vector similarity

Vespa's `hybrid-search` query profile combines both signals. The ranking configuration lives in the Vespa schema rather than being duplicated by each caller, so the CLI, evaluation runner, and MCP server use consistent retrieval behavior.

```bash
make search query="how do i handle thinking chunk"
```

The result contains ranked chunk content and metadata, including the deep-link citation used by the answering agent.

Implementation:

- [`query.py`](src/search_app/query.py)
- [`search.py`](src/entrypoints/search.py)
- [`Vespa schema migration`](src/search_app/migrations/001_vespa_create_index_schema.py)

## 5. Agent-facing retrieval

The MCP server separates retrieval from answer orchestration. It exposes:

| Tool | Purpose |
| --- | --- |
| `search` | Run hybrid retrieval and return ranked, cited chunks |
| `open` | Expand the context window around a search hit |
| `grep` | Search lexically within one indexed page |
| `navigate` | Move forward or backward through a document |
| `read` | Retrieve a known offset range |
| `delete` | Remove a page and its chunks from the index |

This allows a client to use a short search result for a direct question, or gather additional evidence for a multi-part question before composing an answer.

Batch ingestion remains a CLI operation for this assignment. The MCP `ingest` tool directs users to that path rather than implementing a second ingestion interface.

Implementation:

- [`mcp_server.py`](src/entrypoints/mcp_server.py)
- [`.mcp.json`](.mcp.json)
- [Vibe configuration](.vibe/config.toml)

## 6. Retrieval evaluation

Retrieval is evaluated independently of answer wording. Each of the 18 evaluation questions is labeled with one or more expected citation URLs.

Reported metrics:

| Metric | Question answered |
| --- | --- |
| Hit Rate | Did the top five contain at least one expected source? |
| Recall@5 | Did the top five recover all expected sources? |
| MRR | How highly was the first expected source ranked? |

The engine retrieves ten results so MRR can observe lower ranks; Hit Rate and Recall are scored at five.

### Chunk-size experiment

The heading strategy was held constant while chunk parameters were varied:

| Configuration | Target / maximum / overlap | Chunks | Hit Rate | Recall@5 | MRR |
| --- | --- | ---: | ---: | ---: | ---: |
| Small | 400 / 1,000 / 50 | 360 | 0.889 | 0.889 | 0.763 |
| **Selected** | **800 / 2,000 / 100** | **196** | **0.944** | **0.944** | **0.769** |
| Large | 1,500 / 4,000 / 200 | 104 | 0.889 | 0.889 | 0.718 |

Small chunks fragmented short introductions and code context. Large chunks merged unrelated subsections and produced less-specific representations. The selected configuration gave the best overall retrieval result.

Overlap had little measurable effect on this corpus because most heading-bounded sections do not exceed the maximum chunk size. The experiment indicates that structural boundaries contributed more than overlap tuning.

Evaluation assets:

- [`eval_queries.jsonl`](sample_data/eval_queries.jsonl)
- [`eval_retrieval.py`](src/entrypoints/eval_retrieval.py)

```bash
make eval-retrieval
```

## 7. Verification

The automated tests cover:

- article isolation and removal of page chrome
- extraction of hidden section headings
- removal of known theme duplicates
- heading-aware splitting
- use of original anchor IDs in citation URLs
- citation inheritance for heading-less chunks
- filtering metadata sidecars from ingestion
- hierarchical citation matching and metric calculations
- MCP context-window behavior
- an optional Vespa index-and-search round trip

```bash
make test
```

Tests are under [`tests/`](tests/).

## 8. Design trade-offs and limitations

| Limitation | Impact | Production direction |
| --- | --- | --- |
| Static fetch does not render interactive content | Inactive tabs or collapsed content can be absent | Ingest source MDX, or use a browser-based preprocessor |
| Cleanup depends on the current docs DOM | A site redesign can invalidate selectors | Add extraction monitoring and fixtures from representative layouts |
| Corpus is deliberately focused | Retrieval outside the selected Studio pages is unavailable | Expand coverage incrementally with evaluation labels |
| Images are represented only by available text | Questions about diagrams or screenshots are unsupported | Generate image descriptions with a vision model and retain image URLs |
| Generation is delegated to the MCP client | Answer behavior depends on the consuming agent | Add a controlled generation layer with citation validation and abstention |
| Evaluation set is small | Metrics are directional rather than production-level evidence | Add adversarial, no-answer, multi-hop, and Agents-focused questions |

## 9. Reproduction and demonstration

```bash
# Install dependencies and start Vespa
make installdeps
make setup-vespa

# Ingest the committed corpus
make ingest path=sample_data/mistral_docs

# Inspect retrieval
make search query="how do i handle thinking chunk"

# Run retrieval evaluation and tests
make eval-retrieval
make test

# Start an MCP-capable agent
vibe
```

Suggested demonstration questions:

1. `How do I handle thinking chunks?`
2. `How do I use a fixed prompt registry version and set reasoning to high?`
3. `Does Mistral 3 14B support tool calling?`

## Code map

| Area | Location |
| --- | --- |
| Live-page preprocessing | [`src/entrypoints/preprocess_docs.py`](src/entrypoints/preprocess_docs.py) |
| HTML cleanup, splitting, and enrichment | [`src/search_app/docs_html.py`](src/search_app/docs_html.py) |
| Ingestion routing | [`src/entrypoints/ingest.py`](src/entrypoints/ingest.py) |
| Retrieval construction | [`src/search_app/query.py`](src/search_app/query.py) |
| CLI search | [`src/entrypoints/search.py`](src/entrypoints/search.py) |
| Retrieval evaluation | [`src/entrypoints/eval_retrieval.py`](src/entrypoints/eval_retrieval.py) |
| MCP tools | [`src/entrypoints/mcp_server.py`](src/entrypoints/mcp_server.py) |
| Vespa schema and ranking | [`src/search_app/migrations/001_vespa_create_index_schema.py`](src/search_app/migrations/001_vespa_create_index_schema.py) |
| Golden queries | [`sample_data/eval_queries.jsonl`](sample_data/eval_queries.jsonl) |
| Automated tests | [`tests/`](tests/) |
