"""Unit tests for table_vision.narrate_table_page (rasterizer + vision LLM mocked)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm  # noqa: E402
from pipeline import table_vision  # noqa: E402
from pipeline.tables import tables_json  # noqa: E402


def test_narrate_returns_narration_then_faithful_markdown(monkeypatch):
    monkeypatch.setattr(table_vision, "_render_png", lambda p, n, dpi=200: b"PNG")
    monkeypatch.setattr(llm, "complete_vision",
                        lambda system, user, b64, want_json=True, max_tokens=4096:
                        json.dumps({"markdown": "| Κωδικός | ECTS |\n| --- | --- |\n| ΒΤ_1.1 | 15 |",
                                    "narration": "Το μάθημα ΒΤ_1.1 αξίζει 15 ECTS."}))
    out = table_vision.narrate_table_page("x.pdf", 3)
    assert out.startswith("Το μάθημα ΒΤ_1.1")          # narration first -> semantic vector
    assert "| Κωδικός | ECTS |" in out                  # faithful markdown follows
    assert tables_json(out) is not None                 # table_json still recoverable


def test_narrate_none_when_rasterize_fails(monkeypatch):
    monkeypatch.setattr(table_vision, "_render_png", lambda p, n, dpi=200: None)
    assert table_vision.narrate_table_page("x.pdf", 1) is None


def test_narrate_degrades_on_no_llm_key(monkeypatch):
    monkeypatch.setattr(table_vision, "_render_png", lambda p, n, dpi=200: b"PNG")

    def _no_key(*a, **k):
        raise SystemExit("no key")
    monkeypatch.setattr(llm, "complete_vision", _no_key)
    assert table_vision.narrate_table_page("x.pdf", 1) is None      # no raise -> keeps markdown


def test_narrate_degrades_on_bad_json(monkeypatch):
    monkeypatch.setattr(table_vision, "_render_png", lambda p, n, dpi=200: b"PNG")
    monkeypatch.setattr(llm, "complete_vision", lambda *a, **k: "not json at all")
    assert table_vision.narrate_table_page("x.pdf", 1) is None
