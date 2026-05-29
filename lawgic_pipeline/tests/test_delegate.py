"""Unit tests for delegate.py (pure text, no external deps)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.delegate import extract_delegations, _resolve_enabling  # noqa: E402
from models import (Law, Provision, TYPE_YA, TYPE_KYA, TYPE_NOMOS, TYPE_PD,  # noqa: E402
                    TYPE_KANAP)


def _impl_law(text, itype=TYPE_YA, title="Απόφαση περί X"):
    law = Law(instrument_id="ΥΑ 12345/2024", instrument_key="YA12345/2024",
              instrument_type=itype, title=title)
    law.provisions.append(Provision(
        canonical_id="ΥΑ 12345/2024#αρ.1", instrument_id="ΥΑ 12345/2024",
        instrument_key="YA12345/2024", instrument_type=itype,
        article_no="1", chunk_type="article", text_in_force=text))
    return law


def test_resolve_full_enabling_reference():
    eid, lawno, art, resolved = _resolve_enabling(
        "της παρ. 2 του άρθρου 5 του ν. 4412/2016")
    assert eid == "ν.4412/2016#αρ.5.παρ.2"
    assert lawno == "4412/2016" and art == "5" and resolved is True


def test_resolve_pd_enabling():
    eid, _, _, _ = _resolve_enabling("του άρθρου 12 του π.δ. 80/2021")
    assert eid == "π.δ.80/2021#αρ.12"


def test_resolve_old_law_types():
    eid, _, _, _ = _resolve_enabling("του άρθρου 3 του α.ν. 1846/1951")
    assert eid == "α.ν.1846/1951#αρ.3"
    eid2, _, _, _ = _resolve_enabling("του άρθρου 7 του ν.δ. 356/1974")
    assert eid2 == "ν.δ.356/1974#αρ.7"


def test_extract_katexousiodotisi():
    law = _impl_law(
        "Ο Υπουργός Οικονομικών, έχοντας υπόψη, κατ' εξουσιοδότηση της παρ. 2 "
        "του άρθρου 5 του ν. 4412/2016, αποφασίζει τα εξής.")
    extract_delegations(law)
    assert len(law.delegations) == 1
    d = law.delegations[0]
    assert d.enabling_id == "ν.4412/2016#αρ.5.παρ.2"
    assert d.implementing_id == "ΥΑ 12345/2024"
    assert d.enabling_law_number == "4412/2016"
    assert "Υπουργός" in d.delegated_authority


def test_extract_dynamei_kya():
    law = _impl_law("Δυνάμει του άρθρου 90 του ν. 4622/2019 ορίζεται ότι...",
                    itype=TYPE_KYA)
    extract_delegations(law)
    assert len(law.delegations) == 1
    assert law.delegations[0].enabling_id == "ν.4622/2019#αρ.90"


def test_extract_regulatory_authority_decision():
    law = _impl_law("βάσει του άρθρου 4 του ν. 4001/2011 η Αρχή αποφασίζει.",
                    itype=TYPE_KANAP)
    extract_delegations(law)
    assert len(law.delegations) == 1
    assert law.delegations[0].enabling_id == "ν.4001/2011#αρ.4"


def test_primary_legislation_emits_no_delegations():
    # a νόμος is a grantor, not an exerciser -> no edges even if it cites articles
    law = Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
              instrument_type=TYPE_NOMOS)
    law.provisions.append(Provision(
        canonical_id="ν.5090/2024#αρ.1", instrument_id="ν.5090/2024",
        instrument_key="N5090/2024", instrument_type=TYPE_NOMOS, article_no="1",
        chunk_type="article",
        text_in_force="σύμφωνα με το άρθρο 5 του ν. 4412/2016 ισχύει..."))
    extract_delegations(law)
    assert law.delegations == []


def test_duplicate_reference_deduped():
    law = _impl_law(
        "κατ' εξουσιοδότηση του άρθρου 5 του ν. 4412/2016. "
        "Επίσης δυνάμει του άρθρου 5 του ν. 4412/2016 πάλι.")
    extract_delegations(law)
    assert len(law.delegations) == 1     # same (enabling, implementing) deduped


def test_no_reference_no_edge():
    law = _impl_law("Ο Υπουργός αποφασίζει χωρίς ρητή παραπομπή σε νόμο.")
    extract_delegations(law)
    assert law.delegations == []


def test_preamble_enabling_full_text():
    # codifying π.δ.: enabling stated in «Έχοντας υπόψη», before the first Άρθρο
    law = Law(instrument_id="π.δ.62/2025", instrument_key="PD62/2025",
              instrument_type=TYPE_PD, title="Κώδικας Εργατικού Δικαίου")
    full = ("ΠΡΟΕΔΡΙΚΟ ΔΙΑΤΑΓΜΑ ΥΠ' ΑΡΙΘΜ. 62\nΈχοντας υπόψη:\n"
            "1. Την παρ. 6 του άρθρου 67 του ν. 4622/2019, αποφασίζουμε:\n"
            "Άρθρο 1\nσύμφωνα με τις διατάξεις του άρθρου 5 του ν. 9999/2000 ...")
    extract_delegations(law, full_text=full)
    ids = [d.enabling_id for d in law.delegations]
    assert ids == ["ν.4622/2019#αρ.67.παρ.6"]      # preamble only, not the body ref


def test_body_cross_reference_not_a_delegation():
    # a citation deep in an article body (after the operative verb) must NOT emit
    law = _impl_law("αποφασίζει τα εξής. Άρθρο 1. σύμφωνα με τις διατάξεις "
                    "του άρθρου 5 του ν. 4412/2016 ισχύει ο κανόνας.")
    extract_delegations(law)
    assert law.delegations == []


def test_pd_is_now_an_implementing_type():
    from pipeline.delegate import IMPLEMENTING_TYPES
    assert TYPE_PD in IMPLEMENTING_TYPES


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}"); passed += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
