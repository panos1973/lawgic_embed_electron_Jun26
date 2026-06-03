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

> **Implementation status (branch claude/dazzling-cannon-qcmZk):** steps 1–2, 4, 5
> are **code-complete and tested** (214 passing) — schema fields, source-side
> capture + effective-date fallback, versioned `load_law`, `assemble_article_timeline`
> (§5A), `graph_status`, and the `consolidate`/`graph-status` CLI. **Remaining:**
> step 3 cross-reference *population* (`target_article`/`supersedes_article` links —
> the data + UUIDs are ready, the `reference_add` pass is not yet written) and step 6
> the P1/P2 retrieval API. **Operational:** the per-version UUID change needs a
> collection recreate + re-ingest on the live cluster before it takes effect.


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

## 5A. The timeline-assembly (consolidation) pass — exact algorithm

This generalizes today's `consolidate_cross_law`. The whole thing is **one pure
function over one article identity**, plus triggers that call it. Order of ingestion
does not affect the result — only which articles are ready to assemble.

### 5A.1 The core function
```
assemble_article_timeline(client, tenant, C)   # C = canonical_id, e.g. "ν.4412/2016#αρ.15"

  # 1. Find the starting (enacted) text.
  base = enacted_text_for(C)          # the article's text as first written by its own law
  edges = amendment edges where target_canonical_id == C
          AND effective_date is not null
          AND action in {replaces, consolidates, adds, repeals, modifies}

  # 2. If we have neither a base nor an 'adds' that creates it -> nothing to do yet.
  if base is None and no 'adds' edge in edges:
      mark those edges pending; return   # base law not ingested yet (out-of-order)

  # 3. Order the edits deterministically.
  edges = sort(edges, key=(effective_date, sub_edit_ordinal, source_law_number))

  # 4. Seed the timeline.
  if base exists:
      timeline = [ V(text=base.text, valid_from=base.effective_date, status="in_force") ]
  else:                                  # the first 'adds' edge *is* the enacted version
      first = edges.pop_first_adds()
      timeline = [ V(text=first.new_text, valid_from=first.effective_date, status="in_force") ]

  # 5. Fold each amendment forward.
  for e in edges:
      prev = timeline[-1]
      prev.valid_to = e.effective_date            # close the previous window
      if e.action == "repeals":
          timeline.append(V(text=prev.text, valid_from=e.effective_date,
                            valid_to=None, status="repealed"))
          break                                    # repealed: chain ends here
      else:
          new_text = apply(e, prev.text)           # replace / add / modify the text
          timeline.append(V(text=new_text, valid_from=e.effective_date, status="amended"))

  # 6. Flag the head of the chain.
  timeline[-1].is_current = True                   # newest window; valid_to stays null
  for v in timeline[:-1]: v.is_current = False

  # 7. Persist (idempotent — see 5A.4).
  for i, v in enumerate(timeline):
      v.uuid = generate_uuid5("art:" + C + "@" + v.valid_from)
      upsert v into ARTICLE collection (graph) and FLAT collection (Option B, with vector)
      if i > 0: link v.supersedes_article -> timeline[i-1].uuid
  # point each amendment edge at the version it PRODUCED
  for e in edges:
      produced = timeline version whose valid_from == e.effective_date
      link e.target_article -> produced.uuid
```

`apply(e, prev.text)`: `replaces`/`consolidates` → `e.new_text` becomes the text;
`adds` → append the new unit to `prev.text`; `modifies` → same as replace at the
sub-unit. Article-level granularity: a paragraph/case-scoped edit still rewrites the
whole article node's text (sub-edit applied within it).

### 5A.2 When it runs (triggers — all call the same function)
1. **After ingesting a base (older) law** → call `assemble_article_timeline(C)` for
   each of that law's articles. *This is your scenario:* the moment the old law lands,
   every amendment edge that was waiting on it gets folded in and the timeline appears.
2. **After ingesting an amending (newer) law** → for each amendment it emitted, call
   the function on its `target_canonical_id`. If the base exists, the chain **extends**
   (the old head gets a `valid_to` and `is_current=False`; a new head is appended). If
   the base isn't there yet, the edge simply stays pending until trigger 1 fires.
3. **Standalone `consolidate` / finalize pass** → re-run for every article identity
   that still has pending edges. Safe to run any time.

### 5A.3 Worked example (today = 2026-06-03)
Base `ν.4412/2016#αρ.15` enacted 2016-08-08; amended by ν.4782/2021 (eff. 2021-03-09)
and ν.5090/2024 (eff. 2024-04-01).

- **Ingest 2016 first, then 2021, then 2024:** v1 seeded; 2021 arrives → v1.valid_to=2021-03-09, append v2; 2024 arrives → v2.valid_to=2024-04-01, append v3 (is_current).
- **Ingest 2024 first (before 2016):** the 2024 edge is stored **pending** (target absent). Ingest 2021 → also pending. Ingest 2016 → seeds v1, folds the two pending edges → v2, v3. **Identical final timeline.**

Result either way:
```
v1 2016-08-08 → 2021-03-09   is_current=false
v2 2021-03-09 → 2024-04-01   is_current=false
v3 2024-04-01 → (null)       is_current=true   ← served for "now" on 2026-06-03
```
`as_of(2022-01-01)` → v2; `as_of(2018)` → v1.

### 5A.4 Idempotency & re-runs
Version UUID = `"art:"+C+"@"+valid_from`, so re-running produces the **same** ids.
Before writing, compare the computed text to what's stored; write only on change. The
**only** node that mutates on a later amendment is the previous head (its `valid_to`
flips from null to a date and `is_current`→false) plus the one new head appended. No
duplicates, no schema flags needed.

### 5A.5 Edge cases (decide once, encode explicitly)
| Case | Rule |
|---|---|
| Amendment with **no effective_date** | Cannot place on the timeline → keep pending, surface in `graph-status`. Never guess a date. |
| Two amendments **same effective_date** | Deterministic tie-break: `sub_edit_ordinal`, then `source_law_number`. |
| **`adds`** a brand-new article (no base) | The `adds` edge seeds v1 (`valid_from = its effective_date`). |
| **Repeal then later re-enactment** | Chain ends at repeal (v.status=`repealed`); a later re-enactment is a known gap → flag, handle in a later phase. |
| Target law simply **not in corpus** | Edge stays pending forever → this is the dangling-target signal (missing base law, e.g. pre-2000). |
| Sub-article scope (`paragraph`/`case`) | Applied within the article text; node granularity stays article-level. |

### 5A.6 Cost note
Each version carries its own vector (Option B). Re-assembling on a new amendment
re-embeds only the **new head** (and re-stamps the prior head's metadata — no re-embed
of unchanged versions). So steady-state embedding cost ≈ one vector per *new amendment*,
not per article per run.

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
