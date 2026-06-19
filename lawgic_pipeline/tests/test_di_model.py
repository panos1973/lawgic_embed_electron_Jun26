"""The Azure DI prebuilt model is configurable (config.AZURE_DI_MODEL), not hardcoded.
Verifies _azure_layout_markdown sends the CONFIGURED model to begin_analyze_document.
The azure SDK is faked via sys.modules, so no SDK install / network is needed."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import pipeline.extract as extract  # noqa: E402


def _fake_azure(captured):
    """Build fake azure.* modules; capture the model/pages passed to DI."""
    class _Poller:
        def result(self):
            return types.SimpleNamespace(content="# t\n| a |\n|---|\n| 1 |")

    class DocumentIntelligenceClient:
        def __init__(self, endpoint=None, credential=None):
            captured["endpoint"] = endpoint

        def begin_analyze_document(self, model, body, output_content_format=None, pages=None):
            captured["model"] = model
            captured["pages"] = pages
            return _Poller()

    di = types.ModuleType("azure.ai.documentintelligence")
    di.DocumentIntelligenceClient = DocumentIntelligenceClient

    models = types.ModuleType("azure.ai.documentintelligence.models")
    models.AnalyzeDocumentRequest = lambda bytes_source=None: types.SimpleNamespace(
        bytes_source=bytes_source)
    models.DocumentContentFormat = types.SimpleNamespace(MARKDOWN="markdown")

    cred = types.ModuleType("azure.core.credentials")
    cred.AzureKeyCredential = lambda key: types.SimpleNamespace(key=key)

    return {
        "azure": types.ModuleType("azure"),
        "azure.ai": types.ModuleType("azure.ai"),
        "azure.core": types.ModuleType("azure.core"),
        "azure.ai.documentintelligence": di,
        "azure.ai.documentintelligence.models": models,
        "azure.core.credentials": cred,
    }


def test_di_uses_configured_model(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "AZURE_DI_ENDPOINT", "https://lawgic.cognitiveservices.azure.com/")
    monkeypatch.setattr(config, "AZURE_DI_KEY", "k")
    monkeypatch.setattr(config, "AZURE_DI_MODEL", "prebuilt-read")
    captured = {}
    for name, mod in _fake_azure(captured).items():
        monkeypatch.setitem(sys.modules, name, mod)
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    out = extract._azure_layout_markdown(str(pdf), pages=[3])
    assert captured["model"] == "prebuilt-read"      # the CONFIGURED model, not hardcoded
    assert captured["pages"] == "3"
    assert "| a |" in out


def test_di_defaults_to_layout_when_blank(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "AZURE_DI_ENDPOINT", "https://x/")
    monkeypatch.setattr(config, "AZURE_DI_KEY", "k")
    monkeypatch.setattr(config, "AZURE_DI_MODEL", "")     # blank -> safe fallback
    captured = {}
    for name, mod in _fake_azure(captured).items():
        monkeypatch.setitem(sys.modules, name, mod)
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    extract._azure_layout_markdown(str(pdf))
    assert captured["model"] == "prebuilt-layout"
