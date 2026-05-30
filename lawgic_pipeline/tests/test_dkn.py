"""Unit tests for the deterministic ΔΚΝ (Ραπτάρχης) classifier (no LLM, no network).

classify_dkn maps the shared cited-code/keyword signals to canonical Ραπτάρχης
top-level volume names, plus a folded-keyword pass for ΔΚΝ-only volumes
(agriculture, shipping, ecclesiastical, public works, local government).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.enrich import classify_dkn, DKN_VOLUMES  # noqa: E402
from models import Law, Provision, TYPE_NOMOS  # noqa: E402


def _law(title, text):
    law = Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
              instrument_type=TYPE_NOMOS, title=title)
    law.provisions.append(Provision(
        canonical_id="ν.5090/2024#αρ.1", instrument_id="ν.5090/2024",
        instrument_key="N5090/2024", instrument_type=TYPE_NOMOS,
        article_no="1", text_in_force=text))
    return law


def _dkn(title, text):
    return classify_dkn(_law(title, text)).provisions[0].domain_dkn


def test_cited_code_maps_to_volume():
    # Ποινικός Κώδικας -> criminal -> ΠΟΙΝΙΚΗ ΝΟΜΟΘΕΣΙΑ
    assert "ΠΟΙΝΙΚΗ ΝΟΜΟΘΕΣΙΑ" in _dkn("Ποινικός Κώδικας", "Τροποποιείται ο Ποινικός Κώδικας.")


def test_framework_law_maps_to_volume():
    assert "ΔΗΜΟΣΙΕΣ ΣΥΜΒΑΣΕΙΣ" in _dkn("", "κατά τον ν. 4412/2016 περί δημοσίων συμβάσεων")


def test_keyword_fallback_maps_to_volume():
    # no cited code, labor keywords -> ΕΡΓΑΤΙΚΗ ΝΟΜΟΘΕΣΙΑ
    assert "ΕΡΓΑΤΙΚΗ ΝΟΜΟΘΕΣΙΑ" in _dkn("", "Ο μισθός του εργαζομένου και η απόλυση.")


def test_corporate_and_insolvency_share_commercial_volume():
    corp = _dkn("", "ανώνυμη εταιρεία κατά τον ν. 4548/2018")
    ins = _dkn("", "πτωχευτικός κώδικας και αφερεγγυότητα")
    assert "ΕΜΠΟΡΙΚΗ ΝΟΜΟΘΕΣΙΑ" in corp
    assert "ΕΜΠΟΡΙΚΗ ΝΟΜΟΘΕΣΙΑ" in ins


def test_extra_volume_agriculture_no_legal_domain_counterpart():
    # 'γεωργικ' is a ΔΚΝ-only volume with no legal_domain label
    assert "ΓΕΩΡΓΙΚΗ ΝΟΜΟΘΕΣΙΑ" in _dkn("", "Ρυθμίσεις για τις γεωργικές εκμεταλλεύσεις.")


def test_extra_volume_shipping():
    assert "ΕΜΠΟΡΙΚΗ ΝΑΥΤΙΛΙΑ" in _dkn("", "νηολόγηση πλοίου και λιμενικές αρχές")


def test_extra_volume_ecclesiastical():
    assert "ΕΚΚΛΗΣΙΑΣΤΙΚΗ ΝΟΜΟΘΕΣΙΑ" in _dkn("", "Η Ιερά Σύνοδος και οι μητροπόλεις.")


def test_mapped_and_extra_can_coexist():
    # a tax provision that also concerns farming -> both volumes
    doms = _dkn("", "Φορολογία εισοδήματος των γεωργικών εκμεταλλεύσεων.")
    assert "ΦΟΡΟΛΟΓΙΚΗ ΝΟΜΟΘΕΣΙΑ" in doms
    assert "ΓΕΩΡΓΙΚΗ ΝΟΜΟΘΕΣΙΑ" in doms


def test_no_signal_leaves_empty():
    assert _dkn("", "Γενική διάταξη χωρίς σαφές αντικείμενο 123.") == []


def test_no_duplicate_volumes():
    # criminal + criminal keywords both present -> volume appears once
    doms = _dkn("Ποινικός Κώδικας", "Η ποινή και το έγκλημα κατά τον Ποινικό Κώδικα.")
    assert doms.count("ΠΟΙΝΙΚΗ ΝΟΜΟΘΕΣΙΑ") == 1


def test_every_emitted_volume_is_declared():
    samples = [
        ("Ποινικός Κώδικας", "ποινή"),
        ("", "ν. 4412/2016 δημόσιες συμβάσεις"),
        ("", "γεωργικές εκμεταλλεύσεις"),
        ("", "πλοίο και λιμένας"),
        ("", "Ιερά Σύνοδος"),
        ("", "δημόσια έργα και οδοποιία"),
        ("", "οργανισμοί τοπικής αυτοδιοίκησης και δημοτικό συμβούλιο"),
    ]
    for title, text in samples:
        for vol in _dkn(title, text):
            assert vol in DKN_VOLUMES, vol


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
