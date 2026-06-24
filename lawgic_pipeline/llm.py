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
_vision_client = None
_vision_kind = None
_vision_for = None        # which provider the cached vision client was built for
_classify_client = None
_classify_kind = None
_classify_for = None      # which provider the cached identification client was built for


def _make_client(provider: str):
    """Build (client, kind) for one provider. Bounded timeout + max_retries=0 so
    ratelimit.with_retry owns retries (see the NET_TIMEOUT rationale in config)."""
    spec = config.PROVIDERS[provider]
    key = getattr(config, spec["key"], "")
    if not key:
        raise SystemExit(f"Missing {spec['key']} for provider={provider}")
    if spec["sdk"] == "anthropic":
        import anthropic
        return (anthropic.Anthropic(api_key=key, timeout=config.NET_TIMEOUT,
                                    max_retries=0), "anthropic")
    if spec["sdk"] == "azure":
        # Azure OpenAI: same chat.completions surface as OpenAI, but pinned to the
        # resource endpoint + api-version; `model` is the Azure DEPLOYMENT name.
        if not config.AZURE_OPENAI_ENDPOINT:
            raise SystemExit("Missing AZURE_OPENAI_ENDPOINT for provider=azure")
        from openai import AzureOpenAI
        return (AzureOpenAI(api_key=key, azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
                            api_version=config.AZURE_OPENAI_API_VERSION,
                            timeout=config.NET_TIMEOUT, max_retries=0), "openai")
    from openai import OpenAI
    return (OpenAI(api_key=key, base_url=spec["base_url"],
                   timeout=config.NET_TIMEOUT, max_retries=0), "openai")


def _ensure_client():
    global _client, _kind
    if _client is None:
        _client, _kind = _make_client(config.LLM_PROVIDER)


def _ensure_vision_client():
    """Lazily build the client for the VISION provider (VISION_PROVIDER, or the main
    LLM_PROVIDER when unset). Cached, and rebuilt if the provider changes."""
    global _vision_client, _vision_kind, _vision_for
    prov = vision_provider()
    if _vision_client is None or _vision_for != prov:
        _vision_client, _vision_kind = _make_client(prov)
        _vision_for = prov


def _ensure_classify_client():
    """Lazily build the client for the identification provider (CLASSIFY_PROVIDER, or
    the main LLM_PROVIDER when unset). Cached, rebuilt if the provider changes."""
    global _classify_client, _classify_kind, _classify_for
    prov = classify_provider()
    if _classify_client is None or _classify_for != prov:
        _classify_client, _classify_kind = _make_client(prov)
        _classify_for = prov


def model_name() -> str:
    spec = config.PROVIDERS[config.LLM_PROVIDER]
    return config.LLM_MODEL or spec["default_model"]


# Providers whose default models accept image input on the chat surface we use.
# DeepSeek V4 (deepseek-chat / deepseek-v4-*) is TEXT-ONLY and rejects `image_url`
# with a hard 400 ("unknown variant image_url"), so table-vision must skip it (or
# route to a different provider) instead of firing a doomed, retried call per page.
_VISION_PROVIDERS = {"anthropic", "openai", "azure", "gemini"}
# Sensible default model when a VISION_/CLASSIFY_ provider is set without a model. For
# azure this is the DEPLOYMENT name — name your Azure OpenAI deployment to match (or set
# the *_MODEL var). gpt-4.1-mini is the cheap, capable reader/classifier.
_OVERRIDE_DEFAULT_MODEL = {"azure": "gpt-4.1-mini", "openai": "gpt-4.1-mini"}


def _provider_can_see(provider: str, model: str) -> bool:
    if provider in _VISION_PROVIDERS:
        return True
    if provider == "qwen":                # only the qwen-vl-* line is multimodal
        return "vl" in (model or "").lower()
    return False                          # deepseek (and anything else) — text only


def vision_provider() -> str:
    """Which provider reads table/figure IMAGES. Order: an explicit VISION_PROVIDER
    override; else the main LLM_PROVIDER if it can see; else AUTO-FALL-BACK to an
    already-configured vision-capable provider — preferring the existing Azure OpenAI
    (no new credentials), then OpenAI / Gemini / Anthropic. So a text-only main model
    (DeepSeek) gets table-vision for free off the keys already in Settings."""
    if config.VISION_PROVIDER:
        return config.VISION_PROVIDER
    if _provider_can_see(config.LLM_PROVIDER, model_name()):
        return config.LLM_PROVIDER
    if config.AZURE_OPENAI_ENDPOINT and config.AZURE_OPENAI_KEY:
        return "azure"
    if config.OPENAI_API_KEY:
        return "openai"
    if config.GEMINI_API_KEY:
        return "gemini"
    if config.ANTHROPIC_API_KEY:
        return "anthropic"
    return config.LLM_PROVIDER             # nothing configured -> stays, vision skipped


def vision_model() -> str:
    if config.VISION_MODEL:
        return config.VISION_MODEL
    p = vision_provider()
    # vision uses the main provider with no override -> honour the main model choice
    # (e.g. LLM_MODEL=qwen-vl-max).
    if p == config.LLM_PROVIDER and config.LLM_MODEL:
        return config.LLM_MODEL
    return _OVERRIDE_DEFAULT_MODEL.get(p, config.PROVIDERS[p]["default_model"])


def classify_provider() -> str:
    """Provider for the identification fallback — CLASSIFY_PROVIDER if set, else main."""
    return config.CLASSIFY_PROVIDER or config.LLM_PROVIDER


