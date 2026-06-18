"""ratelimit.py — token-bucket rate limiting + retry/backoff for API calls.

When the document-level worker pool runs N laws concurrently, each firing many
DeepSeek (enrichment) and Voyage (embedding) calls, an unthrottled fan-out hits
provider 429s almost immediately. These helpers cap the request rate per provider
and retry transient failures (429 / 5xx / timeouts) with exponential backoff +
jitter, so parallelism speeds things up instead of just failing faster.

Thread-safe: one shared bucket per provider, guarded by a lock.
"""
from __future__ import annotations

import random
import threading
import time

import logsetup
from errors import RateLimitExhausted

log = logsetup.get("ratelimit")


class TokenBucket:
    """Classic token bucket: `rate` permits/sec, burst capacity `capacity`.
    acquire() blocks until a permit is available. Shared across worker threads."""

    def __init__(self, rate: float, capacity: float):
        self.rate = float(rate)
        self.capacity = float(capacity)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, n: float = 1.0) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity,
                                   self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                deficit = n - self._tokens
                wait = deficit / self.rate if self.rate > 0 else 0.05
            time.sleep(min(wait, 1.0))


# One bucket per provider key. Conservative defaults; override via set_limits().
_BUCKETS: dict[str, TokenBucket] = {}
_BUCKETS_LOCK = threading.Lock()
_DEFAULTS = {
    "deepseek": (8.0, 16.0),     # ~8 req/s, burst 16
    "voyage":   (4.0, 8.0),      # ~4 req/s, burst 8
}


def set_limits(provider: str, rate: float, capacity: float) -> None:
    with _BUCKETS_LOCK:
        _BUCKETS[provider] = TokenBucket(rate, capacity)


def configure(*, voyage_rpm=None, llm_provider=None, llm_rpm=None) -> None:
    """Override per-provider request rates from RPM settings (called once at start).

    Unset / zero values keep the built-in defaults. RPM -> permits/sec; burst is
    ~2s of headroom. Buckets are PER PROCESS, so when N app instances run in
    parallel against ONE shared API quota, set each instance to about
    account_limit / N so the combined request rate stays under the quota.
    """
    if voyage_rpm and voyage_rpm > 0:
        rate = float(voyage_rpm) / 60.0
        set_limits("voyage", rate, max(rate * 2.0, 1.0))
        log.info("voyage rate limit set to %.0f req/min (%.2f/s)", voyage_rpm, rate)
    if llm_rpm and llm_rpm > 0 and llm_provider:
        rate = float(llm_rpm) / 60.0
        set_limits(llm_provider, rate, max(rate * 2.0, 1.0))
        log.info("%s rate limit set to %.0f req/min (%.2f/s)", llm_provider, llm_rpm, rate)


def _bucket(provider: str) -> TokenBucket:
    with _BUCKETS_LOCK:
        b = _BUCKETS.get(provider)
        if b is None:
            rate, cap = _DEFAULTS.get(provider, (4.0, 8.0))
            b = TokenBucket(rate, cap)
            _BUCKETS[provider] = b
        return b


def acquire(provider: str) -> None:
    """Block until a request permit for `provider` is available."""
    _bucket(provider).acquire()


_RETRYABLE = ("429", "rate limit", "too many requests", "timeout", "timed out",
              "503", "502", "500", "overloaded", "temporarily")


def _is_retryable(err: Exception) -> bool:
    m = str(err).lower()
    return any(s in m for s in _RETRYABLE)


def with_retry(fn, *, provider: str, max_attempts: int = 5, base: float = 2.0):
    """Call fn() with rate limiting + exponential backoff on transient errors.

    Token-limit / content errors (non-retryable) are raised immediately so they
    surface as the real bug, not masked by retries. Returns fn()'s result.
    """
    last = None
    for attempt in range(1, max_attempts + 1):
        acquire(provider)
        try:
            return fn()
        except Exception as e:                    # noqa: BLE001
            last = e
            # do not retry hard, non-transient failures (e.g. >32k token limit)
            if "too many tokens" in str(e).lower() or "context window" in str(e).lower():
                raise
            if not _is_retryable(e):
                raise                              # non-transient -> surface now
            if attempt == max_attempts:
                # a transient error that survived EVERY retry is no longer a blip —
                # it is systemic (throttling / exhausted quota). Signal a PAUSE so
                # the run stops cleanly instead of erroring document after document.
                raise RateLimitExhausted(provider, e) from e
            delay = base ** attempt + random.uniform(0, 1.0)
            log.warning("%s call failed (attempt %d/%d), retrying in %.1fs: %s",
                        provider, attempt, max_attempts, delay, str(e)[:160])
            time.sleep(delay)
    raise last if last else RuntimeError("with_retry: exhausted")
