"""masthead.py — parse the FEK cover / instrument header into identity metadata.

The masthead is what turns an extracted blob into an *identified* law: instrument
type + number + year (the backbone of the canonical id and therefore of the
idempotent UUID) plus the FEK reference (series / number / date).

Pure text in, dict out — no I/O, fully unit-testable. Every field is best-effort
and independent: whatever cannot be found is left as None and noted in warnings,
so a partial masthead never crashes the pipeline (the doc just routes to review).

Real-world variants handled:
  - spelling:   ΕΦΗΜΕΡΙΣ (older) and ΕΦΗΜΕΡΙΔΑ (newer)
  - apostrophe/keraia in "ΥΠ' ΑΡΙΘΜ.":  ' ʼ ΄ ´ ’  (or omitted)
  - abbreviation: ΑΡΙΘΜ. / ΑΡΙΘ. / ΑΡΙΘΜΟΝ / ΑΡΙΘΜΟΣ
  - series words: ΠΡΩΤΟ/ΔΕΥΤΕΡΟ/ΤΡΙΤΟ/ΤΕΤΑΡΤΟ  ->  Α/Β/Γ/Δ
  - "Αρ. Φύλλου" / "Αριθμός Φύλλου" / "Αριθμ. Φύλλου"
"""
from __future__ import annotations

import re
from typing import Optional

from models import (TYPE_NOMOS, TYPE_PD, TYPE_PNP, TYPE_YA, TYPE_KYA, TYPE_PSIFISMA,
                    TYPE_AN, TYPE_ND, TYPE_KANVOULIS, TYPE_KANAP, TYPE_APOF_DIOIK,
                    TYPE_APOF_PERIF, TYPE_APOF_NPDD)

# Apostrophe / keraia variants that appear in "ΥΠ' ΑΡΙΘΜ." — match any or none.
_APOS = r"['ʼ΄´’‘]?"
# "ΑΡΙΘΜ." abbreviation variants.
_ARITHM = r"ΑΡΙΘ(?:ΜΟΣ|ΜΟΝ|Μ|\.)?\.?"

# Instrument-type header patterns, most specific first. Each captures the number
# in group 'num' when the header carries one inline. Order matters: multi-word and
# qualified headers (ΑΝΑΓΚΑΣΤΙΚΟΣ/ΝΟΜΟΘΕΤΙΚΟ) must precede the bare ΝΟΜΟΣ/ΔΙΑΤΑΓΜΑ.
_TYPE_PATTERNS = [
    (TYPE_PNP, re.compile(r"ΠΡΑΞΗ\s+ΝΟΜΟΘΕΤΙΚΟΥ\s+ΠΕΡΙΕΧΟΜΕΝΟΥ", re.IGNORECASE)),
    # FEK Α΄ — additional primary legislation (before the bare ΝΟΜΟΣ/ΔΙΑΤΑΓΜΑ rules)
    (TYPE_AN, re.compile(
        r"ΑΝΑΓΚΑΣΤΙΚΟΣ\s+ΝΟΜΟΣ\s+ΥΠ" + _APOS + r"\s*" + _ARITHM +
        r"\s*(?P<num>\d{1,5})", re.IGNORECASE)),
    (TYPE_ND, re.compile(
        r"ΝΟΜΟΘΕΤΙΚΟ\s+ΔΙΑΤΑΓΜΑ\s+ΥΠ" + _APOS + r"\s*" + _ARITHM +
        r"\s*(?P<num>\d{1,5})", re.IGNORECASE)),
    (TYPE_KANVOULIS, re.compile(r"ΚΑΝΟΝΙΣΜΟΣ\s+(?:ΤΗΣ\s+)?ΒΟΥΛΗΣ", re.IGNORECASE)),
    (TYPE_PD, re.compile(
        r"ΠΡΟΕΔΡΙΚΟ\s+ΔΙΑΤΑΓΜΑ\s+ΥΠ" + _APOS + r"\s*" + _ARITHM +
        r"\s*(?P<num>\d{1,5})", re.IGNORECASE)),
    # FEK Β΄ — regulatory acts (most specific phrasings first)
    (TYPE_KANAP, re.compile(
        r"ΚΑΝΟΝΙΣΤΙΚΗ\s+(?:ΠΡΑΞΗ|ΑΠΟΦΑΣΗ)", re.IGNORECASE)),
    (TYPE_APOF_PERIF, re.compile(
        r"ΑΠΟΦΑΣΗ\s+ΠΕΡΙΦΕΡΕΙΑΡΧΗ|ΑΠΟΦΑΣΗ\s+(?:ΔΗΜΑΡΧΟΥ|ΔΗΜΟΤΙΚΟΥ\s+ΣΥΜΒΟΥΛΙΟΥ)",
        re.IGNORECASE)),
    (TYPE_APOF_NPDD, re.compile(
        r"ΑΠΟΦΑΣΗ\s+(?:ΤΟΥ\s+)?Δ(?:ΙΟΙΚΗΤΙΚΟΥ)?\.?\s*Σ(?:ΥΜΒΟΥΛΙΟΥ)?\.?\s+"
        r"(?:ΤΟΥ\s+)?ΝΠΔΔ", re.IGNORECASE)),
    (TYPE_APOF_DIOIK, re.compile(
        r"ΑΠΟΦΑΣΗ\s+(?:ΔΙΟΙΚΗΤΗ|ΓΕΝΙΚΟΥ\s+ΓΡΑΜΜΑΤΕΑ|ΓΕΝ\.?\s*ΓΡΑΜΜΑΤΕΑ)",
        re.IGNORECASE)),
    (TYPE_KYA, re.compile(
        r"ΚΟΙΝΗ\s+ΥΠΟΥΡΓΙΚΗ\s+ΑΠΟΦΑΣΗ", re.IGNORECASE)),
    (TYPE_YA, re.compile(
        r"ΥΠΟΥΡΓΙΚΗ\s+ΑΠΟΦΑΣΗ", re.IGNORECASE)),
    (TYPE_PSIFISMA, re.compile(r"ΨΗΦΙΣΜΑ", re.IGNORECASE)),
    (TYPE_NOMOS, re.compile(
        r"ΝΟΜΟΣ\s+ΥΠ" + _APOS + r"\s*" + _ARITHM +
        r"\s*(?P<num>\d{1,5})", re.IGNORECASE)),
    # Bare "Π.Δ." / "π.δ." fallback (rare in masthead but seen in inline refs).
    (TYPE_PD, re.compile(r"\bΠ\.?\s*Δ\.?\s*" + _ARITHM +
                         r"?\s*(?P<num>\d{1,5})", re.IGNORECASE)),
]

