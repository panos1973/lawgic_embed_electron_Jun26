# Lawgic FEK Ingestion

Builds the Greek-legal vector store that powers Lawbot: local FEK (Φ.Ε.Κ.) gazette
PDFs → extract, normalize, segment, classify, amend/consolidate, embed → Weaviate
(the `Jun2026*` collections). Ships as a headless Python core plus an Electron
desktop app.

## Layout
- `create_all_collections.py` — one-time: creates the 5 Weaviate collections (full schema, the source of truth for every field below).
- `lawgic_pipeline/` — headless Python core (the actual ingestion). See its `CLAUDE.md`.
- `lawgic_electron/` — desktop shell over the core (Ingest · Review · Settings).
- `samplefek/` — sample FEK PDFs for testing.
- `*.md` — design references (extraction front-end, KG schema, collection design, blueprint).

## Setup
```
# 1. collections (after rotating the Weaviate key)
pip install "weaviate-client>=4.16.4"
python create_all_collections.py

# 2. core deps
cd lawgic_pipeline && pip install -r requirements.txt

# 3. desktop app
cd ../lawgic_electron && npm install && npm start
```

---

## Technologies

| Layer | Technology | Role |
|-------|-----------|------|
| **Vector / graph DB** | **Weaviate Cloud** (≥1.32, client ≥4.16.4) | Stores the legal data: chunks (with vectors) + graph nodes/edges. Multi-tenant, replicated. |
| **Embeddings** | **Voyage AI `voyage-context-3`** (1024-d, contextualized) | Turns provision text into vectors. Self-provided to Weaviate. |
| **Rerank** | **Voyage AI `rerank-2.5`** | Query-time re-ranking (retrieval side). |
| **OCR / tables** | **Azure Document Intelligence** | Table-heavy / scanned pages; structured table extraction. |
| **PDF text** | **pdfplumber / pdfminer.six** | Born-digital PDF text + layout. |
| **Enrichment LLM** | **Anthropic Claude / DeepSeek / Gemini** (provider-agnostic) | Summaries, keywords, EUROVOC, extra ΔΚΝ. Optional — skips without a key. |
| **Local state** | **SQLite** | Per-document pipeline state (resume, dedup). Not legal data. |
| **Core** | **Python 3.11** | Ingestion pipeline + CLI. |
| **Desktop** | **Electron** (Node 20) | UI shell; spawns the core, encrypts secrets at rest (OS keychain). |
| **Packaging** | **PyInstaller** + **electron-builder** | Freeze core → bundle Windows `.exe` (no Python needed on the user's machine). |

---

## Data stores — what is saved where

### 1. Weaviate Cloud — the legal data (5 collections, all multi-tenant by jurisdiction, replication factor 3)

| Collection | Vectors? | One row = | Stores |
|------------|:--------:|-----------|--------|
| **`Jun2026GRLegaDocs`** (flat) | ✅ | one **chunk / provision** | The primary hybrid-search target. Full searchable text + all classification/metadata. **This is the main embedded collection.** |
| **`Jun2026LawArticle`** (graph) | ✅ | one **article** (+ version) | Article node with versioning (`version`, `valid_from/to`, `is_current`) and cross-references; also embedded. |
| **`Jun2026LawDocument`** (graph) | — | one **law / document** | Document-level metadata node (FEK identity, type, category, dates). No vectors. |
| **`Jun2026Amendment`** (graph) | — | one **amendment edge** | "law X article N replaces/adds/repeals law Y article M" + the new text. No vectors. |
| **`Jun2026Delegation`** (graph) | — | one **delegation edge** | FEK B implementing act → FEK A enabling authority. No vectors. |

Idempotent: every object's UUID is a deterministic `generate_uuid5` of its
`canonical_id`, so re-ingesting upserts instead of duplicating.

### 2. SQLite (`lawgic_state.db`) — pipeline state only (never the legal text)
One row per document: `doc_id`, `path`, `content_hash` (dedup), `status`
(`pending|processing|done|review|error`), last `stage`, `confidence`, `error`,
timestamps. Drives **resume** and **skip-unchanged** dedup. Lives in the app's
data dir (Electron `userData`).

### 3. `lawgic.log` — rotating run log
Next to the state DB. Stage-by-stage record including per-batch embedding
telemetry and full tracebacks. Openable from the app (Settings → Open logs folder).

---

## Embedding specification — the full spec of what we embed

### Model & vector
- **Model:** `voyage-context-3` — *contextualized* embeddings: a law's chunks are
  embedded together so each provision vector carries surrounding-article context.
- **Dimensions:** **1024**, unit-normalized.
- **`input_type`:** `document` at ingest, `query` at search time.
- **Provided to Weaviate as self-provided vectors** (no server-side vectorizer).
- **Context window:** **32,000 tokens per input document.** A law longer than this
  is split into multiple context windows (budget = `0.75 × 32k`); chunks share
  context within a window. (`voyage_embed.py`.)

### What text gets embedded
- The vector is computed from **`text_in_force`** — the provision's **consolidated,
  currently-in-force text** (after amendments are applied), *not* the raw enacted
  text. This is what lands in `chunk_text`.
- **Chunk granularity:** one chunk per **article** (and, where segmented, per
  paragraph), to preserve a self-contained semantic unit.
- **Tables:** `chunk_text` holds the **markdown** table (embedded for semantic
  search); `table_json` holds the **structured rows/columns** for exact cell lookup.

### Vector index & search characteristics (both vector collections)
- **Index:** HNSW + **8-bit Rotational Quantization (RQ)**, **DOT** distance
  (for unit-normalized vectors).
- **HNSW:** `ef=200`, `ef_construction=256`, `max_connections=32`, RQ `rescore_limit=200`
  (over-fetch compressed, re-rank full-precision).
- **Hybrid (BM25) side:** `b=0.3`, `k1=1.5` (tuned for long Greek legal text),
  Greek stopword list, plus two Greek-specific companion fields per chunk:
  `text_normalized` (accent-folded — **precision**) and `text_stemmed` (Snowball-
  stemmed — **recall** across inflection: νόμος/νόμου/νόμων → one term). Both are
  written on ingest by the loader. **Query side (TS app):** fold the query for
  `text_normalized` and run it through `greek_stem.stem_query` for `text_stemmed`
  — the same transforms applied on ingest — and search both; never stemmed-only
  (the stemmer is aggressive, so it boosts recall but loses precision alone).
- **Tokenization per field:** `WORD` (searchable text), `TRIGRAM` (titles/summaries,
  fuzzy), `FIELD` (ids/urls, exact), `LOWERCASE` (`canonical_id` / `hierarchy_path`,
  keeps `Ν.5090/2024` a single token).

### Metadata fields written per chunk (`Jun2026GRLegaDocs`)
These are the fields the loader populates for every embedded provision today
(`weaviate_io.py` → `_flat_props`):

| Field | Type | Meaning |
|-------|------|---------|
| `canonical_id` | text (LOWERCASE) | Pinpoint id, e.g. `ν.4675/2024#αρ.24.παρ.2` (exact match) |
| `instrument_key` | text (FIELD) | ASCII citation key, e.g. `N4675/2024` |
| `document_type` | text | `NOMOS \| PD \| PNP \| KYA \| YA \| EGKYKLIOS \| PSIFISMA \| AN \| ND \| VD` |
| `law_number` | text | e.g. `5090/2024` |
| `fek_reference` | text | `{series}_{year}_{number}`, e.g. `A_2024_52` |
| `article_number` | text | e.g. `5`, `12a`, `103` |
| `article_title` | text (TRIGRAM) | Article heading |
| `legal_force_status` | text | `in_force \| repealed \| amended \| suspended \| pending` |
| `chunk_type` | text | `article \| paragraph \| table \| preamble \| ...` |
| `hierarchy_path` | text (LOWERCASE) | `Ν.5090/2024 > ΚΕΦΑΛΑΙΟ Α > Άρθρο 5` |
| `legal_domain` | text[] | Fast 17-label subject enum (labor, tax, criminal, …) |
| `domain_dkn` | text[] | Ραπτάρχης ΔΚΝ top-level subject volumes (deterministic) |
| `domain_eurovoc` | text[] | EUROVOC descriptors (LLM, when enabled) |
| `keywords` | text[] | 5–10 Greek keywords |
| `chunk_summary` | text (TRIGRAM) | 2–3 sentence Greek summary |
| `chunk_text` | text (WORD) | **Embedded text** — in-force provision text / markdown table |
| `text_normalized` | text (WORD) | Accent-folded text — diacritic-insensitive Greek BM25 (**precision**) |
| `text_stemmed` | text (WORD) | Snowball-stemmed text — collapses Greek inflection (**recall**) |
| `table_json` | text (FIELD) | Structured table for exact cell lookup |
| `amends_provisions` | text[] | What this provision amends |
| `amended_by_provisions` | text[] | What amends this provision |
| `external_law_references` | text[] | Cited laws, e.g. `['4808/2021','4172/2013']` |
| `language` | text | `el` |

> The flat collection's **full schema declares ~58 properties** (penalties, EU
> transposition, court refs, blob URLs, English semantic tags, etc.) as a forward
> superset; `create_all_collections.py` is the authoritative list. The table above
> is the ~22-field subset the pipeline writes today. The `Jun2026LawArticle`
> collection adds versioning fields (`version`, `valid_from/to`, `is_current`,
> `content_hash`) and graph references (`document`, `supersedes_article`).

