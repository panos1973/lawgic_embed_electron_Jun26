"""delegate.py — delegation-edge extraction.

A delegation edge records that *this* document (an implementing act published in
FEK Β΄ — a ΥΑ, ΚΥΑ, regulatory/authority decision, etc.) exercises authority
granted by an *enabling* provision (a law/decree article in FEK Α΄). The textual
signal is an authority phrase followed by a nested-genitive reference:

    "κατ' εξουσιοδότηση της παρ. 2 του άρθρου 5 του ν. 4412/2016"
    "δυνάμει του άρθρου 90 του ν. 4622/2019"
    "σύμφωνα με το άρθρο 12 του π.δ. 80/2021"
    "Έχοντας υπόψη: ... τις διατάξεις του άρθρου 5 του ν. 4412/2016"

We only emit edges from implementing-type instruments (FEK Β΄). Primary
legislation (νόμος/π.δ./Π.Ν.Π./α.ν./ν.δ./Κανονισμός Βουλής) is the *grantor* of
authority, not the exerciser, so it does not originate delegation edges here.

Reference resolution reuses the same grammar as amend.py (article + optional
paragraph + instrument), producing the enabling provision's canonical_id.
"""
from __future__ import annotations

import re

from models import (Law, DelegationEdge, TYPE_NOMOS, TYPE_PD, TYPE_PNP,
                    TYPE_AN, TYPE_ND, TYPE_KANVOULIS,
                    TYPE_YA, TYPE_KYA, TYPE_KANAP, TYPE_APOF_DIOIK,
                    TYPE_APOF_PERIF, TYPE_APOF_NPDD,
                    make_instrument_id, make_provision_id)

# Instrument types that EXERCISE delegated authority. Cite-based: besides FEK Β΄
# decisions, a π.δ. can be a delegated act too — e.g. a codifying π.δ. issued
# "κατ' εξουσιοδότηση" of a law (π.δ. 62/2025, Κώδικας Εργατικού Δικαίου, under
# παρ. 6 άρθρου 67 ν. 4622/2019). Primary νόμος is enacted by Parliament, not
# delegated, so it is excluded (its «Έχοντας υπόψη» basis is not a delegation).
IMPLEMENTING_TYPES = {
    TYPE_YA, TYPE_KYA, TYPE_KANAP, TYPE_APOF_DIOIK, TYPE_APOF_PERIF,
    TYPE_APOF_NPDD, TYPE_PD,
}

# Authority phrases that introduce an enabling reference.
_AUTHORITY = re.compile(
    r"κατ['΄ʼ’]?\s*εξουσιοδότηση|δυνάμει\s+(?:των\s+διατάξεων\s+)?(?:του|της)|"
    r"σύμφωνα\s+με\s+(?:το|τις\s+διατάξεις)|"
    r"βάσει\s+(?:του|της)|τις\s+διατάξεις\s+(?:του|της)",
    re.IGNORECASE)

# Preamble marker: implementing acts open their legal basis with «Έχοντας υπόψη».
# The first specific (παρ.→άρθρο→νόμος) reference under it is the primary enabling
# provision, even without an explicit "κατ' εξουσιοδότηση" keyword.
_PREAMBLE = re.compile(r"Έχοντας\s+υπόψη", re.IGNORECASE)
_PREAMBLE_WINDOW = 600

# Reference sub-patterns (shared shape with amend.py).
_REF_PAR = re.compile(r"παρ(?:άγραφος|αγράφου|\.|άγραφο)?\s*(\d+[α-ωΑ-Ω]?)")
_REF_ART = re.compile(r"άρθρ(?:ο|ου|α|ων)\s*(\d+[Α-Ωα-ω]?)")
_REF_INSTR = re.compile(
    r"(?P<type>α\.?ν\.|ν\.?δ\.|π\.?\s*δ\.|ν\.|νόμ\w*|προεδρικ\w*)?\s*"
    r"(?P<num>\d{1,5})\s*/\s*(?P<year>\d{4})")

# Authority recipient, best-effort (e.g. "ο Υπουργός Οικονομικών").
_RECIPIENT = re.compile(
    r"\b(ο|η|οι)\s+(Υπουργ\w+|Γενικ\w+\s+Γραμματ\w+|Διοικητ\w+|Περιφερειάρχ\w+|"
    r"Δήμαρχ\w+)[^.\n]{0,60}", re.IGNORECASE)

