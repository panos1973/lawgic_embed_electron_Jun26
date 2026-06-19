"""diag.py — credential / connectivity self-check.

Pings the configured services and reports OK / auth-error / unreachable WITHOUT
running a full embed, so the operator can confirm keys from the app (cli diag).
Never prints secret values — only whether each credential is present and whether
the live endpoint accepts it.

HTTP is done with urllib (no SDK connection needed, so it works even where the
gRPC Weaviate client can't connect). The opener is injectable for tests.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request

import config

# Document Intelligence GA REST surface (List Models) — an authenticated GET that
# does NOT analyse a document, so it's a free, side-effect-free credential probe.
_DI_API_VERSION = "2024-11-30"


def _opener():
    return urllib.request.urlopen


def _fp(secret: str) -> str:
    """Safe fingerprint of a secret — first4…last4·length, never the value. Lets the
    operator confirm WHICH key is loaded (and spot a wrong/stale/whitespace key)
    without ever exposing it."""
    s = (secret or "").strip()
    if not s:
        return "(not set)"
    if len(s) < 12:
        return f"(set·{len(s)} chars)"
    return f"{s[:4]}…{s[-4:]}·{len(s)}"


def _host(url: str) -> str:
    """Just the host of a URL, for display (no path, no secrets)."""
    import urllib.parse
    u = (url or "").strip()
    if not u:
        return "(not set)"
    return urllib.parse.urlparse(u if "://" in u else "https://" + u).netloc or u


def check_azure_di(opener=None) -> dict:
    """Ping the Azure DI endpoint and classify the result."""
    opener = opener or _opener()
    ep = (config.AZURE_DI_ENDPOINT or "").strip().rstrip("/")
    key = (config.AZURE_DI_KEY or "").strip()
    if not ep or not key:
        return {"service": "azure_di", "ok": False, "status": "not_configured",
                "detail": "DI_ENDPOINT / DI_KEY not set — OCR for scanned pages is OFF"}
    who = f"endpoint {_host(ep)}, key {_fp(key)}"
    url = f"{ep}/documentintelligence/documentModels?api-version={_DI_API_VERSION}"
    req = urllib.request.Request(url, headers={"Ocp-Apim-Subscription-Key": key})
    try:
        resp = opener(req, timeout=20)
        code = getattr(resp, "status", 200)
        try:
            resp.read(256)
        except Exception:  # noqa: BLE001 — body is irrelevant, status is the signal
            pass
        return {"service": "azure_di", "ok": True, "status": "ok",
                "detail": f"{who} accepted (HTTP {code})"}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"service": "azure_di", "ok": False, "status": "auth_error",
                    "detail": f"{who} → rejected (HTTP {e.code}). This key is not valid "
                              "for this resource — make sure the DI key and endpoint come "
                              "from the SAME Azure resource (the 'Azure AI Services' tab)."}
        if e.code == 404:
            return {"service": "azure_di", "ok": False, "status": "not_found",
                    "detail": f"{who} → HTTP 404 — wrong endpoint (resource or region)"}
        return {"service": "azure_di", "ok": False, "status": "http_error",
                "detail": f"{who} → HTTP {e.code} {e.reason}"}
    except urllib.error.URLError as e:
        return {"service": "azure_di", "ok": False, "status": "unreachable",
                "detail": f"cannot reach endpoint ({e.reason}) — check DI_ENDPOINT / network"}
    except Exception as e:  # noqa: BLE001
        return {"service": "azure_di", "ok": False, "status": "error",
                "detail": f"{type(e).__name__}: {e}"}


def check_weaviate(opener=None) -> dict:
    """Ping Weaviate's readiness endpoint (REST; no gRPC needed)."""
    opener = opener or _opener()
    url = (config.WEAVIATE_URL or "").strip().rstrip("/")
    if not url:
        return {"service": "weaviate", "ok": False, "status": "not_configured",
                "detail": "WEAVIATE_URL not set"}
    headers = {}
    if config.WEAVIATE_API_KEY:
        headers["Authorization"] = f"Bearer {config.WEAVIATE_API_KEY}"
    who = f"{_host(url)}, key {_fp(config.WEAVIATE_API_KEY)}"
    req = urllib.request.Request(f"{url}/v1/.well-known/ready", headers=headers)
    try:
        resp = opener(req, timeout=20)
        code = getattr(resp, "status", 200)
        return {"service": "weaviate", "ok": code in (200, 204), "status": "ok",
                "detail": f"{who} → cluster ready (HTTP {code})"}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"service": "weaviate", "ok": False, "status": "auth_error",
                    "detail": f"{who} → key rejected (HTTP {e.code})"}
        return {"service": "weaviate", "ok": False, "status": "http_error",
                "detail": f"{who} → HTTP {e.code} {e.reason}"}
    except urllib.error.URLError as e:
        return {"service": "weaviate", "ok": False, "status": "unreachable",
                "detail": f"cannot reach cluster ({e.reason}) — check WEAVIATE_URL / network"}
    except Exception as e:  # noqa: BLE001
        return {"service": "weaviate", "ok": False, "status": "error",
                "detail": f"{type(e).__name__}: {e}"}