# Types that legitimately carry no instrument number in the masthead (decisions,
# resolutions, standing orders) — so "instrument number not found" is not a warning.
_NUMBERLESS_TYPES = {
    TYPE_PNP, TYPE_YA, TYPE_KYA, TYPE_PSIFISMA, TYPE_KANVOULIS,
    TYPE_KANAP, TYPE_APOF_DIOIK, TYPE_APOF_PERIF, TYPE_APOF_NPDD,
}

# Standalone number line, used when the type word and the number are on separate
# lines, e.g.  "ΝΟΜΟΣ ΥΠ' ΑΡΙΘΜ.\n5090".
_BARE_NUM = re.compile(r"^\s*(\d{1,5})\s*$", re.MULTILINE)

# FEK series (τεύχος) word -> single-letter code used everywhere else.
_SERIES = {
    "ΠΡΩΤΟ": "Α", "ΔΕΥΤΕΡΟ": "Β", "ΤΡΙΤΟ": "Γ", "ΤΕΤΑΡΤΟ": "Δ",
    "ΠΕΜΠΤΟ": "Ε",
}
_SERIES_RE = re.compile(
    r"ΤΕΥΧΟΣ\s+(ΠΡΩΤΟ|ΔΕΥΤΕΡΟ|ΤΡΙΤΟ|ΤΕΤΑΡΤΟ|ΠΕΜΠΤΟ)", re.IGNORECASE)
# Fallback: the running page header carries the abbreviated form "Τεύχος Α'"
# (the letter may be a Latin homoglyph A/B/E). Used when the full τεύχος word was
# separated by two-column reconstruction.
_SERIES_ABBR_RE = re.compile(r"Τε[υύ]χος\s+([ΑΒΓΔΕABEZH])\s*['΄ʼ’]", re.IGNORECASE)
_SERIES_ABBR = {"Α": "Α", "A": "Α", "Β": "Β", "B": "Β", "Γ": "Γ",
                "Δ": "Δ", "Ε": "Ε", "E": "Ε"}

# "Αρ. Φύλλου 52" / "Αριθμός Φύλλου 52" / "Αριθμ. Φύλλου 52".
_FEK_NO_RE = re.compile(
    r"Αρ(?:ιθμ(?:ός|\.)?|\.)\s*Φ[υύ]λλου\s*(\d{1,6})", re.IGNORECASE)

