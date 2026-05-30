"""Generate a tiny text+table PDF fixture for sidecar/extract tests (reportlab).

Writes <dir>/fek_sample.pdf: a 2-page document whose page 1 is a FEK-style
masthead + an article, and page 2 contains a small table.

A Greek-capable TrueType font is registered and used for every cell so that the
glyphs embed in the PDF and round-trip cleanly through pdfplumber's text layer
(reportlab's built-in Helvetica has no Greek glyphs -> extraction yields cid:0).
"""
import os
import sys

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak)

# Candidate Greek-capable TTFs, in order of preference.
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
]
_FONT_NAME = "FixtureGreek"


def _register_font() -> str:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont(_FONT_NAME, path))
            return _FONT_NAME
    raise RuntimeError(
        "no Greek-capable TTF found; install fonts-dejavu-core "
        f"(looked in: {', '.join(_FONT_CANDIDATES)})")


def build(path: str) -> None:
    font = _register_font()
    doc = SimpleDocTemplate(path, pagesize=A4)
    styles = getSampleStyleSheet()
    body = styles["Normal"].clone("greek")
    body.fontName = font

    story = []
    for line in [
        "ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ",
        "ΤΗΣ ΕΛΛΗΝΙΚΗΣ ΔΗΜΟΚΡΑΤΙΑΣ",
        "26 Μαρτίου 2024 &nbsp;&nbsp;&nbsp; ΤΕΥΧΟΣ ΠΡΩΤΟ &nbsp;&nbsp;&nbsp; Αρ. Φύλλου 52",
        "ΝΟΜΟΣ ΥΠ' ΑΡΙΘΜ. 5090",
        "Δοκιμαστικός τίτλος νόμου για έλεγχο.",
        "Άρθρο 1",
        "Σκοπός του παρόντος είναι ο έλεγχος της εξαγωγής κειμένου.",
    ]:
        story.append(Paragraph(line, body))
        story.append(Spacer(1, 8))

    story.append(PageBreak())
    story.append(Paragraph("Άρθρο 2", body))
    story.append(Spacer(1, 8))
    data = [["Στήλη Α", "Στήλη Β", "Στήλη Γ"],
            ["1", "2", "3"],
            ["α", "β", "γ"]]
    tbl = Table(data)
    tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
    ]))
    story.append(tbl)
    doc.build(story)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "fek_sample.pdf"
    build(out)
    print(f"wrote {out}")
