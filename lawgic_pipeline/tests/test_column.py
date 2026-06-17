"""Unit tests for the sidecar's band-aware column reconstruction (pure logic, no
pdfplumber). Guards the FEK-decision mixed-layout fix: a two-column block followed
by full-width single-column text must NOT be read line-by-line across the gutter,
while a clean two-column page and a single-column page keep their existing output.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# sidecar.pdf_extract imports pdfplumber lazily inside detect(), so these pure
# helpers import without it.
from sidecar.pdf_extract import _rows, _spans_gutter, _banded_text  # noqa: E402

G = 300.0


def _w(text, x0, x1, top):
    return {"text": text, "x0": x0, "x1": x1, "top": top}


def test_spans_gutter_distinguishes_fullwidth_from_two_column():
    # a word straddling the centre => full-width (single-column) row
    assert _spans_gutter([_w("ΚΥΒΕΡΝΗΣΕΩΣ", 230, 430, 10)], G) is True
    # left word + right word with a clear gutter gap => two-column row
    assert _spans_gutter([_w("αριστερά", 50, 250, 40),
                          _w("δεξιά", 330, 520, 40)], G) is False


def test_banded_text_reconstructs_twocolumn_block_then_fullwidth():
    """masthead (full-width) -> two-column block -> full-width body. The two-column
    block is emitted whole-left-then-whole-right (not interleaved), bracketed by the
    full-width lines in place."""
    rows = [
        [_w("ΕΦΗΜΕΡΙΔΑ", 200, 420, 10)],                                  # full-width
        [_w("L1", 50, 250, 40), _w("R1", 330, 520, 40)],                  # two-col row
        [_w("L2", 50, 250, 70), _w("R2", 330, 520, 70)],                  # two-col row
        [_w("L3", 50, 250, 100), _w("R3", 330, 520, 100)],                # two-col row
        [_w("Resolution", 100, 500, 130)],                                # full-width
    ]
    out = _banded_text(rows, G).split("\n")
    assert out == ["ΕΦΗΜΕΡΙΔΑ", "L1", "L2", "L3", "R1", "R2", "R3", "Resolution"]


def test_banded_text_pure_single_column_is_top_to_bottom():
    rows = [
        [_w("The", 100, 140, 10), _w("Security", 145, 250, 10), _w("Council", 255, 360, 10)],
        [_w("Reaffirming", 100, 220, 30), _w("its", 225, 260, 30)],
    ]
    out = _banded_text(rows, G).split("\n")
    assert out == ["The Security Council", "Reaffirming its"]


def test_rows_groups_words_by_visual_line():
    words = [_w("β", 330, 360, 12), _w("α", 50, 90, 10),   # same line (top~10-12)
             _w("γ", 50, 90, 60)]                          # next line
    rows = _rows(words)
    assert [[w["text"] for w in r] for r in rows] == [["α", "β"], ["γ"]]
