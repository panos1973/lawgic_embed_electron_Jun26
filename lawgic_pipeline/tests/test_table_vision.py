"""Unit tests for table_vision.read_page_vision (rasterizer + vision LLM mocked)."""
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
    out, why = table_vision.read_page_vision("x.pdf", 3)
    assert why is None                                  # success -> no reason
    assert out.startswith("Το μάθημα ΒΤ_1.1")          # narration first -> semantic vector
    assert "| Κωδικός | ECTS |" in out                  # faithful markdown follows
    assert tables_json(out) is not None                 # table_json still recoverable


def test_read_page_vision_handles_scanned_figure_page(monkeypatch):
    # a scanned form/seal page (no table) is read too — text + figure description
    monkeypatch.setattr(table_vision, "_render_png", lambda p, n, dpi=200: b"PNG")
    monkeypatch.setattr(llm, "complete_vision",
                        lambda system, user, b64, want_json=True, max_tokens=4096:
                        json.dumps({"markdown": "ΥΠΟΔΕΙΓΜΑ ΠΡΑΚΤΙΚΟΥ ΕΞΕΤΑΣΗΣ\n"
                                                "[σφραγίδα: Πανεπιστήμιο Πατρών]",
                                    "narration": "Σελίδα με υπόδειγμα πρακτικού και σφραγίδα."}))
    out, why = table_vision.read_page_vision("x.pdf", 4)
    assert why is None
    assert out.startswith("Σελίδα με υπόδειγμα")        # narration first
    assert "σφραγίδα" in out and "ΥΠΟΔΕΙΓΜΑ" in out      # figure description + text


def test_narrate_none_when_rasterize_fails(monkeypatch):
    monkeypatch.setattr(table_vision, "_render_png", lambda p, n, dpi=200: None)
    out, why = table_vision.read_page_vision("x.pdf", 1)
    assert out is None and "rasterize" in why            # reason surfaced to caller


def test_narrate_degrades_on_no_llm_key(monkeypatch):
    monkeypatch.setattr(table_vision, "_render_png", lambda p, n, dpi=200: b"PNG")

    def _no_key(*a, **k):
        raise SystemExit("no key")
    monkeypatch.setattr(llm, "complete_vision", _no_key)
    out, why = table_vision.read_page_vision("x.pdf", 1)  # no raise -> keeps markdown
    assert out is None and "no LLM key" in why


def test_narrate_degrades_on_bad_json(monkeypatch):
    monkeypatch.setattr(table_vision, "_render_png", lambda p, n, dpi=200: b"PNG")
    monkeypatch.setattr(llm, "complete_vision", lambda *a, **k: "not json at all")
    out, why = table_vision.read_page_vision("x.pdf", 1)
    assert out is None and "JSON" in why                 # the actual cause, not just "not read"


def test_vision_call_error_is_surfaced(monkeypatch):
    monkeypatch.setattr(table_vision, "_render_png", lambda p, n, dpi=200: b"PNG")

    def _boom(*a, **k):
        raise RuntimeError("Error code: 400 - content_filter triggered")
    monkeypatch.setattr(llm, "complete_vision", _boom)
    out, why = table_vision.read_page_vision("x.pdf", 7)
    assert out is None and "content_filter" in why       # real API error reaches the log


def test_lenient_json_strips_code_fences(monkeypatch):
    # models often wrap JSON in ```json fences despite the instruction; tolerate it
    monkeypatch.setattr(table_vision, "_render_png", lambda p, n, dpi=200: b"PNG")
    fenced = '```json\n{"markdown": "| a |\\n|---|\\n| 1 |", "narration": "πίνακας"}\n```'
    monkeypatch.setattr(llm, "complete_vision", lambda *a, **k: fenced)
    out, why = table_vision.read_page_vision("x.pdf", 2)
    assert why is None and out.startswith("πίνακας") and "| a |" in out
