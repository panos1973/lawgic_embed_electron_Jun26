"""Unit tests for ratelimit.configure() — RPM overrides map to per-second rates."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ratelimit  # noqa: E402


def test_connection_drops_are_retryable():
    # the Azure DI "Connection aborted / RemoteDisconnected" failure that silently lost
    # a scanned page must now be treated as transient (retried), while a content 400 is
    # still NOT retried.
    assert ratelimit._is_retryable(Exception("('Connection aborted.', RemoteDisconnected(...))"))
    assert ratelimit._is_retryable(Exception("Connection reset by peer"))
    assert not ratelimit._is_retryable(Exception("400 - the example at index 0 has ..."))


def test_with_retry_recovers_after_transient_connection_drop():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Connection aborted, RemoteDisconnected")
        return "ok"
    assert ratelimit.with_retry(flaky, provider="azure_di", base=0.0) == "ok"
    assert calls["n"] == 2          # retried the transient drop, then succeeded


def test_configure_overrides_voyage_and_llm_rates():
    ratelimit.configure(voyage_rpm=600, llm_provider="azure", llm_rpm=300)
    assert abs(ratelimit._bucket("voyage").rate - 10.0) < 1e-9     # 600/60
    assert abs(ratelimit._bucket("azure").rate - 5.0) < 1e-9       # 300/60
    # burst capacity is ~2s of headroom
    assert abs(ratelimit._bucket("voyage").capacity - 20.0) < 1e-9


def test_configure_blank_or_zero_keeps_existing():
    ratelimit.set_limits("voyage", 4.0, 8.0)          # known baseline
    ratelimit.configure(voyage_rpm=None, llm_provider="azure", llm_rpm=0)
    assert abs(ratelimit._bucket("voyage").rate - 4.0) < 1e-9      # untouched
