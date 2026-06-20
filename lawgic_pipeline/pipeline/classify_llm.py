"""classify_llm.py — last-resort instrument-type identification via the LLM.

When the deterministic masthead parser cannot type a FEK act (instrument_type=None),
this reads the OPENING TEXT and names the instrument type from OUR taxonomy. The
canonical id is still built deterministically (orchestrator._build_law) from that type
+ the gazette coordinates, so it stays idempotent — the LLM only supplies the missing
label (and a number when the type carries one).

No web lookup: a ΦΕΚ states its own type in the masthead, so reading the document is
sufficient — and the model already knows what each Greek instrument type is. Skips
cleanly without an LLM key (the act then routes to review, as before).
"""
from __future__ import annotations

import json
from typing import Optional

import llm
from models import (TYPE_NOMOS, TYPE_PD, TYPE_PNP, TYPE_PSIFISMA, TYPE_AN, TYPE_ND,
                    TYPE_KANVOULIS, TYPE_KANAP, TYPE_APOF_DIOIK, TYPE_APOF_PERIF,
                    TYPE_APOF_NPDD, TYPE_YA, TYPE_KYA)
from normalize import fold_for_bm25

# The Greek heading that opens each instrument -> our taxonomy code.
_LABELS = {
    "ΝΟΜΟΣ": TYPE_NOMOS,
    "ΠΡΟΕΔΡΙΚΟ ΔΙΑΤΑΓΜΑ": TYPE_PD,
    "ΠΡΑΞΗ ΝΟΜΟΘΕΤΙΚΟΥ ΠΕΡΙΕΧΟΜΕΝΟΥ": TYPE_PNP,
    "ΨΗΦΙΣΜΑ": TYPE_PSIFISMA,
    "ΑΝΑΓΚΑΣΤΙΚΟΣ ΝΟΜΟΣ": TYPE_AN,
    "ΝΟΜΟΘΕΤΙΚΟ ΔΙΑΤΑΓΜΑ": TYPE_ND,
    "ΚΑΝΟΝΙΣΜΟΣ ΒΟΥΛΗΣ": TYPE_KANVOULIS,
    "ΚΑΝΟΝΙΣΤΙΚΗ ΑΠΟΦΑΣΗ": TYPE_KANAP,
    "ΑΠΟΦΑΣΗ ΔΙΟΙΚΗΤΗ": TYPE_APOF_DIOIK,
    "ΑΠΟΦΑΣΗ ΠΕΡΙΦΕΡΕΙΑΡΧΗ": TYPE_APOF_PERIF,
    "ΑΠΟΦΑΣΗ ΝΠΔΔ": TYPE_APOF_NPDD,
    "ΥΠΟΥΡΓΙΚΗ ΑΠΟΦΑΣΗ": TYPE_YA,
    "ΚΟΙΝΗ ΥΠΟΥΡΓΙΚΗ ΑΠΟΦΑΣΗ": TYPE_KYA,
}
# accent/case-folded lookup, so the model's label matches even if it varies the casing
_FOLDED = {fold_for_bm25(k): v for k, v in _LABELS.items()}

# Types that carry an instrument NUMBER («ΥΠ' ΑΡΙΘΜ. N»); the rest are dateless.
_NUMBERED = {TYPE_NOMOS, TYPE_AN, TYPE_ND}

_SYSTEM = (
    "You identify the TYPE of a Greek government gazette (ΦΕΚ) instrument from its "
    "opening text — a ΦΕΚ states its own type in the masthead. Reply with ONLY JSON: "
    '{"type": "<one label EXACTLY from the list>", "number": <int or null>}.\n'
    "Labels:\n" + "\n".join(f"- {k}" for k in _LABELS) + "\n"
    "A ΝΟΜΟΣ / ΑΝΑΓΚΑΣΤΙΚΟΣ ΝΟΜΟΣ / ΝΟΜΟΘΕΤΙΚΟ ΔΙΑΤΑΓΜΑ carries a number "
    "(«ΥΠ' ΑΡΙΘΜ. N») -> put it in `number`; everything else has no number -> null. "
    'If none of the labels fits, reply {"type": null, "number": null}.'
)


def classify_instrument(text: str, complete=None) -> Optional[dict]:
    """Return {"instrument_type": TYPE_*, "number": int|None} from the LLM, or None if
    the document cannot be placed in our taxonomy. Raises SystemExit when no LLM key is
    configured (the caller treats that as 'fallback unavailable'). `complete` injectable."""
    if complete is None:
        complete = llm.complete
    head = (text or "").strip()[:2500]
    if not head:
        return None
    out = complete(_SYSTEM, head, want_json=True, max_tokens=120)
    try:
        data = json.loads(out)
    except Exception:                       # noqa: BLE001 — bad JSON -> give up cleanly
        return None
    t = _FOLDED.get(fold_for_bm25((data.get("type") or "").strip()))
    if not t:
        return None
    num = data.get("number")
    if t in _NUMBERED and num not in (None, "", "null"):
        try:
            num = int(num)
        except (TypeError, ValueError):
            num = None
    else:
        num = None
    return {"instrument_type": t, "number": num}
