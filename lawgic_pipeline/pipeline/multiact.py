"""multiact.py — split one FEK PDF into the N instruments it actually contains.

Primary legislation (νόμος / π.δ. / Π.Ν.Π. / α.ν. / ν.δ. / ψήφισμα / Κανονισμός
Βουλής) is always one act per gazette issue, so we pass it through unchanged.

Decision issues are different: a single FEK (Α΄ ministerial section, or any Β΄
issue) bundles several independent acts, listed in a ΠΕΡΙΕΧΟΜΕΝΑ index and then
printed one after another, each opening with an `Αριθμ. <num> (<item>)` header
followed by a title and an issuer line. We split on those `Αριθμ.` headers and
classify each act by its issuer:

    Οι Υπουργοί ...                     -> ΚΥΑ
    Ο/Η Υπουργός / Υφυπουργός ...       -> ΥΑ
    Ο Γραμματέας Αποκεντρωμένης /       -> απόφαση Διοικητή/Γ.Γ.
      Ο Διοικητής / Γενικός Γραμματέας
    Ο Περιφερειάρχης / Δημοτικό Συμβ.   -> απόφαση Περιφερειάρχη/ΟΤΑ
    Η Σύγκλητος / Διοικητικό Συμβούλιο  -> απόφαση Δ.Σ. ΝΠΔΔ
    Η Ρυθμιστική Αρχή / Επιτροπή / Αρχή -> κανονιστική απόφαση Αρχής

Single-act decision issues (no ΠΕΡΙΕΧΟΜΕΝΑ) fall through to one segment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from models import (TYPE_NOMOS, TYPE_PD, TYPE_PNP, TYPE_AN, TYPE_ND,
                    TYPE_PSIFISMA, TYPE_KANVOULIS, TYPE_YA, TYPE_KYA,
                    TYPE_KANAP, TYPE_APOF_DIOIK, TYPE_APOF_PERIF, TYPE_APOF_NPDD)

PRIMARY_TYPES = {TYPE_NOMOS, TYPE_PD, TYPE_PNP, TYPE_AN, TYPE_ND,
                 TYPE_PSIFISMA, TYPE_KANVOULIS}

# Act header: "Αριθμ. <num>" optionally followed by the ΠΕΡΙΕΧΟΜΕΝΑ item "(n)".
# The number is alphanumeric/composite (Φ.1413/ΑΣ6519, 12/101, Ε-142/2025, 5332).
_ACT_HEAD = re.compile(
    r"(?m)^\s*Αριθ(?:μ(?:ός)?)?\.?\s*(?P<num>[^\n()]{1,60}?)\s*"
    r"(?:\(\s*(?P<item>\d{1,3})\s*\))?\s*$")

# Issuer -> instrument type, most specific first (plural ministers before single).
_ISSUERS = [
    (TYPE_KYA, re.compile(r"ΟΙ\s+ΥΠΟΥΡΓΟΙ|ΟΙ\s+ΥΦΥΠΟΥΡΓΟΙ", re.IGNORECASE)),
    (TYPE_APOF_DIOIK, re.compile(
        r"Ο\s+ΓΡΑΜΜΑΤΕΑΣ\s+ΑΠΟΚΕΝΤΡΩΜΕΝΗΣ\s+ΔΙΟΙΚΗΣΗΣ|"
        r"Ο\s+ΔΙΟΙΚΗΤΗΣ|[ΟΗ]\s+ΓΕΝΙΚ[ΟΗ]Σ?\s+ΓΡΑΜΜΑΤΕ[ΑΥ]Σ?", re.IGNORECASE)),
    (TYPE_APOF_PERIF, re.compile(
        r"Ο\s+ΠΕΡΙΦΕΡΕΙΑΡΧΗΣ|ΤΟ\s+ΔΗΜΟΤΙΚΟ\s+ΣΥΜΒΟΥΛΙΟ|Ο\s+ΔΗΜΑΡΧΟΣ|"
        r"Η\s+ΟΙΚΟΝΟΜΙΚΗ\s+ΕΠΙΤΡΟΠΗ", re.IGNORECASE)),
    (TYPE_APOF_NPDD, re.compile(
        r"Η\s+ΣΥΓΚΛΗΤΟΣ|ΤΟ\s+ΔΙΟΙΚΗΤΙΚΟ\s+ΣΥΜΒΟΥΛΙΟ|Η\s+ΔΙΟΙΚΟΥΣΑ\s+ΕΠΙΤΡΟΠΗ|"
        r"ΤΟ\s+ΠΡΥΤΑΝΙΚΟ\s+ΣΥΜΒΟΥΛΙΟ", re.IGNORECASE)),
    (TYPE_KANAP, re.compile(
        r"Η\s+ΡΥΘΜΙΣΤΙΚΗ\s+ΑΡΧΗ|Η\s+ΑΡΧΗ|Η\s+ΕΠΙΤΡΟΠΗ\s|"
        r"Ο\s+ΠΡΟΕΔΡΟΣ\s+ΤΗΣ\s+(?:ΡΥΘΜΙΣΤΙΚΗΣ|ΑΡΧΗΣ|ΕΠΙΤΡΟΠΗΣ)", re.IGNORECASE)),
    (TYPE_YA, re.compile(
        r"[ΟΗ]\s+Υ?ΦΥΠΟΥΡΓΟΣ|[ΟΗ]\s+ΥΠΟΥΡΓΟΣ|[ΟΗ]\s+ΑΝΑΠΛΗΡΩΤΗΣ\s+ΥΠΟΥΡΓΟΣ",
        re.IGNORECASE)),
]

# How far past an Αριθμ. header to look for the issuer line.
_ISSUER_WINDOW = 1500
# ΠΕΡΙΕΧΟΜΕΝΑ index block — dropped from each act (kept only for amendment mining).
_TOC = re.compile(r"ΠΕΡΙΕΧΟΜΕΝΑ", re.IGNORECASE)
# Markers that a text really is a decision gazette (vs a stray "Αριθμ." reference
# in loose body text): the "ΑΠΟΦΑΣΕΙΣ" section header or a ΠΕΡΙΕΧΟΜΕΝΑ index.
_DECISION_GAZETTE = re.compile(r"ΑΠΟΦΑΣΕΙΣ|ΠΕΡΙΕΧΟΜΕΝΑ")


@dataclass
class ActSegment:
    instrument_type: Optional[str]
    number: str               # raw Αριθμ. value (display); "" for primary laws
    item: Optional[int]       # ΠΕΡΙΕΧΟΜΕΝΑ item index, when multi-act
    issuer: str
    title: str
    text: str
    is_decision: bool


def _classify_issuer(window: str) -> tuple[Optional[str], str]:
    for itype, pat in _ISSUERS:
        m = pat.search(window)
        if m:
            return itype, m.group(0).strip()
    return None, ""


def _title_between(text: str, head_end: int, issuer_pos: int) -> str:
    """The act title sits between the Αριθμ. header and the issuer line."""
    chunk = text[head_end:issuer_pos] if issuer_pos > head_end else text[head_end:head_end + 200]
    lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
    return " ".join(lines)[:300]


def split_acts(text: str, masthead: dict) -> list[ActSegment]:
    """Return one ActSegment per instrument contained in the gazette text."""
    mtype = (masthead or {}).get("instrument_type")

    # Primary legislation: exactly one act, identity already in the masthead.
    if mtype in PRIMARY_TYPES:
        return [ActSegment(instrument_type=mtype, number="", item=None, issuer="",
                           title=(masthead or {}).get("title", ""), text=text,
                           is_decision=False)]

    # Decision issue: split on Αριθμ. act headers.
    heads = list(_ACT_HEAD.finditer(text))
    # Keep only headers that have an issuer within their window — filters out
    # stray "αριθμ." references in body text.
    acts = []
    for i, h in enumerate(heads):
        nxt = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        window = text[h.end():min(h.end() + _ISSUER_WINDOW, nxt)]
        itype, issuer = _classify_issuer(window)
        has_item = h.group("item") is not None
        # An explicit "(n)" index ties this header to a ΠΕΡΙΕΧΟΜΕΝΑ entry, so it is
        # a real act even if we can't classify the issuer -> keep it as a generic
        # regulatory decision. A bare "Αριθμ." with neither index nor issuer is a
        # stray body reference and is skipped.
        if itype is None:
            if not has_item:
                continue
            itype = TYPE_KANAP
        body = text[h.start():nxt].strip()
        issuer_pos = h.end() + window.find(issuer) if issuer else h.end()
        acts.append(ActSegment(
            instrument_type=itype,
            number=(h.group("num") or "").strip(),
            item=int(h.group("item")) if h.group("item") else None,
            issuer=issuer,
            title=_title_between(text, h.end(), issuer_pos),
            text=body, is_decision=True))

    if acts:
        return acts

    # Single-act fallback: a decision gazette with valid FEK coordinates and a real
    # "ΑΠΟΦΑΣΕΙΣ"/ΠΕΡΙΕΧΟΜΕΝΑ structure, but whose lone act header couldn't be
    # classified (e.g. a regulatory Authority whose issuer phrasing we don't list,
    # and no "(n)" index), is still ONE instrument. Emit the whole text as one
    # decision rather than dropping the entire gazette to review; the caller forms
    # the id from the gazette coordinates (Β΄<φύλλο>/<έτος>). A bare stray "Αριθμ."
    # in loose text has no such gazette structure and still falls through to review.
    mh = masthead or {}
    if (_DECISION_GAZETTE.search(text)
            and mh.get("fek_series") and mh.get("fek_number") and mh.get("year")):
        return [ActSegment(instrument_type=mtype or TYPE_KANAP, number="", item=None,
                           issuer="", title=mh.get("title", ""), text=text,
                           is_decision=True)]

    # No identifiable act structure at all: nothing to split — return empty so the
    # caller routes the document to review.
    return []
