"""Full-spine dry run: extract -> normalize -> segment -> amend -> classify ->
embed -> load, with the two external clients (Voyage, Weaviate) mocked so the
orchestrator logic is exercised without network or API keys.

Skips if pdfplumber/reportlab are unavailable.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

pytest.importorskip("pdfplumber")
pytest.importorskip("reportlab")

from tests.make_fixture_pdf import build  # noqa: E402


@pytest.fixture(scope="module")
def fek_pdf(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("pdf") / "fek_sample.pdf")
    build(path)
    return path


def test_full_spine_processes_to_done(fek_pdf, tmp_path, monkeypatch):
    import orchestrator
    import voyage_embed as ve
    import weaviate_io as wio
    from state import State

    # --- stub Voyage embedding: return a deterministic vector per chunk ---
    monkeypatch.setattr(ve, "embed_law_chunks",
                        lambda chunks, progress=None: [[0.0] * 8 for _ in chunks])

    # --- capture what would be written to Weaviate ---
    loaded = {"docs": 0, "laws": 0, "provisions": 0, "amendments": 0, "tenant": None}

    def fake_load_document(client, law, tenant=None):
        loaded["docs"] += 1

    def fake_load_law(client, law, vectors, tenant=None):
        loaded["laws"] += 1
        loaded["provisions"] += len(law.provisions)
        loaded["tenant"] = law.jurisdiction
        assert len(vectors) == len(law.provisions)         # alignment invariant
        # every provision id is namespaced under the real instrument id
        assert all(p.canonical_id.startswith(law.instrument_id + "#")
                   for p in law.provisions)

    def fake_load_amendments(client, ops, source_law=None, tenant=None):
        loaded["amendments"] += len(ops)

    def fake_load_delegations(client, edges, source_law=None, tenant=None):
        loaded["delegations"] = loaded.get("delegations", 0) + len(edges)

    monkeypatch.setattr(wio, "load_document", fake_load_document)
    monkeypatch.setattr(wio, "load_law", fake_load_law)
    monkeypatch.setattr(wio, "load_amendments", fake_load_amendments)
    monkeypatch.setattr(wio, "load_delegations", fake_load_delegations)

    st = State(str(tmp_path / "state.db"))
    events = []
    status = orchestrator.process_document(
        client=object(), st=st, path=fek_pdf,
        progress=lambda stage, msg="": events.append(stage))
    st.close()

    assert status == "done"
    assert loaded["docs"] == 1
    assert loaded["laws"] == 1
    assert loaded["provisions"] >= 2
    assert loaded["tenant"] == "gr"                        # tenancy non-negotiable
    # the spine emitted each stage in order
    for stage in ("extract", "segment", "amend", "classify", "embed", "load", "done"):
        assert stage in events


def test_fatal_credential_error_stops_and_requeues(fek_pdf, tmp_path, monkeypatch):
    """A wrong/expired API key (auth error) during embed must raise FatalIngestError
    and leave the doc 'pending' (resumable) — NOT mark it 'error' and continue."""
    import orchestrator
    import voyage_embed as ve
    from errors import FatalIngestError
    from state import State

    def _auth_boom(chunks, progress=None):
        raise Exception("AuthenticationError: invalid api key (401)")
    monkeypatch.setattr(ve, "embed_law_chunks", _auth_boom)

    st = State(str(tmp_path / "state.db"))
    with pytest.raises(FatalIngestError) as ei:
        orchestrator.process_document(client=object(), st=st, path=fek_pdf)
    assert ei.value.provider == "Voyage" and "embed" in ei.value.stage
    counts = st.counts()
    st.close()
    # released for resume, never marked 'error'
    assert counts.get("pending") == 1 and not counts.get("error")


def test_persistent_rate_limit_pauses_and_requeues(fek_pdf, tmp_path, monkeypatch):
    """A rate limit that survived every retry (RateLimitExhausted) must PAUSE the run
    (FatalIngestError) and leave the doc 'pending' for resume — NOT mark it 'error'.
    This is the 'if we hit a limit, stop and let me fix it, then redo this doc whole'
    behaviour."""
    import orchestrator
    import voyage_embed as ve
    from errors import FatalIngestError, RateLimitExhausted
    from state import State

    def _throttled(chunks, progress=None):
        raise RateLimitExhausted("voyage", RuntimeError("429 Too Many Requests"))
    monkeypatch.setattr(ve, "embed_law_chunks", _throttled)

    st = State(str(tmp_path / "s.db"))
    with pytest.raises(FatalIngestError) as ei:
        orchestrator.process_document(client=object(), st=st, path=fek_pdf)
    assert "embed" in ei.value.stage
    counts = st.counts()
    st.close()
    assert counts.get("pending") == 1 and not counts.get("error")


def test_unidentifiable_pdf_routes_to_review(tmp_path, monkeypatch):
    """A PDF with no parseable masthead must go to review, not invent an id."""
    import orchestrator
    import pipeline.extract as extract
    from state import State
    from pipeline.extract import ExtractResult

    # extract returns text with no masthead fields resolved
    monkeypatch.setattr(extract, "extract_pdf", lambda path, use_azure=True:
                        ExtractResult(text="κάποιο κείμενο χωρίς ταυτότητα",
                                      classification="text", table_pages=[],
                                      pages_markdown=["x"], warnings=[],
                                      masthead={"instrument_type": None, "number": None,
                                                "year": None, "warnings": ["none"]}))
    fake = tmp_path / "x.pdf"
    fake.write_bytes(b"%PDF-1.4 test")
    st = State(str(tmp_path / "s.db"))
    status = orchestrator.process_document(client=object(), st=st, path=str(fake))
    st.close()
    assert status == "review"
