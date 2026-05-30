"""Unit tests for pipeline/quality.py — text-quality scoring / OCR routing."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.quality import score_text, needs_ocr  # noqa: E402

GOOD = ("Άρθρο 1 Σκοπός. Σκοπός του παρόντος νόμου είναι η ρύθμιση των θεμάτων "
        "που αφορούν στην οργάνωση και λειτουργία της δημόσιας διοίκησης, "
        "σύμφωνα με τις διατάξεις του Συντάγματος και της κείμενης νομοθεσίας.")


def test_clean_greek_scores_high():
    q = score_text(GOOD)
    assert q.is_valid and q.score >= 80
    assert not needs_ocr(GOOD)


def test_cid_page_routed_to_ocr():
    cid = " ".join(f"(cid:{n})" for n in range(120)) + " ΕΦΗΜΕΡΙΔΑ ΚΥΒΕΡΝΗΣΕΩΣ"
    q = score_text(cid)
    assert not q.is_valid
    assert "cid" in q.reason.lower()
    assert needs_ocr(cid)


def test_question_mark_garble_routed():
    # ASCII-substitution failure: Greek glyphs render as '?' so almost no real
    # Greek survives — the hallmark of a broken text layer.
    garble = "????? ?? ??????? ?? ???????? " * 6
    assert needs_ocr(garble)


def test_too_short_is_invalid_but_distinct_reason():
    q = score_text("Άρθρο 2")
    assert not q.is_valid
    assert "short" in q.reason


def test_valid_latin_text_passes():
    # English/French treaty text is valid even with little Greek
    latin = ("Article III Meeting of the Parties. The Parties shall hold "
             "ordinary meetings at intervals of not more than three years, "
             "unless they decide otherwise, and shall review the implementation.")
    assert score_text(latin).is_valid


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
