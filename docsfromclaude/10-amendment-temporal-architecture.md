# Lawgic — Amendment & Temporal Retrieval Architecture

> Authoritative plan. Supersedes the *scope* of `09-kg-linking-pass-corrected.md`
> (which assumed overwrite-in-place + linking only). The decision to support **both**
> "what is in force now" and "what was in force as of date D" upgrades this from a
> linking pass into a **versioned temporal model**, with edge-linking riding on top.
>
> Design principle agreed: model retrieval as **two composable primitives**
> (point-in-time node fetch + typed edge traversal). Build the **amendment** edge
> first; the relational edges (cites / delegation / EU transposition) reuse the same
> machinery and are deferred to a later phase, not redesigned.
>
> Grounded in: `models.py`, `weaviate_io.py`, `create_all_collections.py`,
> `config.py`, `cli.py` (branch `claude/dazzling-cannon-qcmZk`). No code yet.

---

## 1. Goal: the questions this must answer

**Temporal axis (priority, v1):**
- A. "What are the amendments on law X?" — changelog, both directions.
- B. "For my case: what is in force *now* / *as of date D*, plus the still-active
  amendments that produced it — excluding inactive content."

**Relational axis (designed-for, deferred):**
- C. "What does this provision cite, and what cites it?" (`cites` edges)
- D. "What secondary legislation (ΥΑ/ΚΥΑ/π.δ.) was issued under this enabling
  provision?" (delegation edges)
- E. "Which Greek provision transposes EU Directive Y, article Z?" (EU refs)
- F. Composite/multi-hop (B + D + status in one answer).

A and B are fully covered by the temporal backbone below. C–F are the **same two
primitives** over different edge types — the backbone must not bake in "amends" as
the only edge.

---

## 2. The core change: stop overwriting, start versioning

Today consolidation **overwrites article text in place** (`consolidate_cross_law`
updates the single `generate_uuid5("art:"+canonical_id)` node, always
`is_current=True`). That answers "now" but destroys the history "as-of-date" needs.

**New model — versioned article nodes.** Each amendment that changes an article's
text produces a new version node carrying a validity interval:

| field | meaning |
|---|---|
| `canonical_id` | article **identity**, stable across versions (`ν.4412/2016#αρ.15`) |
| `version` | monotonic per article (already exists on the schema) |
| `valid_from` | effective_date of the amendment that produced this text (RFC3339) |
| `valid_to` | effective_date of the **next** amendment; `null` = still in force |
| `is_current` | `true` only for the latest non-repealed version |
| `legal_force_status` | `in_force` \| `amended` \| **`repealed`** |
| `chunk_text` | the consolidated text *of this version* |

### 2.1 Version identity (the decision to lock once)
Per-version UUID — **deterministic, idempotent, recomputable from an edge**:
```
article version  = generate_uuid5("art:" + canonical_id + "@" + valid_from)
# enacted/original version uses the law's own effective date (or fek_date) as valid_from
```
Unchanged: flat chunk = `generate_uuid5(canonical_id)`, document =
`generate_uuid5("doc:" + instrument_id)`. An amendment edge can point at the exact
version it created by recomputing this key. **Getting this right once avoids a second
re-embed** — settle it before any loader change.

### 2.2 Repeals (today's silent gap)
`consolidate_cross_law` only applies `replaces`/`consolidates`; it **skips
`καταργείται`/repeals**, so a repealed article still serves as live. In the versioned
model a repeal writes a **terminal version**: `legal_force_status="repealed"`,
`valid_to` set, `is_current` reflects "repealed as of date", no successor.

### 2.3 "We don't need inactive" = a filter, not a deletion
Because as-of-date needs the history, inactive versions are **kept** but **never
served in the "now" view**. "Exclude inactive" is the query filter in §4, not a delete.

---

## 3. Edges as a composable primitive

All relationships are directed edges from a source node to a target node, declared as
Weaviate v4 `ReferenceProperty` and populated by **one generic linking pass**
parameterized by edge type. Already-declared refs:

| Edge collection | from → to | v1? | source scalar present today |
|---|---|---|---|
| `Jun2026Amendment` `target_article`/`target_document` | amendment → amended version/doc | ✅ | `target_canonical_id` ✓ |
| `Jun2026Amendment` `source_article`/`source_document` | amendment → amending version/doc | ✅ | ❌ needs `amend.py` to record host-provision id |
| `Jun2026LawArticle` `supersedes_article` | version → prior version | ✅ | falls out of versioning |
| `Jun2026Delegation` `enabling_*`/`implementing_*` | impl act → enabling provision | ⏳ | needs loader to persist `enabling_id`/`implementing_id` |
| `cites` (currently scalar list on Provision) | provision → cited provision | ⏳ | `cites` extracted; no ref declared yet |
| EU transposition (`implements_eu_*`) | provision → EU norm | ⏳ | extracted by `refs.py`; mapping target modeling TBD |

