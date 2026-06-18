"""Tests for diag.py — credential/connectivity self-check. HTTP is mocked via an
injected opener, so no network and no real keys are needed. Verifies the OK /
auth-error / unreachable / not-configured classification and that secret values
are never echoed."""
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import diag  # noqa: E402


class _Resp:
    def __init__(self, status=200):
        self.status = status

    def read(self, n=None):
        return b"{}"


def _ok(req, timeout=None):
    return _Resp(200)


def _http(code):
    def f(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "err", {}, None)
    return f


def _down(req, timeout=None):
    raise urllib.error.URLError("name or service not known")


def test_di_not_configured(monkeypatch):
    monkeypatch.setattr(config, "AZURE_DI_ENDPOINT", "")
    monkeypatch.setattr(config, "AZURE_DI_KEY", "")
    r = diag.check_azure_di(_ok)
    assert r["ok"] is False and r["status"] == "not_configured"


def test_di_ok(monkeypatch):
    monkeypatch.setattr(config, "AZURE_DI_ENDPOINT", "https://x.cognitiveservices.azure.com/")
    monkeypatch.setattr(config, "AZURE_DI_KEY", "secret-di-key")
    r = diag.check_azure_di(_ok)
    assert r["ok"] is True and r["status"] == "ok"
    assert "secret-di-key" not in str(r)            # never echo the key


def test_di_auth_error(monkeypatch):
    monkeypatch.setattr(config, "AZURE_DI_ENDPOINT", "https://x/")
    monkeypatch.setattr(config, "AZURE_DI_KEY", "bad")
    r = diag.check_azure_di(_http(403))
    assert r["ok"] is False and r["status"] == "auth_error"


def test_di_not_found(monkeypatch):
    monkeypatch.setattr(config, "AZURE_DI_ENDPOINT", "https://wrong/")
    monkeypatch.setattr(config, "AZURE_DI_KEY", "k")
    assert diag.check_azure_di(_http(404))["status"] == "not_found"


def test_di_unreachable(monkeypatch):
    monkeypatch.setattr(config, "AZURE_DI_ENDPOINT", "https://nope/")
    monkeypatch.setattr(config, "AZURE_DI_KEY", "k")
    assert diag.check_azure_di(_down)["status"] == "unreachable"


def test_weaviate_ok_and_auth(monkeypatch):
    monkeypatch.setattr(config, "WEAVIATE_URL", "https://w.example/")
    monkeypatch.setattr(config, "WEAVIATE_API_KEY", "wk")
    assert diag.check_weaviate(_ok)["ok"] is True
    assert diag.check_weaviate(_http(401))["status"] == "auth_error"


def test_llm_not_configured(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    r = diag.check_llm()
    assert r["ok"] is False and r["status"] == "not_configured"


def test_llm_ok_when_deployment_responds(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    r = diag.check_llm(complete=lambda *a, **k: "OK")    # injected: no real call
    assert r["ok"] is True and r["status"] == "ok"


def test_llm_deployment_not_found(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "azure")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "k")

    def boom(*a, **k):
        raise RuntimeError("DeploymentNotFound: the API deployment does not exist")
    r = diag.check_llm(complete=boom)
    assert r["ok"] is False and r["status"] == "not_found"   # the wrong-deployment trap


def test_llm_auth_error(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "azure")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "bad")

    def boom(*a, **k):
        raise RuntimeError("Error code: 401 - Unauthorized: invalid api key")
    r = diag.check_llm(complete=boom)
    assert r["ok"] is False and r["status"] == "auth_error"


def test_configured_credentials_presence_only(monkeypatch):
    monkeypatch.setattr(config, "AZURE_DI_ENDPOINT", "https://x/")
    monkeypatch.setattr(config, "AZURE_DI_KEY", "supersecret")
    monkeypatch.setattr(config, "VOYAGE_API_KEY", "")
    c = diag.configured_credentials()
    assert c["azure_di"] is True and c["voyage_key"] is False
    assert "supersecret" not in str(c)              # presence only, never values
