# Interview notes — Mistral docs search

Design decisions, trade-offs, and demo script for the take-home. Focus is on **what I learned and chose**. Implementation is assisted by AI.

---

## 30-second pitch

Mistral docs are React/Next.js pages — raw HTML is noisy (sidebar, TOC) and incomplete (inactive code tabs, collapsed FAQ answers live in RSC payloads, not the DOM). I built a **two-stage ingest pipeline**: fetch + domain-specific HTML cleanup → committed local corpus → toolkit markdown conversion → **heading-aware chunks with section deep-link citations** → hybrid Vespa retrieval → **MCP for agent-driven Q&A**. Retrieval works out of the box; the hard part was upstream ingestion quality.

---

## What I learned

1. **The Search Toolkit is document-oriented.** Plain-text and OCR paths both sit on `FileLoader`. Live `http(s)` URLs route to OCR, which is wrong for structured docs pages — you lose section anchors and still miss client-hydrated content. **Local files as source of truth** is the better fit.

2. **OCR is for PDFs/scans, not live HTML.** Default MCP ingest uses `MistralOCRExtractor` for non-text files. On docs URLs it either fails (download as octet-stream) or returns megabytes of raw HTML as one blob — no markdown structure, no anchor metadata.

3. **HTML extraction is the easy part; cleaning is the hard part.** `HTMLExtractor` + `MarkdownifyConverter` handle conversion once you feed them isolated article HTML. The bespoke work is `isolate_article`: Mistral Studio uses `div[data-slot=section-tab]` + sr-only headings instead of plain `<h2>` tags.

4. **Chunk boundaries should follow doc structure, not arbitrary token counts.** `MarkdownTextSplitter` on `#` / `##` headers matches how Mistral docs are written. Overlap tuning barely mattered — every section on my corpus sits under `chunk_max_size`, so separator sub-split never ran.

5. **Hybrid retrieval fits real user queries.** Users mix exact terms (`mistral-small-3.1`, `tool calling`) with semantic paraphrase (`how do I parse thinking chunks`). Starter Vespa `hybrid-search` profile handled both without tuning.

---

## Architecture

```
Stage 1 — preprocess (optional network; output committed to repo)
  docs.mistral.ai URL → fetch → isolate_article → sample_data/mistral_docs/*.html + .meta.json

Stage 2 — ingest (reviewer runs offline)
  local HTML → DocsHTMLFileLoader → HTMLExtractor → MarkdownTextSplitter → DocsChunkEnricher → embed → Vespa

Delivery
  MCP server (search, open, navigate, grep) → Vibe / agent synthesizes answer with citation_url metadata
```

**Corpus:** 13 Studio conversation pages under `sample_data/urls.txt` — first major "Build" section, enough to demo keyword + semantic + anchored citations.

**Key modules:** `src/search_app/docs_html.py` (preprocess + chunking), `src/entrypoints/preprocess_docs.py`, `src/entrypoints/ingest.py`, `src/entrypoints/mcp_server.py`.

---

## Decisions & trade-offs

| Decision | Chose | Rejected / alternative | Why |
| -------- | ----- | ---------------------- | --- |
| Extraction | `HTMLExtractor` on cleaned article HTML | Default OCR ingest | Preserves headings, markdown, and anchor ids; ~1.2 MB raw HTML → ~9k chars markdown |
| Ingest model | Two-stage: preprocess → commit → ingest locally | Live URL ingest at review time | Deterministic for reviewer; no network dependency; stable evaluation |
| HTML cleanup | Bespoke `isolate_article` (scope `<main>`, lift sr-only headings, strip TOC/tab chrome) | Raw full-page extraction | Raw page indexes sidebar nav; 10× more HTML, worse chunks, no citation metadata |
| Source upgrade path | Ship HTML pipeline; document MDX (`platform-docs-public`) as v2 | Jump straight to MDX | MDX is source of truth (all tab languages) but adds external repo dep + custom MDX→markdown script |
| Chunk split level | `#` + `##` only (page title + first section level) | Also split on `###` | Mistral-specific: avoids over-fragmenting; keeps one chunk per major section |
| Overlap | 100 chars (~10% of chunk_size) | Heavy overlap tuning | Never triggered — sections are 300–1300 chars, all under `chunk_max_size` (4096) |
| Orphan chunks | Sequential metadata inheritance (heading + `citation_url`) | Leave orphans at page-level URL | Code fences and other heading-less splits inherit parent section — applies to **all** orphans, not just code |
| Citation anchors | DOM section ids from pre-process | Slugify-only fallback | Deterministic; matches "Copy section link" URLs on live docs |
| Retrieval | Hybrid BM25 + vector (starter defaults) | Vector-only or BM25-only | Handles model names/API terms and natural-language questions |
| MCP scope | Retrieval + navigation tools; ingest via CLI | Live MCP URL ingest | Batch ingest via `urls.txt` is enough for assignment; production would ingest in-memory via same modules, not save HTML locally |
| Agent loop | MCP delivery; agent synthesizes answers | Custom RAG CLI in scope | Interviewer focus is IR quality; MCP already exposes search + context expansion |