### Controlled vocabularies (used identically across collections)
- **`document_type`:** `NOMOS | PD | PNP | KYA | YA | EGKYKLIOS | PSIFISMA | AN | ND | VD`
- **amendment `action`:** `repeals | replaces | adds | amends | modifies | renumbers | consolidates | none`
- **amendment `scope`:** `document | article | paragraph | case | subcase`

---

## Status (what's real vs to-build)
**REAL:** collections + full schema, state/dedup/resume, canonical IDs + citation
parser, Greek normalization, contextualized voyage-context-3 embedding (windowed to
the 32k limit), tenant-aware Weaviate loader, orchestrator, CLI, Electron shell,
deterministic domain + document-category + ΔΚΝ classifiers, provider-agnostic LLM
enrichment, embedding telemetry + rotating log.

**TO BUILD (where accuracy is won — see `lawgic_pipeline/CLAUDE.md`):**
1. Segmentation of **amending laws** (articles that quote/insert articles of other
   laws; ΜΕΡΟΣ/ΚΕΦΑΛΑΙΟ/παρ/annex morphology).
2. `pipeline/amend.py` — amendment target resolution + cross-law consolidation.
3. Trained ΔΚΝ model (GLC/Raptarchis47k) to augment the deterministic volumes.

## Amendment extraction — validate before flipping the LLM extractor
Amendments are the highest-value, hardest edges. Two extractors exist behind one
switch (`AMEND_EXTRACTOR`, default `deterministic`):