def classify_model() -> str:
    if config.CLASSIFY_MODEL:
        return config.CLASSIFY_MODEL
    p = classify_provider()
    if p == config.LLM_PROVIDER and config.LLM_MODEL:   # same provider -> honour main model
        return config.LLM_MODEL
    return _OVERRIDE_DEFAULT_MODEL.get(p, config.PROVIDERS[p]["default_model"])


def supports_vision() -> bool:
    """True if the resolved VISION provider/model can read an image. With a text-only
    main model this reflects the auto-selected fallback (e.g. the configured Azure
    OpenAI), so table-vision turns on whenever a capable provider is already set up."""
    return _provider_can_see(vision_provider(), vision_model())


def _resolve_client(role: str):
    """Return (client, kind, model, provider) for a call. role='classify' uses the
    CLASSIFY_* provider when one is configured; every other role uses the main
    LLM_PROVIDER. Keeps the bulk work (enrich/amend) on the cheap main model while a
    rare identification call can go to a stronger one."""
    if role == "classify" and config.CLASSIFY_PROVIDER:
        _ensure_classify_client()
        return _classify_client, _classify_kind, classify_model(), classify_provider()
    _ensure_client()
    return _client, _kind, model_name(), config.LLM_PROVIDER


def complete(system: str, user: str, want_json: bool = True,
             max_tokens: int = 1024, role: str = "main") -> str:
    """Return the model's text output. `system` should be the STABLE prefix. `role`
    selects the client: 'classify' may use CLASSIFY_PROVIDER, else the main model."""
    client, kind, model, provider = _resolve_client(role)

    import ratelimit

    if kind == "anthropic":
        kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                      messages=[{"role": "user", "content": user}])
        if config.LLM_THINKING:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 2048}
            kwargs["temperature"] = 1.0          # required when thinking is on
        else:
            kwargs["temperature"] = config.LLM_TEMPERATURE
        msg = ratelimit.with_retry(lambda: client.messages.create(**kwargs),
                                   provider=provider)
        return "".join(getattr(b, "text", "") for b in msg.content
                       if getattr(b, "type", "") == "text")

    # openai-compatible (deepseek / gemini / openai / azure / qwen)
    kwargs = dict(model=model, max_tokens=max_tokens,
                  temperature=config.LLM_TEMPERATURE,
                  messages=[{"role": "system", "content": system},
                            {"role": "user", "content": user}])
    if want_json:
        kwargs["response_format"] = {"type": "json_object"}
    if provider == "deepseek":
        # DeepSeek V4: thinking is a per-request param; level via reasoning_effort.
        # Flash defaults non-thinking; Pro defaults thinking — so we set it explicitly.
        if config.LLM_THINKING:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            kwargs["reasoning_effort"] = config.LLM_REASONING_EFFORT
        else:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    elif provider == "gemini":
        # Gemini 2.5 Flash is a thinking model with reasoning ON by default. On the
        # OpenAI-compatible endpoint that reasoning is billed against the same
        # max_tokens budget as the answer, so on a tight extraction call (e.g. the
        # 700-token enrichment) the thinking eats the budget and the JSON comes back
        # truncated/empty. We disable it (the pipeline's THINKING-OFF intent) via the
        # Gemini-specific thinking_config so the whole budget goes to the answer.
        budget = -1 if config.LLM_THINKING else 0   # -1 = dynamic (model decides)
        kwargs["extra_body"] = {
            "extra_body": {"google": {"thinking_config": {"thinking_budget": budget}}}}
    elif provider == "qwen":
        # Qwen3 (e.g. qwen-plus) is a hybrid thinking model. DashScope's
        # OpenAI-compatible endpoint toggles it via enable_thinking; we keep it OFF
        # for extraction (the pipeline default) so reasoning doesn't eat the budget.
        # NOTE: thinking ON requires streaming on DashScope, which this path doesn't
        # use — so leave LLM_THINKING off for qwen unless a streaming path is added.
        kwargs["extra_body"] = {"enable_thinking": bool(config.LLM_THINKING)}
    # "openai" (gpt-4.1 / gpt-4.1-mini): non-reasoning models — no thinking knob,
    # JSON mode already set above; nothing provider-specific to add.
    resp = ratelimit.with_retry(
        lambda: client.chat.completions.create(**kwargs), provider=provider)
    return resp.choices[0].message.content or ""


def complete_vision(system: str, user: str, image_b64: str, want_json: bool = True,
                    max_tokens: int = 4096) -> str:
    """Like complete(), but with a PNG image attached — for READING a table page.

    `image_b64` is raw base64 of a PNG. Routed to the VISION provider (VISION_PROVIDER
    / VISION_MODEL), which may differ from the main LLM_PROVIDER — so a text-only main
    model (DeepSeek) keeps doing the bulk text work while images go to a multimodal one
    (e.g. openai / gpt-4.1-mini). Same rate-limit/retry path as complete().
    """
    _ensure_vision_client()
    model = vision_model()
    import ratelimit
    provider = vision_provider()

    if _vision_kind == "anthropic":
        content = [{"type": "text", "text": user},
                   {"type": "image", "source": {"type": "base64",
                    "media_type": "image/png", "data": image_b64}}]
        kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                      temperature=config.LLM_TEMPERATURE,
                      messages=[{"role": "user", "content": content}])
        msg = ratelimit.with_retry(lambda: _vision_client.messages.create(**kwargs),
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
        lambda: _vision_client.chat.completions.create(**kwargs), provider=provider)
    return resp.choices[0].message.content or ""
