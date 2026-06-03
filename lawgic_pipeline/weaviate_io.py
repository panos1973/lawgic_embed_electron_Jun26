"""weaviate_io.py — tenant-aware loader for the Jun2026 collections.

Idempotent upserts via deterministic UUIDs from canonical_id; writes the flat
search record + the graph article (with versioning) + amendment/delegation edges.
"""
from __future__ import annotations
import re
import weaviate
from weaviate.util import generate_uuid5
from weaviate.classes.tenants import Tenant
import config
from models import Law, Provision, AmendmentOp
from greek_stem import stem_text
from pipeline.tables import tables_json
from pipeline.normalize import language_of


def _stem(s: str) -> str:
    """Snowball-stem a short field (summary/title); empty-safe."""
    return stem_text(s) if s else ""


def _table_json(p: Provision) -> str | None:
    """Structured JSON for any table embedded in the chunk (EXACT cell lookup).

    Honour an explicit Provision.table_json if upstream ever sets one; otherwise
    recover it from the rendered markdown table that lives in text_in_force.
    """
    return p.table_json or tables_json(p.text_in_force or "")


def connect() -> weaviate.WeaviateClient:
    config.require("WEAVIATE_API_KEY")
    return weaviate.connect_to_weaviate_cloud(
        cluster_url=config.WEAVIATE_URL,
        auth_credentials=weaviate.auth.AuthApiKey(config.WEAVIATE_API_KEY))


def ensure_tenant(client, coll_name: str, tenant: str):
    coll = client.collections.use(coll_name)
    if tenant not in set(coll.tenants.get().keys()):
        coll.tenants.create([Tenant(name=tenant)])


# ── collection registry + reset (for fast test cycles) ──
# Stable keys -> collection names, so the UI can list/reset by key without
# hardcoding the Jun2026* strings.
def collection_registry() -> dict:
    return {
        "flat": config.FLAT_COLLECTION,
        "document": config.GRAPH_DOCUMENT,
        "article": config.GRAPH_ARTICLE,
        "amendment": config.GRAPH_AMENDMENT,
        "delegation": config.GRAPH_DELEGATION,
    }


def collection_count(client, name: str, tenant: str = None) -> int:
    """Object count for one collection's tenant (-1 if the collection is absent)."""
    tenant = tenant or config.DEFAULT_TENANT
    if not client.collections.exists(name):
        return -1
    coll = client.collections.use(name)
    if tenant not in set(coll.tenants.get().keys()):
        return 0
    res = coll.with_tenant(tenant).aggregate.over_all(total_count=True)
    return int(res.total_count or 0)


def reset_collection(client, name: str, tenant: str = None) -> dict:
    """Wipe all objects of one collection for `tenant`, KEEPING the schema.

    Implemented as tenant remove+recreate: instant, and it preserves the
    collection's HNSW/RQ/BM25 config (unlike dropping the collection, which would
    force a re-run of create_all_collections.py). Returns the before/after counts.
    """
    tenant = tenant or config.DEFAULT_TENANT
    if not client.collections.exists(name):
        return {"collection": name, "tenant": tenant, "deleted": 0,
                "error": "collection does not exist"}
    coll = client.collections.use(name)
    before = collection_count(client, name, tenant)
    existing = set(coll.tenants.get().keys())
    if tenant in existing:
        coll.tenants.remove([tenant])      # drops every object for this tenant
    coll.tenants.create([Tenant(name=tenant)])   # recreate empty tenant
    return {"collection": name, "tenant": tenant,
            "deleted": before if before > 0 else 0}


def _rfc3339(date: str | None) -> str | None:
    """'YYYY-MM-DD' -> RFC3339 ('YYYY-MM-DDT00:00:00Z') for Weaviate DATE fields."""
    if date and len(date) == 10 and date[4] == "-" and date[7] == "-":
        return f"{date}T00:00:00Z"
    return None


