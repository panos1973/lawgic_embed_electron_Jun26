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


def language_of(text: str) -> str:
    """Tag a chunk 'el' | 'en' | 'mixed' from its Greek/Latin letter ratio.

    Replaces the hard-coded language='el' so bilingual chunks — e.g. a ratified
    UN resolution or treaty annex carried verbatim in English inside a Greek law —
    are labelled honestly. Counts letters only (digits/punctuation ignored);
    Greek-default when there is no alphabetic content at all.
    """
    gr = len(_GREEK.findall(text or ""))
    lat = len(_LATIN.findall(text or ""))
    total = gr + lat
    if total == 0:
        return "el"
    lat_frac = lat / total
    if lat_frac < 0.15:
        return "el"
    if lat_frac > 0.85:
        return "en"
    return "mixed"


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
# Header FRAGMENTS: two-column dewrapping splits the running header into pieces
# that the full-header pattern misses — a lone issue stamp "Τεύχος A' 9/19.01.2024"
# (series letter is often a Latin homoglyph A/B; keraia ’ or ΄), and the masthead
# word alone on a line, optionally glued to a page number ("68 ΕΦΗΜΕΡΙΔΑ",
# "ΚΥΒΕΡΝΗΣΕΩΣ 69"). These repeat on every page and must not enter provision text.
_ISSUE_STAMP = re.compile(
    r"\bΤεύχος\s+[ΑΒΓΔΕABΓ]['’΄ʼ]?\s*\d+/\d{1,2}\.\d{1,2}\.\d{4}")
_HEADER_FRAG = re.compile(
    r"^\s*\d{0,5}\s*(?:ΕΦΗΜΕΡΙ[ΔΑ∆]+Α?|ΚΥΒΕΡΝΗΣΕΩΣ)\s*\d{0,5}\s*$",
    re.MULTILINE)
# Azure Document Intelligence page markers in its markdown output:
# <!-- PageNumber="91" -->, <!-- PageHeader="..." -->, <!-- PageBreak -->.
# These leak mid-article into chunk_text on the Azure path (table/scanned pages).
_AZURE_COMMENT = re.compile(r"<!--\s*Page(?:Number|Header|Footer|Break)[^>]*-->",
                            re.IGNORECASE)
# Barcode line, e.g. *01000231402240020*
_BARCODE = re.compile(r"^\s*\*\d{6,}\*\s*$", re.MULTILINE)
# Εθνικό Τυπογραφείο trailer: everything from the printing-house anchor onward.
_TRAILER = re.compile(
    r"(Καποδιστρίου\s+34|ΕΞΥΠΗΡΕΤΗΣΗ\s+ΚΟΙΝΟΥ|Το\s+Εθνικό\s+Τυπογραφείο)"
    r".*\Z", re.IGNORECASE | re.DOTALL)
# Promulgation + signature trailer of a νόμος: the closing "Παραγγέλλομε τη
# δημοσίευση ... ως νόμου του Κράτους", the President + ministers' signatures, the
# "Θεωρήθηκε ... Μεγάλη Σφραγίδα" attestation and the all-caps ΕΘΝΙΚΟ ΤΥΠΟΓΡΑΦΕΙΟ
# footer that _TRAILER's "Το Εθνικό Τυπογραφείο" anchor misses. None of it is
# substantive law, but with two-column dewrapping it lands inside the final article
# ("Έναρξη ισχύος") — polluting that chunk's vector/summary with ministers' names.
# Cut from the end-only promulgation/seal anchor to EOF (swallowing an optional
# inline running-header just before it). Safe: a law's BODY never says
# "Παραγγέλλομε" — the enacting formula at the START uses "Εκδίδομε".
_PROMULGATION = re.compile(
    r"\s*(?:ΕΦΗΜΕΡΙ[ΔΑ∆]+Α?\s+Τ?ΗΣ\s+ΚΥΒΕΡΝΗΣΕΩΣ\s*\d*\s*)?"
    r"(?:Παραγγ[εέ]λλο(?:υ)?με|Θεωρήθηκε\s+και\s+τέθηκε\s+η\s+Μεγάλη\s+Σφραγίδα)"
    r".*\Z", re.IGNORECASE | re.DOTALL)


def strip_furniture(text: str) -> str:
    if not text:
        return text
    text = _AZURE_COMMENT.sub("", text)       # Azure DI <!-- Page… --> comments
    text = _TRAILER.sub("", text)
    text = _PROMULGATION.sub("", text)        # promulgation + signatures + seal trailer
    text = _RUN_HEADER.sub("", text)
    text = _ISSUE_STAMP.sub(" ", text)        # lone "Τεύχος A' 9/19.01.2024" stamps
    text = _HEADER_FRAG.sub("", text)         # lone masthead-word lines (± page no.)
    text = _BARCODE.sub("", text)
    # collapse the blank lines the removals leave behind
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*\n([ \t]*\n)+", "\n\n", text)
    return text.strip()
