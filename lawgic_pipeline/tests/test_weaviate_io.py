"""Unit tests for weaviate_io loaders using an in-memory fake client.

Verifies property construction, tenant usage, idempotent UUIDs and null-dropping
for the document + amendment writers — no real Weaviate, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import weaviate_io as wio  # noqa: E402  (conftest stubs the weaviate SDK)
from models import Law, Provision, AmendmentOp, TYPE_NOMOS  # noqa: E402


class _FakeData:
    def __init__(self, sink, name):
        self.sink, self.name = sink, name

    def insert(self, properties, uuid):
        self.sink.setdefault(self.name, []).append({"props": properties, "uuid": uuid})


class _FakeBatchCtx:
    def __init__(self, sink, name):
        self.sink, self.name = sink, name

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def add_object(self, properties, uuid, vector=None):
        self.sink.setdefault(self.name, []).append({"props": properties, "uuid": uuid})


class _FakeBatch:
    def __init__(self, sink, name):
        self.sink, self.name = sink, name
        self.failed_objects = []

    def dynamic(self):
        return _FakeBatchCtx(self.sink, self.name)


class _FakeTenants:
    def get(self):
        return {"gr": object()}            # tenant already exists -> no create

    def create(self, tenants):             # pragma: no cover
        pass


class _FakeCollection:
    def __init__(self, sink, name):
        self.data = _FakeData(sink, name)
        self.batch = _FakeBatch(sink, name)
        self.tenants = _FakeTenants()
        self._with = name

    def with_tenant(self, t):
        self.last_tenant = t
        return self


class _FakeCollections:
    def __init__(self, sink):
        self.sink = sink

    def use(self, name):
        return _FakeCollection(self.sink, name)


class _FakeClient:
    def __init__(self):
        self.sink = {}
        self.collections = _FakeCollections(self.sink)


def _law():
    law = Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
              instrument_type=TYPE_NOMOS, title="Δοκιμαστικός νόμος",
              fek_series="Α", fek_number="52", fek_date="2024-03-26")
    law.provisions.append(Provision(
        canonical_id="ν.5090/2024#αρ.1", instrument_id="ν.5090/2024",
        instrument_key="N5090/2024", instrument_type=TYPE_NOMOS,
        article_no="1", chunk_type="article", text_in_force="κείμενο"))
    return law


def test_load_document_props_and_idempotent_uuid():
    c = _FakeClient()
    law = _law()
    wio.load_document(c, law)
    recs = c.sink["Jun2026LawDocument"]
    assert len(recs) == 1
    props = recs[0]["props"]
    assert props["instrument_key"] == "N5090/2024"
    assert props["fek_reference"] == "Α_2024_52"
    assert props["publication_date"] == "2024-03-26T00:00:00Z"   # RFC3339
    assert props["fek_year"] == 2024 and props["fek_issue"] == 52
    assert props["total_articles"] == 1
    # idempotent: same uuid every run
    c2 = _FakeClient(); wio.load_document(c2, _law())
    assert recs[0]["uuid"] == c2.sink["Jun2026LawDocument"][0]["uuid"]


def test_load_document_drops_null_dates():
    c = _FakeClient()
    law = _law(); law.fek_date = ""        # no date
    wio.load_document(c, law)
    props = c.sink["Jun2026LawDocument"][0]["props"]
    assert "publication_date" not in props  # null DATE dropped, not written as None
    assert "fek_year" not in props


def test_load_amendments_denormalizes_target():
    c = _FakeClient()
    ops = [AmendmentOp(op="replaces", target_id="ν.4675/2024#αρ.24.παρ.2",
                       scope="paragraph", new_text="νέο", resolved=True,
                       sub_edit_ordinal="1")]
    wio.load_amendments(c, ops, source_law=_law())
    rec = c.sink["Jun2026Amendment"][0]["props"]
    assert rec["target_law_number"] == "4675/2024"
    assert rec["target_article_number"] == "24"
    assert rec["source_law_number"] == "5090/2024"
    assert rec["action"] == "replaces" and rec["resolved"] is True
    # default extractor label round-trips (deterministic ops keep this default)
    assert rec["extraction_method"] == "pattern_matching"


def test_load_amendments_denormalizes_multiletter_article():
    # multi-letter Greek article suffixes (6ΣΤ, 17Β, 40Δ) must survive into the
    # scalar target_article_number — the old '?' regex truncated 6ΣΤ -> 6Σ and
    # broke filtering by article number for those articles.
    c = _FakeClient()
    ops = [AmendmentOp(op="adds", target_id="ν.4186/2013#αρ.6ΣΤ",
                       scope="article", new_text="…", resolved=False,
                       sub_edit_ordinal="0")]
    wio.load_amendments(c, ops, source_law=_law())
    rec = c.sink["Jun2026Amendment"][0]["props"]
    assert rec["target_article_number"] == "6ΣΤ"
    assert rec["target_canonical_id"] == "ν.4186/2013#αρ.6ΣΤ"


def test_load_amendments_records_real_extraction_method():
    # an op stamped by the LLM extractor must write its real method, not the
    # previously-hardcoded "pattern_matching" constant.
    c = _FakeClient()
    ops = [AmendmentOp(op="adds", target_id="ν.4763/2020#αρ.5.παρ.2",
                       scope="subcase", new_text="…", resolved=False,
                       sub_edit_ordinal="0", extraction_method="llm:deepseek")]
    wio.load_amendments(c, ops, source_law=_law())
    assert c.sink["Jun2026Amendment"][0]["props"]["extraction_method"] == "llm:deepseek"


def test_flat_and_article_write_bm25_fields():
    """The Greek BM25 recall fields must be populated on BOTH search collections,
    under the exact names the schema declares (text_normalized / text_stemmed +
    the stemmed summary/title)."""
    from pipeline import segment
    from models import Law
    law = Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
              instrument_type=TYPE_NOMOS, title="Δοκιμή", fek_date="2024-03-26")
    segment.segment("Άρθρο 1\nΟι διατάξεις των νόμων τροποποιούνται.\n", law)
    law.provisions[0].chunk_summary = "Σύνοψη των τροποποιήσεων."
    c = _FakeClient()
    wio.load_law(c, law, [[0.0] * 4])

    flat = c.sink["Jun2026GRLegaDocs"][0]["props"]
    art = c.sink["Jun2026LawArticle"][0]["props"]
    for props in (flat, art):
        assert props["text_normalized"] and props["text_stemmed"]
        # stemmed differs from folded (inflection collapsed): νόμων->νομ etc.
        assert props["text_stemmed"] != props["text_normalized"]
        assert props["chunk_summary_stemmed"]                 # summary stemmed
    assert flat["article_title_stemmed"]                      # title stemmed


def test_loader_props_are_declared_in_schema():
    """Guard against the chunk_text_stemmed/text_stemmed class of bug: every
    property the loaders write must be a property the schema declares, else the
    data lands on an auto-schema field and the configured BM25 index stays empty.
    Parses create_all_collections.py statically (no Weaviate needed)."""
    import ast
    import config
    from pipeline import segment
    from models import Law

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src = open(os.path.join(root, "create_all_collections.py"), encoding="utf-8").read()
    tree = ast.parse(src)

    # Collections are built via delete_and_create(client, <NAME_CONST>, properties=[...]).
    # Resolve the collection-name module constants (FLAT_COLLECTION = "Jun2026...")
    # then collect each call's declared Property(name=...).
    name_consts = {n.targets[0].id: n.value.value
                   for n in tree.body
                   if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                   and isinstance(n.targets[0], ast.Name)}

    declared: dict[str, set] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "delete_and_create"):
            continue
        # 2nd positional arg is the collection-name constant (a Name)
        name = None
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Name):
            name = name_consts.get(node.args[1].id)
        props = set()
        for kw in node.keywords:
            if kw.arg != "properties":
                continue
            for elt in kw.value.elts:                         # Property(...) calls
                for pkw in getattr(elt, "keywords", []):
                    if pkw.arg == "name" and isinstance(pkw.value, ast.Constant):
                        props.add(pkw.value.value)
        if name:
            declared[name] = props

    # build a representative law and capture what each loader actually writes
    law = Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
              instrument_type=TYPE_NOMOS, title="Δοκιμή", fek_date="2024-03-26")
    segment.segment("Άρθρο 1\nΟι διατάξεις τροποποιούνται.\n", law)
    c = _FakeClient()
    wio.load_law(c, law, [[0.0] * 4])
    wio.load_document(c, law)

    pairs = [(config.FLAT_COLLECTION, c.sink["Jun2026GRLegaDocs"][0]["props"]),
             (config.GRAPH_ARTICLE, c.sink["Jun2026LawArticle"][0]["props"]),
             (config.GRAPH_DOCUMENT, c.sink["Jun2026LawDocument"][0]["props"])]
    for coll, written in pairs:
        undeclared = set(written) - declared.get(coll, set())
        assert not undeclared, f"{coll}: loader writes undeclared props {undeclared}"


# --- cross-law consolidation: self-contained fake store -------------------------
from weaviate.util import generate_uuid5  # noqa: E402


class _ConsoData:
    def __init__(self, store):
        self.store = store
        self.updates = []

    def update(self, uuid, properties, vector=None):
        self.updates.append(uuid)
        if uuid in self.store:
            self.store[uuid].update(properties)


class _ConsoQuery:
    def __init__(self, store):
        self.store = store

    def fetch_object_by_id(self, uuid):
        if uuid not in self.store:
            return None
        return type("Obj", (), {"properties": self.store[uuid]})


class _ConsoColl:
    def __init__(self, store, edges=None):
        self.data = _ConsoData(store)
        self.query = _ConsoQuery(store)
        self._edges = edges or []

    def with_tenant(self, t):
        return self

    def iterator(self):
        for e in self._edges:
            yield type("E", (), {"uuid": e["uuid"], "properties": e["props"]})


class _ConsoClient:
    def __init__(self, store, edges):
        self._flat = _ConsoColl(store)
        self._art = _ConsoColl(store)
        self._amd = _ConsoColl(store, edges)

    def collections_use_map(self):
        return {"flat": self._flat, "art": self._art, "amd": self._amd}

    class _Collections:
        def __init__(self, outer):
            self.outer = outer

        def use(self, name):
            import config
            return {config.FLAT_COLLECTION: self.outer._flat,
                    config.GRAPH_ARTICLE: self.outer._art,
                    config.GRAPH_AMENDMENT: self.outer._amd}[name]

    @property
    def collections(self):
        return _ConsoClient._Collections(self)


def test_consolidate_cross_law_applies_then_idempotent(monkeypatch):
    import voyage_embed as ve
    monkeypatch.setattr(ve, "embed_law_chunks", lambda chunks: [[0.0] * 4 for _ in chunks])

    target_id = "ν.4675/2024#αρ.24"
    store = {generate_uuid5(target_id): {"chunk_text": "παλιό"},
             generate_uuid5("art:" + target_id): {"chunk_text": "παλιό"}}
    edges = [{"uuid": "e1", "props": {"action": "replaces",
                                      "new_text": "νέο κείμενο",
                                      "target_canonical_id": target_id}}]
    client = _ConsoClient(store, edges)

    r1 = wio.consolidate_cross_law(client, tenant="gr")
    assert r1["applied"] == 1 and r1["skipped_missing_target"] == 0
    assert store[generate_uuid5(target_id)]["chunk_text"] == "νέο κείμενο"

    # second run: target already holds new text -> no-op
    r2 = wio.consolidate_cross_law(client, tenant="gr")
    assert r2["applied"] == 0 and r2["already"] == 1


def test_consolidate_skips_missing_target(monkeypatch):
    import voyage_embed as ve
    monkeypatch.setattr(ve, "embed_law_chunks", lambda chunks: [[0.0] * 4 for _ in chunks])
    edges = [{"uuid": "e1", "props": {"action": "replaces", "new_text": "νέο",
                                      "target_canonical_id": "ν.9999/2099#αρ.1"}}]
    client = _ConsoClient({}, edges)         # empty store -> target not ingested
    r = wio.consolidate_cross_law(client, tenant="gr")
    assert r["applied"] == 0 and r["skipped_missing_target"] == 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}"); passed += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
