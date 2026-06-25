"""Unit tests for the FEK masthead parser (pure-text, no external deps)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.masthead import parse_masthead  # noqa: E402
from pipeline.normalize import normalize_glyphs  # noqa: E402
from models import TYPE_NOMOS, TYPE_PD, TYPE_PNP, TYPE_ANAKOINOSI, TYPE_PYS  # noqa: E402


PYS = """ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ
ΤΗΣ ΕΛΛΗΝΙΚΗΣ ΔΗΜΟΚΡΑΤΙΑΣ
14 Φεβρουαρίου 2025 ΤΕΥΧΟΣ ΠΡΩΤΟ Αρ. Φύλλου 20
ΠΡΑΞΕΙΣ ΥΠΟΥΡΓΙΚΟΥ ΣΥΜΒΟΥΛΙΟΥ
Πράξη: 1 της 10.1.2025
ΤΟ ΥΠΟΥΡΓΙΚΟ ΣΥΜΒΟΥΛΙΟ
"""


def test_best_masthead_keeps_the_source_that_resolves_the_series():
    # two-column reconstruction can split the full-width masthead, dropping the series
    # ("…ΤΕΥΧΟΣ" with "ΔΕΥΤΕΡΟ Αρ. Φύλλου N" carried off elsewhere) — which collapses
    # identification for the whole FEK Β΄ doc. _best_masthead must keep the raw, intact
    # reading where the series survives, regardless of argument order.
    from pipeline.extract import _best_masthead
    mangled = "8 Ιανουαρίου 2025 ΤΕΥΧΟΣ\nΑΠΟΦΑΣΕΙΣ\nΑριθμ. ΥΠΕΝ/1/2\n"
    intact = "8 Ιανουαρίου 2025 ΤΕΥΧΟΣ ΔΕΥΤΕΡΟ Αρ. Φύλλου 4\nΑΠΟΦΑΣΕΙΣ\n"
    assert _best_masthead(mangled, intact)["fek_series"] == "Β"
    assert _best_masthead(intact, mangled)["fek_series"] == "Β"
    assert _best_masthead("", intact)["fek_series"] == "Β"          # empty source skipped


def test_praxi_ypourgikou_symvouliou_typed():
    r = parse_masthead(PYS)
    assert r["instrument_type"] == TYPE_PYS                       # Act of the Cabinet
    assert r["fek_series"] == "Α" and r["fek_number"] == "20" and r["year"] == 2025
    assert "instrument number not found" not in r["warnings"]     # numberless -> no warning


def test_pys_genitive_citation_does_not_mistype_a_law():
    # a ΝΟΜΟΣ that cites «της Πράξης Υπουργικού Συμβουλίου» in its body stays NOMOS:
    # the Π.Υ.Σ. pattern matches ΠΡΑΞΗ/ΠΡΑΞΕΙΣ but NOT the genitive ΠΡΑΞΗΣ, and the real
    # ΝΟΜΟΣ header sits earlier anyway.
    txt = ("ΝΟΜΟΣ ΥΠ' ΑΡΙΘΜ. 5090\nΠοινικός Κώδικας.\n"
           "Άρθρο 1\nΚατ' εφαρμογήν της Πράξης Υπουργικού Συμβουλίου 8/2011.\n")
    assert parse_masthead(txt)["instrument_type"] == TYPE_NOMOS


# A real FEK Α΄ announcement issue: a MFA notice that a ratified treaty entered force.
ANAKOINOSI = """ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ
ΤΗΣ ΕΛΛΗΝΙΚΗΣ ΔΗΜΟΚΡΑΤΙΑΣ
11 Μαρτίου 2026 ΤΕΥΧΟΣ ΠΡΩΤΟ Αρ. Φύλλου 36
ΑΝΑΚΟΙΝΩΣΕΙΣ
Αριθμ. Φ.0544/Μ.7675/ΑΣ 10817
Θέση σε ισχύ της Συμφωνίας ... που κυρώθηκαν με τον ν. 5074/2023 (Α΄ 205).
"""


def test_anakoinosi_notice_typed_from_standalone_header():
    r = parse_masthead(ANAKOINOSI)
    assert r["instrument_type"] == TYPE_ANAKOINOSI
    assert r["number"] is None                       # a notice carries no NUM/YEAR
    assert r["fek_series"] == "Α" and r["fek_number"] == "36" and r["year"] == 2026
    assert "instrument number not found" not in r["warnings"]   # numberless -> no warning


def test_anakoinosi_body_mention_does_not_mistype_a_law():
    # a real ΝΟΜΟΣ whose body merely mentions "ανακοινώσεις" stays NOMOS — the
    # ΑΝΑΚΟΙΝΩΣΕΙΣ pattern only matches a STANDALONE header line, and the ΝΟΜΟΣ header
    # sits earlier anyway.
    txt = ("ΝΟΜΟΣ ΥΠ' ΑΡΙΘΜ. 5090\nΠοινικός Κώδικας.\n"
           "Άρθρο 1\nΟι σχετικές ανακοινώσεις δημοσιεύονται στον τύπο.\n")
    assert parse_masthead(txt)["instrument_type"] == TYPE_NOMOS


def test_latin_homoglyph_masthead_identified_after_glyph_repair():
    """A real-FEK gotcha: the headline ΝΟΜΟΣ is often encoded as Latin 'NOMO' + Greek
    'Σ'. The all-Greek masthead regexes miss it raw (-> type=None -> review). extract.py
    now glyph-repairs the head before parse_masthead; this proves that closes the gap."""
    # N,O,M,O are Latin homoglyphs here; final Σ is Greek (so the token is "Greek-ish"
    # and the repair fires). ΥΠ/ΑΡΙΘΜ kept Greek.
    raw = ("ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ\n26 Μαρτίου 2024  ΤΕΥΧΟΣ ΠΡΩΤΟ  Αρ. Φύλλου 52\n"
           "NOMOΣ ΥΠ' ΑΡΙΘΜ. 5090\nΠοινικός Κώδικας.\n")
    assert parse_masthead(raw)["instrument_type"] is None           # raw homoglyph -> missed
    fixed = parse_masthead(normalize_glyphs(raw))
    assert fixed["instrument_type"] == TYPE_NOMOS                   # repaired -> identified
    assert fixed["number"] == 5090 and fixed["year"] == 2024


# A realistic ΝΟΜΟΣ cover, modern spelling, number inline.
NOMOS_INLINE = """ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ
ΤΗΣ ΕΛΛΗΝΙΚΗΣ ΔΗΜΟΚΡΑΤΙΑΣ
26 Μαρτίου 2024            ΤΕΥΧΟΣ ΠΡΩΤΟ            Αρ. Φύλλου 52

