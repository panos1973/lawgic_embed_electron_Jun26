"""amend.py — amendment detection, target resolution, and consolidation.

Three deterministic steps:

  1. detect — find each canonical amending verb (αντικαθίσταται, προστίθεται,
     καταργείται, ...) inside a provision.
  2. resolve — parse the Greek nested-genitive reference that precedes the verb
     ("Η παρ. 2 του άρθρου 24 του ν. 4675/2024 ...") into a target canonical_id
     plus a scope (document | article | paragraph | case), and capture the quoted
     replacement text. Produces an AmendmentOp per edit.
  3. consolidate — for edits whose target lives *in the same law* (corrigenda,
     self-replacements), rewrite that provision's text_in_force to the new text so
     what we store/serve is the in-force version (the non-negotiable). Edits that
     target *other* laws are recorded as resolved ops for the store-level
     cross-law consolidation pass (a single PDF does not contain the target law's
     text, so cross-law merge cannot happen here).

Reference grammar handled (closest reference to the verb wins):
    [Η/Το/Η περ. X] [της παρ. N] [του άρθρου M] [του ν./π.δ. NUM/YEAR]
Instrument is optional; when absent the edit is treated as targeting the current
law (self-amendment), with resolved=True only if at least an article was found.
"""
from __future__ import annotations

import re

from models import (Law, AmendmentOp, TYPE_NOMOS, TYPE_PD,
                    make_instrument_id, make_provision_id)
from normalize import fold_for_bm25

# Amending verb -> canonical op. Order matters only for reporting; each provision
# is scanned for every verb.
_VERB = {
    "replaces":     r"αντικαθίσται?ται|αντικαθίστανται",
    "adds":         r"προστίθε(?:ται|νται)",
    "repeals":      r"καταργ(?:είται|ούνται)",
    "renumbers":    r"αναριθμ(?:είται|ούνται)",
    "consolidates": r"διαμορφώνεται\s+ως\s+εξής",
    "modifies":     r"επέρχονται\s+οι\s+ακόλουθες\s+τροποποιήσεις|τροποποιείται",
}

# Reference sub-patterns (all optional except as required by scope logic).
_REF_CASE = re.compile(r"περ(?:ίπτωση|\.)\s*([α-ωΑ-Ω0-9]+)")
_REF_PAR = re.compile(r"παρ(?:άγραφος|αγράφου|αγράφων|\.|άγραφο)?\s*(\d+[α-ωΑ-Ω]?)")
_REF_ART = re.compile(r"άρθρ(?:ο|ου|α|ων)\s*(\d+[Α-Ωα-ω]?)")
_REF_INSTR = re.compile(
    r"(?P<type>ν\.|π\.?\s*δ\.|νόμ\w*|προεδρικ\w*)?\s*(?P<num>\d{1,5})\s*/\s*(?P<year>\d{4})")

# Quoted replacement text: Greek guillemets or straight/smart double quotes.
_QUOTED = re.compile(r"[«\"“](.+?)[»\"”]", re.DOTALL)

# How far back from the verb to look for the target reference.
_LOOKBACK = 240


def _resolve_reference(window: str, default_instrument: str) -> tuple[str, str, bool]:
    """Parse a reference window -> (target_canonical_id, scope, resolved).

    `default_instrument` is the current law's id, used when the reference names no
    instrument (self-amendment). scope is the deepest unit mentioned.
    """
    art = _REF_ART.search(window)
    par = _REF_PAR.search(window)
    case = _REF_CASE.search(window)
    instr = _REF_INSTR.search(window)

    if instr:
        t = (instr.group("type") or "").lower()
        itype = TYPE_PD if t.startswith("π") or t.startswith("προ") else TYPE_NOMOS
        instrument_id = make_instrument_id(itype, int(instr.group("num")),
                                           int(instr.group("year")))
    else:
        instrument_id = default_instrument

    if not art:
        # No article anchor -> document-level (e.g. whole-law repeal) or unresolved.
        scope = "document"
        resolved = bool(instr)
        return instrument_id, scope, resolved

    article = art.group(1)
    paragraph = par.group(1) if par else None
    target_id = make_provision_id(instrument_id, article, paragraph)
    if case:
        target_id += f".περ.{case.group(1)}"
        scope = "case"
    elif paragraph:
        scope = "paragraph"
    else:
        scope = "article"
    return target_id, scope, True


def extract_amendments(law: Law) -> Law:
    """Populate law.amendments with resolved AmendmentOps (no text mutation here)."""
    ordinal = 0
    for p in law.provisions:
        t = p.text_in_force
        for op, verb in _VERB.items():
            for vm in re.finditer(verb, t):
                ordinal += 1
                window = t[max(0, vm.start() - _LOOKBACK):vm.start()]
                target_id, scope, resolved = _resolve_reference(
                    window, law.instrument_id)
                # quoted replacement after the verb (for replaces/adds/consolidates)
                quoted = _QUOTED.search(t, vm.end())
                new_text = quoted.group(1).strip() if quoted else None
                law.amendments.append(AmendmentOp(
                    op=op, target_id=target_id, scope=scope,
                    new_text=new_text, resolved=resolved,
                    sub_edit_ordinal=str(ordinal)))
    return law


def consolidate(law: Law) -> Law:
    """Apply in-law edits to text_in_force so stored text is the in-force version.

    Only edits whose target_id resolves to a provision of THIS law are applied
    (replaces/consolidates with new text). Cross-law edits are left for the
    store-level consolidation pass. Idempotent: re-running on already-consolidated
    text is a no-op when the new text already matches.
    """
    by_id = {p.canonical_id: p for p in law.provisions}
    for op in law.amendments:
        if op.op not in ("replaces", "consolidates"):
            continue
        if not op.new_text or not op.resolved:
            continue
        target = by_id.get(op.target_id)
        if target is None:
            continue                      # external target -> store-level pass
        if target.text_in_force != op.new_text:
            target.text_as_enacted = target.text_as_enacted or target.text_in_force
            target.text_in_force = op.new_text
            target.text_normalized = fold_for_bm25(op.new_text)
            target.version += 1
            target.amended_by = list(dict.fromkeys(
                target.amended_by + [op.sub_edit_ordinal or "in-law"]))
    return law
