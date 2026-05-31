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
    big = "α" * 30_000                          # ~20k est tokens each (len/1.5)
    chunks = [big] * 6                          # ~120k est tokens total > budget
    batches = ve._plan_batches(chunks)
    assert len(batches) > 1
    assert [i for b in batches for i in b] == list(range(6))   # all chunks, in order
    budget = int(ve.CONTEXT_WINDOW_TOKENS * ve.SAFETY)
    for b in batches:                           # no window exceeds the budget
        assert sum(ve._est_tokens(chunks[i]) for i in b) <= budget


def test_real_greek_law_windows_stay_under_voyage_limit():
    # mirrors the live failure: a 32-page law (~57 chunks, ~138k chars of Greek)
    # was estimated at ~46k tokens (len//3) and packed into windows that were
    # really ~46k tokens each -> Voyage rejected (>32000). With CHARS_PER_TOKEN=1.5
    # every window must come out under the hard 32k limit.
    chunks = ["άρθρο " + "λ" * 2400 for _ in range(57)]   # ~138k chars total
    batches = ve._plan_batches(chunks)
    assert len(batches) >= 1
    assert [i for b in batches for i in b] == list(range(57))  # nothing dropped
    for b in batches:
        real_est = sum(ve._est_tokens(chunks[i]) for i in b)
        assert real_est < 32_000          # the actual Voyage hard limit



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


# ── oversized-chunk splitter ──
def test_split_oversized_covers_all_text_no_loss():
    budget = 100                                # max_chars = 150
    text = ("Πρώτη πρόταση. " * 20) + "\n\n" + ("Δεύτερη πρόταση· " * 20)
    segs = ve._split_oversized(text, budget)
    assert len(segs) > 1
    max_chars = int(budget * ve.CHARS_PER_TOKEN)
    assert all(len(s) <= max_chars for s in segs)        # each piece fits
    # no text lost: every non-space char is preserved across the segments
    assert "".join(segs).replace(" ", "").replace("\n", "") == \
        text.replace(" ", "").replace("\n", "")


def test_split_oversized_hard_cuts_unbroken_blob():
    budget = 50
    blob = "x" * 1000                            # no paragraph/sentence boundaries
    segs = ve._split_oversized(blob, budget)
    assert "".join(segs) == blob                 # every char kept
    assert all(len(s) <= int(budget * ve.CHARS_PER_TOKEN) for s in segs)


def test_pool_returns_unit_vector():
    pooled = ve._pool([[3.0] + [0.0] * (config.EMBED_DIM - 1),
                       [0.0, 4.0] + [0.0] * (config.EMBED_DIM - 2)])
    norm = sum(x * x for x in pooled) ** 0.5
    assert abs(norm - 1.0) < 1e-9               # re-normalized to unit length
    assert len(pooled) == config.EMBED_DIM


def test_embed_oversized_chunk_yields_one_pooled_vector(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(ve, "client", lambda: fake)
    monkeypatch.setattr(ve, "CONTEXT_WINDOW_TOKENS", 100)   # budget 75, max ~112 chars
    monkeypatch.setattr(ve, "SAFETY", 0.75)
    small = "μικρό"
    huge = "Πρόταση ένα. " * 60                  # ~720 chars -> well over budget, alone
    out = ve.embed_law_chunks([small, huge])
    assert len(out) == 2                         # STILL one vector per input chunk
    assert all(len(v) == config.EMBED_DIM for v in out)


def test_embed_oversized_preserves_count_in_mixed_law(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(ve, "client", lambda: fake)
    monkeypatch.setattr(ve, "CONTEXT_WINDOW_TOKENS", 100)
    monkeypatch.setattr(ve, "SAFETY", 0.75)
    chunks = ["α", "β", "Μεγάλο. " * 80, "γ", "δ"]   # one oversized in the middle
    out = ve.embed_law_chunks(chunks)
    assert len(out) == len(chunks)               # 5 in -> 5 out, order preserved


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
