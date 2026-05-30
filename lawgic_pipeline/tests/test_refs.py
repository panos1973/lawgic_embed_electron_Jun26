"""Unit tests for pipeline/refs.py — EU external-reference tagging."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.refs import extract_external_refs  # noqa: E402
from models import Law, Provision, TYPE_NOMOS  # noqa: E402


def _law(text):
    law = Law(instrument_id="ν.5188/2025", instrument_key="x",
              instrument_type=TYPE_NOMOS)
    law.provisions.append(Provision(
        canonical_id="ν.5188/2025#αρ.1", instrument_id="ν.5188/2025",
        instrument_key="x", instrument_type=TYPE_NOMOS, article_no="1",
        chunk_type="article", text_in_force=text))
    return law


def test_eu_regulation_parenthesised():
    law = extract_external_refs(_law(
        "Μέτρα εφαρμογής του Κανονισμού (ΕΕ) 2022/868 και του Κανονισμού (ΕΕ) 2016/679."))
    assert "ΕΕ:Κανονισμός 2022/868" in law.provisions[0].cites
    assert "ΕΕ:Κανονισμός 2016/679" in law.provisions[0].cites


def test_eu_directive_postfix_form():
    law = extract_external_refs(_law("σύμφωνα με την Οδηγία 2011/83/ΕΕ"))
    assert "ΕΕ:Οδηγία 2011/83" in law.provisions[0].cites


def test_no_eu_ref_leaves_cites_empty():
    law = extract_external_refs(_law("σύμφωνα με το άρθρο 5 του ν. 4412/2016"))
    assert law.provisions[0].cites == []


def test_dedup():
    law = extract_external_refs(_law(
        "Κανονισμού (ΕΕ) 2022/868 ... πάλι Κανονισμός (ΕΕ) 2022/868."))
    assert law.provisions[0].cites.count("ΕΕ:Κανονισμός 2022/868") == 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    p = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); p += 1
        except AssertionError as e:
            print("FAIL", fn.__name__, e)
    print(f"\n{p}/{len(fns)} passed")
    sys.exit(0 if p == len(fns) else 1)
