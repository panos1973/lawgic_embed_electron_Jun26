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
                 delegate (FEK Β΄ implementing acts -> enabling-provision edges).
                 loader populates all 5 collections (flat, article, document,
                 amendment, delegation); validate.py offline accuracy harness.
PARTIAL/EXT:     enrich_llm (summary/keywords/EUROVOC/ΔΚΝ — real, skips w/o key).
                 cross-law consolidation (in-law done; cross-law = store-level pass,
                 consolidate_cross_law + `cli consolidate`).
                 classify_dkn (hook for trained Ραπτάρχης/GLC model; corpus not bundled).
                 delegation graph cross-refs (scalar props written; ref-linking TBD).

Instrument types: FEK Α΄ ν./π.δ./Π.Ν.Π./ψήφισμα/Κανονισμός Βουλής/α.ν./ν.δ.;
FEK Β΄ ΥΑ/ΚΥΑ/κανονιστική απόφαση/απόφαση Διοικητή-Γ.Γ./Περιφερειάρχη-ΟΤΑ/ΝΠΔΔ.

Real-FEK hardening (validated against samplefek/ corpus):
  Phase 1 — pipeline/normalize.py: token-aware Latin->Greek homoglyph repair
    (NOMOΣ->ΝΟΜΟΣ), ∆->Δ, (cid:NNN) stripping, furniture/boilerplate removal;
    sidecar column-aware extraction (two-column reading order). Fixed masthead
    type=None and scrambled article order on real laws.
  Phase 2 — pipeline/multiact.py: one gazette PDF -> N instruments. Primary laws
    pass through; decision issues split on Αριθμ. headers, typed by issuer
    (Υπουργοί->ΚΥΑ, Υπουργός->ΥΑ, Γραμματέας/Διοικητής->Διοικητή, Περιφερειάρχης/
    Δήμαρχος->Περιφ., Σύγκλητος/Δ.Σ.->ΝΠΔΔ, Αρχή->κανονιστική). Decision ids via
    make_decision_id (Β΄913/2025#1). segment.py single-provision fallback for
    decisions with no Άρθρο. orchestrator loops over acts.
  Phase 3 — edge refinements: cite-based delegation (incl. codifying π.δ.; search
    restricted to the act header «Έχοντας υπόψη»+operative for precision). amend.py
    resolves decision targets named by gazette ref ("(Β΄ 7738)"+date ->
    Β΄7738/2023). pipeline/refs.py tags EU-law references (ΕΕ:Κανονισμός 2022/868)
    into provision.cites. segment.py adds spelled-ordinal (Άρθρο πρώτο/μόνο) and
    Roman-numeral (Άρθρο XII) article anchors. ΜΕΡΟΣ/ΚΕΦΑΛΑΙΟ tier already worked
    post-Phase-1 (verified: ν.5086 -> ΜΕΡΟΣ Α' > ΚΕΦΑΛΑΙΟ Α' > Άρθρο 1).
  Phase 4 (planned, not built) — OCR via Azure DI: selective (scanned/CID/table
    pages only), rasterize+OCR for CID-encoded born-digital pages (Σύνταγμα-class),
    guarded behind DI_ENDPOINT/DI_KEY. Known issue: codification π.δ. with an
    end correspondence-table over-segments (Άρθρο anchor) — to be bounded.

Tests: lawgic_pipeline/tests/ — 78 passing (masthead, segment, amend, enrich,
delegate, normalize, multiact, refs, weaviate_io loaders, extract integration,
full-spine e2e, validate harness). Run: `python -m pytest tests/ -q`.

## Remaining (where accuracy is still won)
1. trained ΔΚΝ classifier (GLC/Raptarchis47k) behind classify_dkn.
2. delegation/amendment graph cross-reference linking (endpoints exist -> link).
3. real-FEK validation: run genuine gazette PDFs through `python validate.py`.
4. confirm target Weaviate cluster (code default vs env WEAVIATE_URL differ).

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
