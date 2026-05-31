"""Unit tests for segment.py (pure text, no external deps)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.segment import segment  # noqa: E402
from models import Law, TYPE_NOMOS  # noqa: E402


def test_spelled_ordinal_articles():
    law = Law(instrument_id="Π.Ν.Π.1/2023", instrument_key="x",
              instrument_type=TYPE_NOMOS)
    txt = "Άρθρο πρώτο\nΠρώτη ρύθμιση.\nΆρθρο δεύτερο\nΈναρξη ισχύος.\n"
    law = segment(txt, law)
    assert [p.article_no for p in law.provisions] == ["πρώτο", "δεύτερο"]


def test_roman_numeral_articles():
    law = Law(instrument_id="ν.5011/2023", instrument_key="x",
              instrument_type=TYPE_NOMOS)
    # post-normalization XII->ΧΙΙ, XIII->ΧΙΙΙ (Greek homoglyphs)
    txt = "Άρθρο ΧΙΙ\nΕπίλυση διαφορών.\nΆρθρο ΧΙΙΙ\nΥπογραφή.\n"
    law = segment(txt, law)
    assert [p.article_no for p in law.provisions] == ["ΧΙΙ", "ΧΙΙΙ"]


def test_correspondence_table_artifacts_dropped():
    # codifying π.δ. end with bare "Άρθρο N" pairs (empty body) and repeats of
    # numbers already emitted — these must not become provisions.
    law = Law(instrument_id="π.δ.62/2025", instrument_key="x",
              instrument_type=TYPE_NOMOS)
    txt = ("Άρθρο 1\nΣκοπός του Κώδικα είναι η ρύθμιση.\n"
           "Άρθρο 2\nΟρισμοί κατά την έννοια του παρόντος.\n"
           # end correspondence table: bare pairs, incl. a repeat of Άρθρο 1
           "Άρθρο 1\nΆρθρο 148\nΆρθρο 2\nΆρθρο 149\n")
    law = segment(txt, law)
    assert [p.article_no for p in law.provisions] == ["1", "2"]   # table dropped


def _law():
    return Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
               instrument_type=TYPE_NOMOS)


NESTED = """ΝΟΜΟΣ ΥΠ' ΑΡΙΘΜ. 5090
Κάποιος τίτλος.

Ο ΠΡΟΕΔΡΟΣ ΤΗΣ ΕΛΛΗΝΙΚΗΣ ΔΗΜΟΚΡΑΤΙΑΣ

ΜΕΡΟΣ ΠΡΩΤΟ
ΓΕΝΙΚΕΣ ΔΙΑΤΑΞΕΙΣ

ΚΕΦΑΛΑΙΟ Α
ΣΚΟΠΟΣ ΚΑΙ ΟΡΙΣΜΟΙ

Άρθρο 1
Σκοπός
Σκοπός του παρόντος νόμου είναι η ρύθμιση.

Άρθρο 2
Ορισμοί
Για την εφαρμογή ισχύουν οι ακόλουθοι ορισμοί.

ΚΕΦΑΛΑΙΟ Β
ΟΥΣΙΑΣΤΙΚΕΣ ΔΙΑΤΑΞΕΙΣ

Άρθρο 3
Πεδίο εφαρμογής
Ο παρών νόμος εφαρμόζεται σε όλους.

ΜΕΡΟΣ ΔΕΥΤΕΡΟ
ΤΕΛΙΚΕΣ ΔΙΑΤΑΞΕΙΣ

Άρθρο 4
Έναρξη ισχύος
Η ισχύς αρχίζει από τη δημοσίευση.