def _version_key(canonical_id: str, valid_from: str | None) -> str:
    """Stable per-version key: article identity + the date this text became valid.
    Recomputable from an amendment edge's effective_date, so version upserts and
    cross-reference links stay idempotent across runs and ingestion orders."""
    return f"{canonical_id}@{valid_from or 'enacted'}"


def _flat_version_uuid(canonical_id: str, valid_from: str | None):
    return generate_uuid5(_version_key(canonical_id, valid_from))


def _art_version_uuid(canonical_id: str, valid_from: str | None):
    return generate_uuid5("art:" + _version_key(canonical_id, valid_from))


def _enacted_valid_from(p: Provision, law: Law = None) -> str | None:
    """RFC3339 date the provision's enacted text took effect: the provision's own
    valid_from if set, else its FEK date, else the parent law's FEK date."""
    return (_rfc3339(p.valid_from) or _rfc3339(p.fek_date)
            or _rfc3339(law.fek_date if law else None))


def _flat_props(p: Provision, law: Law = None, index: int = None,
                total: int = None) -> dict:
    props = {
        "canonical_id": p.canonical_id, "instrument_key": p.instrument_key,
        "document_type": p.instrument_type, "law_number": p.instrument_id.split(".")[-1],
        "fek_reference": f"{p.fek_series}_{p.fek_date[:4]}_{p.fek_number}" if p.fek_date else "",
        "article_number": p.article_no, "legal_force_status": p.status,
        "legal_domain": p.legal_domain, "domain_dkn": p.domain_dkn,
        "domain_eurovoc": p.domain_eurovoc, "chunk_type": p.chunk_type,
        "hierarchy_path": p.hierarchy_path, "article_title": p.article_title,
        "chunk_summary": p.chunk_summary, "chunk_text": p.text_in_force,
        "text_normalized": p.text_normalized, "text_stemmed": p.text_stemmed,
        "chunk_summary_stemmed": _stem(p.chunk_summary),
        "article_title_stemmed": _stem(p.article_title),
        "table_json": _table_json(p),
        "keywords": p.keywords, "amends_provisions": p.amends,
        "amended_by_provisions": p.amended_by, "external_law_references": p.cites,
        "language": language_of(p.text_in_force or p.text_normalized or ""),
        # deterministic document-context metadata (no LLM): publication date for
        # point-in-time filtering, parent-law title for recall/display, and the
        # chunk's position so a hit can be re-ordered within its law.
        "publication_date": _rfc3339(p.fek_date or (law.fek_date if law else "")),
        "document_title": law.title if law else "",
        "document_title_stemmed": _stem(law.title) if law else "",
        "chunk_index": index,
        "total_chunks": total,
    }
    # drop None so we never write nulls into typed (DATE/INT) fields, and so a
    # chunk with no table omits table_json rather than storing an empty value
    return {k: v for k, v in props.items() if v is not None}


def _fek_year(law: Law) -> int | None:
    if law.fek_date and len(law.fek_date) >= 4 and law.fek_date[:4].isdigit():
        return int(law.fek_date[:4])
    return None


def load_document(client, law: Law, tenant: str = None):
    """Write the Jun2026LawDocument graph node (one per law, no vectors).

    Derived from the masthead identity already on the Law plus the deterministic
    document_category classifier. Fields we do not yet extract (issuing_authority,
    effective_date) are omitted. Idempotent via generate_uuid5 on the instrument id.
    """
    tenant = tenant or law.jurisdiction or config.DEFAULT_TENANT
    ensure_tenant(client, config.GRAPH_DOCUMENT, tenant)
    doc = client.collections.use(config.GRAPH_DOCUMENT).with_tenant(tenant)

    fek_issue = int(law.fek_number) if str(law.fek_number).isdigit() else None
    props = {
        "instrument_key": law.instrument_key,
        "fek_reference": f"{law.fek_series}_{law.fek_date[:4]}_{law.fek_number}"
                         if law.fek_date else "",
        "law_number": law.instrument_id.split(".")[-1],
        "document_type": law.instrument_type,
        "document_category": law.document_category or None,
        "title": law.title,
        "publication_date": _rfc3339(law.fek_date),
        "legal_force_status": "in_force",
        "fek_type": law.fek_series or None,
        "fek_year": _fek_year(law),
        "fek_issue": fek_issue,
        "total_articles": sum(1 for p in law.provisions if p.chunk_type == "article"),
    }
    # drop None so we never write nulls into typed (DATE/INT) fields
    props = {k: v for k, v in props.items() if v is not None}
    doc.data.insert(properties=props, uuid=generate_uuid5("doc:" + law.instrument_id))


