"""Unit tests for the per-page OCR-routing decision (sidecar.pdf_extract).

These exercise `_page_needs_ocr` directly — a pure function, no PDF needed — so
they always run even where pdfplumber/reportlab are not installed. The guarantee
under test: a scanned/blank page anywhere in a long document is detected on its
own merits (page 43 of 100, or a handful scattered through 500), and the two
"needs OCR" cases are told apart so corrupt garble is blanked while a near-empty
image page keeps its few clean chars as a no-DI fallback.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sidecar.pdf_extract import _page_needs_ocr, _TEXT_CHAR_THRESHOLD  # noqa: E402
from pipeline.quality import score_text  # noqa: E402

# A real FEK body page is dense legal text — hundreds of chars of clean Greek.
CLEAN = ("Άρθρο 1 Σκοπός. Σκοπός του παρόντος νόμου είναι η ρύθμιση των θεμάτων "
         "που αφορούν στην οργάνωση και λειτουργία της δημόσιας διοίκησης, "
         "σύμφωνα με τις διατάξεις του Συντάγματος και της κείμενης νομοθεσίας.")

# What pdfplumber pulls off a scanned image page: a few chars of header noise.
SCANNED = "ΕΦΗΜΕΡΙΣ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ 12345"

# Enough characters survive, but they are garble (CID-keyed font, no ToUnicode).
GARBLED = " ".join(f"(cid:{n})" for n in range(120)) + " ΕΦΗΜΕΡΙΔΑ"


def test_clean_full_page_stays_on_text_layer():
    needs, corrupt, reason = _page_needs_ocr(CLEAN, score_text)
    assert needs is False and corrupt is False and reason == ""


def test_scanned_image_page_routed_but_not_corrupt():
    # The defining bug this fixes: a near-empty scanned page (few chars, no real
    # text layer) MUST be routed to OCR. It is not "corrupt", so its little clean
    # text is kept as a fallback for when DI is not configured.
    assert len(SCANNED) < _TEXT_CHAR_THRESHOLD
    needs, corrupt, reason = _page_needs_ocr(SCANNED, score_text)
    assert needs is True and corrupt is False
    assert "text layer" in reason


def test_blank_page_routed():
    for blank in ("", "   ", "\n\n  \n"):
        needs, corrupt, _ = _page_needs_ocr(blank, score_text)
        assert needs is True and corrupt is False


def test_short_but_clean_page_still_routed_kept_as_fallback():
    # A page below the text-layer threshold is routed even if its few chars are
    # clean (it is either a near-blank divider or a scanned page that leaked a
    # few real glyphs). is_corrupt=False => the clean text is preserved, so DI
    # being absent never loses it.
    short_clean = "Άρθρο 50. Έναρξη ισχύος."
    assert len(short_clean) < _TEXT_CHAR_THRESHOLD
    needs, corrupt, _ = _page_needs_ocr(short_clean, score_text)
    assert needs is True and corrupt is False


def test_garbled_full_page_routed_and_blanked():
    # Enough text to clear the length threshold, but it is garbage -> route to
    # OCR AND mark corrupt so the caller blanks it (DI's render replaces it).
    assert len(GARBLED) >= _TEXT_CHAR_THRESHOLD
    needs, corrupt, reason = _page_needs_ocr(GARBLED, score_text)
    assert needs is True and corrupt is True
    assert reason  # a human-readable reason from score_text


def test_no_scorer_only_length_gate():
    # In the sidecar's import-fallback path score_text is None: we can still
    # catch image/blank pages by length, we just cannot judge garble quality.
    assert _page_needs_ocr(SCANNED, None) == (True, False,
                                              "no usable text layer (image/blank page)")
    needs, corrupt, reason = _page_needs_ocr(CLEAN, None)
    assert needs is False and corrupt is False
    # garble has length, but with no scorer it cannot be flagged corrupt
    assert _page_needs_ocr(GARBLED, None)[0] is False


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
