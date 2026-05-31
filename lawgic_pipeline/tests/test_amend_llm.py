"""Tests for the LLM-assisted amendment extractor (no network — LLM is stubbed).

Verifies parsing, the new_text capture, multi-target splitting, the source==target
self-loop guard, the fabrication guard, and the cheap no-verb gate.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.amend_llm import extract_amendments_llm, _AMEND_STEMS  # noqa: E402
from models import Law, Provision, TYPE_NOMOS  # noqa: E402


def _law(*texts, number="5090/2024"):
    law = Law(instrument_id=f"ν.{number}", instrument_key="N5090/2024",
              instrument_type=TYPE_NOMOS, title="t", fek_date="2024-05-01")
    for i, t in enumerate(texts, 1):
        law.provisions.append(Provision(
            canonical_id=f"ν.{number}#αρ.{i}", instrument_id=f"ν.{number}",
            instrument_key="N5090/2024", instrument_type=TYPE_NOMOS,
            article_no=str(i), text_in_force=t))
    return law


def _fake(payload):
    """Return a complete() stub that always answers with `payload` (a dict)."""
    return lambda system, user, want_json=True, max_tokens=1500: json.dumps(payload)


def test_replaces_with_new_text_and_nested_scope():
    law = _law("Η περ. α΄ της παρ. 1 του άρθρου 60 του ν. 4172/2013 "
               "αντικαθίσταται ως εξής: «Ειδικά, η δήλωση υποβάλλεται…»")
    extract_amendments_llm(law, complete=_fake({"amendments": [{
        "action": "replaces", "scope": "case", "target_law_number": "4172/2013",
        "target_article_number": "60", "target_paragraph": "1", "target_case": "α",
        "new_text": "Ειδικά, η δήλωση υποβάλλεται…", "position": ""}]}))
    assert len(law.amendments) == 1
    op = law.amendments[0]
    assert op.op == "replaces" and op.scope == "case"
    assert op.target_id == "ν.4172/2013#αρ.60.παρ.1"
    assert op.new_text.startswith("Ειδικά")
    assert op.resolved is False          # cross-law target, not resolved locally


def test_multi_target_split_into_two_ops():
    law = _law("Οι παρ. 8 και 9 του άρθρου 64 του ν. 4172/2013 "
               "αντικαθίστανται ως εξής: «8. … 9. …»")
    extract_amendments_llm(law, complete=_fake({"amendments": [
        {"action": "replaces", "scope": "paragraph", "target_law_number": "4172/2013",
         "target_article_number": "64", "target_paragraph": "8", "new_text": "8. …"},
        {"action": "replaces", "scope": "paragraph", "target_law_number": "4172/2013",
         "target_article_number": "64", "target_paragraph": "9", "new_text": "9. …"},
    ]}))
    assert len(law.amendments) == 2
    assert {op.target_id for op in law.amendments} == {
        "ν.4172/2013#αρ.64.παρ.8", "ν.4172/2013#αρ.64.παρ.9"}


def test_adds_with_position():
    law = _law("Στο τέλος του άρθρου 13, μετά την παρ. 17, προστίθενται "
               "παράγραφοι 18 έως 22, ως εξής: «18. …»")
    extract_amendments_llm(law, complete=_fake({"amendments": [{
        "action": "adds", "scope": "paragraph", "target_law_number": None,
        "target_article_number": "13", "new_text": "18. …",
        "position": "στο τέλος, μετά την παρ. 17"}]}))
    op = law.amendments[0]
    assert op.op == "adds"
    assert op.target_id == "ν.5090/2024#αρ.13"     # in-law (target_law null)
    assert op.resolved is True
    assert "μετά" in op.target_raw


def test_self_loop_guard_demotes_to_in_law():
    # model wrongly returns the enacting law as the target -> must NOT self-loop
    law = _law("Η παρ. 1 του άρθρου 7 αντικαθίσταται ως εξής: «…»", number="346/2025")
    extract_amendments_llm(law, complete=_fake({"amendments": [{
        "action": "replaces", "scope": "paragraph", "target_law_number": "346/2025",
        "target_article_number": "7", "target_paragraph": "1", "new_text": "…"}]}))
    op = law.amendments[0]
    assert op.target_id == "ν.346/2025#αρ.7.παρ.1"
    assert op.resolved is True          # demoted to in-law, not a cross-law self-loop


def test_fabrication_guard_drops_empty_edges():
    law = _law("τροποποιείται κάτι ασαφές")
    extract_amendments_llm(law, complete=_fake({"amendments": [
        {"action": "amends", "scope": "article"}]}))   # no article, no new_text
    assert law.amendments == []


def test_no_verb_provision_is_not_sent_to_llm():
    calls = []
    def spy(system, user, want_json=True, max_tokens=1500):
        calls.append(user)
        return json.dumps({"amendments": []})
    law = _law("Γενική διάταξη χωρίς καμία μεταβολή.")
    extract_amendments_llm(law, complete=spy)
    assert calls == []                   # cheap gate skipped the call entirely


def test_missing_key_propagates_systemexit():
    def no_key(system, user, want_json=True, max_tokens=1500):
        raise SystemExit("Missing DEEPSEEK_API_KEY")
    law = _law("Το άρθρο 5 αντικαθίσταται ως εξής: «…»")
    try:
        extract_amendments_llm(law, complete=no_key)
        assert False, "should have propagated SystemExit"
    except SystemExit:
        pass


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