def load_law(client, law: Law, vectors: list[list[float]], tenant: str = None):
    """Write all provisions of a law (flat + graph article). Vectors aligned to law.provisions."""
    tenant = tenant or law.jurisdiction or config.DEFAULT_TENANT
    ensure_tenant(client, config.FLAT_COLLECTION, tenant)
    ensure_tenant(client, config.GRAPH_ARTICLE, tenant)
    flat = client.collections.use(config.FLAT_COLLECTION).with_tenant(tenant)
    art = client.collections.use(config.GRAPH_ARTICLE).with_tenant(tenant)

    total = len(law.provisions)
    # Each provision lands as its ENACTED version (v1): valid_from = the law's FEK
    # date, valid_to = null, is_current = True. Later amendments append further
    # versions via assemble_article_timeline. UUIDs are per-version so the history
    # is preserved (point-in-time) rather than overwritten.
    with flat.batch.dynamic() as b:
        for i, (p, vec) in enumerate(zip(law.provisions, vectors)):
            vf = _enacted_valid_from(p, law)
            props = _flat_props(p, law, i, total)
            props["version"] = p.version
            props["is_current"] = p.is_current
            if vf:
                props["valid_from"] = vf
            vt = _rfc3339(p.valid_to)
            if vt:
                props["valid_to"] = vt
            b.add_object(properties=props, vector=vec,
                         uuid=_flat_version_uuid(p.canonical_id, vf))
    if flat.batch.failed_objects:
        raise RuntimeError(f"flat load failed: {flat.batch.failed_objects[:2]}")

    with art.batch.dynamic() as b:
        for i, (p, vec) in enumerate(zip(law.provisions, vectors)):
            vf = _enacted_valid_from(p, law)
            props = {
                "canonical_id": p.canonical_id, "instrument_key": p.instrument_key,
                "document_law_number": p.instrument_id.split(".")[-1],
                "article_number": p.article_no, "article_title": p.article_title,
                "chunk_text": p.text_in_force, "table_json": _table_json(p),
                "text_normalized": p.text_normalized, "text_stemmed": p.text_stemmed,
                "chunk_summary": p.chunk_summary,
                "chunk_summary_stemmed": _stem(p.chunk_summary),
                "article_title_stemmed": _stem(p.article_title),
                "chunk_index": i, "total_chunks": total,
                "version": p.version, "is_current": p.is_current,
                "legal_force_status": p.status, "content_hash": p.content_hash,
                "hierarchy_path": p.hierarchy_path, "legal_domain": p.legal_domain,
                "domain_dkn": p.domain_dkn, "domain_eurovoc": p.domain_eurovoc,
                "keywords": p.keywords,
            }
            if vf:
                props["valid_from"] = vf
            vt = _rfc3339(p.valid_to)
            if vt:
                props["valid_to"] = vt
            b.add_object(properties={k: v for k, v in props.items() if v is not None},
                         vector=vec, uuid=_art_version_uuid(p.canonical_id, vf))
    if art.batch.failed_objects:
        raise RuntimeError(f"article load failed: {art.batch.failed_objects[:2]}")


def _target_law_number(target_id: str) -> str:
    """'ν.4675/2024#αρ.24.παρ.2' -> '4675/2024' (denormalized for filtering)."""
    head = target_id.split("#", 1)[0]
    return head.split(".")[-1] if "." in head else head


