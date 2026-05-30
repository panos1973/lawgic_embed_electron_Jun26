"""Unit tests for pipeline/multiact.py + decision id helpers (pure text)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.multiact import split_acts  # noqa: E402
from models import (make_decision_id, make_decision_key, TYPE_NOMOS, TYPE_YA,  # noqa: E402
                    TYPE_KYA, TYPE_APOF_DIOIK, TYPE_APOF_PERIF, TYPE_APOF_NPDD,
                    TYPE_KANAP)

MULTI = """ΠΕΡΙΕΧΟΜΕΝΑ
ΑΠΟΦΑΣΕΙΣ
1 Πρώτη απόφαση.
2 Δεύτερη απόφαση.
3 Τρίτη απόφαση.

Αριθμ. 5332 (1)
Τροποποίηση της Πράξης Προσαρμογής.
Ο ΓΡΑΜΜΑΤΕΑΣ ΑΠΟΚΕΝΤΡΩΜΕΝΗΣ ΔΙΟΙΚΗΣΗΣ
Έχοντας υπόψη: το άρθρο 1 του ν. 4600/2019 αποφασίζει.

Αριθμ. 27924 (2)
Καθιέρωση υπερωριακής απασχόλησης.
Ο ΠΕΡΙΦΕΡΕΙΑΡΧΗΣ
Έχοντας υπόψη: τις διατάξεις αποφασίζει.

Αριθμ. 10511 (3)
Ίδρυση ΠΜΣ.
Η ΣΥΓΚΛΗΤΟΣ
Έχοντας υπόψη αποφασίζει.
"""

SINGLE = """ΑΠΟΦΑΣΕΙΣ
Αριθμ. Φ.1413/ΑΣ6519
Απόφαση 2700 του Συμβουλίου Ασφαλείας.
Ο ΥΠΟΥΡΓΟΣ ΕΞΩΤΕΡΙΚΩΝ
Έχοντας υπόψη αποφασίζει.
"""

KYA = """ΑΠΟΦΑΣΕΙΣ
Αριθμ. 7066
Ειδικό πρόγραμμα επιχορήγησης.
ΟΙ ΥΠΟΥΡΓΟΙ ΕΘΝΙΚΗΣ ΟΙΚΟΝΟΜΙΑΣ ΚΑΙ ΟΙΚΟΝΟΜΙΚΩΝ
Έχοντας υπόψη αποφασίζουν.
"""


def test_primary_law_passthrough():
    acts = split_acts("ΝΟΜΟΣ ΥΠ' ΑΡΙΘΜ. 5090 ... body",
                      {"instrument_type": TYPE_NOMOS, "title": "Τίτλος"})
    assert len(acts) == 1
    assert acts[0].is_decision is False
    assert acts[0].instrument_type == TYPE_NOMOS


def test_multi_act_split_and_types():
    acts = split_acts(MULTI, {"instrument_type": None, "fek_series": "Β",
                              "fek_number": "913", "year": 2025})
    assert len(acts) == 3
    assert [a.item for a in acts] == [1, 2, 3]
    assert [a.instrument_type for a in acts] == [
        TYPE_APOF_DIOIK, TYPE_APOF_PERIF, TYPE_APOF_NPDD]
    assert all(a.is_decision for a in acts)
    assert acts[0].number == "5332"


def test_single_act_no_item_suffix():
    acts = split_acts(SINGLE, {"instrument_type": None, "fek_series": "Α",
                               "fek_number": "18", "year": 2024})
    assert len(acts) == 1
    assert acts[0].item is None            # single act -> no ΠΕΡΙΕΧΟΜΕΝΑ index
    assert acts[0].instrument_type == TYPE_YA
    assert acts[0].number == "Φ.1413/ΑΣ6519"


def test_kya_plural_ministers():
    acts = split_acts(KYA, {"instrument_type": None, "fek_series": "Β",
                            "fek_number": "1269", "year": 2025})
    assert len(acts) == 1
    assert acts[0].instrument_type == TYPE_KYA


def test_indexed_unclassified_kept_as_generic():
    txt = ("ΑΠΟΦΑΣΕΙΣ\nΑριθμ. 474 (5)\nΚαθορισμός ωρών.\n"
           "Κάποιο άγνωστο όργανο.\nΈχοντας υπόψη αποφασίζει.\n")
    acts = split_acts(txt, {"instrument_type": None, "fek_series": "Β",
                            "fek_number": "913", "year": 2025})
    assert len(acts) == 1
    assert acts[0].item == 5
    assert acts[0].instrument_type == TYPE_KANAP    # indexed but unclassified


def test_stray_arithm_without_index_or_issuer_skipped():
    acts = split_acts("κείμενο\nΑριθμ. 123\nχωρίς εκδότη ή δείκτη\n",
                      {"instrument_type": None, "fek_series": "Β",
                       "fek_number": "1", "year": 2025})
    assert acts == []


def test_decision_id_helpers():
    assert make_decision_id("Β", "913", 2025, 1) == "Β΄913/2025#1"
    assert make_decision_id("Β", "734", 2025, None) == "Β΄734/2025"
    assert make_decision_key("Β", "913", 2025, 1) == "Β913/2025#1"


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
