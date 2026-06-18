"""Fail-fast classification (errors.py) + state requeue for resume."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from errors import looks_fatal, as_fatal, FatalIngestError, RateLimitExhausted  # noqa: E402
from state import State  # noqa: E402


def test_rate_limit_exhausted_is_fatal_but_raw_throttle_is_not():
    # a transient throttle that survived EVERY retry is systemic -> pause the run
    exhausted = RateLimitExhausted("voyage", RuntimeError("429 Too Many Requests"))
    assert looks_fatal(exhausted) is True
    # but a raw 429 seen mid-retry is still transient -> retried, NOT a pause
    assert looks_fatal(RuntimeError("429 Too Many Requests")) is False


def test_looks_fatal_auth_and_endpoint_errors():
    for msg in ["AuthenticationError: invalid api key",
                "Error code: 401 - Unauthorized",
                "PermissionDenied: 403 forbidden",
                "Connection error: failed to establish a new connection",
                "getaddrinfo failed",
                "SSL: CERTIFICATE_VERIFY_FAILED self-signed certificate",
                "DeploymentNotFound",
                "Resource not found (404)"]:
        assert looks_fatal(Exception(msg)), msg


def test_looks_fatal_ignores_transient():
    for msg in ["429 Too Many Requests", "Rate limit exceeded",
                "Read timed out", "503 Service Unavailable",
                "overloaded — try again later"]:
        assert not looks_fatal(Exception(msg)), msg


def test_looks_fatal_ignores_plain_content_error():
    assert not looks_fatal(ValueError("could not parse article header"))


def test_as_fatal_carries_provider_stage():
    fe = as_fatal(RuntimeError("boom"), "Voyage", "embed")
    assert isinstance(fe, FatalIngestError)
    assert fe.provider == "Voyage" and fe.stage == "embed" and "boom" in fe.detail


def test_requeue_stale_resets_processing_to_pending(tmp_path):
    st = State(str(tmp_path / "s.db"))
    st.claim("doc1", "/x/doc1.pdf", "h1")          # -> processing
    assert st.counts().get("processing") == 1
    n = st.requeue_stale()
    assert n == 1
    assert st.counts().get("pending") == 1 and not st.counts().get("processing")
    st.close()
