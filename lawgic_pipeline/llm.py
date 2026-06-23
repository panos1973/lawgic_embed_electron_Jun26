"""llm.py — one call, five providers (Claude · DeepSeek V4 · Gemini · OpenAI · Qwen).

Dispatch on config.LLM_PROVIDER. Claude uses the Anthropic SDK; the rest use the
OpenAI SDK — OpenAI natively (base_url=None), DeepSeek / Gemini / Qwen via their
OpenAI-compatible base_url. JSON mode (response_format=json_object) is requested for
extraction calls so the body comes back as pure JSON the callers json.loads directly
— no code-fence/preamble stripping needed.

CACHING NOTE: keep the SYSTEM prompt byte-identical across calls (stable prefix)
and put the variable per-provision text in the USER message (suffix). DeepSeek
caches repeated prefixes automatically and bills cache hits at a fraction of the
miss rate; Anthropic/Gemini cache the prefix too. So the big instruction/schema
block is paid for once, then near-free on every subsequent provision.

THINKING: for summarization + metadata extraction, leave thinking OFF (set in
config). It's a bounded extraction task — chain-of-thought adds latency and billed
reasoning tokens without real quality gain. Reserve thinking for hard reasoning.
"""
from __future__ import annotations
import config

_client = None
_kind = None


def _ensure_client():
    global _client, _kind
    if _client is not None:
        return
    spec = config.PROVIDERS[config.LLM_PROVIDER]
    key = getattr(config, spec["key"], "")
    if not key:
        raise SystemExit(f"Missing {spec['key']} for LLM_PROVIDER={config.LLM_PROVIDER}")
    # Bound every call and let ratelimit.with_retry own ALL retries: max_retries=0
    # disables the SDK's own (silent, slow) retry loop, so a stalled call fails within
    # NET_TIMEOUT and is retried fast by us — or escalated to a clean PAUSE — instead
    # of blocking a worker thread for the SDK's 600s × internal-retries default.
    if spec["sdk"] == "anthropic":
        import anthropic
        _client = anthropic.Anthropic(api_key=key, timeout=config.NET_TIMEOUT,
                                      max_retries=0)
        _kind = "anthropic"
    elif spec["sdk"] == "azure":
        # Azure OpenAI: same chat.completions surface as OpenAI, but the client is
        # pinned to the resource endpoint + api-version, and `model` (set by
        # model_name()) is the Azure DEPLOYMENT name.
        if not config.AZURE_OPENAI_ENDPOINT:
            raise SystemExit("Missing AZURE_OPENAI_ENDPOINT for LLM_PROVIDER=azure")
        from openai import AzureOpenAI
        _client = AzureOpenAI(api_key=key,
                              azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
                              api_version=config.AZURE_OPENAI_API_VERSION,
                              timeout=config.NET_TIMEOUT, max_retries=0)
        _kind = "openai"
    else:
        from openai import OpenAI
        _client = OpenAI(api_key=key, base_url=spec["base_url"],
                         timeout=config.NET_TIMEOUT, max_retries=0)
        _kind = "openai"


def model_name() -> str:
    spec = config.PROVIDERS[config.LLM_PROVIDER]
    return config.LLM_MODEL or spec["default_model"]


