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

# Instrument types that EXERCISE delegated authority (FEK Β΄ implementing acts).
IMPLEMENTING_TYPES = {
    TYPE_YA, TYPE_KYA, TYPE_KANAP, TYPE_APOF_DIOIK, TYPE_APOF_PERIF, TYPE_APOF_NPDD,
}

# Authority phrases that introduce an enabling reference.
_AUTHORITY = re.compile(
    r"κατ['΄ʼ’]?\s*εξουσιοδότηση|δυνάμει\s+(?:των\s+διατάξεων\s+)?(?:του|της)|"
    r"σύμφωνα\s+με\s+(?:το|τις\s+διατάξεις)|"
    r"βάσει\s+(?:του|της)|τις\s+διατάξεις\s+(?:του|της)",
    re.IGNORECASE)

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


def extract_delegations(law: Law) -> Law:
    """Populate law.delegations. Only implementing-type instruments emit edges."""
    if law.instrument_type not in IMPLEMENTING_TYPES:
        return law

    seen = set()
    for p in law.provisions:
        t = p.text_in_force
        recipient_m = _RECIPIENT.search(t)
        recipient = recipient_m.group(0).strip() if recipient_m else ""
        for am in _AUTHORITY.finditer(t):
            window = t[am.end():am.end() + _LOOKAHEAD]
            enabling_id, law_no, art_no, resolved = _resolve_enabling(window)
            if not resolved or not enabling_id:
                continue
            key = (enabling_id, law.instrument_id)
            if key in seen:
                continue
            seen.add(key)
            law.delegations.append(DelegationEdge(
                enabling_id=enabling_id,
                implementing_id=law.instrument_id,
                enabling_law_number=law_no,
                enabling_article_number=art_no,
                delegated_authority=recipient,
                delegation_scope=(law.title or "")[:200],
                resolved=resolved))
    return law
