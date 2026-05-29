"""Unit tests for the FEK masthead parser (pure-text, no external deps)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.masthead import parse_masthead  # noqa: E402
from models import TYPE_NOMOS, TYPE_PD, TYPE_PNP  # noqa: E402


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
