"""errors.py — shared fail-fast classification for an ingest run.

A credential/endpoint failure (bad or missing API key, wrong URL/endpoint, missing
deployment) recurs on EVERY file. Marking thousands of documents 'error' one by one
is useless and hides the real cause, so we STOP the whole run with a clear
attribution (which provider, which stage) the moment such an error is seen. The
operator fixes the key/URL in Settings, saves, and resumes — the state DB skips the
files already done and retries the one that was interrupted.

Kept dependency-free so every layer (orchestrator, enrich, embed, loader) can import
it without an import cycle.
"""
from __future__ import annotations


class FatalIngestError(Exception):
    """A credential/endpoint error that will recur on every document.

    Carries the provider (Voyage / Weaviate / Azure / the LLM provider) and the
    stage so the UI can point the operator at the exact setting to fix.
    """
    def __init__(self, provider: str, stage: str, detail: str):
        self.provider = provider
        self.stage = stage
        self.detail = detail
        super().__init__(f"{provider} — {stage}: {detail}")


# Substrings that mark a recurring credential/endpoint failure (NOT a transient
# hiccup). Matched case-insensitively against "<ExcType> <message>".
_FATAL = (
    "authenticat", "unauthor", "invalid api key", "invalid_api_key", "api key",
    "api-key", "permission denied", "permissiondenied", "forbidden", "401", "403",
    "could not connect", "connection error", "failed to establish",
    "connection refused", "name or service not known", "nodename nor servname",
    "getaddrinfo", "could not resolve", "no address associated", "ssl",
    "certificate", "deploymentnotfound", "resource not found", "404",
)
# Transient signals checked FIRST — these are retried upstream, never fatal.
_TRANSIENT = (
    "429", "rate limit", "too many requests", "overloaded", "timeout", "timed out",
    "503", "502", "temporarily unavailable", "try again",
)


def looks_fatal(exc: BaseException) -> bool:
    """True if `exc` is a recurring credential/endpoint failure (auth, bad URL/
    endpoint, missing key/deployment) rather than a transient, retryable hiccup."""
    s = f"{type(exc).__name__} {exc}".lower()
    if any(m in s for m in _TRANSIENT):
        return False
    return any(m in s for m in _FATAL)


def as_fatal(exc: BaseException, provider: str, stage: str) -> FatalIngestError:
    """Wrap an arbitrary exception as a FatalIngestError with provider/stage tags."""
    return FatalIngestError(provider, stage, f"{type(exc).__name__}: {exc}")
