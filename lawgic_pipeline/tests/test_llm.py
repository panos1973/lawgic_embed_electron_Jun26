"""Tests for the provider dispatch in llm.complete (no network — client stubbed).

Focus: the per-provider request kwargs, especially that Gemini's default-on
reasoning is DISABLED for extraction calls so it does not eat the max_tokens
budget and return truncated/empty JSON (the empty-enrichment regression).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import llm  # noqa: E402


class _FakeResp:
    class _Choice:
        class _Msg:
            content = '{"ok": true}'
        message = _Msg()
    choices = [_Choice()]


class _FakeClient:
    """Records the kwargs passed to chat.completions.create."""
    def __init__(self, sink):
        self._sink = sink
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self._sink.update(kwargs)
        return _FakeResp()


def _capture(provider, thinking):
    """Run complete() against a stubbed openai client and return the sent kwargs."""
    sink = {}
    old_provider, old_thinking = config.LLM_PROVIDER, config.LLM_THINKING
    old_client, old_kind = llm._client, llm._kind
    try:
        config.LLM_PROVIDER, config.LLM_THINKING = provider, thinking
        llm._client, llm._kind = _FakeClient(sink), "openai"
        llm.complete("SYS", "USER", want_json=True, max_tokens=700)
    finally:
        config.LLM_PROVIDER, config.LLM_THINKING = old_provider, old_thinking
        llm._client, llm._kind = old_client, old_kind
    return sink


def test_gemini_disables_thinking_by_default():
    # thinking OFF (the pipeline default for extraction) -> budget 0 so the whole
    # max_tokens budget goes to the answer, not Gemini's reasoning.
    kw = _capture("gemini", thinking=False)
    assert kw["extra_body"] == {
        "extra_body": {"google": {"thinking_config": {"thinking_budget": 0}}}}
    assert kw["response_format"] == {"type": "json_object"}


def test_gemini_dynamic_thinking_when_enabled():
    # thinking ON -> -1 lets the model decide (dynamic budget), not a hard 0.
    kw = _capture("gemini", thinking=True)
    assert kw["extra_body"] == {
        "extra_body": {"google": {"thinking_config": {"thinking_budget": -1}}}}


def test_deepseek_thinking_disabled_uses_deepseek_flag():
    # the DeepSeek branch must keep its own thinking flag shape, not Gemini's.
    kw = _capture("deepseek", thinking=False)
    assert kw["extra_body"] == {"thinking": {"type": "disabled"}}


def test_openai_sends_no_thinking_extra_body():
    # gpt-4.1 is non-reasoning: JSON mode set, and NO provider-specific extra_body
    # (which could break the request), regardless of the THINKING flag.
    kw = _capture("openai", thinking=False)
    assert kw["response_format"] == {"type": "json_object"}
    assert "extra_body" not in kw
    assert "extra_body" not in _capture("openai", thinking=True)


def test_qwen_disables_thinking_by_default():
    # Qwen3 hybrid thinking is turned OFF for extraction so it doesn't eat the budget.
    assert _capture("qwen", thinking=False)["extra_body"] == {"enable_thinking": False}
    assert _capture("qwen", thinking=True)["extra_body"] == {"enable_thinking": True}


def test_new_providers_are_registered():
    # both providers must resolve to a default model so model_name() never blanks.
    for prov in ("openai", "qwen"):
        assert prov in config.PROVIDERS
        assert config.PROVIDERS[prov]["default_model"]


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
