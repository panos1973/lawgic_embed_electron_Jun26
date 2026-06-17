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


def check_azure_di(opener=None) -> dict:
    """Ping the Azure DI endpoint and classify the result."""
    opener = opener or _opener()
    ep = (config.AZURE_DI_ENDPOINT or "").strip().rstrip("/")
    key = (config.AZURE_DI_KEY or "").strip()
    if not ep or not key:
        return {"service": "azure_di", "ok": False, "status": "not_configured",
                "detail": "DI_ENDPOINT / DI_KEY not set — OCR for scanned pages is OFF"}
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
                "detail": f"endpoint reachable, key accepted (HTTP {code})"}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"service": "azure_di", "ok": False, "status": "auth_error",
                    "detail": f"key rejected (HTTP {e.code}) — check DI_KEY"}
        if e.code == 404:
            return {"service": "azure_di", "ok": False, "status": "not_found",
                    "detail": "HTTP 404 — check DI_ENDPOINT (wrong resource or region)"}
        return {"service": "azure_di", "ok": False, "status": "http_error",
                "detail": f"HTTP {e.code} {e.reason}"}
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
    req = urllib.request.Request(f"{url}/v1/.well-known/ready", headers=headers)
    try:
        resp = opener(req, timeout=20)
        code = getattr(resp, "status", 200)
        return {"service": "weaviate", "ok": code in (200, 204), "status": "ok",
                "detail": f"cluster ready (HTTP {code})"}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"service": "weaviate", "ok": False, "status": "auth_error",
                    "detail": f"key rejected (HTTP {e.code}) — check WEAVIATE_API_KEY"}
        return {"service": "weaviate", "ok": False, "status": "http_error",
                "detail": f"HTTP {e.code} {e.reason}"}
    except urllib.error.URLError as e:
        return {"service": "weaviate", "ok": False, "status": "unreachable",
                "detail": f"cannot reach cluster ({e.reason}) — check WEAVIATE_URL / network"}
    except Exception as e:  # noqa: BLE001
        return {"service": "weaviate", "ok": False, "status": "error",
                "detail": f"{type(e).__name__}: {e}"}


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


def run_diagnostics(opener=None) -> dict:
    """Assemble the full diagnostics report."""
    checks = [check_weaviate(opener), check_azure_di(opener)]
    return {
        "configured": configured_credentials(),
        "checks": checks,
        "ok": all(c["ok"] for c in checks),
    }
