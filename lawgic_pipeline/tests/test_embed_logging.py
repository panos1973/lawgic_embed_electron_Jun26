"""Tests for embedding telemetry + durable logging (no network).

Covers the pure batch planner, the embed loop's progress callbacks (with a fake
Voyage client), and that logsetup writes a real rotating log file.
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config            # noqa: E402
import logsetup          # noqa: E402
import voyage_embed as ve  # noqa: E402


# ── batch planner (pure) ──
def test_plan_single_batch_when_small():
    assert ve._plan_batches(["a", "b", "c"]) == [[0, 1, 2]]


def test_plan_empty():
    assert ve._plan_batches([]) == []


def test_long_law_splits_into_multiple_windows():
    # regression: a law bigger than one 32k-token context window must be split,
    # not sent as a single document (which Voyage rejects with a 32000-token error).
    big = "α" * 30_000                          # ~10k est tokens each (len//3)
    chunks = [big] * 6                          # ~60k est tokens total > budget
    batches = ve._plan_batches(chunks)
    assert len(batches) > 1
    assert [i for b in batches for i in b] == list(range(6))   # all chunks, in order
    budget = int(ve.CONTEXT_WINDOW_TOKENS * ve.SAFETY)
    for b in batches:                           # no window exceeds the budget
        assert sum(ve._est_tokens(chunks[i]) for i in b) <= budget


def test_plan_splits_by_chunk_count(monkeypatch):
    monkeypatch.setattr(ve, "MAX_CHUNKS", 2)
    batches = ve._plan_batches(["a", "b", "c", "d", "e"])
    assert batches == [[0, 1], [2, 3], [4]]


def test_plan_splits_by_token_budget(monkeypatch):
    # tiny window so each ~ chunk lands in its own window
    monkeypatch.setattr(ve, "CONTEXT_WINDOW_TOKENS", 3)  # budget = int(3*0.9) = 2 tokens
    monkeypatch.setattr(ve, "SAFETY", 0.9)
    chunks = ["xxxxxx", "yyyyyy", "zzzzzz"]     # _est_tokens = len//3 = 2 each
    batches = ve._plan_batches(chunks)
    assert [i for b in batches for i in b] == [0, 1, 2]   # every index once, in order
    assert len(batches) == 3


# ── embed loop with a fake client (offline) ──
class _FakeClient:
    def __init__(self):
        self.calls = 0

    def contextualized_embed(self, inputs, model, input_type, output_dimension):
        self.calls += 1
        span = inputs[0]
        embs = [[0.1] * output_dimension for _ in span]
        return type("R", (), {"results": [type("X", (), {"embeddings": embs})()]})()


def test_embed_returns_one_vector_per_chunk(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(ve, "client", lambda: fake)
    out = ve.embed_law_chunks(["a", "b", "c", "d"])
    assert len(out) == 4
    assert all(len(v) == config.EMBED_DIM for v in out)
    assert fake.calls == 1                      # single batch


def test_embed_emits_progress(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(ve, "client", lambda: fake)
    msgs = []
    out = ve.embed_law_chunks(["a", "b", "c"], progress=msgs.append)
    assert len(out) == 3
    assert any("chunks" in m for m in msgs)     # start line
    assert any("done" in m for m in msgs)       # completion line


def test_embed_progress_reports_each_batch(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(ve, "client", lambda: fake)
    monkeypatch.setattr(ve, "MAX_CHUNKS", 2)    # force 2 batches for 3 chunks
    msgs = []
    out = ve.embed_law_chunks(["a", "b", "c"], progress=msgs.append)
    assert len(out) == 3
    assert fake.calls == 2
    assert sum(1 for m in msgs if m.startswith("batch ")) == 2


def test_embed_empty_returns_empty(monkeypatch):
    monkeypatch.setattr(ve, "client", lambda: (_ for _ in ()).throw(AssertionError()))
    assert ve.embed_law_chunks([]) == []        # never touches the client


# ── durable log file ──
def test_logsetup_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DB", str(tmp_path / "lawgic_state.db"))
    monkeypatch.setattr(logsetup, "_initialized", False)
    # drop any handler a previous test attached so init() re-attaches to tmp
    logging.getLogger(logsetup.ROOT).handlers.clear()
    path = logsetup.init()
    assert path == str(tmp_path / "lawgic.log")
    logsetup.get("embed").info("hello-embed-log")
    for h in logging.getLogger(logsetup.ROOT).handlers:
        h.flush()
    assert os.path.exists(path)
    assert "hello-embed-log" in open(path, encoding="utf-8").read()


def test_log_path_follows_state_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DB", str(tmp_path / "sub" / "state.db"))
    assert logsetup.log_path() == str(tmp_path / "sub" / "lawgic.log")


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
