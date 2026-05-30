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