def _target_article(target_id: str) -> str:
    # capture the FULL Greek-letter suffix (6ΣΤ, 17Β, 40Δ, 151Α), not just the
    # first letter — '?' truncated multi-letter articles (6ΣΤ -> 6Σ), breaking
    # scalar filtering by article number. '.' is not in the class, so the match
    # stops cleanly before '.παρ.'/'.περ.'.
    m = re.search(r"#αρ\.(\d+[Α-Ωα-ω]*)", target_id)
    return m.group(1) if m else ""


def load_amendments(client, ops: list[AmendmentOp], source_law: Law = None,
                    tenant: str = None):
    tenant = tenant or (source_law.jurisdiction if source_law else None) \
        or config.DEFAULT_TENANT
    ensure_tenant(client, config.GRAPH_AMENDMENT, tenant)
    amd = client.collections.use(config.GRAPH_AMENDMENT).with_tenant(tenant)
    src_num = source_law.instrument_id.split(".")[-1] if source_law else ""
    with amd.batch.dynamic() as b:
        for op in ops:
            props = {
                "action": op.op, "scope": op.scope,
                "target_canonical_id": op.target_id,
                "target_law_number": _target_law_number(op.target_id),
                "target_article_number": _target_article(op.target_id),
                "new_text": op.new_text or "",
                "resolved": op.resolved,
                "extraction_method": op.extraction_method,
            }
            if src_num:
                props["source_law_number"] = src_num
            if op.source_id:                       # host provision that made the edit
                props["source_canonical_id"] = op.source_id
                sa = _target_article(op.source_id)
                if sa:
                    props["source_article_number"] = sa
            # An amendment takes effect on the publication date of the law that
            # made it, unless an explicit date was extracted. Falling back to the
            # source law's FEK date keeps amendments on the timeline instead of
            # parking every undated edit as "pending".
            ed = _rfc3339(op.effective_date) or \
                _rfc3339(source_law.fek_date if source_law else None)
            if ed:
                props["effective_date"] = ed
            b.add_object(properties=props,
                         uuid=generate_uuid5(
                             f"amd:{op.op}:{op.target_id}:{op.sub_edit_ordinal}"))
    if amd.batch.failed_objects:
        raise RuntimeError(f"amendment load failed: {amd.batch.failed_objects[:2]}")


def load_delegations(client, edges, source_law: Law = None, tenant: str = None):
    """Write Jun2026Delegation edges (FEK B implementing -> FEK A enabling).

    Writes the denormalized scalar properties. The graph cross-references
    (enabling_document/enabling_article/implementing_document) are linked by a
    later graph-linking pass once both endpoints' nodes exist — a single ingest
    cannot guarantee the enabling law is present, so we do not fabricate refs.
    """
    if not edges:
        return
    tenant = tenant or (source_law.jurisdiction if source_law else None) \
        or config.DEFAULT_TENANT
    ensure_tenant(client, config.GRAPH_DELEGATION, tenant)
    deleg = client.collections.use(config.GRAPH_DELEGATION).with_tenant(tenant)
    impl_num = source_law.instrument_id.split(".")[-1] if source_law else ""
    with deleg.batch.dynamic() as b:
        for e in edges:
            props = {
                "enabling_law_number": e.enabling_law_number,
                "enabling_article_number": e.enabling_article_number,
                "implementing_law_number": impl_num or e.implementing_id.split(".")[-1],
                "delegated_authority": e.delegated_authority,
                "delegation_scope": e.delegation_scope,
            }
            b.add_object(properties=props,
                         uuid=generate_uuid5(
                             f"deleg:{e.implementing_id}:{e.enabling_id}"))
    if deleg.batch.failed_objects:
        raise RuntimeError(f"delegation load failed: {deleg.batch.failed_objects[:2]}")


def _apply_edit(edge: dict, prev_text: str) -> str:
    """Article text AFTER applying one amendment edge to prev_text (docs §5A)."""
    action = edge.get("action")
    nt = (edge.get("new_text") or "").strip()
    if action == "repeals":
        return prev_text                       # text frozen; status marks repealed
    if action == "adds" and nt:
        return (prev_text + "\n" + nt).strip() if prev_text else nt
    if nt:                                     # replaces / consolidates / modifies
        return nt
    return prev_text


