"""normalize.py — Greek text normalization (real, deterministic).

Run between extraction and segmentation. Fixes the glyph/dehyphenation/homoglyph
issues called out in the FEK morphology reference and emits an accent-folded copy
for BM25.
"""
from __future__ import annotations
import re
import unicodedata

# Greek/Latin homoglyph repair (Latin -> Greek) for ALL-CAPS headings etc.
_HOMOGLYPH = str.maketrans({
    "A": "Α", "B": "Β", "E": "Ε", "Z": "Ζ", "H": "Η", "I": "Ι", "K": "Κ",
    "M": "Μ", "N": "Ν", "O": "Ο", "P": "Ρ", "T": "Τ", "X": "Χ", "Y": "Υ",
})
# Glyph variants -> canonical
_GLYPH = {
    "\u2206": "\u0394",  # ∆ -> Δ
    "\u2019": "'", "\u2018": "'", "\u0384": "'", "\u00b4": "'",  # apostrophes/keraia
}


def _fix_glyphs(text: str) -> str:
    for a, b in _GLYPH.items():
        text = text.replace(a, b)
    return text


def dehyphenate(text: str) -> str:
    # join word broken across line: "αποκλεισμέ-\nνους" -> "αποκλεισμένους"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # join law numbers split across lines: "2019/\n1151" -> "2019/1151"
    text = re.sub(r"(\d)/\n(\d)", r"\1/\2", text)
    return text


# Bold / outlined headings are drawn twice in the PDF, so extraction reads each
# glyph twice: "ΟΡΟΙ" -> "ΟΟΡΡΟΟΙΙ", "ΔΙΚΑΙΟΛΟΓΗΤΙΚΑ" -> "ΔΔΙΙΚΚΑΑΙΙΟΟΛΛΟΟΓΓΗΗΤΤΙΙΚΚΑΑ",
# "Μ.Δ.Ε." -> "ΜΜ..ΔΔ..ΕΕ..". A doubled run is even-length and made ENTIRELY of
# identical adjacent pairs — a shape natural words never have (Greek doubles like
# σσ/λλ/γγ are isolated, never at every position). So collapse ONLY a whole token
# that is fully paired, and never one containing a digit (1122 != 12) — real double
# letters and numbers are left intact.
_TOKEN = re.compile(r"\S+")


def _is_doubled(s: str) -> bool:
    n = len(s)
    return (n >= 6 and n % 2 == 0 and not any(c.isdigit() for c in s)
            and all(s[i] == s[i + 1] for i in range(0, n, 2)))


# Trailing / leading punctuation a doubled word may carry ("…χαρακτήρες," "«όροι»").
# '.' is intentionally NOT peeled — it can be part of the doubled pattern itself
# ("ΜΜ..ΔΔ..ΕΕ.." -> "Μ.Δ.Ε."), which the whole-token check already handles.
_PUNCT_TAIL = ",·;:)]}»”’"
_PUNCT_LEAD = "([{«“‘"


def _undouble_token(tok: str) -> str:
    if _is_doubled(tok):                     # whole token fully paired
        return tok[::2]
    lead, core, tail = "", tok, ""           # else peel attached punctuation and retry
    while core and core[-1] in _PUNCT_TAIL:
        tail = core[-1] + tail
        core = core[:-1]
    while core and core[0] in _PUNCT_LEAD:
        lead += core[0]
        core = core[1:]
    if core != tok and _is_doubled(core):
        return lead + core[::2] + tail
    return tok


def collapse_doubled_glyphs(text: str) -> str:
    """Repair the bold/double-struck extraction artifact (every glyph read twice)."""
    return _TOKEN.sub(lambda m: _undouble_token(m.group(0)), text)


def normalize_display(text: str) -> str:
    """Clean text for storage/segmentation (keeps accents and case)."""
    text = unicodedata.normalize("NFC", text)
    text = _fix_glyphs(text)
    text = collapse_doubled_glyphs(text)        # repair bold/double-struck headings
    text = dehyphenate(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def repair_caps_homoglyphs(heading: str) -> str:
    """Apply ONLY to ALL-CAPS headings, where Latin homoglyphs are common."""
    if heading.isupper():
        return heading.translate(_HOMOGLYPH)
    return heading


def fold_for_bm25(text: str) -> str:
    """Accent-folded, lowercase, final-sigma normalized — for the *_normalized field."""
    text = unicodedata.normalize("NFC", text).lower().replace("ς", "σ")
    text = "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")
    return unicodedata.normalize("NFC", text)
