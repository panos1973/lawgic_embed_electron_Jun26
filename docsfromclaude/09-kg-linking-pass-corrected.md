# Lawgic KG Linking Pass — Corrected & Remapped Spec

> This supersedes the draft `LAWGIC_KG_LINKING_PASS.md`. The architecture in that
> draft is sound; this version remaps every field name, UUID derivation, tenant,
> CLI shape, and retrieval direction to what the code **actually** does, and fixes
> two contradictions (source-side linking has no backing data; delegation linking
> needs a loader change, not an extraction change).
>
> Verified against: `models.py`, `weaviate_io.py`, `create_all_collections.py`,
> `config.py`, `cli.py` (commit on branch `claude/dazzling-cannon-qcmZk`).

---

## 0. What this pass does and does NOT do

**Does:** populate the declared-but-empty Weaviate v4 cross-references so the graph
is traversable (one-hop queries), and so we can detect dangling targets and dedup.

**Does NOT:** improve amendment *extraction accuracy*. Recall/precision on the
relationships lives in `amend.py` (verb detection + nested-genitive resolution) and
`consolidate_cross_law`. The scalar `target_canonical_id` / `target_law_number` /
`target_article_number` fields already let us **find amendments today** with a plain
filter query. Linking is an ergonomics/integrity win, not an accuracy win. Name the
benefit honestly.

---

## 1. Ground truth (the draft got these wrong)

### 1.1 Canonical IDs and UUIDs
`models.py:make_provision_id` →
```
{instrument_id}#αρ.{article}[.παρ.{paragraph}]
# real example:  ν.4412/2016#αρ.15.παρ.2      (Greek type prefix + Greek locator)
```
NOT `4412/2016-art15-par2`. The instrument id keeps its Greek prefix (`ν.`, `π.δ.`).

UUIDs are `weaviate.util.generate_uuid5(...)` over **prefixed** keys (`weaviate_io.py`):

| Node | UUID key |
|------|----------|
| flat chunk    | `generate_uuid5(canonical_id)` |
| **article**   | `generate_uuid5("art:" + canonical_id)` |
| document      | `generate_uuid5("doc:" + instrument_id)` |

The draft's `deterministic_uuid(canonical_id)` (no prefix) resolves to the *flat*
node, so every article-existence check would fail. **Always derive the article UUID
as `generate_uuid5("art:" + canonical_id)`.** Reuse `generate_uuid5` — do **not**
invent a new namespace.

### 1.2 Tenant
`config.DEFAULT_TENANT = "gr"` (from `JURISDICTION`). `Jun2026…` is the **collection
name prefix**, never the tenant. All calls use `.with_tenant("gr")`.

### 1.3 Client API
Codebase uses **v4** `client.collections.use(NAME)` (not `.get`). Mirror it.

### 1.4 What the amendment edge actually stores (`load_amendments`)
Written today: `action`, `scope`, `target_canonical_id`, `target_law_number`,
`target_article_number`, `new_text`, `resolved`, `extraction_method`,
`source_law_number`, `effective_date`.

Declared in schema but **never written**: `source_article_number`,
`change_description`, `target_paragraph/case/subcase`, `confidence`.

Cross-refs declared on `Jun2026Amendment`: `source_document`, `source_article`,
`target_document`, `target_article`. On `Jun2026Delegation`: `enabling_document`,
`enabling_article`, `implementing_document`. On `Jun2026LawArticle`: `document`,
`supersedes_article`.

**Consequence:** there is **no source-article identifier** on an amendment record
(`AmendmentOp` carries only `target_id`). The source side cannot be linked from
existing data — see §4.

---

## 2. Linkability matrix (what we can populate, and from what)

