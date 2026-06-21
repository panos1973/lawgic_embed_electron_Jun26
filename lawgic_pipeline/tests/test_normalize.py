"""Unit tests for pipeline/normalize.py — built from real FEK glyph quirks."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.normalize import (normalize_glyphs, strip_furniture,  # noqa: E402
                                cid_ratio)
from normalize import collapse_doubled_glyphs, normalize_display  # noqa: E402


def test_collapse_doubled_glyph_headings():
    # bold/double-struck headings get every glyph read twice -> collapse whole tokens
    assert collapse_doubled_glyphs("ΔΔΙΙΚΚΑΑΙΙΟΟΛΛΟΟΓΓΗΗΤΤΙΙΚΚΑΑ") == "ΔΙΚΑΙΟΛΟΓΗΤΙΚΑ"
    assert collapse_doubled_glyphs("ΟΟΡΡΟΟΙΙ") == "ΟΡΟΙ"
    assert collapse_doubled_glyphs("ΜΜ..ΔΔ..ΕΕ..") == "Μ.Δ.Ε."
    # real Greek double letters (σσ/μμ/νν/γγ) and numbers are left intact
    assert collapse_doubled_glyphs("θάλασσα γράμμα Άννα συγγραφή") == "θάλασσα γράμμα Άννα συγγραφή"
    assert collapse_doubled_glyphs("1122 200200") == "1122 200200"
    # integrated through normalize_display
    assert normalize_display(
        "ΔΔΙΙΚΚΑΑΙΙΟΟΛΛΟΟΓΓΗΗΤΤΙΙΚΚΑΑ ΥΥΠΠΟΟΨΨΗΗΦΦΙΙΟΟΤΤΗΗΤΤΑΑΣΣ"
    ) == "ΔΙΚΑΙΟΛΟΓΗΤΙΚΑ ΥΠΟΨΗΦΙΟΤΗΤΑΣ"


def test_collapse_doubled_glyph_with_attached_punctuation():
    # a doubled word that carries trailing/leading punctuation is odd-length as a
    # whole token, so peel the punctuation, collapse the core, re-attach.
    assert collapse_doubled_glyphs("χχααρραακκττήήρρεεςς,") == "χαρακτήρες,"
    assert collapse_doubled_glyphs("«ΟΟΡΡΟΟΙΙ»") == "«ΟΡΟΙ»"
    assert collapse_doubled_glyphs("((ττοουυ))") == "(του)"
    assert collapse_doubled_glyphs("ΟΟΡΡΟΟΙΙ·") == "ΟΡΟΙ·"
    # when the quotes are themselves double-struck the whole token is fully paired,
    # so the whole-token check collapses everything in one pass
    assert collapse_doubled_glyphs("««ΟΟΡΡΟΟΙΙ»»") == "«ΟΡΟΙ»"
    # the dot is NOT a peelable mark — abbreviations stay intact via whole-token check
    assert collapse_doubled_glyphs("ΜΜ..ΔΔ..ΕΕ..") == "Μ.Δ.Ε."
    # words that merely END in a real double + punctuation are left alone
    assert collapse_doubled_glyphs("γράμμα, θάλασσα.") == "γράμμα, θάλασσα."


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


def test_body_mention_of_printing_house_not_truncated():
    """A provision BODY may name the National Printing House or 'public service' in
    running (title/lower-case) prose; that must NOT trigger the all-caps printing-house
    trailer and delete the rest of the law. Regression: a loose IGNORECASE anchor once
    cut ν.4368/2016 at an art-8 body mention, losing 84% of the text (arts 8→101)."""
    body = ("Άρθρο 8 Συμβάσεις\nΟι συμβάσεις που έχουν συναφθεί από το Εθνικό "
            "Τυπογραφείο θεωρούνται έγκυρες.\n"
            "Άρθρο 90 Τελικές διατάξεις\nΗ εξυπηρέτηση κοινού στην Καποδιστρίου 34 "
            "συνεχίζεται.\nΤέλος.")
    out = strip_furniture(body)
    assert "Άρθρο 90" in out and "Τέλος" in out       # nothing truncated
    assert "Εθνικό Τυπογραφείο" in out                # body mention preserved


def test_real_allcaps_printing_house_footer_stripped():
    """The genuine footer is set in capitals (ΑΠΟ ΤΟ ΕΘΝΙΚΟ ΤΥΠΟΓΡΑΦΕΙΟ / ΚΑΠΟΔΙΣΤΡΙΟΥ
    34) and must still be cut from its anchor to EOF."""
    body = ("Άρθρο 1 Σκοπός.\nΚείμενο της διάταξης.\n"
            "ΑΠΟ ΤΟ ΕΘΝΙΚΟ ΤΥΠΟΓΡΑΦΕΙΟ\nΚΑΠΟΔΙΣΤΡΙΟΥ 34 * ΑΘΗΝΑ 104 32 * ΤΗΛ. 210 5279000")
    out = strip_furniture(body)
    assert "Άρθρο 1 Σκοπός." in out
    assert "ΤΥΠΟΓΡΑΦΕΙΟ" not in out and "ΚΑΠΟΔΙΣΤΡΙΟΥ" not in out



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


def test_strip_promulgation_and_signature_trailer():
    """The closing promulgation order, the ministers' signatures, the Μεγάλη Σφραγίδα
    attestation and the Εθνικό Τυπογραφείο footer must be stripped — otherwise they
    pollute the final article ('Έναρξη ισχύος') with names/furniture. The START-of-law
    enacting formula ('Εκδίδομε…') must be kept (it contains no 'Παραγγέλλομε')."""
    body = ("Ο ΠΡΟΕΔΡΟΣ ΤΗΣ ΕΛΛΗΝΙΚΗΣ ΔΗΜΟΚΡΑΤΙΑΣ Εκδίδομε τον ακόλουθο νόμο.\n"
            "Άρθρο 40 Έναρξη ισχύος Η ισχύς αρχίζει από τη δημοσίευση.\n"
            "ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ 255\n"
            "Παραγγέλλομε τη δημοσίευση του παρόντος στην Εφημερίδα της Κυβερνήσεως "
            "και την εκτέλεσή του ως νόμου του Κράτους.\n"
            "Αθήνα, 14 Φεβρουαρίου 2024 Η Πρόεδρος της Δημοκρατίας ΚΑΤΕΡΙΝΑ ΣΑΚΕΛΛΑΡΟΠΟΥΛΟΥ\n"
            "Οι Υπουργοί ΚΩΝΣΤΑΝΤΙΝΟΣ ΧΑΤΖΗΔΑΚΗΣ\n"
            "Θεωρήθηκε και τέθηκε η Μεγάλη Σφραγίδα του Κράτους.\n"
            "<figure> ET </figure> # ΕΘΝΙΚΟ ΤΥΠΟΓΡΑΦΕΙΟ")
    out = strip_furniture(body)
    assert "Εκδίδομε τον ακόλουθο νόμο" in out            # enacting formula kept
    assert "Η ισχύς αρχίζει από τη δημοσίευση." in out     # the real article text kept
    assert "Παραγγέλλομε" not in out
    assert "ΣΑΚΕΛΛΑΡΟΠΟΥΛΟΥ" not in out and "ΧΑΤΖΗΔΑΚΗΣ" not in out
    assert "Σφραγίδα" not in out and "ΤΥΠΟΓΡΑΦΕΙΟ" not in out


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