def _upsert_version(coll, uuid, props, vector=None) -> bool:
    """Insert a version node, or patch it if present. Returns True iff NEW (so
    re-runs over unchanged data write nothing)."""
    props = {k: v for k, v in props.items() if v is not None}
    existing = coll.query.fetch_object_by_id(uuid)
    if existing is None:
        coll.data.insert(properties=props, uuid=uuid, vector=vector)
        return True
    if (existing.properties or {}) == props:
        return False                           # unchanged -> idempotent no-op
    coll.data.update(uuid=uuid, properties=props, vector=vector)
    return False


# Version-specific fields recomputed per version; everything else is cloned from
# the base node so each version keeps the article's stable identity/search fields.
_VERSION_FIELDS = {"chunk_text", "text_normalized", "text_stemmed", "language",
                   "version", "is_current", "legal_force_status",
                   "valid_from", "valid_to", "content_hash"}


def _write_version_pair(flat, art, flat_base, art_base, tcid, text, vf, version,
                        status, is_current, embed, valid_to=None) -> int:
    """Upsert one version onto BOTH flat + article. Returns 1 iff a node was newly
    created (used to count versions_written)."""
    vec = embed([text]) if text else None
    vector = vec[0] if vec else None
    common = {"chunk_text": text, "language": language_of(text or ""),
              "text_stemmed": _stem(text), "text_normalized": text,
              "version": version, "is_current": is_current,
              "legal_force_status": status, "valid_from": vf, "valid_to": valid_to}
    fprops = {k: v for k, v in flat_base.items() if k not in _VERSION_FIELDS}
    aprops = {k: v for k, v in art_base.items() if k not in _VERSION_FIELDS}
    fprops.update(common); aprops.update(common)
    new_f = _upsert_version(flat, _flat_version_uuid(tcid, vf), fprops, vector)
    new_a = _upsert_version(art, _art_version_uuid(tcid, vf), aprops, vector)
    return 1 if (new_f or new_a) else 0


