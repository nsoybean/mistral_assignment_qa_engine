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

3. **Chunk boundaries should follow doc structure, not arbitrary token counts.** `MarkdownTextSplitter` on `#` / `##` / `###` headers matches how Mistral docs are written. Size tuning matters less than getting the split boundaries right.

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

**Corpus:** 22 studio pages under `sample_data/urls.txt` — conversations + agents sections, enough to demo keyword + semantic + anchored citations.

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
| Chunk split level | `#` + `##` + `###` (page title + section + subsection) | `##`-only | Finer granularity needed for sections with distinct subsections (e.g. function-calling steps) |
| Overlap | 100 chars (~10% of chunk_size) | Heavy overlap tuning | Inert on this corpus — sections sit under `chunk_max_size` so sub-splitting rarely fires |
| Orphan chunks | Sequential metadata inheritance (heading + `citation_url`) | Leave orphans at page-level URL | Code fences and other heading-less splits inherit parent section |
| Citation anchors | DOM section ids from pre-process | - | Deterministic; matches "Copy section link" URLs on live docs |
| Retrieval | Hybrid BM25 + vector (starter defaults) | Vector-only or BM25-only | Handles term look up + natural-language questions |
| MCP scope | Retrieval + navigation tools; ingest via CLI | Agent MCP ingest tool | Batch ingest via `urls.txt` is enough for assignment |
| Agent loop | MCP delivery; agent synthesizes answers | own agentic loop | Focus on ingestion and retrieving pipeline. Use coding agent (vibe) for orchestration |

---

## Chunking strategy & hyperparameter tuning

### Strategy (biggest contributor)

The primary chunking decision is **split by document headings**, not arbitrary token counts. Mistral docs have clear `## Section` / `### Subsection` structure, so `MarkdownTextSplitter` on header boundaries preserves section integrity and maps 1:1 to citation anchors.

- **`strip_headers=False`** — section titles stay in chunk text, giving the embedding model retrieval context
- **Section metadata inheritance** — chunks without a leading heading inherit the parent section's `citation_url`

### Hyperparameters we can tweak

| Param | Role | Current value |
| ----- | ---- | ------------- |
| `HEADERS_TO_SPLIT_ON` | Which heading levels create chunk boundaries | `#`, `##`, `###` |
| `chunk_size` | Merge target — small adjacent sections merge up to this | 800 |
| `chunk_max_size` | Cap before sub-splitting a long section | 2000 |
| `chunk_overlap` | Overlap between sub-chunks (only fires when `chunk_max_size` triggers) | 100 |

### Qualitative tuning (inspect-docs)

Used `make inspect-docs chunk_size=1` (per-section view) to understand corpus shape:
- Mistral doc sections are typically 300–1300 chars
- Documentation often have small h2→h3 transition paragraphs (e.g. a one-line intro before code). Setting too small `chunk_size` will result in them being a chunk of their own: meaningless. 
- Too large `chunk_size` merges unrelated `###` sections. i.e. 'pollutes' the context of a chunk.

### Quantitative validation (eval-retrieval)

Swept three configs on an 18-query golden set (`sample_data/eval_queries.jsonl`) with `make eval-retrieval`:

| Config | chunk_size | chunk_max_size | overlap | Chunks | Hit rate | Recall@5 | MRR |
| ------ | ---------- | -------------- | ------- | ------ | -------- | -------- | ---- |
| Small | 400 | 1000 | 50 | 360 | 0.889 | 0.889 | 0.763 |
| **Default** | **800** | **2000** | **100** | **196** | **0.944** | **0.944** | **0.769** |
| Large | 1500 | 4000 | 200 | 104 | 0.889 | 0.889 | 0.718 |

**Findings:**

1. **Default (800/2000) is the sweet spot** — best Hit rate and MRR. Sections merge cleanly without losing identity.
2. **Too small (400/1000)** — 360 chunks (2× default). Short sections split mid-code-block, diluting embedding quality. Hit rate drops.
3. **Too large (1500/4000)** — 104 chunks. Unrelated h3 sections merge into blobs. MRR drops because merged chunks rank lower (less specific embeddings).
4. **Overlap is inert on this corpus** — sections sit well under `chunk_max_size`, so sub-splitting rarely triggers. Confirmed by comparing overlap 50 vs 200 with no metric change.

### Takeaway

Heading-based splitting is the biggest contributor — it structures chunks around concepts rather than byte counts. Size tuning is secondary but validates the choice: the default config outperforms both smaller and larger alternatives on retrieval metrics.

---

## Retrieval evaluation

**How:** label queries with expected section `citation_url`s in [`sample_data/eval_queries.jsonl`](sample_data/eval_queries.jsonl), then `make eval-retrieval`.

**Metrics (Hit rate / Recall@5 / MRR):** Hit = any gold section found; Recall@5 = all golds in top 5 (multi-hop); MRR = how high the first gold ranked. Matching allows a **page-level** `citation_url` to satisfy a gold `#section` on the same page (chunk merge often leaves `##` mid-chunk so enrichment inherits the parent). Label ideal deep links in `eval_queries.jsonl` anyway. See README "Evaluate retrieval".

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

**AI helped with:** Search Toolkit pipeline wiring, Make targets, fast iteration on extraction experiments, test scaffolding, evaluation framework setup.

**I steered / validated manually:**

- DOM structure discovery (`section-tab`, sr-only headings) — risk of overfitting to one page
- Architecture choice: OCR vs HTMLExtractor (wrong default for this use case)
- Chunking strategy and granularity, citation inheritance logic, corpus selection

---

## Reviewer quick-start

```bash
make setup-vespa
make ingest path=sample_data/mistral_docs
make test           # offline extraction tests
make eval-retrieval # retrieval eval → Hit rate / Recall@5 / MRR
make search query="how do i handle thinking chunk"
vibe                # MCP agent to use 'search' tool
```

See [README.md](README.md) for full setup and MCP tool reference.
