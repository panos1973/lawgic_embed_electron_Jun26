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


def _clear_vision_creds(monkeypatch):
    # no fallback provider configured -> isolate the main-provider capability check
    monkeypatch.setattr(config, "VISION_PROVIDER", "")
    monkeypatch.setattr(config, "VISION_MODEL", "")
    for attr in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_KEY", "OPENAI_API_KEY",
                 "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setattr(config, attr, "")


def test_supports_vision_gates_text_only_providers(monkeypatch):
    # DeepSeek is text-only: complete_vision would fire a doomed 400 per page, so the
    # table-vision path must be gated off for it. Vision providers stay enabled.
    _clear_vision_creds(monkeypatch)
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek")
    assert llm.supports_vision() is False
    for p in ("openai", "anthropic", "azure", "gemini"):
        monkeypatch.setattr(config, "LLM_PROVIDER", p)
        assert llm.supports_vision() is True
    # qwen only when the multimodal qwen-vl line is the main model
    monkeypatch.setattr(config, "LLM_PROVIDER", "qwen")
    monkeypatch.setattr(config, "LLM_MODEL", "qwen-plus")
    assert llm.supports_vision() is False
    monkeypatch.setattr(config, "LLM_MODEL", "qwen-vl-max")
    assert llm.supports_vision() is True


def test_vision_auto_selects_configured_azure_for_text_only_main(monkeypatch):
    # The shipped scenario: main = DeepSeek (text-only), Azure OpenAI already set up in
    # Settings. Vision must auto-route to Azure with NO new variables, using the
    # existing AZURE_OPENAI_* credentials and the gpt-4.1-mini deployment by default.
    _clear_vision_creds(monkeypatch)
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(config, "AZURE_OPENAI_ENDPOINT", "https://lawgic.openai.azure.com/")
    monkeypatch.setattr(config, "AZURE_OPENAI_KEY", "azkey")
    assert llm.vision_provider() == "azure"
    assert llm.vision_model() == "gpt-4.1-mini"
    assert llm.supports_vision() is True
    # an explicit VISION_MODEL (non-standard Azure deployment name) still wins
    monkeypatch.setattr(config, "VISION_MODEL", "my-4o-mini")
    assert llm.vision_model() == "my-4o-mini"


def test_vision_routes_to_separate_provider(monkeypatch):
    # the hybrid: text-only main model (deepseek) + a dedicated vision provider so
    # complete_vision goes to openai/gpt-4.1-mini while bulk text stays on deepseek.
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(config, "VISION_PROVIDER", "openai")
    monkeypatch.setattr(config, "VISION_MODEL", "gpt-4.1-mini")
    monkeypatch.setattr(llm, "_vision_client", None)
    monkeypatch.setattr(llm, "_vision_for", None)

    assert llm.supports_vision() is True
    assert llm.vision_provider() == "openai" and llm.vision_model() == "gpt-4.1-mini"

    calls = {}

    class _Comp:
        def create(self, **k):
            calls.update(k)
            msg = type("M", (), {"content": '{"ok": 1}'})()
            return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()

    class _FakeClient:
        chat = type("Chat", (), {"completions": _Comp()})()

    built = {}

    def fake_make(provider):
        built["provider"] = provider
        return _FakeClient(), "openai"
    monkeypatch.setattr(llm, "_make_client", fake_make)

    out = llm.complete_vision("sys", "user text", "QkFTRTY0UE5H", want_json=True)
    assert out == '{"ok": 1}'
    assert built["provider"] == "openai"           # built the vision provider's client
    assert calls["model"] == "gpt-4.1-mini"        # used the vision model
    parts = calls["messages"][-1]["content"]       # image rode along as an image_url
    assert any(p.get("type") == "image_url" for p in parts)


def _fake_openai_client(calls):
    class _Comp:
        def create(self, **k):
            calls.update(k)
            msg = type("M", (), {"content": '{"type": "ΝΟΜΟΣ", "number": 5090}'})()
            return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()

    class _Client:
        chat = type("Chat", (), {"completions": _Comp()})()
    return _Client()


def test_complete_classify_role_routes_to_classify_provider(monkeypatch):
    # role='classify' uses CLASSIFY_PROVIDER (e.g. azure/gpt-4.1-mini) while the bulk
    # work stays on the main model.
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(config, "CLASSIFY_PROVIDER", "azure")
    monkeypatch.setattr(config, "CLASSIFY_MODEL", "gpt-4.1-mini")
    monkeypatch.setattr(llm, "_classify_client", None)
    monkeypatch.setattr(llm, "_classify_for", None)
    assert llm.classify_provider() == "azure" and llm.classify_model() == "gpt-4.1-mini"

    calls, built = {}, {}
    monkeypatch.setattr(llm, "_make_client",
                        lambda p: (built.update(provider=p) or _fake_openai_client(calls), "openai"))
    llm.complete("sys", "user", role="classify")
    assert built["provider"] == "azure"            # built the classify provider's client
    assert calls["model"] == "gpt-4.1-mini"
    # deepseek/gemini/qwen thinking knobs must NOT leak onto the azure call
    assert "extra_body" not in calls


def test_complete_classify_role_falls_back_to_main_when_unset(monkeypatch):
    monkeypatch.setattr(config, "CLASSIFY_PROVIDER", "")     # no override -> main model
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(config, "LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm, "_kind", None)
    assert llm.classify_provider() == "deepseek"
    calls, built = {}, {}
    monkeypatch.setattr(llm, "_make_client",
                        lambda p: (built.update(provider=p) or _fake_openai_client(calls), "openai"))
    llm.complete("sys", "user", role="classify")
    assert built["provider"] == "deepseek"          # fell back to the main provider


def test_classify_instrument_uses_classify_role(monkeypatch):
    import pipeline.classify_llm as cl
    from models import TYPE_NOMOS
    seen = {}

    def fake_complete(system, user, want_json=True, max_tokens=1024, role="main"):
        seen["role"] = role
        return '{"type": "ΝΟΜΟΣ", "number": 5090}'
    monkeypatch.setattr(cl.llm, "complete", fake_complete)
    r = cl.classify_instrument("ΝΟΜΟΣ ΥΠ' ΑΡΙΘΜ. 5090 ...")
    assert seen["role"] == "classify"               # the rare ID call is routed to classify
    assert r["instrument_type"] == TYPE_NOMOS and r["number"] == 5090
