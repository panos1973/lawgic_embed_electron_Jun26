"""Tests for parallel-ingest safety: atomic claim, rate limiter, concurrent state.

No network. Verifies the guarantees that matter for processing 100Ks of laws in
parallel: a law is claimed by exactly one worker, the state store survives
concurrent access, and the rate limiter throttles + retries correctly.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ratelimit  # noqa: E402
from state import State  # noqa: E402


# ── atomic claim (no double-processing) ──
def test_claim_is_exclusive(tmp_path):
    st = State(str(tmp_path / "s.db"))
    assert st.claim("doc1", "/p", "hashA") is True       # first claim wins
    assert st.claim("doc1", "/p", "hashA") is False      # already 'processing'
    st.close()


def test_claim_skips_done_hash(tmp_path):
    st = State(str(tmp_path / "s.db"))
    st.claim("doc1", "/p", "hashA")
    st.set_status("doc1", "done", stage="load")
    # same content re-submitted (different doc_id) -> dedup, not re-processed
    assert st.claim("doc2", "/p2", "hashA") is False
    st.close()


def test_claim_under_threads_grants_once(tmp_path):
    st = State(str(tmp_path / "s.db"))
    results = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()                                   # maximize contention
        results.append(st.claim("same", "/p", "h"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1                      # exactly ONE worker won
    assert results.count(False) == 7
    st.close()


def test_concurrent_set_status_no_corruption(tmp_path):
    st = State(str(tmp_path / "s.db"))
    for i in range(20):
        st.claim(f"d{i}", "/p", f"h{i}")

    def finish(i):
        st.set_status(f"d{i}", "done", stage="load")

    threads = [threading.Thread(target=finish, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert st.counts().get("done") == 20                 # all updates landed
    st.close()


# ── rate limiter ──
def test_token_bucket_throttles():
    # 5 permits/sec, burst 2: the 4th acquire must wait (~ (4-2)/5 = 0.4s)
    b = ratelimit.TokenBucket(rate=5.0, capacity=2.0)
    t0 = time.monotonic()
    for _ in range(4):
        b.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.3                                 # was forced to wait


def test_with_retry_retries_transient_then_succeeds():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("429 Too Many Requests")
        return "ok"

    # tiny backoff base so the test is fast
    out = ratelimit.with_retry(flaky, provider="test", max_attempts=5, base=1.001)
    assert out == "ok"
    assert len(calls) == 3                                # failed twice, then ok


def test_with_retry_does_not_retry_token_limit():
    calls = []

    def hard():
        calls.append(1)
        raise RuntimeError("example has too many tokens ... context window of 32000")

    try:
        ratelimit.with_retry(hard, provider="test", max_attempts=5, base=1.001)
        assert False, "should have raised"
    except RuntimeError:
        pass
    assert len(calls) == 1                                # raised immediately, no retry


def test_with_retry_gives_up_after_max():
    def always():
        raise RuntimeError("503 temporarily unavailable")

    try:
        ratelimit.with_retry(always, provider="test", max_attempts=3, base=1.001)
        assert False, "should have raised"
    except RuntimeError as e:
        assert "503" in str(e)


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