def assemble_article_timeline(client, tenant: str = None) -> dict:
    """Build the version timeline for every article that amendment edges target.

    Per target article: seed from its enacted version (or, if the article was
    *created* by an 'adds', from that edge), fold the amendment edges forward by
    effective_date — setting valid_from/valid_to/is_current and writing a repeal as
    a terminal 'repealed' version — and upsert one node per version on the flat +
    article collections (Option B: both temporally filterable). Implements §5A.

    Order-independent: edges whose target law is not yet ingested are counted
    `pending` and resolved on a later run. Idempotent: per-version UUIDs are
    deterministic and unchanged versions are skipped. (Amended versions recompute
    chunk_text/language/text_stemmed; the richer normalize fold is left to a future
    pass — noted in docs §5A.)

    Returns {"articles": a, "versions_written": v, "pending": p}.
    """
    from voyage_embed import embed_law_chunks

    tenant = tenant or config.DEFAULT_TENANT
    amd = client.collections.use(config.GRAPH_AMENDMENT).with_tenant(tenant)
    flat = client.collections.use(config.FLAT_COLLECTION).with_tenant(tenant)
    art = client.collections.use(config.GRAPH_ARTICLE).with_tenant(tenant)
    doc = client.collections.use(config.GRAPH_DOCUMENT).with_tenant(tenant)

    by_target: dict[str, list] = {}
    for obj in amd.iterator():
        p = obj.properties
        tcid = (p.get("target_canonical_id") or "").strip()
        if tcid and (p.get("effective_date") or "").strip():
            by_target.setdefault(tcid, []).append(p)

    articles = versions = pending = 0
    for tcid, edges in by_target.items():
        edges.sort(key=lambda e: ((e.get("effective_date") or ""),
                                  (e.get("sub_edit_ordinal") or ""),
                                  (e.get("source_law_number") or "")))
        instrument_id = tcid.split("#", 1)[0]
        docobj = doc.query.fetch_object_by_id(generate_uuid5("doc:" + instrument_id))
        base_vf = docobj.properties.get("publication_date") if docobj else None
        base_flat = (flat.query.fetch_object_by_id(_flat_version_uuid(tcid, base_vf))
                     if base_vf else None)
        base_art = (art.query.fetch_object_by_id(_art_version_uuid(tcid, base_vf))
                    if base_vf else None)

        adds = [e for e in edges if e.get("action") == "adds"]
        if base_art is None and not adds:
            pending += len(edges)              # target law not ingested yet
            continue
        articles += 1

        flat_base = (dict(base_flat.properties) if base_flat else
                     {"canonical_id": tcid, "law_number": _target_law_number(tcid),
                      "article_number": _target_article(tcid)})
        art_base = (dict(base_art.properties) if base_art else
                    {"canonical_id": tcid, "document_law_number": _target_law_number(tcid),
                     "article_number": _target_article(tcid)})

        if base_art is not None:
            cur_text = base_art.properties.get("chunk_text") or ""
            cur_vf = base_vf
            version = int(base_art.properties.get("version") or 1)
        else:                                  # 'adds' creates the article -> seed v1
            seed = adds[0]
            edges = [e for e in edges if e is not seed]
            cur_text, cur_vf, version = (seed.get("new_text") or ""), seed.get("effective_date"), 1
            versions += _write_version_pair(flat, art, flat_base, art_base, tcid,
                                            cur_text, cur_vf, version, "in_force",
                                            True, embed_law_chunks)

        for e in edges:
            new_vf = e.get("effective_date")
            new_text = _apply_edit(e, cur_text)
            if new_vf == cur_vf:               # same-date edit -> fold, no new window
                cur_text = new_text
                continue
            # close the previous window (no re-embed; metadata only)
            _write_version_pair(flat, art, flat_base, art_base, tcid, cur_text,
                                cur_vf, version, flat_base.get("legal_force_status")
                                or "in_force", False, embed_law_chunks, valid_to=new_vf)
            version += 1
            status = "repealed" if e.get("action") == "repeals" else "amended"
            versions += _write_version_pair(flat, art, flat_base, art_base, tcid,
                                            new_text, new_vf, version, status, True,
                                            embed_law_chunks)
            cur_text, cur_vf = new_text, new_vf
            if e.get("action") == "repeals":
                break

    return {"articles": articles, "versions_written": versions, "pending": pending}


# Back-compat alias: the orchestrator/CLI still call consolidate_cross_law.
consolidate_cross_law = assemble_article_timeline


def graph_status(client, tenant: str = None) -> dict:
    """Amendment-graph completeness / QA report — the extraction-accuracy lens.

    For every amendment edge, classify whether its target law is present in the
    store. A `dangling` target (target law absent after a full ingest) is either a
    missing base law (e.g. pre-2000) or a mis-resolved reference; `undated` edges
    couldn't be placed on a timeline. Read-only.

    Returns {"amendments", "target_law_present", "target_law_missing", "undated",
             "dangling_targets" (sample)}.
    """
    tenant = tenant or config.DEFAULT_TENANT
    amd = client.collections.use(config.GRAPH_AMENDMENT).with_tenant(tenant)
    doc = client.collections.use(config.GRAPH_DOCUMENT).with_tenant(tenant)

    total = present = missing = undated = 0
    dangling: set[str] = set()
    for obj in amd.iterator():
        p = obj.properties
        total += 1
        if not (p.get("effective_date") or "").strip():
            undated += 1
        tcid = (p.get("target_canonical_id") or "").strip()
        instrument_id = tcid.split("#", 1)[0] if tcid else ""
        docobj = (doc.query.fetch_object_by_id(generate_uuid5("doc:" + instrument_id))
                  if instrument_id else None)
        if docobj is None:
            missing += 1
            if tcid:
                dangling.add(tcid)
        else:
            present += 1
    return {"amendments": total, "target_law_present": present,
            "target_law_missing": missing, "undated": undated,
            "dangling_targets": sorted(dangling)[:50]}