def complete(system: str, user: str, want_json: bool = True,
             max_tokens: int = 1024) -> str:
    """Return the model's text output. `system` should be the STABLE prefix."""
    _ensure_client()
    model = model_name()

    import ratelimit
    provider = config.LLM_PROVIDER

    if _kind == "anthropic":
        kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}])
        if config.LLM_THINKING:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 2048}
            kwargs["temperature"] = 1.0          # required when thinking is on
        else:
            kwargs["temperature"] = config.LLM_TEMPERATURE
        msg = ratelimit.with_retry(lambda: _client.messages.create(**kwargs),
                                   provider=provider)
        return "".join(getattr(b, "text", "") for b in msg.content
                       if getattr(b, "type", "") == "text")

    # openai-compatible (deepseek / gemini)
    kwargs = dict(model=model, max_tokens=max_tokens,
                  temperature=config.LLM_TEMPERATURE,
                  messages=[{"role": "system", "content": system},
                            {"role": "user", "content": user}])
    if want_json:
        kwargs["response_format"] = {"type": "json_object"}
    if config.LLM_PROVIDER == "deepseek":
        # DeepSeek V4: thinking is a per-request param; level via reasoning_effort.
        # Flash defaults non-thinking; Pro defaults thinking — so we set it explicitly.
        if config.LLM_THINKING:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            kwargs["reasoning_effort"] = config.LLM_REASONING_EFFORT
        else:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    elif config.LLM_PROVIDER == "gemini":
        # Gemini 2.5 Flash is a thinking model with reasoning ON by default. On the
        # OpenAI-compatible endpoint that reasoning is billed against the same
        # max_tokens budget as the answer, so on a tight extraction call (e.g. the
        # 700-token enrichment) the thinking eats the budget and the JSON comes back
        # truncated/empty. We disable it (the pipeline's THINKING-OFF intent) via the
        # Gemini-specific thinking_config so the whole budget goes to the answer.
        budget = -1 if config.LLM_THINKING else 0   # -1 = dynamic (model decides)
        kwargs["extra_body"] = {
            "extra_body": {"google": {"thinking_config": {"thinking_budget": budget}}}}
    elif config.LLM_PROVIDER == "qwen":
        # Qwen3 (e.g. qwen-plus) is a hybrid thinking model. DashScope's
        # OpenAI-compatible endpoint toggles it via enable_thinking; we keep it OFF
        # for extraction (the pipeline default) so reasoning doesn't eat the budget.
        # NOTE: thinking ON requires streaming on DashScope, which this path doesn't
        # use — so leave LLM_THINKING off for qwen unless a streaming path is added.
        kwargs["extra_body"] = {"enable_thinking": bool(config.LLM_THINKING)}
    # "openai" (gpt-4.1 / gpt-4.1-mini): non-reasoning models — no thinking knob,
    # JSON mode already set above; nothing provider-specific to add.
    resp = ratelimit.with_retry(
        lambda: _client.chat.completions.create(**kwargs), provider=provider)
    return resp.choices[0].message.content or ""


def complete_vision(system: str, user: str, image_b64: str, want_json: bool = True,
                    max_tokens: int = 4096) -> str:
    """Like complete(), but with a PNG image attached — for READING a table page.

    `image_b64` is raw base64 of a PNG. Supported on the multimodal providers
    (OpenAI / Azure gpt-4.1*, Gemini, Claude). Same rate-limit/retry path as complete().
    """
    _ensure_client()
    model = model_name()
    import ratelimit
    provider = config.LLM_PROVIDER

    if _kind == "anthropic":
        content = [{"type": "text", "text": user},
                   {"type": "image", "source": {"type": "base64",
                    "media_type": "image/png", "data": image_b64}}]
        kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                      temperature=config.LLM_TEMPERATURE,
                      messages=[{"role": "user", "content": content}])
        msg = ratelimit.with_retry(lambda: _client.messages.create(**kwargs),
                                   provider=provider)
        return "".join(getattr(b, "text", "") for b in msg.content
                       if getattr(b, "type", "") == "text")

    # openai-compatible (openai / azure / gemini / qwen): image via image_url data URI
    content = [{"type": "text", "text": user},
               {"type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_b64}"}}]
    kwargs = dict(model=model, max_tokens=max_tokens, temperature=config.LLM_TEMPERATURE,
                  messages=[{"role": "system", "content": system},
                            {"role": "user", "content": content}])
    if want_json:
        kwargs["response_format"] = {"type": "json_object"}
    resp = ratelimit.with_retry(
        lambda: _client.chat.completions.create(**kwargs), provider=provider)
    return resp.choices[0].message.content or ""
