"""Tests for the collection reset helpers (fake Weaviate client, no network).

reset_collection must wipe a tenant's objects while KEEPING the schema — i.e.
remove+recreate the tenant, never delete the collection.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import weaviate_io as wio  # noqa: E402


class _FakeTenants:
    def __init__(self, names):
        self._names = set(names)
        self.removed = []
        self.created = []

    def get(self):
        return {n: object() for n in self._names}

    def remove(self, names):
        for n in names:
            self._names.discard(n)
        self.removed.append(list(names))

    def create(self, tenants):
        for t in tenants:
            name = getattr(t, "name", t)
            self._names.add(name)
        self.created.append(tenants)


class _FakeAgg:
    def __init__(self, n):
        self._n = n

    def over_all(self, total_count=True):
        return type("R", (), {"total_count": self._n})()


class _FakeTenantView:
    def __init__(self, n):
        self.aggregate = _FakeAgg(n)


class _FakeCollection:
    def __init__(self, count, tenants):
        self.tenants = _FakeTenants(tenants)
        self._count = count

    def with_tenant(self, t):
        return _FakeTenantView(self._count)


class _FakeCollections:
    def __init__(self, present, count, tenants):
        self._present = present
        self._coll = _FakeCollection(count, tenants)

    def exists(self, name):
        return self._present

    def use(self, name):
        return self._coll


class _FakeClient:
    def __init__(self, present=True, count=42, tenants=("gr",)):
        self.collections = _FakeCollections(present, count, tenants)


def test_registry_has_all_five():
    reg = wio.collection_registry()
    assert set(reg.keys()) == {"flat", "document", "article", "amendment", "delegation"}
    assert reg["flat"] == config.FLAT_COLLECTION


def test_count_returns_total(monkeypatch):
    c = _FakeClient(count=7)
    assert wio.collection_count(c, config.FLAT_COLLECTION, "gr") == 7


def test_count_absent_collection_is_minus_one():
    c = _FakeClient(present=False)
    assert wio.collection_count(c, "Nope") == -1


def test_reset_removes_and_recreates_tenant():
    c = _FakeClient(count=10, tenants=("gr",))
    coll = c.collections.use("x")
    res = wio.reset_collection(c, config.FLAT_COLLECTION, "gr")
    assert res["deleted"] == 10
    assert coll.tenants.removed == [["gr"]]        # tenant dropped (wipes objects)
    assert len(coll.tenants.created) == 1          # and recreated empty
    assert "gr" in coll.tenants.get()              # tenant exists again
    assert res.get("error") is None                # collection NOT deleted


def test_reset_absent_collection_reports_error():
    c = _FakeClient(present=False)
    res = wio.reset_collection(c, "Nope", "gr")
    assert res["deleted"] == 0
    assert "does not exist" in res["error"]


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
