"""normalize.py — text-layer hygiene for real FEK PDFs.

Three deterministic passes, all idempotent, applied to extracted text before
masthead parsing and segmentation:

  normalize_glyphs(text)
    Greek gazette fonts mix Latin homoglyphs into otherwise-Greek words — the
    headline "ΝΟΜΟΣ" is frequently encoded as Latin "NOMO" + Greek "Σ", and the
    capital delta is the INCREMENT sign ∆ (U+2206) rather than Greek Δ (U+0394).
    We repair Latin homoglyphs ONLY inside tokens that already contain a Greek
    letter, so all-Latin tokens (COVID-19, Master, www.et.gr, EU names) are left
    untouched. ∆→Δ and stray PDF (cid:NNN) artifacts are handled globally.

  strip_furniture(text)
    Removes the per-page running header (e.g. "240 ΕΦΗΜΕΡΙΔΑ ΤΗΣ ΚΥΒΕΡΝΗΣΕΩΣ
    Τεύχος A' 23/14.02.2024"), the Εθνικό Τυπογραφείο trailer block, and the
    *NNNN* barcode lines. These repeat on every page and pollute provision text
    and embeddings; they are redundant with the masthead. The page-1 masthead
    block itself is NOT a running header and is preserved.

  cid_ratio(text)
    Fraction of whitespace tokens that are (cid:NNN) artifacts — a page that is
    mostly CID has no usable ToUnicode map and should be routed to OCR.
"""
from __future__ import annotations

import re

# Latin capital -> visually identical Greek capital. Only letters with a true
# Greek look-alike are listed; C, D, F, G, L, etc. have none and are left as-is
# (so a Latin-only token like "COVID" is never half-converted).
_LAT2GR_UP = {
    "A": "Α", "B": "Β", "E": "Ε", "H": "Η", "I": "Ι", "K": "Κ", "M": "Μ",
    "N": "Ν", "O": "Ο", "P": "Ρ", "T": "Τ", "X": "Χ", "Y": "Υ", "Z": "Ζ",
}
_LAT2GR_LOW = {
    "a": "α", "e": "ε", "o": "ο", "v": "ν", "i": "ι", "k": "κ", "n": "η",
    "p": "ρ", "t": "τ", "x": "χ", "y": "υ",
}
_GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")     # any Greek letter
_LATIN = re.compile(r"[A-Za-z]")
_TOKEN = re.compile(r"\S+")
_CID = re.compile(r"\(cid:\d+\)")


def _fix_token(tok: str) -> str:
    """Repair Latin homoglyphs in a token that is predominantly Greek."""
    if not _GREEK.search(tok) or not _LATIN.search(tok):
        return tok                                   # all-Greek or all-Latin: leave
    # Map only Latin chars that have a Greek look-alike; keep the rest verbatim,
    # so "ΧΙV" (Greek Χ Ι + Latin V) stays "ΧΙV" (no Greek V exists).
    return "".join(_LAT2GR_UP.get(c, _LAT2GR_LOW.get(c, c)) if c.isascii()
                   and c.isalpha() else c for c in tok)


def normalize_glyphs(text: str) -> str:
    if not text:
        return text
    text = text.replace("∆", "Δ")          # ∆ INCREMENT -> Δ Greek
    text = text.replace("∇", "ν")          # ∇ (rare) -> ν, defensive
    text = _CID.sub(" ", text)                       # drop (cid:NNN) artifacts
    return _TOKEN.sub(lambda m: _fix_token(m.group(0)), text)


def cid_ratio(text: str) -> float:
    if not text:
        return 0.0
    toks = text.split()
    if not toks:
        return 0.0
    return sum(1 for t in toks if t.startswith("(cid:")) / len(toks)


# --- furniture stripping ---------------------------------------------------

# Running page header: optional leading page number, then the gazette name and
# the τεύχος/φύλλο/date stamp. Matched per line (post glyph-normalization).
_RUN_HEADER = re.compile(
    r"^\s*\d{0,5}\s*ΕΦΗΜΕΡΙ[ΔΑ∆]+Α?\s+Τ?ΗΣ\s+ΚΥΒΕΡΝΗΣΕΩΣ.*$",
    re.IGNORECASE | re.MULTILINE)
# Barcode line, e.g. *01000231402240020*
_BARCODE = re.compile(r"^\s*\*\d{6,}\*\s*$", re.MULTILINE)
# Εθνικό Τυπογραφείο trailer: everything from the printing-house anchor onward.
_TRAILER = re.compile(
    r"(Καποδιστρίου\s+34|ΕΞΥΠΗΡΕΤΗΣΗ\s+ΚΟΙΝΟΥ|Το\s+Εθνικό\s+Τυπογραφείο)"
    r".*\Z", re.IGNORECASE | re.DOTALL)


def strip_furniture(text: str) -> str:
    if not text:
        return text
    text = _TRAILER.sub("", text)
    text = _RUN_HEADER.sub("", text)
    text = _BARCODE.sub("", text)
    # collapse the blank lines the removals leave behind
    text = re.sub(r"\n[ \t]*\n([ \t]*\n)+", "\n\n", text)
    return text.strip()
