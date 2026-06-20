"""Tests for classify_llm — the LLM type-fallback. The LLM call is injected, so no
network/key is needed. Verifies taxonomy mapping, number handling, and clean
degradation (unknown label / bad JSON -> None; no key -> SystemExit propagates)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from pipeline import classify_llm  # noqa: E402
from models import TYPE_NOMOS, TYPE_PNP, TYPE_YA  # noqa: E402


def _mock(payload):
    return lambda system, user, want_json=True, max_tokens=120: json.dumps(payload)


def test_identifies_numbered_law():
    g = classify_llm.classify_instrument(
        "ΝΟΜΟΣ ΥΠ' ΑΡΙΘΜ. 5086 ...", complete=_mock({"type": "ΝΟΜΟΣ", "number": 5086}))
    assert g == {"instrument_type": TYPE_NOMOS, "number": 5086}


def test_pnp_is_numberless_even_if_model_returns_a_number():
    # a Π.Ν.Π. is dateless; a stray number from the model is dropped so the
    # deterministic FEK-ref id is used (not a bogus NUM/YEAR).
    g = classify_llm.classify_instrument(
        "ΠΡΑΞΗ ΝΟΜΟΘΕΤΙΚΟΥ ΠΕΡΙΕΧΟΜΕΝΟΥ ...",
        complete=_mock({"type": "ΠΡΑΞΗ ΝΟΜΟΘΕΤΙΚΟΥ ΠΕΡΙΕΧΟΜΕΝΟΥ", "number": 132}))
    assert g == {"instrument_type": TYPE_PNP, "number": None}


def test_decision_type_mapped():
    g = classify_llm.classify_instrument(
        "Αριθμ. 1 ... Ο ΥΠΟΥΡΓΟΣ ...",
        complete=_mock({"type": "ΥΠΟΥΡΓΙΚΗ ΑΠΟΦΑΣΗ", "number": None}))
    assert g["instrument_type"] == TYPE_YA and g["number"] is None


def test_label_match_is_accent_and_case_tolerant():
    g = classify_llm.classify_instrument("x", complete=_mock({"type": "Νόμος", "number": 1}))
    assert g["instrument_type"] == TYPE_NOMOS


def test_unknown_label_returns_none():
    assert classify_llm.classify_instrument(
        "x", complete=_mock({"type": "ΕΓΚΥΚΛΙΟΣ", "number": None})) is None
    assert classify_llm.classify_instrument(
        "x", complete=_mock({"type": None, "number": None})) is None


def test_bad_json_returns_none():
    assert classify_llm.classify_instrument("x", complete=lambda *a, **k: "not json") is None


def test_empty_text_skips_call():
    called = []
    classify_llm.classify_instrument("   ", complete=lambda *a, **k: called.append(1) or "{}")
    assert not called


def test_no_key_propagates_systemexit():
    def no_key(*a, **k):
        raise SystemExit("no key")
    with pytest.raises(SystemExit):
        classify_llm.classify_instrument("ΝΟΜΟΣ ...", complete=no_key)
