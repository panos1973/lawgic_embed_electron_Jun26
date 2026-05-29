"""Unit tests for segment.py (pure text, no external deps)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.segment import segment  # noqa: E402
from models import Law, TYPE_NOMOS  # noqa: E402


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
