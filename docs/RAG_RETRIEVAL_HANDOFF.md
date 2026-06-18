# RAG Retrieval Handoff — Legislative-History / "Trace the Changes" capability

**Audience:** the code session building the **RAG (answer-serving) application** — a *separate* repo from this ingestion pipeline (`lawgic_embed_electron_Jun26`).
**Goal:** implement retrieval that lets a lawyer ask things like *"trace the latest developments in the Criminal Code"* or *"show me the full history of changes to article 5 of ν.4619/2019"* — and get a correct, time-aware, chained answer.

> **TL;DR.** All the data already exists in one shared Weaviate cluster. You do **not** create new collections or re-embed anything. You implement **read queries** that combine three axes already present: **subject** (`legal_domain`/`domain_dkn`), **time** (`effective_date`, `valid_from`/`valid_to`/`is_current`), and **chain** (`target_law_number` + `target_article_number` + `target_canonical_id` on the amendment edges). The chain tracing is **scalar/graph filtering — no vectors**; vectors are only for the semantic "find the relevant provisions" step.

---

## 0. Roles & boundaries (read this first)

- **This pipeline repo** ingests FEK PDFs → parses → embeds → writes 5 Weaviate collections. It also owns build/QA tools (`cli consolidate`, `cli graph-status`, `cli history`). **You do not touch ingestion.**
- **Your RAG repo** issues **read-only queries** against the **same** Weaviate cluster + tenant to answer questions. Nothing is "owned" — both point at the same collections.
- **Do not** prepend document summaries to queries, re-chunk, or re-embed. The corpus is already chunked one-article-per-chunk and embedded with a contextual model (details below).

---

## 1. Connection & embedding (critical setup)

- **Weaviate:** multi-tenant. **Every query MUST set tenant `gr`** (`.with_tenant("gr")`). Omitting the tenant returns nothing.
- **Vectors are "bring your own" (voyage), not a Weaviate vectorizer module.** Weaviate will **not** auto-embed your query. At query time you must:
  1. Embed the query text with **voyage-context-3**, `input_type="query"`, `output_dimension=1024`.
  2. Pass that vector to Weaviate via `near_vector` (or `hybrid(..., vector=<v>)`).
- **Embed model:** `voyage-context-3`, **1024 dims**. **Query embedding:** `voyageai` `contextualized_embed(inputs=[[query]], model="voyage-context-3", input_type="query", output_dimension=1024)` → `result.results[0].embeddings[0]`.
- **Reranker (optional, recommended):** `rerank-2.5` (voyage) over the candidate chunk texts to sharpen top-k.
- **gRPC note:** if your environment blocks gRPC, use the **REST GraphQL** endpoint (`POST {WEAVIATE_URL}/v1/graphql`) — every query below is expressible in GraphQL `Get`/`Aggregate` with `where`/`nearVector`/`sort`.

---

## 2. The collections you query

| collection | vectors? | one row = | use it for |
|---|---|---|---|
| **`Jun2026GRLegaDocs`** (flat) | ✅ 1024-d | a chunk (one article, or one section of a no-article doc) | **semantic + keyword search** (the entry point) |
| **`Jun2026LawArticle`** | ✅ 1024-d | one **version** of an article | point-in-time text, article-level graph node |
| **`Jun2026LawDocument`** | ❌ | a law/decision | document metadata, `document_summary`, `total_articles` |
| **`Jun2026Amendment`** | ❌ | one amendment **edge** | **the chain** — who changed what, when |
| **`Jun2026Delegation`** | ❌ | a delegation edge (FEK Β→Α) | "issued under authority of …" |

### Fields you will actually use

**`Jun2026Amendment`** (the chain — no vectors):
`source_canonical_id`, `source_law_number`, `source_article_number` (which article of the **newer** law made the change), `target_canonical_id`, `target_law_number`, `target_article_number`, `target_paragraph` (the **older** provision changed), `action` (`adds|replaces|amends|modifies|repeals|renumbers|consolidates`), `scope`, `new_text`, `effective_date` (RFC3339), `resolved` (**`false` ⇒ target law not yet ingested — a still-open link**), `extraction_method`.

**`Jun2026GRLegaDocs`** (flat — search target):
`canonical_id`, `law_number`, `article_number`, `document_type`, `legal_domain` (multi-label subject), `domain_dkn` (Ραπτάρχης volumes), `effective_date`, `valid_from`, `valid_to` (**null = current**), `is_current` (bool), `legal_force_status` (`in_force|amended|repealed|suspended`), `amends_provisions`, `amended_by_provisions`, `hierarchy_path`, `chunk_summary`, `chunk_text` (the embedded verbatim text), `document_title`, `article_title`, `language` (`el|en|mixed`), `chunk_index`, `total_chunks`.

