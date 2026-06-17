"""Unit tests for weaviate_io loaders using an in-memory fake client.

Verifies property construction, tenant usage, idempotent UUIDs and null-dropping
for the document + amendment writers — no real Weaviate, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import weaviate_io as wio  # noqa: E402  (conftest stubs the weaviate SDK)
from models import (Law, Provision, AmendmentOp, DelegationEdge,  # noqa: E402
                    TYPE_NOMOS)


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


def test_load_amendments_writes_source_and_effective_date_fallback():
    # source_id -> source_canonical_id (+ derived source_article_number); an op
    # with no explicit effective_date falls back to the amending law's FEK date.
    c = _FakeClient()
    ops = [AmendmentOp(op="replaces", target_id="ν.4675/2024#αρ.24",
                       scope="article", new_text="νέο", resolved=True,
                       sub_edit_ordinal="1", source_id="ν.5090/2024#αρ.3")]
    wio.load_amendments(c, ops, source_law=_law())   # _law().fek_date == 2024-03-26
    rec = c.sink["Jun2026Amendment"][0]["props"]
    assert rec["source_canonical_id"] == "ν.5090/2024#αρ.3"
    assert rec["source_article_number"] == "3"
    assert rec["effective_date"] == "2024-03-26T00:00:00Z"   # fell back to source law


def test_load_amendments_explicit_effective_date_wins():
    c = _FakeClient()
    ops = [AmendmentOp(op="replaces", target_id="ν.4675/2024#αρ.24", scope="article",
                       new_text="νέο", effective_date="2025-01-01", sub_edit_ordinal="1")]
    wio.load_amendments(c, ops, source_law=_law())
    assert c.sink["Jun2026Amendment"][0]["props"]["effective_date"] == "2025-01-01T00:00:00Z"


def test_load_amendments_records_real_extraction_method():
    # an op stamped by the LLM extractor must write its real method, not the
    # previously-hardcoded "pattern_matching" constant.
    c = _FakeClient()
    ops = [AmendmentOp(op="adds", target_id="ν.4763/2020#αρ.5.παρ.2",
                       scope="subcase", new_text="…", resolved=False,
                       sub_edit_ordinal="0", extraction_method="llm:deepseek")]
    wio.load_amendments(c, ops, source_law=_law())
    assert c.sink["Jun2026Amendment"][0]["props"]["extraction_method"] == "llm:deepseek"


def test_load_law_denormalizes_amends_provisions_from_source_id():
    # the source article's flat record should list WHAT it amends (from edge.source_id),
    # so a hit on it shows the relationship without a graph traversal.
    law = _law()                                   # provision ν.5090/2024#αρ.1
    law.amendments.append(AmendmentOp(
        op="replaces", target_id="ν.4675/2024#αρ.24", scope="article",
        new_text="νέο", resolved=False, sub_edit_ordinal="0",
        source_id="ν.5090/2024#αρ.1"))
    c = _FakeClient()
    wio.load_law(c, law, [[0.0] * 4])
    flat = c.sink["Jun2026GRLegaDocs"][0]["props"]
    assert flat["amends_provisions"] == ["ν.4675/2024#αρ.24:replaces"]


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


def test_loader_populates_table_json_and_language():
    """A chunk that carries a rendered markdown table must get a structured
    table_json (not None) on both search collections, and the language tag must
    reflect the actual script mix instead of a hard-coded 'el'."""
    import json
    law = _law()
    p = law.provisions[0]
    p.text_in_force = ("Πίνακας τελών\n| Υπηρεσία | Τέλος |\n| --- | --- |\n"
                       "| Α | 10 |\n| Β | 20 |")
    c = _FakeClient()
    wio.load_law(c, law, [[0.0] * 4])
    flat = c.sink["Jun2026GRLegaDocs"][0]["props"]
    art = c.sink["Jun2026LawArticle"][0]["props"]
    for props in (flat, art):
        assert json.loads(props["table_json"]) == \
            [[["Υπηρεσία", "Τέλος"], ["Α", "10"], ["Β", "20"]]]
    assert flat["language"] == "el"           # Greek chunk -> el (not hard-coded)
    # deterministic doc-context metadata now populated (was declared-but-empty)
    assert flat["publication_date"] == "2024-03-26T00:00:00Z"   # from law.fek_date
    assert flat["document_title"] == "Δοκιμαστικός νόμος"
    assert flat["document_title_stemmed"]
    assert flat["chunk_index"] == 0 and flat["total_chunks"] == 1
    assert art["chunk_index"] == 0 and art["total_chunks"] == 1


def test_loader_table_json_none_and_language_for_english_chunk():
    law = _law()
    law.provisions[0].text_in_force = (
        "The Security Council, acting under Chapter VII, decides to extend the "
        "mandate and authorises the measures set out herein.")
    c = _FakeClient()
    wio.load_law(c, law, [[0.0] * 4])
    flat = c.sink["Jun2026GRLegaDocs"][0]["props"]
    assert flat.get("table_json") is None     # no table -> key omitted (no null)
    assert flat["language"] == "en"           # verbatim English body tagged en


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

    # build a representative law and capture what each loader actually writes.
    # The amendment op carries source_id + effective_date so the optional scalar
    # props (source_*/effective_date) are exercised too.
    law = Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
              instrument_type=TYPE_NOMOS, title="Δοκιμή", fek_date="2024-03-26")
    segment.segment("Άρθρο 1\nΟι διατάξεις τροποποιούνται.\n", law)
    c = _FakeClient()
    wio.load_law(c, law, [[0.0] * 4])
    wio.load_document(c, law)
    wio.load_amendments(c, [AmendmentOp(
        op="replaces", target_id="ν.4675/2024#αρ.24.παρ.2", scope="paragraph",
        new_text="νέο", resolved=True, sub_edit_ordinal="1",
        source_id="ν.5090/2024#αρ.3", effective_date="2025-01-01")], source_law=law)
    wio.load_delegations(c, [DelegationEdge(
        enabling_id="ν.4412/2016#αρ.5", implementing_id="Β΄913/2025#1",
        enabling_law_number="4412/2016", enabling_article_number="5",
        delegated_authority="Υπουργός", delegation_scope="σκοπός")], source_law=law)

    pairs = [(config.FLAT_COLLECTION, c.sink["Jun2026GRLegaDocs"][0]["props"]),
             (config.GRAPH_ARTICLE, c.sink["Jun2026LawArticle"][0]["props"]),
             (config.GRAPH_DOCUMENT, c.sink["Jun2026LawDocument"][0]["props"]),
             (config.GRAPH_AMENDMENT, c.sink["Jun2026Amendment"][0]["props"]),
             (config.GRAPH_DELEGATION, c.sink["Jun2026Delegation"][0]["props"])]
    for coll, written in pairs:
        undeclared = set(written) - declared.get(coll, set())
        assert not undeclared, f"{coll}: loader writes undeclared props {undeclared}"


# --- versioned timeline assembly: full in-memory store (all collections) --------
from weaviate.util import generate_uuid5  # noqa: E402
import datetime as _dt  # noqa: E402

# Weaviate returns DATE properties as datetime objects, not strings. The fake store
# keeps what was written (strings), so coerce date fields on READ to mimic the real
# client — this is what surfaced the consolidate '.strip() on datetime' crash.
_VDATE_FIELDS = {"effective_date", "publication_date", "valid_from", "valid_to"}


def _vread(d):
    out = dict(d)
    for f in _VDATE_FIELDS:
        v = out.get(f)
        if isinstance(v, str) and len(v) >= 10 and v[4:5] == "-":
            try:
                out[f] = _dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                pass
    return out


class _VColl:
    """One collection over a shared {name: {uuid: props}} store; supports the
    batch/insert/update/fetch/iterator surface assemble_article_timeline uses."""
    def __init__(self, store, name):
        self.store, self.name = store, name
        self.failed_objects = []

    def with_tenant(self, t):
        return self

    @property
    def tenants(self):
        return type("T", (), {"get": lambda self_: {"gr": object()},
                              "create": lambda self_, ts: None})()

    # batch
    @property
    def batch(self):
        return self

    def dynamic(self):
        store, name = self.store, self.name

        class Ctx:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def add_object(self_, properties, uuid, vector=None):
                store.setdefault(name, {})[uuid] = dict(properties)
        return Ctx()

    # data
    @property
    def data(self):
        store, name = self.store, self.name

        class D:
            def insert(self_, properties, uuid, vector=None):
                store.setdefault(name, {})[uuid] = dict(properties)

            def update(self_, uuid, properties, vector=None):
                store.setdefault(name, {}).setdefault(uuid, {}).update(properties)
        return D()

    # query
    @property
    def query(self):
        store, name = self.store, self.name

        class Q:
            def fetch_object_by_id(self_, uuid):
                d = store.get(name, {}).get(uuid)
                return None if d is None else type("Obj", (), {"properties": _vread(d), "uuid": uuid})
        return Q()

    def iterator(self, **kw):
        for uuid, d in list(self.store.get(self.name, {}).items()):
            yield type("E", (), {"uuid": uuid, "properties": _vread(d)})


class _VClient:
    def __init__(self):
        self.store = {}
        outer = self

        class Colls:
            def use(self_, name): return _VColl(outer.store, name)
            def exists(self_, name): return True
        self.collections = Colls()

    def close(self):
        pass


def _base_law():
    law = Law(instrument_id="ν.4412/2016", instrument_key="N4412/2016",
              instrument_type=TYPE_NOMOS, title="Βάση", fek_date="2016-08-08")
    law.provisions.append(Provision(
        canonical_id="ν.4412/2016#αρ.15", instrument_id="ν.4412/2016",
        instrument_key="N4412/2016", instrument_type=TYPE_NOMOS,
        article_no="15", chunk_type="article", text_in_force="αρχικό κείμενο"))
    return law


def _amending_law(date):
    return Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
               instrument_type=TYPE_NOMOS, fek_date=date)


def test_timeline_builds_v2_and_closes_v1(monkeypatch):
    import voyage_embed as ve
    monkeypatch.setattr(ve, "embed_law_chunks", lambda ch: [[0.0] * 4 for _ in ch])
    c = _VClient()
    base = _base_law()
    wio.load_document(c, base)
    wio.load_law(c, base, [[0.0] * 4])
    wio.load_amendments(c, [AmendmentOp(
        op="replaces", target_id="ν.4412/2016#αρ.15", scope="article",
        new_text="νέο κείμενο", effective_date="2024-04-01", sub_edit_ordinal="1",
        source_id="ν.5090/2024#αρ.2")], source_law=_amending_law("2024-04-01"))

    r = wio.assemble_article_timeline(c, tenant="gr")
    assert r["articles"] == 1 and r["versions_written"] == 1 and r["pending"] == 0

    arts = c.store["Jun2026LawArticle"]
    v1 = arts[wio._art_version_uuid("ν.4412/2016#αρ.15", "2016-08-08T00:00:00Z")]
    v2 = arts[wio._art_version_uuid("ν.4412/2016#αρ.15", "2024-04-01T00:00:00Z")]
    assert v1["valid_to"] == "2024-04-01T00:00:00Z" and v1["is_current"] is False
    assert v2["chunk_text"] == "νέο κείμενο" and v2["is_current"] is True
    assert v2["valid_from"] == "2024-04-01T00:00:00Z" and v2["version"] == 2
    # flat collection is versioned too (Option B)
    assert wio._flat_version_uuid("ν.4412/2016#αρ.15", "2024-04-01T00:00:00Z") \
        in c.store["Jun2026GRLegaDocs"]
    # idempotent: a second run writes no new version
    assert wio.assemble_article_timeline(c, tenant="gr")["versions_written"] == 0


def test_timeline_out_of_order_then_resolves(monkeypatch):
    import voyage_embed as ve
    monkeypatch.setattr(ve, "embed_law_chunks", lambda ch: [[0.0] * 4 for _ in ch])
    c = _VClient()
    # amendment arrives BEFORE the base law it targets
    wio.load_amendments(c, [AmendmentOp(
        op="replaces", target_id="ν.4412/2016#αρ.15", scope="article",
        new_text="νέο", effective_date="2024-04-01", sub_edit_ordinal="1")],
        source_law=_amending_law("2024-04-01"))
    r0 = wio.assemble_article_timeline(c, tenant="gr")
    assert r0["pending"] == 1 and r0["articles"] == 0      # parked, no base yet

    # base law lands later -> same assembly resolves it
    base = _base_law()
    wio.load_document(c, base)
    wio.load_law(c, base, [[0.0] * 4])
    r1 = wio.assemble_article_timeline(c, tenant="gr")
    assert r1["articles"] == 1 and r1["versions_written"] == 1


def test_graph_status_flags_dangling_target(monkeypatch):
    import voyage_embed as ve
    monkeypatch.setattr(ve, "embed_law_chunks", lambda ch: [[0.0] * 4 for _ in ch])
    c = _VClient()
    base = _base_law()
    wio.load_document(c, base)
    wio.load_law(c, base, [[0.0] * 4])
    # one resolvable amendment (target law present) + one dangling (target absent)
    wio.load_amendments(c, [
        AmendmentOp(op="replaces", target_id="ν.4412/2016#αρ.15", scope="article",
                    new_text="νέο", effective_date="2024-04-01", sub_edit_ordinal="1"),
        AmendmentOp(op="replaces", target_id="ν.9999/2099#αρ.1", scope="article",
                    new_text="x", effective_date="2024-04-01", sub_edit_ordinal="2"),
    ], source_law=_amending_law("2024-04-01"))
    s = wio.graph_status(c, tenant="gr")
    assert s["amendments"] == 2
    assert s["target_law_present"] == 1 and s["target_law_missing"] == 1
    assert "ν.9999/2099#αρ.1" in s["dangling_targets"]


def test_timeline_repeal_marks_terminal(monkeypatch):
    import voyage_embed as ve
    monkeypatch.setattr(ve, "embed_law_chunks", lambda ch: [[0.0] * 4 for _ in ch])
    c = _VClient()
    base = _base_law()
    wio.load_document(c, base)
    wio.load_law(c, base, [[0.0] * 4])
    wio.load_amendments(c, [AmendmentOp(
        op="repeals", target_id="ν.4412/2016#αρ.15", scope="article",
        effective_date="2025-01-01", sub_edit_ordinal="1")],
        source_law=_amending_law("2025-01-01"))
    wio.assemble_article_timeline(c, tenant="gr")
    rep = c.store["Jun2026LawArticle"][
        wio._art_version_uuid("ν.4412/2016#αρ.15", "2025-01-01T00:00:00Z")]
    assert rep["legal_force_status"] == "repealed"


# --- Browse inspectors: list_laws + fetch_law_objects -----------------------
class _InspectObj:
    def __init__(self, props, uuid): self.properties = props; self.uuid = uuid


class _InspectColl:
    def __init__(self, rows): self._rows = rows

    @property
    def tenants(self):
        return type("T", (), {"get": lambda s: {"gr": object()}})()

    def with_tenant(self, t): return self

    def iterator(self, return_properties=None):
        for d in self._rows:
            yield _InspectObj(d, d.get("canonical_id", "u"))

    @property
    def query(self):
        rows = self._rows

        class Q:  # the stub Filter is opaque, so return the preset rows (the test
            def fetch_objects(self_, filters=None, limit=2000):   # sets up only the law's rows)
                return type("R", (), {"objects":
                    [_InspectObj(d, d.get("canonical_id", "u")) for d in rows[:limit]]})()
        return Q()


class _InspectClient:
    def __init__(self, store): self._store = store

    @property
    def collections(self):
        store = self._store

        class C:
            def exists(s, name): return name in store
            def use(s, name): return _InspectColl(store.get(name, []))
        return C()


def test_list_laws_dedups_counts_and_sorts():
    flat = [
        {"law_number": "5090/2024", "instrument_key": "N5090/2024",
         "document_title": "Νόμος Α", "canonical_id": "ν.5090/2024#αρ.1"},
        {"law_number": "5090/2024", "instrument_key": "N5090/2024",
         "document_title": "Νόμος Α", "canonical_id": "ν.5090/2024#αρ.2"},
        {"law_number": "4675/2024", "instrument_key": "N4675/2024",
         "document_title": "Νόμος Β", "canonical_id": "ν.4675/2024#αρ.1"},
        {"law_number": "", "canonical_id": "x"},          # blank -> skipped
    ]
    c = _InspectClient({wio.config.FLAT_COLLECTION: flat})
    laws = wio.list_laws(c, tenant="gr")
    assert [r["law_number"] for r in laws] == ["4675/2024", "5090/2024"]   # sorted
    by = {r["law_number"]: r for r in laws}
    assert by["5090/2024"]["chunks"] == 2
    assert by["5090/2024"]["instrument_key"] == "N5090/2024"
    assert by["4675/2024"]["document_title"] == "Νόμος Β"


def test_fetch_law_objects_sorts_by_chunk_index_and_tags_uuid():
    rows = [
        {"canonical_id": "ν.5090/2024#αρ.2", "article_number": "2",
         "chunk_index": 1, "law_number": "5090/2024"},
        {"canonical_id": "ν.5090/2024#αρ.1", "article_number": "1",
         "chunk_index": 0, "law_number": "5090/2024"},
    ]
    c = _InspectClient({wio.config.FLAT_COLLECTION: rows})
    objs = wio.fetch_law_objects(c, wio.config.FLAT_COLLECTION, "5090/2024", tenant="gr")
    assert [o["chunk_index"] for o in objs] == [0, 1]      # sorted ascending
    assert all("_uuid" in o for o in objs)


def test_fetch_law_objects_absent_collection_returns_empty():
    assert wio.fetch_law_objects(_InspectClient({}), wio.config.FLAT_COLLECTION,
                                 "5090/2024", tenant="gr") == []


def test_law_fields_mapping_covers_all_collections():
    assert wio._law_fields(wio.config.GRAPH_ARTICLE) == ["document_law_number"]
    assert set(wio._law_fields(wio.config.GRAPH_AMENDMENT)) == {
        "source_law_number", "target_law_number"}
    assert set(wio._law_fields(wio.config.GRAPH_DELEGATION)) == {
        "enabling_law_number", "implementing_law_number"}


def test_vf_str_normalizes_datetime_and_strings():
    import datetime as dt
    aware = dt.datetime(2024, 2, 14, 0, 0, tzinfo=dt.timezone.utc)
    # datetime (how Weaviate returns DATE) -> the canonical write-time string
    assert wio._vf_str(aware) == "2024-02-14T00:00:00Z"
    # Weaviate's millisecond display form -> same canonical string
    assert wio._vf_str("2024-02-14T00:00:00.000Z") == "2024-02-14T00:00:00Z"
    assert wio._vf_str("2024-02-14T00:00:00Z") == "2024-02-14T00:00:00Z"
    assert wio._vf_str(None) is None and wio._vf_str("") is None
    # critical: a datetime read-back must produce the SAME version UUID as the
    # write-time string, or consolidation can't find the base version.
    assert wio._art_version_uuid("ν.1/2024#αρ.5", wio._vf_str(aware)) == \
        wio._art_version_uuid("ν.1/2024#αρ.5", "2024-02-14T00:00:00Z")


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