ΝΟΜΟΣ ΥΠ' ΑΡΙΘΜ. 5090
Ποινικός Κώδικας και άλλες επείγουσες διατάξεις.

Ο ΠΡΟΕΔΡΟΣ
ΤΗΣ ΕΛΛΗΝΙΚΗΣ ΔΗΜΟΚΡΑΤΙΑΣ

Άρθρο 1
Σκοπός
"""

# Older spelling ΕΦΗΜΕΡΙΣ, keraia apostrophe, ΑΡΙΘ. abbreviation, number inline.
NOMOS_VARIANT = """ΕΦΗΜΕΡΙΣ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ
ΤΗΣ ΕΛΛΗΝΙΚΗΣ ΔΗΜΟΚΡΑΤΙΑΣ
ΤΕΥΧΟΣ ΔΕΥΤΕΡΟ        Αριθμός Φύλλου 1151
7 Ιουλίου 2017

ΝΟΜΟΣ ΥΠ΄ ΑΡΙΘ. 4481
Συλλογική διαχείριση δικαιωμάτων.
"""

# Number on its own line after the header.
NOMOS_NUM_ON_NEXT_LINE = """ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ
1 Ιανουαρίου 2020   ΤΕΥΧΟΣ ΠΡΩΤΟ   Αρ. Φύλλου 3
ΝΟΜΟΣ ΥΠ' ΑΡΙΘΜ.
4659
Κάποιος τίτλος νόμου.
"""

PD = """ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ
15 Μαΐου 2021   ΤΕΥΧΟΣ ΠΡΩΤΟ   Αρ. Φύλλου 90