**`Jun2026LawArticle`** (per-version node): `canonical_id`, `document_law_number`, `article_number`, `version`, `valid_from`, `valid_to`, `is_current`, `legal_force_status`, `chunk_text`, `chunk_summary`, `legal_domain`, `domain_dkn`, `hierarchy_path`.

**`Jun2026LawDocument`**: `instrument_key`, `law_number`, `document_type`, `document_category`, **`document_summary`** (whole-law overview), `title`, `publication_date`, `effective_date`, `legal_force_status`, `fek_type/fek_year/fek_issue`, `total_articles`.

---

## 3. The canonical-id join key (the linchpin)

Provisions and amendment targets are joined by a **stable canonical id**:

```
ν.4619/2019#αρ.5                 (article 5 of law 4619/2019)
ν.4619/2019#αρ.5.παρ.2           (paragraph 2)
ν.4619/2019#αρ.5.παρ.2.περ.γ     (case γ)
π.δ.62/2025#αρ.10                (presidential decree)
```
Instrument prefixes: `ν.` law, `π.δ.` presidential decree, `α.ν.` compulsory law, `ν.δ.`, `Π.Ν.Π.`, etc. Decisions (FEK Β/ΥΑ) use a FEK-ref id, e.g. `Β΄7738/2023` or `Α΄18/2024` (and `#τμ.N` for sections of a no-article act).

**Robustness tip (important):** an amendment may target a **paragraph/case** (`…#αρ.5.παρ.2`), while you usually want **all edits to article 5**. Do **not** match `target_canonical_id` exactly for "all changes to article 5" — instead filter the denormalized **`target_law_number` + `target_article_number`** (both are stored on every edge). That gives the article-level chain regardless of paragraph/case granularity. Use exact `target_canonical_id` only when you want a single paragraph's edits.

---

## 4. The temporal model (how to be time-correct)

`cli consolidate` (ingestion side) folds amendments into **versioned** nodes on flat + article:
- `is_current = true` → the **in-force** version right now.
- `valid_from ≤ D < valid_to` (with `valid_to = null` meaning "still current") → the version in force **as of date D**.
- `legal_force_status` ∈ `in_force | amended | repealed | suspended`.

So: **"current law"** = filter `is_current = true`. **"law as of date D"** = filter `valid_from ≤ D AND (valid_to IS NULL OR valid_to > D)`.

---

## 5. The four query patterns to implement

> Examples use Weaviate Python client v4 shapes for legibility. Translate to your stack; the **filters + sorts + fields** are what matter. Always `.with_tenant("gr")`.

### A. Semantic subject search — *"latest developments in criminal law about X"* (the entry point)
Hybrid (vector + BM25) over **flat**, filtered by subject, biased to recent + current.
```python
qv = voyage_query_vector(user_question)            # voyage-context-3, input_type="query", 1024-d
flat.query.hybrid(
    query=user_question,                            # BM25 leg (Greek/English)
    vector=qv,                                      # vector leg (you supply it)
    filters=(Filter.by_property("legal_domain").contains_any(["criminal"])
             & Filter.by_property("is_current").equal(True)),
    limit=40,
)
# then: rerank-2.5 over the returned chunk_text, and/or sort by effective_date desc for "latest"
```
- Subject by **controlled label** (`legal_domain`) — see the vocabulary in §6 — or by **`domain_dkn`** volume (e.g. `"ΠΟΙΝΙΚΗ ΝΟΜΟΘΕΣΙΑ"`).
- For "latest developments" specifically, sort candidates by `effective_date` desc (or `valid_from` desc).

### B. All changes to a whole code/law — *"every amendment to the Criminal Code, newest first"*
Pure scalar query on **`Jun2026Amendment`** (no vector):
```python
amendment.query.fetch_objects(
    filters=Filter.by_property("target_law_number").equal("4619/2019"),   # the ΠΚ
    sort=Sort.by_property("effective_date", ascending=False),             # newest first
    limit=500,
)
# each row: source_law_number/source_article_number (who changed it), action, target_article_number,
# target_paragraph, new_text, effective_date, resolved
```