# Greek month (genitive) -> month number, for "26 Μαρτίου 2024".
_MONTHS = {
    "ιανουαριου": 1, "φεβρουαριου": 2, "μαρτιου": 3, "απριλιου": 4,
    "μαιου": 5, "ιουνιου": 6, "ιουλιου": 7, "αυγουστου": 8,
    "σεπτεμβριου": 9, "οκτωβριου": 10, "νοεμβριου": 11, "δεκεμβριου": 12,
}
_DATE_RE = re.compile(
    r"(\d{1,2})\s+([Α-Ωα-ωΆ-ώ]+)\s+(\d{4})")


def _fold(s: str) -> str:
    """Accent-fold + lowercase a Greek word for dictionary lookup."""
    import unicodedata
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _parse_date(text: str) -> Optional[str]:
    """Return the first 'DD Month YYYY' as ISO 'YYYY-MM-DD', else None."""
    for m in _DATE_RE.finditer(text):
        day, mon_word, year = m.group(1), _fold(m.group(2)), m.group(3)
        mon = _MONTHS.get(mon_word)
        if mon:
            return f"{int(year):04d}-{mon:02d}-{int(day):02d}"
    return None


def _parse_title(text: str, header_end: int) -> str:
    """Title = the text block right after the instrument header line, up to a
    blank line or the start of the promulgation ('Ο ΠΡΟΕΔΡΟΣ ...') / first article.
    """
    tail = text[header_end:]
    stop = re.search(
        r"(?m)^(?:\s*Ο\s+ΠΡΟΕΔΡΟΣ\b|\s*Άρθρο\s+\d|\s*ΑΡΘΡΟ\s+\d|\n\s*\n)", tail)
    block = tail[: stop.start()] if stop else tail[:600]
    # Collapse internal newlines/space; titles often wrap across several lines.
    title = re.sub(r"\s+", " ", block).strip()
    return title


def parse_masthead(text: str) -> dict:
    """Extract instrument identity + FEK reference from the document head.

    Returns a dict with keys: instrument_type, number (int|None), year (int|None),
    title, fek_series, fek_number, fek_date (ISO|None), warnings (list[str]).
    Only the document head is scanned (first ~4000 chars) — that is where the
    masthead lives and it keeps the regexes cheap on long laws.
    """
    head = text[:4000]
    warnings: list[str] = []

    # --- instrument type + (often) number ---
    instrument_type: Optional[str] = None
    number: Optional[int] = None
    header_end = 0
    for itype, pat in _TYPE_PATTERNS:
        m = pat.search(head)
        if m:
            instrument_type = itype
            header_end = m.end()
            if "num" in m.groupdict() and m.group("num"):
                number = int(m.group("num"))
            break
    if instrument_type is None:
        warnings.append("instrument_type not found in masthead")

    # number on a separate line right after the type header
    if instrument_type is not None and number is None:
        bn = _BARE_NUM.search(head, header_end)
        if bn and bn.start() - header_end < 40:
            number = int(bn.group(1))
            header_end = bn.end()
    if instrument_type is not None and instrument_type not in _NUMBERLESS_TYPES \
            and number is None:
        warnings.append("instrument number not found")

    # --- FEK date / year ---
    fek_date = _parse_date(head)
    year: Optional[int] = int(fek_date[:4]) if fek_date else None
    if year is None:
        warnings.append("fek_date/year not found")

    # --- FEK series + sheet number ---
    fek_series = ""
    sm = _SERIES_RE.search(head)
    if sm:
        fek_series = _SERIES.get(sm.group(1).upper(), "")
    else:
        # fall back to the abbreviated running-header form anywhere in the doc
        am = _SERIES_ABBR_RE.search(text)
        if am:
            fek_series = _SERIES_ABBR.get(am.group(1).upper(), "")
        if not fek_series:
            warnings.append("fek_series (τεύχος) not found")
    fek_number = ""
    fm = _FEK_NO_RE.search(head)
    if fm:
        fek_number = fm.group(1)
    else:
        warnings.append("fek_number (αρ. φύλλου) not found")

    # --- title ---
    title = _parse_title(text, header_end) if instrument_type else ""

    return {
        "instrument_type": instrument_type,
        "number": number,
        "year": year,
        "title": title,
        "fek_series": fek_series,
        "fek_number": fek_number,
        "fek_date": fek_date,
        "warnings": warnings,
    }
