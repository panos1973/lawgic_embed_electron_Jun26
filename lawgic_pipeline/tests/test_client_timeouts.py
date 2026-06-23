"""Every external SDK client must be built with an explicit, bounded timeout and
max_retries=0 (so ratelimit.with_retry is the SINGLE retry authority).

Regression guard for the silent-freeze bug: the LLM (OpenAI/Anthropic) and Voyage
clients defaulted to a ~600s timeout AND the SDKs' own internal retries, so one
stalled socket could block a worker for many minutes without ever raising — long
enough that the retry/pause machinery never fired and the UI just sat on
"ingesting". Bounding the per-attempt time turns that into a fast fail the retry
path can act on.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config        # noqa: E402
import llm           # noqa: E402
import voyage_embed  # noqa: E402


class _Recorder:
    """Stand-in client constructor that records the kwargs it was built with."""
    def __init__(self):
        self.kwargs = None

    def __call__(self, *a, **k):
        self.kwargs = k
        return object()


def test_openai_compatible_client_is_bounded(monkeypatch):
    import openai
    rec = _Recorder()
    monkeypatch.setattr(openai, "OpenAI", rec)
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(config, "NET_TIMEOUT", 123.0)
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm, "_kind", None)

    llm._ensure_client()
    assert rec.kwargs["timeout"] == 123.0
    assert rec.kwargs["max_retries"] == 0


def test_anthropic_client_is_bounded(monkeypatch):
    import anthropic
    rec = _Recorder()
    monkeypatch.setattr(anthropic, "Anthropic", rec)
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(config, "NET_TIMEOUT", 77.0)
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm, "_kind", None)

    llm._ensure_client()
    assert rec.kwargs["timeout"] == 77.0
    assert rec.kwargs["max_retries"] == 0


def test_voyage_client_is_bounded(monkeypatch):
    import voyageai
    rec = _Recorder()
    monkeypatch.setattr(voyageai, "Client", rec)
    monkeypatch.setattr(config, "VOYAGE_API_KEY", "k")
    monkeypatch.setattr(config, "NET_TIMEOUT", 99.0)
    monkeypatch.setattr(voyage_embed, "_client", None)

    voyage_embed.client()
    assert rec.kwargs["timeout"] == 99.0
    assert rec.kwargs["max_retries"] == 0
