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


def _flat_props(p: Provision) -> dict:
    return {
        "canonical_id": p.canonical_id, "instrument_key": p.instrument_key,
        "document_type": p.instrument_type, "law_number": p.instrument_id.split(".")[-1],
        "fek_reference": f"{p.fek_series}_{p.fek_date[:4]}_{p.fek_number}" if p.fek_date else "",
        "article_number": p.article_no, "legal_force_status": p.status,
        "legal_domain": p.legal_domain, "domain_dkn": p.domain_dkn,
        "domain_eurovoc": p.domain_eurovoc, "chunk_type": p.chunk_type,
        "hierarchy_path": p.hierarchy_path, "article_title": p.article_title,
        "chunk_summary": p.chunk_summary, "chunk_text": p.text_in_force,
        "text_normalized": p.text_normalized, "text_stemmed": p.text_stemmed,
        "table_json": p.table_json,
        "keywords": p.keywords, "amends_provisions": p.amends,
        "amended_by_provisions": p.amended_by, "external_law_references": p.cites,
        "language": "el",
    }


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

    with flat.batch.dynamic() as b:
        for p, vec in zip(law.provisions, vectors):
            b.add_object(properties=_flat_props(p), vector=vec,
                         uuid=generate_uuid5(p.canonical_id))
    if flat.batch.failed_objects:
        raise RuntimeError(f"flat load failed: {flat.batch.failed_objects[:2]}")

    with art.batch.dynamic() as b:
        for p, vec in zip(law.provisions, vectors):
            b.add_object(properties={
                "canonical_id": p.canonical_id, "instrument_key": p.instrument_key,
                "document_law_number": p.instrument_id.split(".")[-1],
                "article_number": p.article_no, "article_title": p.article_title,
                "chunk_text": p.text_in_force, "table_json": p.table_json,
                "chunk_summary": p.chunk_summary, "version": p.version,
                "valid_from": p.valid_from, "valid_to": p.valid_to,
                "is_current": p.is_current, "content_hash": p.content_hash,
                "hierarchy_path": p.hierarchy_path, "legal_domain": p.legal_domain,
                "domain_dkn": p.domain_dkn, "domain_eurovoc": p.domain_eurovoc,
                "keywords": p.keywords,
            }, vector=vec, uuid=generate_uuid5("art:" + p.canonical_id))
    if art.batch.failed_objects:
        raise RuntimeError(f"article load failed: {art.batch.failed_objects[:2]}")


def _target_law_number(target_id: str) -> str:
    """'ν.4675/2024#αρ.24.παρ.2' -> '4675/2024' (denormalized for filtering)."""
    head = target_id.split("#", 1)[0]
    return head.split(".")[-1] if "." in head else head


def _target_article(target_id: str) -> str:
    m = re.search(r"#αρ\.(\d+[Α-Ωα-ω]?)", target_id)
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
                "extraction_method": "pattern_matching",
            }
            if src_num:
                props["source_law_number"] = src_num
            ed = _rfc3339(op.effective_date)
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


def consolidate_cross_law(client, tenant: str = None) -> dict:
    """Store-level cross-law consolidation pass.

    Walks unapplied replace/consolidate amendment edges whose target law is
    already ingested, and rewrites the target provision's in-force text (flat +
    article collections) to the new text. This is the half of consolidation that
    cannot happen during a single document's run, because the target law's text
    only exists once that law has itself been ingested.

    Idempotent without any schema change: an edge is skipped when the target
    provision already holds the new text (so re-running applies nothing new).
    Tenant-scoped. Uses the deterministic UUID of the target provision to
    fetch/patch it — no vector search (pinpoint via metadata only, honouring the
    non-vector-for-pinpoint rule).

    Returns {"applied": n, "already": k, "skipped_missing_target": m}.
    """
    from voyage_embed import embed_law_chunks

    tenant = tenant or config.DEFAULT_TENANT
    amd = client.collections.use(config.GRAPH_AMENDMENT).with_tenant(tenant)
    flat = client.collections.use(config.FLAT_COLLECTION).with_tenant(tenant)
    art = client.collections.use(config.GRAPH_ARTICLE).with_tenant(tenant)

    applied = already = skipped = 0
    # only replace/consolidate edges carry replacement text worth applying
    for obj in amd.iterator():
        p = obj.properties
        if p.get("action") not in ("replaces", "consolidates"):
            continue
        new_text = (p.get("new_text") or "").strip()
        target_id = p.get("target_canonical_id") or ""
        if not new_text or not target_id:
            continue

        target_uuid = generate_uuid5(target_id)
        existing = flat.query.fetch_object_by_id(target_uuid)
        if existing is None:
            skipped += 1                       # target law not yet ingested
            continue
        if (existing.properties or {}).get("chunk_text", "").strip() == new_text:
            already += 1                       # already consolidated -> no-op
            continue

        vec = embed_law_chunks([new_text])
        vector = vec[0] if vec else None
        flat.data.update(uuid=target_uuid,
                         properties={"chunk_text": new_text,
                                     "legal_force_status": "amended"},
                         vector=vector)
        art.data.update(uuid=generate_uuid5("art:" + target_id),
                        properties={"chunk_text": new_text, "is_current": True},
                        vector=vector)
        applied += 1

    return {"applied": applied, "already": already,
            "skipped_missing_target": skipped}
