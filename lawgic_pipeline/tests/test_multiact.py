"""Unit tests for pipeline/multiact.py + decision id helpers (pure text)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.multiact import split_acts  # noqa: E402
from models import (make_decision_id, make_decision_key, TYPE_NOMOS, TYPE_YA,  # noqa: E402
                    TYPE_KYA, TYPE_APOF_DIOIK, TYPE_APOF_PERIF, TYPE_APOF_NPDD,
                    TYPE_KANAP, TYPE_PNP, make_dated_instrument_id,
                    make_dated_instrument_key, make_provision_id)


def test_pnp_numberless_instrument_gets_a_dated_id():
    """A Π.Ν.Π. (Act of Legislative Content) has NO instrument number — it is cited by
    gazette coordinates. Its id must be built from the FEK reference, not dropped to
    review (the 'act not identified — type=PNP, number=None' bug)."""
    iid = make_dated_instrument_id(TYPE_PNP, "Α", "132", 2023)
    ikey = make_dated_instrument_key(TYPE_PNP, "Α", "132", 2023)
    assert iid == "Π.Ν.Π. Α΄132/2023"
    assert ikey == "PNPΑ132/2023"
    # articles namespace cleanly under it (spelled ordinals, as Π.Ν.Π. use)
    assert make_provision_id(iid, "πρώτο") == "Π.Ν.Π. Α΄132/2023#αρ.πρώτο"


# regression: a spelled "Αριθμός <word>" heading (NO digit) must NOT be read as an
# act header — it once matched, truncated the real act, and dropped everything after
# it (Β΄734/2025 lost Άρθρα 7-20 + tables: 6 chunks instead of 25).
ARITHMOS_WORD = """ΠΕΡΙΕΧΟΜΕΝΑ
ΑΠΟΦΑΣΕΙΣ
Αριθμ. 10511
Ίδρυση Π.Μ.Σ. «Δοκιμή».
Η ΣΥΓΚΛΗΤΟΣ
Έχοντας υπόψη τις διατάξεις αποφασίζει:
Άρθρο 1
Γενικά.
Άρθρο 7
Αριθμός Εισακτέων
Ο αριθμός εισακτέων ορίζεται σε δεκαπέντε (15).
Άρθρο 8
Λοιπές διατάξεις.
"""


def test_arithmos_word_heading_is_not_an_act_header():
    mh = {"instrument_type": None, "fek_series": "Β", "fek_number": "734", "year": 2025}
    acts = split_acts(ARITHMOS_WORD, mh)
    assert len(acts) == 1                              # NOT split at "Αριθμός Εισακτέων"
    a = acts[0]
    assert a.instrument_type == TYPE_APOF_NPDD         # Η ΣΥΓΚΛΗΤΟΣ
    # nothing after the false header is dropped — Άρθρο 8 still inside the act text
    assert "Αριθμός Εισακτέων" in a.text and "Άρθρο 8" in a.text

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


def test_unclassified_decision_gazette_single_act_fallback():
    # A real decision gazette (ΑΠΟΦΑΣΕΙΣ section, valid coords) whose lone act header
    # has no recognised issuer and no "(n)" index — e.g. a regulatory Authority whose
    # phrasing isn't listed, or an issuer beyond the scan window — is still ONE
    # instrument, not review. (FEK B 302pages -> Β΄4193/2025 'Αριθμ. Ε-142/2025'.)
    txt = ("ΑΠΟΦΑΣΕΙΣ\nΑριθμ. Ε-142/2025\n"
           "Τροποποίηση του Κανονισμού Λειτουργίας της Αγοράς Επόμενης Ημέρας.\n"
           "Άρθρο 1\nΑντικείμενο\nΟ παρών Κανονισμός ρυθμίζει τη λειτουργία της αγοράς.\n")
    acts = split_acts(txt, {"instrument_type": None, "fek_series": "Β",
                            "fek_number": "4193", "year": 2025})
    assert len(acts) == 1
    assert acts[0].is_decision is True
    assert acts[0].instrument_type == TYPE_KANAP   # generic decision when unclassified
    assert acts[0].text == txt                     # whole gazette is the one instrument


def test_stray_arithm_without_index_or_issuer_skipped():
    # No ΑΠΟΦΑΣΕΙΣ/ΠΕΡΙΕΧΟΜΕΝΑ gazette structure -> a bare "Αριθμ." in loose text is a
    # stray reference, not an act: stays unsplit (review), even with valid coords.
    acts = split_acts("κείμενο\nΑριθμ. 123\nχωρίς εκδότη ή δείκτη\n",
                      {"instrument_type": None, "fek_series": "Β",
                       "fek_number": "1", "year": 2025})
    assert acts == []


def test_untyped_fek_a_emits_one_act_for_llm_fallback():
    # A FEK Α΄ primary act whose masthead type the regexes couldn't read must NOT be
    # dropped to review unseen: split_acts emits ONE untyped, non-decision act so the
    # orchestrator's LLM identification fallback gets a chance to name it.
    txt = ("Κείμενο νόμου χωρίς αναγνωρίσιμη κεφαλίδα τύπου.\n"
           "Άρθρο 1\nΑντικείμενο\nΟι διατάξεις ισχύουν.\n")
    acts = split_acts(txt, {"instrument_type": None, "fek_series": "Α",
                            "fek_number": "35", "year": 2026})
    assert len(acts) == 1
    assert acts[0].instrument_type is None        # unknown -> LLM fallback will type it
    assert acts[0].is_decision is False
    assert acts[0].text == txt
    # but an untyped FEK Β΄ with no act structure still goes to review (stray ref)
    assert split_acts(txt, {"instrument_type": None, "fek_series": "Β",
                            "fek_number": "1", "year": 2026}) == []


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