| Cross-ref | Source field present? | Phase | How |
|-----------|----------------------|-------|-----|
| `Amendment.target_article` | ✅ `target_canonical_id` | **1** | `art:`+canonical → exists? → `reference_add` |
| `Amendment.target_document` | ✅ derive from `target_canonical_id.split("#")[0]` | **1** | `doc:`+instrument_id |
| `Amendment.source_article` | ❌ no source provision id stored | 2 | requires capturing host-provision id in `amend.py` |
| `Amendment.source_document` | ⚠️ `source_law_number` only (no type prefix) | 2 | needs source instrument_id |
| `Delegation.enabling_article` | ⚠️ only `enabling_law_number/article_number` written; `DelegationEdge.enabling_id` (canonical) exists but unwritten | **1b** | one-line loader change to persist `enabling_id`, then link |
| `Delegation.implementing_document` | ⚠️ `implementing_id` exists but unwritten | **1b** | persist `implementing_id`, then `doc:`+id |
| `Article.supersedes_article` | ❌ no supersedes canonical id on Provision | 2 | needs version-chain id |

Phase 1 = no changes outside the new linker + CLI. Phase 1b = trivial, sanctioned
**loader** change (persist already-computed canonical ids — not an extraction-logic
change, so it doesn't violate "don't touch amend.py"). Phase 2 = extraction change.

**Recommended scope for first PR: Phase 1 (amendment target side) only.** It is the
high-value half (it answers "what amends article X / law Y") and needs zero schema
or extraction changes.

---

## 3. Idempotency — no schema flags needed

Skip the `*_ref_populated` BOOL columns and the `add_property` migration the draft
proposed. The cross-references are self-describing: fetch the amendment with
`return_references=[QueryReference(link_on="target_article")]` and **skip if already
populated**. This removes a schema migration and a class of flag-vs-reality drift.
`reference_add` on an already-present, identical ref is also harmless. Mirror the
exact idempotency style of `consolidate_cross_law` (re-run applies nothing new).

---

## 4. Source-side linking — the contradiction to resolve

The draft wants `source_article` populated but also says "do not modify `amend.py`".
Both cannot hold: nothing stores which provision *emitted* the amendment. To enable
it (Phase 2), the minimal change is:
- add `source_id: Optional[str]` to `AmendmentOp` (`models.py`), set it in
  `amend.py` to the host provision's `canonical_id` (already in scope at emit time);
- write `source_canonical_id` + `source_article_number` in `load_amendments`;
- link `source_article` = `generate_uuid5("art:" + source_canonical_id)`.

Until you authorize that, Phase 1 ships target-side only and source refs stay empty
(honestly reported by `graph-status`).

---

## 5. New code

### 5.1 `weaviate_io.py` — add `link_graph()` beside `consolidate_cross_law`
Placement note: the draft put this in `pipeline/kg_linker.py`, but our loaders and
the analogous store-level pass live in `weaviate_io.py`. Keep it there for
consistency. Sketch (mirrors the existing iterator+generate_uuid5 style):

```python
def link_graph(client, tenant: str = None, dry_run: bool = False) -> dict:
    """Populate declared cross-references from resolved scalar ids. Idempotent:
    skips refs already present. Phase 1 = Amendment target side only."""
    from weaviate.util import generate_uuid5
    from weaviate.classes.query import QueryReference

    tenant = tenant or config.DEFAULT_TENANT
    amd = client.collections.use(config.GRAPH_AMENDMENT).with_tenant(tenant)
    art = client.collections.use(config.GRAPH_ARTICLE).with_tenant(tenant)
    doc = client.collections.use(config.GRAPH_DOCUMENT).with_tenant(tenant)
    stats = {"linked_article": 0, "linked_document": 0,
             "already": 0, "missing_target": 0}

    for obj in amd.iterator(return_references=[
            QueryReference(link_on="target_article"),
            QueryReference(link_on="target_document")]):
        tcid = (obj.properties.get("target_canonical_id") or "").strip()
        if not tcid:
            continue
        refs = obj.references or {}

        # --- target_article ---
        if not (refs.get("target_article") and refs["target_article"].objects):
            art_uuid = generate_uuid5("art:" + tcid)
            if art.query.fetch_object_by_id(art_uuid) is not None:
                if not dry_run:
                    amd.data.reference_add(from_uuid=obj.uuid,
                        from_property="target_article", to=art_uuid)
                stats["linked_article"] += 1
            else:
                stats["missing_target"] += 1            # target law not yet ingested
        else:
            stats["already"] += 1

        # --- target_document (instrument id is the part before '#') ---
        if not (refs.get("target_document") and refs["target_document"].objects):
            instrument_id = tcid.split("#")[0]
            doc_uuid = generate_uuid5("doc:" + instrument_id)
            if doc.query.fetch_object_by_id(doc_uuid) is not None and not dry_run:
                amd.data.reference_add(from_uuid=obj.uuid,
                    from_property="target_document", to=doc_uuid)
                stats["linked_document"] += 1
    return stats
```
Notes:
- `amd.iterator(...)` already paginates server-side — no manual cursor needed (the
  draft's `limit=10000` + manual cursor is unnecessary here).
- `fetch_object_by_id` returns `None` when absent → that's the "target not yet
  ingested, retry later" branch, exactly like `consolidate_cross_law`.

### 5.2 `graph_status()` — completeness report
Count amendments total vs. those whose `target_article` ref is populated (iterate
with `return_references`, or aggregate). Return
`{"amendments": N, "target_article_linked": k, "target_pending": N-k}`.

### 5.3 Retrieval — `get_article_with_amendments()` (works pre- *and* post-linking)
**Do not** traverse `amended_by_amendments` from the article — no such reverse ref
exists. Query the Amendment collection by the scalar (robust, link-independent):
```python
from weaviate.classes.query import Filter
amd.query.fetch_objects(
    filters=Filter.by_property("target_canonical_id").equal(canonical_id))
```
Return the article node + the list of amendment edges targeting it (action, scope,
new_text, effective_date, source_law_number, extraction_method). Optionally also
fetch the article with `return_references=["target_article"...]` once linked, but the
scalar filter is the dependable path and needs no graph at all.

---

## 6. CLI — subcommands, not flags

`cli.py` uses subparsers dispatched by a dict (`cli.py:278`). Mirror `consolidate`:
```python
sub.add_parser("link-graph").add_argument("--dry-run", action="store_true")
sub.add_parser("graph-status")
# dispatch:
"link-graph": lambda: cmd_link_graph(args.dry_run),
"graph-status": cmd_graph_status,
```
`cmd_link_graph`/`cmd_graph_status` open the client like `cmd_consolidate` and
`emit({"type": "link-graph", **stats})` — one JSON object per line, honoring `--json`.
Optionally call `wio.link_graph(client)` at the tail of `cmd_ingest` right after the
existing `consolidate_cross_law` step (`cli.py:113`) so backward ingestion resolves
forward refs incrementally.

Electron IPC button = Phase 2 (spawn `cli.py link-graph --json`); not in first PR.

---

## 7. Tests (`tests/test_weaviate_io.py`, reuse the `_FakeClient`)
The existing fake client already records `data.insert/update` and supports the loader
tests. Extend it with `reference_add` capture and `fetch_object_by_id`, then:
1. two-law fixture with a known cross-law amendment → `link_graph` links target side,
   stats `linked_article == 1`.
2. target law absent → `missing_target == 1`, nothing linked, no error.
3. idempotency → second run links 0, `already` > 0, no duplicate `reference_add`.
4. retrieval-by-scalar returns the amendment without any links present.

---

## 8. Implementation order (first PR = Phase 1)
1. `link_graph()` + `graph_status()` in `weaviate_io.py` (target side only).
2. `link-graph` / `graph-status` subcommands in `cli.py`; optional ingest-tail call.
3. `get_article_with_amendments()` (scalar-filter retrieval).
4. Tests above. Run `python -m pytest tests/ -q` (currently 210 passing).
5. Report `graph-status` before/after on a real 2-law sample.

Phase 1b (delegation): persist `enabling_id`/`implementing_id` in `load_delegations`,
extend `link_graph` for the delegation refs. Phase 2 (source side, supersedes,
Electron button): only after the above is verified against the live `gr` tenant.

---

## 9. Decisions still open (defaults chosen below; override if needed)
- **Scope of first PR:** Phase 1, amendment **target** side only. *(default)*
- **Idempotency:** ref-presence check, **no** BOOL flags / no schema migration. *(default)*
- **Placement:** `weaviate_io.py`, not a new `pipeline/kg_linker.py`. *(default)*
- **Source-side links:** deferred to Phase 2 (needs the `amend.py` `source_id`
  change). Say the word to pull it forward.