### C. Full history of one provision — *"how did article 5 change over time"* (`cli history` equivalent)
Two reads, merged:
1. **The edits** — `Jun2026Amendment` where `target_law_number == "4619/2019"` **and** `target_article_number == "5"`, sorted `effective_date` asc → the ordered list of changes (and any `resolved=false` ⇒ an older amending law you haven't ingested yet).
2. **The text timeline** — `Jun2026LawArticle` (or flat) where `document_law_number == "4619/2019"` and `article_number == "5"`, all versions, sorted `valid_from` asc → the wording at each stage (`valid_from`/`valid_to`/`is_current`/`legal_force_status`).

Present them zipped: *"v1 (enacted 2019…) → amended by ν.X art.Y on DATE → v2 text … → repealed by ν.Z on DATE."*

### D. Point-in-time text — *"what did article 5 say on 2021-06-01"*
```python
article.query.fetch_objects(
    filters=(Filter.by_property("document_law_number").equal("4619/2019")
             & Filter.by_property("article_number").equal("5")
             & Filter.by_property("valid_from").less_or_equal("2021-06-01T00:00:00Z")
             & (Filter.by_property("valid_to").is_none(True)
                | Filter.by_property("valid_to").greater_than("2021-06-01T00:00:00Z"))),
)
```

---

## 6. Subject vocabulary (`legal_domain` controlled labels)

```
administrative, civil, civil_procedure, commercial, constitutional, corporate,
criminal, criminal_procedure, customs, data_protection, defense, digital,
education, energy, environmental, eu_law, health, immigration, insolvency,
labor, public_procurement, social_security, tax, transport
```
`domain_dkn` carries the Ραπτάρχης volume names (e.g. `ΠΟΙΝΙΚΗ ΝΟΜΟΘΕΣΙΑ`, `ΠΟΙΝΙΚΗ ΔΙΚΟΝΟΜΙΑ`). Both are **multi-label** (a provision can carry several). Filter with `contains_any`.

---

## 7. Composing the lawyer-facing answer ("trace the developments")

1. **Scope** the subject: from the question, pick a `legal_domain` label and/or a specific `target_law_number` (the code). (B) gives "all changes to the code"; (A) gives "the relevant provisions semantically."
2. **Order** by `effective_date` desc for "latest developments"; or by `valid_from` asc for a chronological narrative.
3. **Drill** into any provision with (C) for its full chain, and (D) for the exact text on a given date.
4. **Synthesize** the answer with your LLM from: the chain rows (who/what/when), the version texts, and the `document_summary`/`chunk_summary` for context. Cite `canonical_id` + `effective_date` for each step.

---

## 8. Operational caveats (do not skip)

- **Run `cli consolidate` after every backfill** (ingestion side). The version timeline (`valid_from`/`valid_to`/`is_current`) is **materialized** by that pass. If new older laws were embedded but consolidate didn't re-run, the timeline is stale. (This is the pipeline's job, but your answers depend on it — surface "as of last consolidation" if you must.)
- **`resolved = false` edges** mean the target law isn't ingested yet (you're still backfilling). Treat them as **"known change, older law pending"** rather than dropping them — they're exactly the chain links that complete as the backfill goes further back.
- **Canonical-id consistency is the join.** If a chain looks broken, it's almost always an id/granularity mismatch (e.g. an edge targeting `…#αρ.13Α.παρ.Β.περ.7` vs an article node `…#αρ.13Α`). Prefer the `target_law_number` + `target_article_number` filter (article-level) for chains; it's robust to paragraph/case suffixes. The ingestion side runs `cli graph-status` to report dangling/mismatched targets.
- **Multi-tenant:** `gr` on every call.
- **Vectors are external (voyage):** never rely on Weaviate to embed the query; you supply the 1024-d voyage `input_type="query"` vector.
- **Language:** chunks are `el`/`en`/`mixed`; voyage-context-3 is multilingual, so a Greek query can still retrieve an English passage (e.g. an annexed treaty/UN resolution) — do **not** filter out non-Greek `language`.
- **Don't re-chunk / don't summarize into the vector:** the chunk is already the unit; `document_summary` is document-level metadata, not a per-chunk vector input.

---

## 9. Minimal field cheat-sheet

- **Find provisions (semantic):** `Jun2026GRLegaDocs` → `chunk_text` (vector+BM25), filter `legal_domain`/`domain_dkn`/`is_current`/`document_type`.
- **List changes to a law:** `Jun2026Amendment` → filter `target_law_number`, sort `effective_date` desc.
- **One provision's chain:** `Jun2026Amendment` → filter `target_law_number` + `target_article_number`, sort `effective_date` asc.
- **Text now / as-of-date:** `Jun2026LawArticle` → filter `document_law_number` + `article_number` + (`is_current` | `valid_from`/`valid_to` window).
- **Law overview:** `Jun2026LawDocument` → `document_summary`, `title`, `document_category`, `total_articles`.

---

*Generated from the live `Jun2026*` schema of the ingestion pipeline. If a field name ever disagrees with the cluster, the cluster schema (`GET {WEAVIATE_URL}/v1/schema`) is the source of truth.*
