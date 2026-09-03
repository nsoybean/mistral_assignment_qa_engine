# Interview notes — Mistral docs search

Design decisions, trade-offs, and demo script for the take-home. Focus is on **what I learned and chose**. Implementation is assisted by AI.

---

## Summary

Mistral docs are React/Next.js pages — raw HTML is noisy (sidebar, TOC) and incomplete (inactive code tabs, collapsed FAQ answers live in RSC payloads, not the DOM). The Q&A engine splits into: 
**(1) parsing and ingestion via script** — fetch, domain-specific HTML cleanup, heading-aware chunking with section deep-link citations, embed and index into Vespa; 
**(2) retrieval and answer generation** — hybrid search via CLI (`make search`) or an agent over MCP (Vibe). Retrieval uses vespa's starter hybrid query profile.

---

## What I learned

1. **The Search Toolkit is document-oriented.** Plain-text and OCR paths both sit on `FileLoader`. Live `http(s)` URLs route to OCR, which I feel is non-optimal for structured documentation pages — you lose section anchors and still miss client-hydrated content. 

2. **HTML extraction is the easy part; cleaning is the hard part.** `HTMLExtractor` + `MarkdownifyConverter` handle conversion once you feed them isolated article HTML. The bespoke work is in `isolate_article` to clean up Mistral documentation and extract anchor links before any conversion (into markdown) is done.

3. **Chunk boundaries should follow doc structure, not arbitrary token counts.** `MarkdownTextSplitter` on `#` / `##` headers matches how Mistral docs are written. Overlap tuning barely mattered — every section on my corpus sits under `chunk_max_size`.

4. **Hybrid retrieval fits real user queries.** Users mix exact terms (`mistral-small-3.1`, `tool calling`) with semantic paraphrase (`how do I parse thinking chunks`). Starter Vespa `hybrid-search` profile handled both without tuning.

---

## Architecture

```
preprocess (output committed to repo)
  docs.mistral.ai URL → fetch → isolate_article → sample_data/mistral_docs/*.html + .meta.json

ingest (reviewer runs offline)
  local HTML → DocsHTMLFileLoader → HTMLExtractor → MarkdownTextSplitter → DocsChunkEnricher → embed → Vespa

Delivery
  MCP server (search, open, navigate, grep) → Vibe / agent synthesizes answer with citation_url metadata
```

**Corpus:** 13 studio/conversations pages under `sample_data/urls.txt` — first major "Build" section, enough to demo keyword + semantic + anchored citations.

**Key modules:** 

preprocess + chunking:
[src/entrypoints/preprocess_docs.py](src/entrypoints/preprocess_docs.py), [src/entrypoints/ingest.py](src/entrypoints/ingest.py)

query: 
[src/entrypoints/search.py](src/entrypoints/search.py)

mcp:
[src/entrypoints/mcp_server.py](src/entrypoints/mcp_server.py)

---

## Decisions & trade-offs

| Decision | Chose | Rejected / alternative | Why |
| -------- | ----- | ---------------------- | --- |
| Extraction | `HTMLExtractor` on cleaned article HTML | Default OCR ingest | Preserves headings, markdown, and anchor ids; ~1.2 MB raw HTML → ~9k chars markdown |
| Ingest model | Two-stage: preprocess → local files → ingest locally | Live URL ingest at review time | Deterministic for reviewer; no network dependency; stable evaluation |
| HTML cleanup | Bespoke `isolate_article` (scope `<main>`, lift sr-only headings, strip TOC/tab chrome) | Raw full-page extraction | Raw page indexes sidebar nav; 10× more HTML, worse chunks, no citation metadata |
| Chunk split level | `#` + `##` only (page title + first section level) | Also split on `###` | Mistral documentation specific: avoids over-fragmenting; keeps one chunk per major section |
| Overlap | 100 chars (~10% of chunk_size) | Heavy overlap tuning | Never triggered — sections are 300–1300 chars, all under `chunk_max_size` (3000) |
| Orphan chunks | Sequential metadata inheritance (heading + `citation_url`) | Leave orphans at page-level URL | Code fences and other heading-less splits inherit parent section |
| Citation anchors | DOM section ids from pre-process | - | Deterministic; matches "Copy section link" URLs on live docs |
| Retrieval | Hybrid BM25 + vector (starter defaults) | Vector-only or BM25-only | Handles term look up + natural-language questions |
| MCP scope | Retrieval + navigation tools; ingest via CLI | Agent MCP ingest tool | Batch ingest via `urls.txt` is enough for assignment; |
| Agent loop | MCP delivery; agent synthesizes answers | own agentic loop | Focus on ingestion and retrieving pipeline. Use coding agent (vibe) for orchestration |

