"""Unit tests for pipeline/normalize.py — built from real FEK glyph quirks."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.normalize import (normalize_glyphs, strip_furniture,  # noqa: E402
                                cid_ratio)


def test_latin_homoglyph_nomos():
    # real masthead: "NOMO" is Latin, final Σ is Greek
    assert normalize_glyphs("NOMOΣ ΥΠ' ΑΡΙΘΜ. 5086").startswith("ΝΟΜΟΣ")


def test_increment_delta():
    assert normalize_glyphs("ΕΦΗΜΕΡΙ∆Α") == "ΕΦΗΜΕΡΙΔΑ"
    assert "∆" not in normalize_glyphs("∆ΗΜΟΚΡΑΤΙΑΣ")


def test_all_latin_token_untouched():
    # must NOT half-convert Latin-only tokens
    for tok in ["COVID-19", "www.et.gr", "Master", "webmaster.et@et.gr", "MSc"]:
        assert normalize_glyphs(tok) == tok


def test_mixed_glyph_roman_numeral_preserved():
    # "ΧΙV": Greek Χ Ι + Latin V (no Greek V) -> V stays
    assert normalize_glyphs("ΧΙV") == "ΧΙV"


def test_idempotent():
    s = "NOMOΣ ΕΦΗΜΕΡΙ∆Α COVID-19"
    once = normalize_glyphs(s)
    assert normalize_glyphs(once) == once


def test_cid_strip_and_ratio():
    s = "(cid:14)(cid:235) ΠΡΟΕΔΡΙΚΟ"
    assert "(cid:" not in normalize_glyphs(s)
    assert cid_ratio("(cid:1) (cid:2) word") > 0.6


def test_strip_running_header():
    body = ("240 ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ Τεύχος A' 23/14.02.2024\n"
            "2. Ο Διοικητής είναι πρόσωπο εγνωσμένου κύρους.")
    out = strip_furniture(body)
    assert "ΚΥΒΕΡΝΗΣΕΩΣ Τεύχος" not in out
    assert "Ο Διοικητής" in out


def test_strip_barcode_and_trailer():
    body = ("Άρθρο 1 Σκοπός.\n*01000231402240020*\n"
            "ΕΞΥΠΗΡΕΤΗΣΗ ΚΟΙΝΟΥ\nΠωλήσεις - Συνδρομές: τηλ. 210 5279178")
    out = strip_furniture(body)
    assert "Άρθρο 1 Σκοπός." in out
    assert "ΕΞΥΠΗΡΕΤΗΣΗ" not in out and "*0100" not in out



def test_strip_header_fragments_from_dewrapped_columns():
    """Two-column dewrapping splits the running header into fragments the full
    pattern misses: a lone issue stamp, the masthead word alone, and a page-number
    +masthead. All must be stripped; real body text kept."""
    body = ("ση των άρθρων 24Α και 33Α.\n"
            "ΚΥΒΕΡΝΗΣΕΩΣ Τεύχος A' 9/19.01.2024\n"
            "γ) Προστίθεται παράγραφος.\n"
            "68 ΕΦΗΜΕΡΙΔΑ\n"
            "Άρθρο 4 Σκοπός.\n"
            "ΚΥΒΕΡΝΗΣΕΩΣ 69\n"
            "Τέλος.")
    out = strip_furniture(body)
    assert "Τεύχος" not in out
    assert "68 ΕΦΗΜΕΡΙΔΑ" not in out and "ΚΥΒΕΡΝΗΣΕΩΣ 69" not in out
    assert "Προστίθεται παράγραφος" in out and "Άρθρο 4 Σκοπός" in out and "Τέλος" in out



def test_strip_azure_page_comments():
    body = ("3. Η ισχύς αρχίζει.\n<!-- PageNumber=\"90\" -->\n"
            "<!-- PageHeader=\"ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ\" -->\nΆρθρο 42 Επόμενο.")
    out = strip_furniture(body)
    assert "<!--" not in out and "PageNumber" not in out
    assert "Η ισχύς αρχίζει." in out and "Άρθρο 42 Επόμενο." in out


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