ΠΡΟΕΔΡΙΚΟ ΔΙΑΤΑΓΜΑ ΥΠ' ΑΡΙΘΜ. 80
Έλεγχος δαπανών.
"""

PNP = """ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ
14 Μαρτίου 2020   ΤΕΥΧΟΣ ΠΡΩΤΟ   Αρ. Φύλλου 64

ΠΡΑΞΗ ΝΟΜΟΘΕΤΙΚΟΥ ΠΕΡΙΕΧΟΜΕΝΟΥ
Κατεπείγοντα μέτρα.
"""


def test_nomos_inline():
    r = parse_masthead(NOMOS_INLINE)
    assert r["instrument_type"] == TYPE_NOMOS
    assert r["number"] == 5090
    assert r["year"] == 2024
    assert r["fek_series"] == "Α"
    assert r["fek_number"] == "52"
    assert r["fek_date"] == "2024-03-26"
    assert "Ποινικός Κώδικας" in r["title"]
    assert "Ο ΠΡΟΕΔΡΟΣ" not in r["title"]  # title stops before promulgation
    assert r["warnings"] == []


# Real ν.5082/2024 shape: dewrapped so title + promulgation + TOC share lines,
# and the president is female ("Η ΠΡΟΕΔΡΟΣ").
NOMOS_INLINE_PROMULGATION = """ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ
19 Ιανουαρίου 2024   ΤΕΥΧΟΣ ΠΡΩΤΟ   Αρ. Φύλλου 9
ΝΟΜΟΣ ΥΠ' ΑΡΙΘΜ. 5082
Ενίσχυση του Εθνικού Συστήματος Επαγγελματικής Εκπαίδευσης και Κατάρτισης και άλλες επείγουσες διατάξεις Η ΠΡΟΕΔΡΟΣ ΤΗΣ ΕΛΛΗΝΙΚΗΣ ΔΗΜΟΚΡΑΤΙΑΣ Εκδίδομε τον ακόλουθο νόμο που ψήφισε η Βουλή: ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ ΜΕΡΟΣ Α': ΓΕΝΙΚΕΣ ΔΙΑΤΑΞΕΙΣ
"""


def test_title_stops_at_inline_promulgation_and_toc():
    r = parse_masthead(NOMOS_INLINE_PROMULGATION)
    assert r["number"] == 5082
    assert r["title"] == ("Ενίσχυση του Εθνικού Συστήματος Επαγγελματικής "
                          "Εκπαίδευσης και Κατάρτισης και άλλες επείγουσες διατάξεις")
    assert "ΠΡΟΕΔΡΟΣ" not in r["title"]            # promulgation excluded
    assert "ΠΙΝΑΚΑΣ" not in r["title"]             # TOC excluded
    assert "Εκδίδομε" not in r["title"]


def test_additional_instrument_types():
    from models import (TYPE_AN, TYPE_ND, TYPE_KANAP, TYPE_APOF_DIOIK,
                        TYPE_KANVOULIS)
    cases = [
        ("ΤΕΥΧΟΣ ΠΡΩΤΟ Αρ. Φύλλου 9\nΑΝΑΓΚΑΣΤΙΚΟΣ ΝΟΜΟΣ ΥΠ' ΑΡΙΘΜ. 1846\nτ",
         TYPE_AN, 1846),
        ("ΤΕΥΧΟΣ ΠΡΩΤΟ Αρ. Φύλλου 9\nΝΟΜΟΘΕΤΙΚΟ ΔΙΑΤΑΓΜΑ ΥΠ' ΑΡΙΘΜ. 356\nτ",
         TYPE_ND, 356),
        ("ΤΕΥΧΟΣ ΠΡΩΤΟ Αρ. Φύλλου 9\nΚΑΝΟΝΙΣΜΟΣ ΤΗΣ ΒΟΥΛΗΣ\nτ", TYPE_KANVOULIS, None),
        ("ΤΕΥΧΟΣ ΔΕΥΤΕΡΟ Αρ. Φύλλου 9\nΚΑΝΟΝΙΣΤΙΚΗ ΑΠΟΦΑΣΗ\nτ", TYPE_KANAP, None),
        ("ΤΕΥΧΟΣ ΔΕΥΤΕΡΟ Αρ. Φύλλου 9\nΑΠΟΦΑΣΗ ΔΙΟΙΚΗΤΗ\nτ", TYPE_APOF_DIOIK, None),
    ]
    for text, exp_type, exp_num in cases:
        r = parse_masthead(text)
        assert r["instrument_type"] == exp_type, (exp_type, r["instrument_type"])
        assert r["number"] == exp_num
        # numberless types must NOT raise the missing-number warning
        if exp_num is None:
            assert "instrument number not found" not in r["warnings"]


def test_nomos_variant_spelling_and_keraia():
    r = parse_masthead(NOMOS_VARIANT)
    assert r["instrument_type"] == TYPE_NOMOS
    assert r["number"] == 4481
    assert r["year"] == 2017
    assert r["fek_series"] == "Β"
    assert r["fek_number"] == "1151"
    assert r["fek_date"] == "2017-07-07"


def test_number_on_next_line():
    r = parse_masthead(NOMOS_NUM_ON_NEXT_LINE)
    assert r["instrument_type"] == TYPE_NOMOS
    assert r["number"] == 4659
    assert r["year"] == 2020


def test_presidential_decree():
    r = parse_masthead(PD)
    assert r["instrument_type"] == TYPE_PD
    assert r["number"] == 80
    assert r["year"] == 2021
    assert r["fek_series"] == "Α"


def test_pnp_has_no_number_no_warning_for_number():
    r = parse_masthead(PNP)
    assert r["instrument_type"] == TYPE_PNP
    assert r["number"] is None
    assert r["year"] == 2020
    assert "instrument number not found" not in r["warnings"]


# A ratification law whose TITLE cites a foreign "Ψήφισμα" (e.g. ν.5011/2023
# ratifying ACCOBAMS amendments adopted "με το Ψήφισμα Α/4.1"). The bare ΨΗΦΙΣΜΑ
# keyword must NOT outrank the anchored ΝΟΜΟΣ header that sits above it.
NOMOS_RATIFICATION_CITING_PSIFISMA = """ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ
17 Ιανουαρίου 2023   ΤΕΥΧΟΣ ΠΡΩΤΟ   Αρ. Φύλλου 9
ΝΟΜΟΣ ΥΠ' ΑΡΙΘΜ. 5011
Κύρωση της Συμφωνίας για τη διατήρηση των κητωδών και των τροποποιήσεων που
υιοθετήθηκαν με το Ψήφισμα Α/4.1 της 12ης Νοεμβρίου 2010.
Η ΠΡΟΕΔΡΟΣ ΤΗΣ ΕΛΛΗΝΙΚΗΣ ΔΗΜΟΚΡΑΤΙΑΣ
"""


def test_nomos_ratification_not_misread_as_psifisma():
    # regression: earliest-position type detection — the numbered ΝΟΜΟΣ header wins
    # over a "Ψήφισμα" cited later in the title (which previously forced PSIFISMA +
    # a missing number, routing the law to review).
    r = parse_masthead(NOMOS_RATIFICATION_CITING_PSIFISMA)
    assert r["instrument_type"] == TYPE_NOMOS
    assert r["number"] == 5011
    assert r["year"] == 2023
    assert r["fek_series"] == "Α"
    assert "Κύρωση" in r["title"]          # real title recovered
    assert "ΠΡΟΕΔΡΟΣ" not in r["title"]    # promulgation still excluded


def test_empty_text_is_safe():
    r = parse_masthead("")
    assert r["instrument_type"] is None
    assert r["number"] is None
    assert "instrument_type not found in masthead" in r["warnings"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
