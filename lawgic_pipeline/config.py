"""config.py — central config + secrets for the ingestion pipeline.

Secrets come from environment variables (set them, or use a .env loaded by your
shell). Collection names match create_all_collections.py (the Jun2026 batch).
"""
from __future__ import annotations
import os

# --- Weaviate ---
WEAVIATE_URL = os.environ.get(
    "WEAVIATE_URL",
    "https://dxyeak9tnm4gp8raeh1g.c0.europe-west3.gcp.weaviate.cloud",
)
WEAVIATE_API_KEY = os.environ.get("WEAVIATE_API_KEY", "")

# --- Voyage ---
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY", "")
EMBED_MODEL = "voyage-context-3"
EMBED_DIM = 1024
RERANK_MODEL = "rerank-2.5"

# --- Azure Document Intelligence (table pages) ---
AZURE_DI_ENDPOINT = os.environ.get("DI_ENDPOINT", "")
AZURE_DI_KEY = os.environ.get("DI_KEY", "")
# Which DI prebuilt model to run (NOT an LLM — Azure's own document models):
#   prebuilt-layout — text + TABLES + structure + OCR (markdown). Best for FEK tables.
#   prebuilt-read   — OCR text + reading order only (cheaper, no table structure).
AZURE_DI_MODEL = os.environ.get("AZURE_DI_MODEL", "prebuilt-layout")

# --- LLM for enrichment (summary / keywords / EUROVOC / ΔΚΝ) ---
# Provider-agnostic. Pick via LLM_PROVIDER + LLM_MODEL (or the Electron Settings UI).
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")   # Alibaba DashScope (Qwen)

# Azure OpenAI (gpt-4.1 / gpt-4.1-mini) — needs endpoint + key + api-version; the
# chosen model name is used as the Azure DEPLOYMENT name. Set via the app Settings
# or these env vars. (Endpoint is not a secret; the key must come from env/Settings.)
# api-version note: 2024-10-21 (GA) serves gpt-4.1* TEXT, but the IMAGE/multimodal
# route for these 2025 models needs a newer version — an older one 404s the vision
# call with "DeploymentNotFound" while text works. 2024-12-01-preview serves both
# (it is what Azure's own portal sample shows for the gpt-4.1-mini deployment).
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_KEY = os.environ.get("AZURE_OPENAI_KEY", "")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "deepseek")   # anthropic|deepseek|gemini|openai|qwen
LLM_MODEL = os.environ.get("LLM_MODEL", "")                  # blank -> provider default
LLM_THINKING = os.environ.get("LLM_THINKING", "off").lower() in ("on", "true", "1")
LLM_REASONING_EFFORT = os.environ.get("LLM_REASONING_EFFORT", "high")  # low|medium|high (DeepSeek, when thinking on)
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0"))

# Amendment extractor: "deterministic" (pattern-based amend.py) or "llm"
# (amend_llm.py, provider-agnostic via LLM_PROVIDER). Default stays deterministic
# until the LLM extractor is validated against the gold edges (benchmark).
AMEND_EXTRACTOR = os.environ.get("AMEND_EXTRACTOR", "deterministic")  # deterministic | llm

# Read table pages with the multimodal LLM: rasterize each detected table page and
# let the model READ the image -> faithful markdown (-> table_json for exact lookup)
# + a Greek narration (-> embedded, for semantic retrieval). Source-agnostic (digital
# OR photocopied). Runs ONLY on detected table pages; needs an LLM key (e.g. gpt-4.1-mini).
TABLE_VISION = os.environ.get("TABLE_VISION", "off").lower() in ("on", "true", "1")

# Optional DEDICATED provider/model for the table/figure IMAGE reads only. The main
# LLM_PROVIDER may be text-only (e.g. DeepSeek V4, which rejects image input): set
# these to route ONLY complete_vision() to a multimodal model (e.g. openai /
# gpt-4.1-mini) while the bulk text work (enrich + amend over every provision) stays
# cheap on LLM_PROVIDER. Blank VISION_PROVIDER -> reuse LLM_PROVIDER (prior behavior).
# The vision provider authenticates with ITS provider's existing key (openai ->
# OPENAI_API_KEY); no new key name. Vision pages are a tiny fraction of the corpus, so
# the added spend is negligible. Still gated by TABLE_VISION.
VISION_PROVIDER = os.environ.get("VISION_PROVIDER", "")   # blank -> LLM_PROVIDER
VISION_MODEL = os.environ.get("VISION_MODEL", "")         # blank -> that provider's default