ΠΑΡΑΡΤΗΜΑ Ι
Πίνακας αντιστοιχίσεων
γραμμή πίνακα
"""


def test_article_count_and_ids():
    law = segment(NESTED, _law())
    arts = [p for p in law.provisions if p.chunk_type == "article"]
    assert [p.article_no for p in arts] == ["1", "2", "3", "4"]
    assert arts[0].canonical_id == "ν.5090/2024#αρ.1"


def test_titles_captured():
    law = segment(NESTED, _law())
    by_no = {p.article_no: p for p in law.provisions if p.chunk_type == "article"}
    assert by_no["1"].article_title == "Σκοπός"
    assert by_no["4"].article_title == "Έναρξη ισχύος"


def test_hierarchy_context():
    law = segment(NESTED, _law())
    by_no = {p.article_no: p for p in law.provisions if p.chunk_type == "article"}
    # article 1: ΜΕΡΟΣ ΠΡΩΤΟ / ΚΕΦΑΛΑΙΟ Α
    assert by_no["1"].part == "ΠΡΩΤΟ"
    assert by_no["1"].chapter == "Α"
    assert by_no["1"].hierarchy_path == \
        "ν.5090/2024 > ΜΕΡΟΣ ΠΡΩΤΟ > ΚΕΦΑΛΑΙΟ Α > Άρθρο 1"
    # article 3: chapter advanced to Β, still ΜΕΡΟΣ ΠΡΩΤΟ
    assert by_no["3"].chapter == "Β"
    assert by_no["3"].part == "ΠΡΩΤΟ"
    # article 4: ΜΕΡΟΣ ΔΕΥΤΕΡΟ resets chapter
    assert by_no["4"].part == "ΔΕΥΤΕΡΟ"
    assert by_no["4"].chapter == ""
    assert by_no["4"].hierarchy_path == \
        "ν.5090/2024 > ΜΕΡΟΣ ΔΕΥΤΕΡΟ > Άρθρο 4"


def test_annex_detected():
    law = segment(NESTED, _law())
    annexes = [p for p in law.provisions if p.chunk_type == "annex"]
    assert len(annexes) == 1
    assert annexes[0].canonical_id == "ν.5090/2024#παραρτ.Ι"
    assert "Πίνακας" in annexes[0].article_title


def test_masthead_not_emitted():
    law = segment(NESTED, _law())
    # nothing before Άρθρο 1 (masthead / promulgation) becomes a provision
    assert all("ΠΡΟΕΔΡΟΣ" not in p.text_in_force.split("\n")[0]
               for p in law.provisions)


def test_article_only_text_still_works():
    simple = "Άρθρο 1\nΜόνος\nΚείμενο.\n\nΆρθρο 2\nΔεύτερο\nΚι άλλο."
    law = segment(simple, _law())
    arts = [p for p in law.provisions if p.chunk_type == "article"]
    assert len(arts) == 2
    assert arts[0].part == "" and arts[0].chapter == ""
    assert arts[0].hierarchy_path == "ν.5090/2024 > Άρθρο 1"


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


def test_inserted_articles_in_guillemets_not_emitted_as_own():
    """Articles quoted inside « » (inserted into another law) must NOT become
    articles of THIS law, and must not fragment the host article."""
    law = Law(instrument_id="ν.5082/2024", instrument_key="N5082/2024",
              instrument_type=TYPE_NOMOS)
    txt = (
        "Άρθρο 13\n"
        "Κέντρα - Προσθήκη Κεφαλαίου ΣΤ1 και άρθρων 40Α έως 40ΙΑ στον ν. 4763/2020\n"
        "Μετά το άρθρο 40 του ν. 4763/2020 προστίθενται άρθρα 40Α έως 40ΙΑ ως εξής:\n"
        "«ΚΕΦΑΛΑΙΟ ΣΤ1\n"
        "Άρθρο 40Α\nΑποστολή.\n"
        "Άρθρο 40Β\nΠροϋποθέσεις.»\n\n"
        "Άρθρο 14\nΕπόμενο\nΚείμενο."
    )
    law = segment(txt, law)
    nums = [p.article_no for p in law.provisions]
    assert nums == ["13", "14"]                      # only real articles
    assert "40Α" not in nums and "40Β" not in nums   # inserted ones masked
    a13 = next(p for p in law.provisions if p.article_no == "13")
    assert "Άρθρο 40Α" in a13.text_in_force          # host article stays whole


def test_unclosed_guillemet_masks_to_end():
    law = Law(instrument_id="ν.1/2024", instrument_key="x",
              instrument_type=TYPE_NOMOS)
    txt = "Άρθρο 1\nΕισαγωγή.\nπροστίθεται ως εξής:\n«Άρθρο 5\nΞένο.\nΆρθρο 6\nΚι άλλο."
    law = segment(txt, law)
    assert [p.article_no for p in law.provisions] == ["1"]   # 5,6 stay masked