# How far after an authority phrase to look for the reference.
_LOOKAHEAD = 200

# The delegation lives in the act header: everything up to the operative verb
# ("αποφασίζει/-ουμε", "διατάσσουμε") or the first Άρθρο, whichever comes first.
_OPERATIVE = re.compile(r"αποφασίζ(?:ουμε|ει)|διατάσσουμε|παραγγέλλ|"
                        r"^\s*Άρθρο\s+1\b", re.IGNORECASE | re.MULTILINE)
_HEADER_CAP = 4000


def _header_end(text: str) -> int:
    m = _OPERATIVE.search(text)
    return min(m.end() if m else _HEADER_CAP, _HEADER_CAP)


def _instr_type(token: str) -> str:
    t = (token or "").lower().replace(" ", "")
    if t.startswith("α.ν") or t.startswith("αν"):
        return TYPE_AN
    if t.startswith("ν.δ") or t.startswith("νδ"):
        return TYPE_ND
    if t.startswith("π"):
        return TYPE_PD
    return TYPE_NOMOS


def _resolve_enabling(window: str) -> tuple[str, str, str, bool]:
    """window -> (enabling_canonical_id, law_number, article_number, resolved)."""
    art = _REF_ART.search(window)
    instr = _REF_INSTR.search(window)
    if not instr:
        return "", "", "", False
    itype = _instr_type(instr.group("type"))
    instrument_id = make_instrument_id(itype, int(instr.group("num")),
                                       int(instr.group("year")))
    law_number = f"{instr.group('num')}/{instr.group('year')}"
    if not art:
        # whole-instrument enabling reference (rare but valid)
        return instrument_id, law_number, "", True
    par = _REF_PAR.search(window)
    article = art.group(1)
    enabling_id = make_provision_id(instrument_id, article,
                                    par.group(1) if par else None)
    return enabling_id, law_number, article, True


def extract_delegations(law: Law, full_text: str = "") -> Law:
    """Populate law.delegations. Only implementing-type instruments emit edges.

    `full_text` is the whole act (preamble + body). It matters because a codifying
    π.δ./decision states its enabling provision in the «Έχοντας υπόψη» preamble,
    which sits before the first Άρθρο and so is absent from law.provisions.
    """
    if law.instrument_type not in IMPLEMENTING_TYPES:
        return law

    # A delegation is always declared UP FRONT — in the «Έχοντας υπόψη» preamble
    # and the operative clause, never deep in an article body. Restricting the
    # search to that header region is what keeps precision high: in a long
    # codification the body is full of ordinary cross-references ("σύμφωνα με τις
    # διατάξεις του άρθρου Χ του ν. Υ") that are citations, not delegations.
    base = full_text or "\n".join(p.text_in_force for p in law.provisions)
    header = base[:_header_end(base)]
    rec = _RECIPIENT.search(header)
    recipient = rec.group(0).strip() if rec else ""

    seen = set()

    def _emit(enabling_id, law_no, art_no):
        key = (enabling_id, law.instrument_id)
        if not enabling_id or key in seen:
            return
        seen.add(key)
        law.delegations.append(DelegationEdge(
            enabling_id=enabling_id, implementing_id=law.instrument_id,
            enabling_law_number=law_no, enabling_article_number=art_no,
            delegated_authority=recipient,
            delegation_scope=(law.title or "")[:200], resolved=True))

    # 1) Primary enabling: first specific (παρ.→άρθρο→νόμος) ref after «Έχοντας υπόψη».
    pm = _PREAMBLE.search(header)
    if pm:
        win = header[pm.end():pm.end() + _PREAMBLE_WINDOW]
        eid, law_no, art_no, resolved = _resolve_enabling(win)
        if resolved and art_no:            # require an article anchor for precision
            _emit(eid, law_no, art_no)

    # 2) Explicit authority phrases in the header (e.g. "κατ' εξουσιοδότηση ...").
    for am in _AUTHORITY.finditer(header):
        win = header[am.end():am.end() + _LOOKAHEAD]
        eid, law_no, art_no, resolved = _resolve_enabling(win)
        if resolved and eid:
            _emit(eid, law_no, art_no)
    return law