# sdk: which client. base_url: OpenAI-compatible endpoint (None = native). key: config attr.
PROVIDERS = {
    "anthropic": {"sdk": "anthropic", "base_url": None,
                  "key": "ANTHROPIC_API_KEY", "default_model": "claude-haiku-4-5"},
    "deepseek":  {"sdk": "openai",
                  "base_url": "https://api.deepseek.com",
                  # DeepSeek V4 Pro, run non-thinking (LLM_THINKING=off). Confirm
                  # the exact model id against the DeepSeek API if it changes.
                  "key": "DEEPSEEK_API_KEY", "default_model": "deepseek-v4-pro"},
    "gemini":    {"sdk": "openai",
                  "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                  "key": "GEMINI_API_KEY", "default_model": "gemini-2.5-flash"},
    "openai":    {"sdk": "openai", "base_url": None,   # native api.openai.com/v1
                  # gpt-4.1 is NOT a reasoning model: no thinking to disable, JSON
                  # mode supported. Override the exact snapshot via LLM_MODEL.
                  "key": "OPENAI_API_KEY", "default_model": "gpt-4.1-2025-04-14"},
    "azure":     {"sdk": "azure", "base_url": None,   # Azure OpenAI (endpoint + api-version)
                  # LLM_MODEL is the Azure DEPLOYMENT name (gpt-4.1 / gpt-4.1-mini);
                  # endpoint/key/api-version come from AZURE_OPENAI_* (or Settings).
                  "key": "AZURE_OPENAI_KEY", "default_model": "gpt-4.1"},
    "qwen":      {"sdk": "openai",
                  # Alibaba DashScope, OpenAI-compatible (international endpoint).
                  # For the China region use dashscope.aliyuncs.com; for a
                  # self-hosted Qwen3 (DAEDALUS) point base_url at that server.
                  "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                  "key": "DASHSCOPE_API_KEY", "default_model": "qwen-plus"},
}

# --- Tenancy ---
DEFAULT_TENANT = os.environ.get("JURISDICTION", "gr")

# --- Collection names (must match create_all_collections.py) ---
FLAT_COLLECTION = "Jun2026GRLegaDocs"
GRAPH_DOCUMENT = "Jun2026LawDocument"
GRAPH_ARTICLE = "Jun2026LawArticle"
GRAPH_AMENDMENT = "Jun2026Amendment"
GRAPH_DELEGATION = "Jun2026Delegation"

# --- Local state DB ---
STATE_DB = os.environ.get("STATE_DB", "lawgic_state.db")

# Azure DI output cache. OCR of a scanned page is keyed by the file's CONTENT hash +
# DI model + page range, so the same pages of the same file always return the same
# OCR (deterministic segmentation) and a re-embed pays NOTHING to re-OCR. Set empty
# to disable. Defaults next to the state DB so it persists across runs.
DI_CACHE_DIR = os.environ.get("DI_CACHE_DIR") or \
    os.path.join(os.path.dirname(os.path.abspath(STATE_DB)), "di_cache")

# --- Pipeline tuning ---
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _float_env(name: str):
    """Optional float from env: None when unset/blank/invalid (keep code default)."""
    v = os.environ.get(name, "")
    try:
        return float(v) if v not in ("", None) else None
    except (TypeError, ValueError):
        return None


# Per-document worker pool size (parallel laws within ONE app instance).
CONCURRENCY = _int_env("CONCURRENCY", 4)

# Per-PROVISION LLM fan-out inside ONE law (enrichment + amendment extraction). Those
# stages make one LLM call per provision; a 150-article law ran them serially (~7s
# each ≈ 18 min). The calls are independent and I/O-bound, so we issue up to this many
# at once. The per-provider token-bucket rate limiter still caps the real request rate.
LLM_CONCURRENCY = _int_env("LLM_CONCURRENCY", 8)

# Optional per-process API request-rate caps (requests/minute). Unset -> the
# conservative built-in defaults in ratelimit.py. The limiter is per-process and
# does NOT coordinate across instances, so when running several app instances in
# parallel against ONE shared API quota, set each instance to about
# (account_limit / number_of_instances) so the COMBINED rate stays under the quota.
VOYAGE_RPM = _float_env("VOYAGE_RPM")     # voyage-context-3 embedding requests/min
LLM_RPM = _float_env("LLM_RPM")           # enrichment LLM (LLM_PROVIDER) requests/min

# Per-request network timeout (seconds) for the LLM + Voyage SDK clients. Their
# defaults are uniformly LONG (OpenAI/Anthropic 600s; Voyage falls back to 600s) AND
# the SDKs retry internally (OpenAI defaults to max_retries=2), so one stalled call can
# block a worker for many minutes — up to ~30 min — before it even raises. On a long
# parallel run a few stalled sockets freeze every worker that way: nothing has raised
# yet, so our retry/pause machinery hasn't fired and the UI just sits on "ingesting"
# with no progress (it looks dead). Bounding the per-attempt time — paired with
# max_retries=0 so OUR with_retry owns retries — makes a stalled call fail fast, then
# retry with visible backoff, then escalate to a clean PAUSE if it's systemic. Generous
# enough that no legitimate single call (incl. an 8k-token amendment extraction) hits it.
NET_TIMEOUT = _float_env("NET_TIMEOUT") or 180.0


def require(*names: str) -> None:
    """Fail fast if required secrets are missing."""
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise SystemExit(f"Missing required config: {', '.join(missing)}")