**Convergence:** once versioned, the amendment edge links to the *specific version it
created* (`target_article`), and `supersedes_article` is populated as the chain
between consecutive versions — the timeline and the graph become one structure.

Linking is **idempotent by ref-presence** (skip if the ref is already populated) —
no BOOL flags, no schema migration. Mirrors `consolidate_cross_law`'s "re-run applies
nothing" style. A target that doesn't exist yet → counted as `pending`, retried on a
later pass (laws ingest in any order).

---

## 4. Retrieval primitives (the API)

Two primitives; every question in §1 is a composition.

### P1 — point-in-time node fetch
```
fetch(canonical_id | semantic_query, when="now" | date D, exclude_inactive=True)
```
- `now`   → `is_current=True AND legal_force_status != "repealed"`
- `as_of D` → the one version per article where `valid_from ≤ D AND (valid_to IS NULL OR valid_to > D)`
- semantic entry: vector search over the flat collection, then apply the same temporal filter (this is the front half of scenario B when the law is unknown).

### P2 — typed edge traversal
```
edges(node, type ∈ {amends, supersedes, cites, delegates, transposes}, direction)
```
v1 implements `amends` (+ `supersedes`); others are the same call on another edge
collection.

### The two archetypes, expressed as primitives
- **A "amendments on law X"** = `P2(law X, type=amends, direction=incoming)` for "how X
  was changed", `direction=outgoing` for "what X changed".
- **B "case answer"** = `P1(relevant articles, when)` for the active consolidated text
  + `P2(those versions, type=amends, incoming)` filtered to amendments whose produced
  version is the one P1 returned ⇒ "still-active amendments from previous laws".
- **F composite** = P1 ∪ P2(…, delegates) ∪ status — no new endpoint, just composition.

---

## 5. Phased build

1. **Lock the versioned schema** — version-key (§2.1), `valid_to`, status values,
   confirm `supersedes_article`/`target_article` refs. *(decision + schema)*
2. **Versioned loader + repeal application** — append versions with validity
   intervals instead of overwriting; apply `καταργείται` as terminal versions.
   ⇒ "now" and "as-of-date" both answerable; "exclude inactive" free.
3. **Generic linking pass** (`link_graph` in `weaviate_io.py`, beside
   `consolidate_cross_law`) — v1 wires amendment `target_*` + `supersedes_article`;
   ref-presence idempotency; server-side `iterator()` pagination.
4. **Source-side capture** — add `source_id` to `AmendmentOp` (`models.py`), set it in
   `amend.py` to the host provision's `canonical_id`; write `source_canonical_id`;
   link `source_article`. ⇒ archetype A becomes bidirectional.
5. **`graph-status` = dangling-target QA report** — amendments whose target node
   doesn't exist after a full ingest. *This is the extraction-accuracy instrument.*
6. **Retrieval API** — P1 (`now`/`as_of`) + P2 (`amends`/`supersedes`) + the A/B
   query helpers. CLI **subcommands** (mirror `consolidate`): `link-graph`,
   `graph-status`; retrieval exposed for the app.
7. **Labeled eval** — hand-label amendments in ~5 real FEK laws; assert recall.

**Deferred (relational axis, same machinery):** declare `cites` ref + link it;
persist delegation canonical ids + link; model EU-norm targets + link. Each is a
new edge type fed through the P2 primitive — no backbone change.

---

## 6. Cost & risk flags
- **One re-embed** (the version-key UUID scheme changes) — fold it together with the
  pending table/language/metadata re-embed so it's a single pass, not two.
- **More vectors** — one per version, not per article; amendments/article are few, so
  the increase is modest but nonzero.
- **Multi-PR effort.** Backbone (steps 1–2) is the heavy lift and is prerequisite;
  linking (3–4) is light once ids are stable; retrieval (6) is additive.
- Tenant is `gr`; client is v4 `.use()`; all CLI output is one JSON line per event
  honoring `--json` (Electron contract).

---

## 7. Decisions resolved
- **Temporal scope:** both `now` and `as_of(date)` → versioned model (not overwrite). ✓
- **Design shape:** general edge-traversal primitive in the design; **amendments-only
  in the first implementation.** ✓
- **Idempotency:** ref-presence check, no schema flags. ✓
- **Linker placement:** `weaviate_io.py`, beside `consolidate_cross_law`. ✓

## 8. Open (needed before step 1 code)
- Confirm the **version-key** string (§2.1) — recommended `"art:"+canonical_id+"@"+valid_from`.
- Confirm the **original/enacted** version's `valid_from` source (law effective date vs `fek_date`).
- Confirm repeal of a *whole law* vs a single article propagates to all child versions.