- **`deterministic`** (`pipeline/amend.py`) — regex target resolution. Fast, no
  key, but mis-aligns *which* `«…»` span belongs to *which* target in articles
  with multiple nested references.
- **`llm`** (`pipeline/amend_llm.py`) — provider-agnostic (DeepSeek V4 Pro by
  default), grounded in real Greek amendment morphology, captures `new_text`, and
  guards source==target self-loops. Falls back to deterministic (with a log line)
  if no key is set.

Both extractors run the **same `_clean_amendments` hygiene** (drops heading-only/
empty edits, self-document dumps, unresolved fragments, exact dupes), so the edge
set is consistent whichever is active.

**Validate, then flip.** Do not enable `llm` for a full re-embed until it beats
the deterministic baseline on the gold set:

    # 1. drop the source-law texts named '<law>.txt'/'<law>.pdf' into ./goldlaws
    # 2. baseline (no key):
    python lawgic_pipeline/benchmark_amendments.py \
      --gold lawgic_pipeline/gold_amendments.sample.json --laws-dir ./goldlaws \
      --extractor deterministic
    # 3. LLM extractor (set provider + key; DeepSeek V4 Pro non-thinking):
    AMEND_EXTRACTOR=llm LLM_PROVIDER=deepseek DEEPSEEK_API_KEY=… \
      python lawgic_pipeline/benchmark_amendments.py \
        --gold lawgic_pipeline/gold_amendments.sample.json --laws-dir ./goldlaws \
        --extractor llm

The harness reports precision / recall / F1 and `new_text` coverage. Flip
`AMEND_EXTRACTOR=llm` for ingestion only once the LLM run wins on F1 *and*
coverage.

## Security
Never commit API keys. `create_all_collections.py` reads the Weaviate key from an
env var; rotate any previously-exposed key. Desktop secrets are encrypted at rest
via the OS keychain (`safeStorage`).