---

## Chunking & citations
- **split by header/structure** — suitable for documentation pages with clear structure
- **`strip_headers=False`** — section titles stay in chunk text to provide retrieval context
- **Section metadata inheritance** — subsequent chunks inherits latest (earlier) anchor tags

---

## Retrieval evaluation

**How:** label queries with expected section `citation_url`s in [`sample_data/eval_queries.jsonl`](sample_data/eval_queries.jsonl), then `make eval-retrieval` (uses Search Toolkit `RetrieverEvaluator`, matching on chunk metadata `citation_url`).

**Starter queries:**

| Query | Gold citation(s) | What it demonstrates |
| ----- | ---------------- | -------------------- |
| `how do i handle thinking chunk` | `reasoning#handling-thinking-chunks` | Semantic section match |
| `how do i pin a prompt registry version` | `prompt-registry#pinning-a-version` | Keyword + section anchor |
| `…prompt registry… and set reasoning to high` | prompt-registry + reasoning | Multi-hop / multi-page |
| `five steps of function calling` | `function-calling#five-steps` | Structural heading match |

**Metrics (Hit rate / Recall@5 / MRR only):** Hit = any gold section found; Recall@5 = all golds in top 5 (multi-hop); MRR = how high the first gold ranked. See README “Evaluate retrieval”.

**Where I expect difficulty:**

- Answers requiring evidence from **multiple chunks/pages**
- Overly complex queries needing **query rewriting** before retrieval

**MCP tools:** Demo focuses on `search` + citations. `open` / `grep` / `navigate` are available for agentic search.

---

## Known limitations & mitigations

| Limitation | Impact | Mitigation |
| ---------- | ------ | ---------- |
| Static `fetch()` cannot recover client-hydrated content (tabs, accordions) — content sits in RSC scripts until the UI is interacted with | all hidden data will be absent | Headless browser (e.g. Playwright) to expand tabs and accordions before extract; alternatively ingest MDX from `platform-docs-public` |
| Duplicate code blocks | Light/dark theme → identical Python blocks indexed twice | Post-chunk dedup before embed; low practical impact — agent can ignore duplicates |
| Images in docs | Processed as text/alt only; no visual retrieval | VLM summarization of images → text description + embed; store hosted image URL in metadata |
| Bespoke DOM parsing | Breaks if Mistral redesigns docs layout | - |
| `llms.txt` outdated | Points to old `/docs/capabilities/...` paths | Use `urls.txt` or sitemap / `platform-docs-public` walk |

---

## AI-assisted development — what I owned vs delegated

**AI helped with:** Search Toolkit pipeline wiring, Make targets, fast iteration on extraction experiments, test scaffolding.

**I steered / validated manually:**

- DOM structure discovery (`section-tab`, sr-only headings) — risk of overfitting to one page
- Architecture choice: OCR vs HTMLExtractor (wrong default for this use case)
- Chunking strategy and granularity (`##` only), citation inheritance logic, corpus selection

---

## Reviewer quick-start

```bash
make setup-vespa
make ingest path=sample_data/mistral_docs
make test          # offline extraction tests
make search query="how do i handle thinking chunk" # see topK chunks
vibe               # MCP agent to use 'search' tool
```

See [README.md](README.md) for full setup and MCP tool reference.
