"""Shared test setup.

Make the pipeline package importable, and provide lightweight stand-ins for the
heavy external SDKs (voyageai, weaviate) when they are not installed, so the
orchestrator's import chain resolves in a minimal environment. Tests still mock
the actual call sites (embed_law_chunks, load_law, ...) for behaviour.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ensure_stub(name: str, build) -> None:
    try:
        __import__(name)
    except Exception:  # noqa: BLE001
        sys.modules[name] = build()


def _voyageai_stub() -> types.ModuleType:
    m = types.ModuleType("voyageai")

    class _Client:  # pragma: no cover - never actually called in tests
        def __init__(self, *a, **k):
            pass

    m.Client = _Client
    return m


def _weaviate_stub() -> types.ModuleType:
    m = types.ModuleType("weaviate")
    m.WeaviateClient = object

    def _connect(*a, **k):  # pragma: no cover
        raise RuntimeError("weaviate stub: real connection not available in tests")

    m.connect_to_weaviate_cloud = _connect

    auth = types.ModuleType("weaviate.auth")
    auth.AuthApiKey = lambda *a, **k: None
    m.auth = auth

    util = types.ModuleType("weaviate.util")
    util.generate_uuid5 = lambda v: f"uuid5({v})"
    sys.modules["weaviate.util"] = util

    classes = types.ModuleType("weaviate.classes")
    tenants = types.ModuleType("weaviate.classes.tenants")
    tenants.Tenant = lambda name=None, **k: types.SimpleNamespace(name=name)
    classes.tenants = tenants
    sys.modules["weaviate.classes"] = classes
    sys.modules["weaviate.classes.tenants"] = tenants
    return m


_ensure_stub("voyageai", _voyageai_stub)
_ensure_stub("weaviate", _weaviate_stub)