def check_llm(complete=None) -> dict:
    """Make a TINY completion to prove the LLM provider key + deployment actually
    respond — not just that a key string is present. This is what 'key present' alone
    cannot tell you (a wrong Azure deployment name passes the presence check but 404s
    at embed time, silently disabling enrichment + table vision). Costs ~a few tokens.
    `complete` is injectable for tests.
    """
    prov = config.LLM_PROVIDER
    prov_key_env = (config.PROVIDERS.get(prov) or {}).get("key", "")
    model = config.LLM_MODEL or (config.PROVIDERS.get(prov) or {}).get("default_model", "")
    key = (os.environ.get(prov_key_env, "") or getattr(config, prov_key_env, "") or "")
    # who: exactly what this call uses, so a wrong endpoint/deployment/key is visible
    if prov == "azure":
        who = (f"deployment '{model}' @ {_host(config.AZURE_OPENAI_ENDPOINT)} "
               f"(api {config.AZURE_OPENAI_API_VERSION}), key {_fp(key)}")
    else:
        who = f"model '{model}', key {_fp(key)}"
    if not key.strip():
        return {"service": f"llm ({prov})", "ok": False, "status": "not_configured",
                "detail": f"no {prov} key set — LLM enrichment + table vision are OFF"}
    try:
        if complete is None:
            import llm
            complete = llm.complete
        complete("You are a connectivity probe. Reply with: OK", "ping",
                 want_json=False, max_tokens=5)
        return {"service": f"llm ({prov})", "ok": True, "status": "ok",
                "detail": f"{who} → responded"}
    except SystemExit as e:
        return {"service": f"llm ({prov})", "ok": False, "status": "not_configured",
                "detail": str(e)}
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if any(s in msg for s in ("404", "deploymentnotfound", "resource not found",
                                  "does not exist", "no deployment")):
            return {"service": f"llm ({prov})", "ok": False, "status": "not_found",
                    "detail": f"{who} → NOT FOUND (HTTP 404). The deployment name or endpoint "
                              f"is wrong, OR the api-version doesn't serve this model's route "
                              f"(image needs a newer api-version): {str(e)[:90]}"}
        if any(s in msg for s in ("401", "403", "unauthor", "invalid api key",
                                  "permission denied")):
            return {"service": f"llm ({prov})", "ok": False, "status": "auth_error",
                    "detail": f"{who} → key rejected (HTTP 401/403): {str(e)[:90]}"}
        return {"service": f"llm ({prov})", "ok": False, "status": "error",
                "detail": f"{who} → {type(e).__name__}: {str(e)[:120]}"}


def configured_credentials() -> dict:
    """Presence-only map of which credentials are set (never the values)."""
    def has(v) -> bool:
        return bool((v or "").strip())

    prov = config.LLM_PROVIDER
    prov_key_env = (config.PROVIDERS.get(prov) or {}).get("key", "")
    return {
        "weaviate_url": has(config.WEAVIATE_URL),
        "weaviate_key": has(config.WEAVIATE_API_KEY),
        "voyage_key": has(config.VOYAGE_API_KEY),
        "azure_di": has(config.AZURE_DI_ENDPOINT) and has(config.AZURE_DI_KEY),
        "llm_provider": prov,
        "llm_key": has(os.environ.get(prov_key_env, "")),
    }


def config_fingerprint() -> dict:
    """Effective config with every secret masked to a fingerprint — safe to log at
    run start. Lets you see EXACTLY which endpoint / key / deployment each service is
    using, so a wrong or mismatched credential is obvious in the log."""
    prov = config.LLM_PROVIDER
    prov_key_env = (config.PROVIDERS.get(prov) or {}).get("key", "")
    llm_key = os.environ.get(prov_key_env, "") or getattr(config, prov_key_env, "")
    return {
        "weaviate": {"host": _host(config.WEAVIATE_URL), "key": _fp(config.WEAVIATE_API_KEY)},
        "voyage": {"key": _fp(config.VOYAGE_API_KEY)},
        "azure_di": {"host": _host(config.AZURE_DI_ENDPOINT), "key": _fp(config.AZURE_DI_KEY),
                     "model": getattr(config, "AZURE_DI_MODEL", "prebuilt-layout")},
        "llm": {
            "provider": prov,
            "deployment_or_model": config.LLM_MODEL or "(provider default)",
            "azure_endpoint": _host(config.AZURE_OPENAI_ENDPOINT) if prov == "azure" else "n/a",
            "azure_api_version": config.AZURE_OPENAI_API_VERSION if prov == "azure" else "n/a",
            "key": _fp(llm_key),
        },
        "table_vision": bool(getattr(config, "TABLE_VISION", False)),
        "tenant": config.DEFAULT_TENANT,
    }


def run_diagnostics(opener=None) -> dict:
    """Assemble the full diagnostics report."""
    checks = [check_weaviate(opener), check_azure_di(opener), check_llm()]
    return {
        "configured": configured_credentials(),
        "checks": checks,
        "ok": all(c["ok"] for c in checks),
    }