---

## Chunking & citations (talking points)

- **`strip_headers=False`** — section titles stay in chunk text so the embedder sees context (e.g. `## Handling thinking chunks`).
- **Section metadata inheritance** — when `MarkdownTextSplitter` produces a chunk with no leading heading (typical for code fences), it inherits the last real section's `heading` and `citation_url`. Guard: only markdown headings (`##`+) or the page-title h1 advance state — Python `# comments` do not reset it.
- **Inspect without Vespa:** `make test` or `make inspect-docs chunk_size=1` to print per-section chunks and citation URLs.

---

## Retrieval evaluation

**Queries that worked well:**

| Query | What it demonstrates |
| ----- | -------------------- |
| `does mistral 3 14B support tool calling?` | Hybrid keyword match on model name + capability |
| `how do i handle thinking chunk, show code snippet` | Semantic match + section-level citation with code |

**Where I expect difficulty (not yet tested deeply):**

- Answers requiring evidence from **multiple chunks/pages**
- Overly complex queries needing **query rewriting** before retrieval

**Not tuned:** Vespa ranking weights — starter `hybrid-search` profile is sufficient for this corpus. First tuning lever would be BM25 vs vector weight on queries with exact API identifiers.

**MCP tools:** Demo focuses on `search` + citations. `open` / `grep` / `navigate` are available for multi-hop evidence gathering — I have not yet picked a query that clearly needs them.

---

## Known limitations & mitigations

| Limitation | Impact | Mitigation |
| ---------- | ------ | ---------- |
| Inactive code tabs not in DOM | TypeScript/curl snippets missing; only default (Python) tab server-rendered | MDX ingest from `platform-docs-public`, or Playwright to expand tabs |
| Next.js RSC payloads | FAQ answers, some tab content only in `<script>` flight data | Parse RSC in pre-process, headless browser, or MDX source |
| Duplicate code blocks | Light/dark theme → identical Python blocks indexed twice | Post-chunk dedup before embed; low practical impact — agent can ignore duplicates |
| Images in docs | Processed as text/alt only; no visual retrieval | VLM summarization of images → text description + embed; store hosted image URL in metadata |
| Bespoke DOM parsing | Breaks if Mistral redesigns docs layout | Fixture tests on committed HTML; refresh via `make preprocess-docs` |
| `llms.txt` outdated | Points to old `/docs/capabilities/...` paths | Use `urls.txt` or sitemap / `platform-docs-public` walk |

**Proactive mention in demo:** HTML crawl cannot recover client-hydrated content — this is a fundamental static-fetch limit, not a chunking bug. MDX or headless browser is the production path.

---

## AI-assisted development — what I owned vs delegated

**AI helped with:** Search Toolkit pipeline wiring, Make targets, fast iteration on extraction experiments, test scaffolding.

**I steered / validated manually:**

- DOM structure discovery (`section-tab`, sr-only headings) — risk of overfitting to one page
- Architecture choice: OCR vs HTMLExtractor (wrong default for this use case)
- Chunk granularity (`##` only), citation inheritance logic, corpus selection

---

## 15-minute demo script

| Time | Step | Command / action |
| ---- | ---- | ---------------- |
| 0:00 | Context | 30-second pitch (above) |
| 1:00 | Offline quality proof | `make test` — extraction/chunking tests without Vespa |
| 2:00 | Ingest committed corpus | `make ingest path=sample_data/mistral_docs` |
| 4:00 | CLI retrieval | `make search query="does mistral 3 14B support tool calling?"` |
| 6:00 | Anchored citation | `make search query="how do i handle thinking chunk"` — show `citation_url` in results |
| 8:00 | Agent Q&A | Vibe / MCP — same queries; agent synthesizes with citations |
| 12:00 | Honest limits | Mention inactive tabs, RSC/FAQ gap, MDX upgrade path |
| 14:00 | Optional deep-dive | `make inspect-docs path=sample_data/mistral_docs/studio/conversations/reasoning.html chunk_size=1` |

**If time is tight:** skip `make test`, keep ingest → one CLI search → one Vibe query → limitations.

**Prerequisites:** `make setup-vespa` already done; `MISTRAL_API_KEY` in `.env`.

---

## Reviewer quick-start

```bash
make setup-vespa
make ingest path=sample_data/mistral_docs
make search query="how does function calling work"
make test          # offline extraction tests
vibe               # MCP agent (reads .vibe/config.toml)
```

See [README.md](README.md) for full setup and MCP tool reference.
