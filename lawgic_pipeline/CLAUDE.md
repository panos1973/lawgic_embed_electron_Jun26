# CLAUDE.md — Lawgic FEK ingestion pipeline (headless Python core)

Headless batch pipeline: local PDF -> Weaviate (Jun2026 collections). CLI now;
a thin Electron shell can spawn this as a sidecar later (the shipping-app pattern).

## Decision (locked)
Python core + CLI. Single language for the pipeline; Electron = optional thin shell
that spawns this. Classification uses the deterministic cited-code signal + LLM API
(no local ML server). Voyage + Weaviate + Azure DI all from Python.

## Run
    pip install -r requirements.txt
    copy .env.example .env   (Windows)  &  fill in keys, or set env vars
    python cli.py ingest "C:\path\to\pdfs"
    python cli.py status
    python cli.py retry

## Stage order (orchestrator.py)
extract -> normalize -> segment -> classify -> amend -> embed -> load
State (SQLite) tracks each doc: pending|processing|done|review|error, with
content_hash dedup and resume.

## Status of each piece
REAL & working:  config, state (SQLite), models + canonical IDs + citation parser,
                 normalize (glyphs/dehyphenation/homoglyph/BM25 fold), voyage embed,
                 weaviate loader (5 collections, tenant-aware, idempotent UUIDs),
                 orchestrator spine, CLI.
                 extract (pdfplumber sidecar + Azure DI table-page splice) +
                 masthead parser -> real instrument identity (fixes UUID collision).
                 segment (anchor-based ΜΕΡΟΣ/ΚΕΦΑΛΑΙΟ/ΤΜΗΜΑ hierarchy + annex,
                 article-level provisions with hierarchy_path).
                 amend (verb detect + nested-genitive target resolution + scope +
                 in-law consolidation to text_in_force, version bump).
                 domain classifier (layered cited-code + folded-keyword fallback).
PARTIAL/EXT:     enrich_llm (summary/keywords/EUROVOC/ΔΚΝ — real, skips w/o key).
                 cross-law consolidation (in-law done; cross-law = store-level pass).
                 classify_dkn (hook for trained Ραπτάρχης/GLC model; corpus not bundled).
                 loader populates flat + article + amendment; LawDocument/Delegation TBD.

Tests: lawgic_pipeline/tests/ — 32 passing (masthead, segment, amend, enrich,
extract integration, full-spine e2e dry run). Run: `python -m pytest tests/ -q`.

## Remaining (where accuracy is still won)
1. cross-law consolidation pass at the store level (apply external amend ops).
2. trained ΔΚΝ classifier (GLC/Raptarchis47k) behind classify_dkn.
3. populate Jun2026LawDocument + Jun2026Delegation collections.
4. real-FEK validation: run a batch of genuine gazette PDFs through the spine.

## Non-negotiables
- Insert with .with_tenant("gr") (done in weaviate_io) — never omit.
- Serve/store text_in_force (consolidated), not as-enacted, for current-law answers.
- Embed whole law together (voyage-context-3 nested) — contextualization.
- Idempotent: UUID from canonical_id; content_hash dedup in state.
- Rotate the Weaviate key that was previously exposed.

## Electron later
Thin TS shell: file picker + queue UI + review screen for status='review' docs;
spawns `python cli.py ingest <folder>` (or a small local HTTP wrapper) and reads
progress. No pipeline logic in the renderer.
