"""amend_llm.py — LLM-assisted amendment extraction (provider-agnostic).

Why this exists: the deterministic amend.py is conservative and misses the messy
real morphology of Greek amendment clauses (inflected, nested, multi-target). The
old system's good amendment edges came from an LLM (Gemini); this is the same
idea wired to the configured provider (DeepSeek V4 Pro by default) — but with two
upgrades over the old edges:
  * it CAPTURES new_text (the «...» replacement block the old schema lacked), and
  * it GUARDS against source==target self-loops that polluted the old data.

The prompt is grounded in the morphology actually seen in FEK A amending laws:

  αντικαθίσταται / αντικαθίστανται / αντικαθίσταται και διαμορφώνεται ως εξής: «...»
  στο τέλος του άρθρου 13, μετά την παρ. 17, προστίθενται παράγραφοι 18 έως 22: «...»
  μετά το προηγούμενο εδάφιο, προστίθεται περ. ββ΄, ως εξής: «...»
  Η περ. α΄ της παρ. 1 του άρθρου 60 του ν. 4172/2013 αντικαθίσταται ως εξής: «...»
  Οι παρ. 8 και 9 του άρθρου 64 του ν. 4172/2013 αντικαθίστανται ως εξής: «...»

Cost control: only provisions whose text contains an amendment verb are sent to
the model (cheap pre-filter), and the SYSTEM prompt is a stable prefix so the
provider caches it across calls.
"""
from __future__ import annotations

import json
from typing import Callable, Optional

import config
from models import (AmendmentOp, Law, make_provision_id, make_instrument_id,
                    TYPE_NOMOS, TYPE_PD)
from pipeline.amend import _clean_amendments
import logsetup

log = logsetup.get("amend_llm")

# cheap gate: a provision only goes to the LLM if it contains an amendment verb.
_AMEND_STEMS = ("αντικαθίστ", "προστίθ", "καταργ", "τροποποι", "διαγράφ",
                "αναριθμ", "διαμορφών", "αντικαταστ")

_ACTIONS = {"replaces", "adds", "repeals", "amends", "modifies",
            "renumbers", "consolidates", "none"}
_SCOPES = {"document", "article", "paragraph", "case", "subcase", "phrase"}

# STABLE prefix — byte-identical across calls so the provider caches it.
_SYSTEM = (
    "You extract Greek legislative amendment operations from one statutory "
    "provision. Greek laws amend others with verbs like αντικαθίσταται/"
    "αντικαθίστανται (replace), προστίθεται/προστίθενται (add), καταργείται "
    "(repeal), τροποποιείται (amend), αναριθμείται (renumber), and the "
    "replacement/added wording follows 'ως εξής:' inside « » guillemets. "
    "Targets are written inflected and nested, e.g. 'Η περ. α΄ της παρ. 1 του "
    "άρθρου 60 του ν. 4172/2013'. Return ONLY JSON: "
    '{"amendments":[{'
    '"action":"replaces|adds|repeals|amends|modifies|renumbers|consolidates",'
    '"scope":"article|paragraph|case|subcase|phrase",'
    '"target_law_number":"4172/2013 or null if it amends the SAME law being enacted",'
    '"target_law_type":"law|pd  — pd if the cited instrument is a π.δ./προεδρικό '
    'διάταγμα, else law",'
    '"target_article_number":"e.g. 60",'
    '"target_paragraph":"e.g. 1 or null",'
    '"target_case":"e.g. α or null",'
    '"new_text":"the text inside « » verbatim, or empty",'
    '"position":"e.g. στο τέλος / μετά την παρ. 17, or empty"}]}. '
    "Rules: emit one object per distinct edit (split 'παρ. 8 και 9' into two). "
    "Set target_law_number to null ONLY when no external 'ν. ' is cited and the "
    "edit is to the same law. Never invent a target or new_text. If the provision "
    "makes no amendment, return {\"amendments\":[]}. No prose, JSON only."
)

# max_tokens is an OUTPUT CAP, not spend — a normal article generates far less, so
# raising it costs nothing for them. It must be big enough to echo a full «...»
# restatement verbatim: at 1500 a large block (e.g. ν.5086 art.34 -> ν.4368/2016
# #αρ.90.παρ.7.περ.γ, 6k+ chars) was truncated mid-string, json.loads failed, and
# the whole article's amendments were silently dropped.
_AMEND_MAX_TOKENS = 8000

# Salvage prompt: if the verbatim-echo call STILL truncates (an unusually large
# replacement block), re-ask for the edit STRUCTURE ONLY so the edge — action +
# target, the critical graph fact — is captured even when the full text won't fit.
# The replacement wording already lives in this provision's own chunk_text.
_SYSTEM_NOECHO = _SYSTEM + (
    " OVERRIDE: set every \"new_text\" to an empty string \"\" and do NOT echo the "
    "replacement wording — capture only action, scope and the target locator."
)


def _call_json(complete: Callable[..., str], system: str, text: str,
               max_tokens: int):
    """One LLM call -> parsed dict, or None when the JSON is unparseable (the
    truncation signature). complete()'s own exceptions (incl. SystemExit for a
    missing key) propagate so the caller can fall back."""
    out = complete(system, text, want_json=True, max_tokens=max_tokens)
    try:
        return json.loads(out)
    except Exception:                               # noqa: BLE001 — truncated/invalid
        return None



def _nz(v) -> str:
    """Normalize an LLM-supplied locator field to a clean string.

    The model sometimes echoes a JSON null as the literal text "null" (or "none"/
    "-"/"n/a"). Left unchecked, a target_case of "null" produces a malformed
    canonical id like 'ν.4763/2020#αρ.18.παρ.4.περ.null'. Treat those sentinels as
    empty so the locator is simply omitted.
    """
    s = (v or "").strip()
    return "" if s.lower() in ("null", "none", "n/a", "na", "-", "—", "nil") else s


def _norm_action(a: str) -> str:
    a = (a or "").strip().lower()
    return a if a in _ACTIONS else "amends"


def _norm_scope(s: str) -> str:
    s = (s or "").strip().lower()
    return s if s in _SCOPES else "article"


def _instrument_id(target_law: Optional[str], law_type: Optional[str],
                   own_id: str) -> str:
    """Build the target instrument id, honouring π.δ. vs ν. (default ν.).

    target_law=None -> in-law (the enacting law). Otherwise parse 'NUM/YEAR' and
    use make_instrument_id so a π.δ. target becomes 'π.δ.NUM/YEAR' not 'ν.NUM/YEAR'
    (the deterministic extractor's format — keeps canonical_ids consistent across
    both extractors).
    """
    if not target_law:
        return own_id
    num, _, year = target_law.partition("/")
    if not (num.isdigit() and year.isdigit()):
        return f"ν.{target_law}"                     # defensive: keep raw form
    itype = TYPE_PD if (law_type or "").strip().lower() in ("pd", "π.δ.", "πδ") \
        else TYPE_NOMOS
    return make_instrument_id(itype, int(num), int(year))


def _target_id(target_law: Optional[str], law_type: Optional[str], own_id: str,
               article: str, paragraph: Optional[str], case: Optional[str]) -> str:
    """Build the target canonical id, incl. π.δ. type and the case (.περ.) suffix."""
    if not article:
        return ""
    tid = make_provision_id(_instrument_id(target_law, law_type, own_id),
                            article, paragraph or None)
    if case:
        tid += f".περ.{case}"                        # matches amend.py format
    return tid


def extract_amendments_llm(law: Law,
                           complete: Optional[Callable[..., str]] = None) -> Law:
    """Populate law.amendments via the configured LLM. Falls back is the caller's
    job (a SystemExit from a missing key propagates so the orchestrator can switch
    to the deterministic extractor)."""
    if complete is None:
        import llm
        complete = llm.complete

    # record the real extractor on every op so the loader stops labelling LLM
    # edges as "pattern_matching" (the field is denormalized into Weaviate). Include
    # the model so successive re-embeds are distinguishable — provider alone made
    # DeepSeek V4 Pro and Flash both write "llm:deepseek", indistinguishable in the
    # store. -> "llm:deepseek:deepseek-v4-pro" / "llm:gemini:gemini-2.5-flash".
    model = config.LLM_MODEL or config.PROVIDERS[config.LLM_PROVIDER]["default_model"]
    method = f"llm:{config.LLM_PROVIDER}:{model}"
    own_number = law.instrument_id.split(".")[-1]   # e.g. ν.5090/2024 -> 5090/2024
    seen: dict[str, int] = {}                       # target_id -> next ordinal

    # Annex chunks are verbatim RATIFIED/ENACTED text (a treaty, a codified body, a
    # ratified concession contract). Their internal "Article X is replaced …" / «…
    # αντικαθίσταται …» lines are the instrument's OWN amendments, not amendments this
    # Greek law makes — mining them yields self-targeting false edges that pollute the
    # graph (ν.4368/2016's motorway-concession annex produced 53). Skip them, and the
    # cheap no-verb gate, BEFORE spending an LLM call.
    eligible = [p for p in law.provisions
                if p.chunk_type != "annex"
                and any(stem in (p.text_in_force or "") for stem in _AMEND_STEMS)]

    def _fetch(p):
        """One provision's amendment LLM call (with structure-only retry). Returns the
        parsed dict or None; raises SystemExit (no key) to the caller."""
        try:
            data = _call_json(complete, _SYSTEM, p.text_in_force or "", _AMEND_MAX_TOKENS)
            if data is None:                        # truncated/invalid JSON
                log.warning("amend_llm %s art %s: JSON unparseable (a replacement block "
                            "likely overran the token cap); retrying structure-only so "
                            "the edge is not lost", law.instrument_id, p.article_no)
                data = _call_json(complete, _SYSTEM_NOECHO, p.text_in_force or "", 1500)
            return data
        except SystemExit:
            raise                                   # no key -> let caller fall back
        except Exception as e:                      # noqa: BLE001
            log.warning("amend extract failed on %s art %s: %s",
                        law.instrument_id, p.article_no, e)
            return None

    # Fan the (independent, I/O-bound) LLM calls out across workers — the slow part —
    # but BUILD the ops SERIALLY below, in provision order, so the per-target
    # sub-edit ordinals stay deterministic + idempotent across runs.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    conc = max(1, getattr(config, "LLM_CONCURRENCY", 8))
    fetched: dict = {}
    if eligible:
        with ThreadPoolExecutor(max_workers=min(conc, len(eligible))) as ex:
            futs = {ex.submit(_fetch, p): i for i, p in enumerate(eligible)}
            for fut in as_completed(futs):
                fetched[futs[fut]] = fut.result()   # keyed by index; SystemExit propagates

    for i, p in enumerate(eligible):                # serial, deterministic order
        data = fetched.get(i)
        if data is None:                            # echo + structure-only both failed
            continue
        for a in (data.get("amendments") or []):
            if not isinstance(a, dict):
                continue
            target_law = _nz(a.get("target_law_number")) or None
            article = _nz(a.get("target_article_number"))
            new_text = (a.get("new_text") or "").strip()
            # fabrication guard: need at least a target article or quoted text
            if not article and not new_text:
                continue
            # self-loop guard: a cross-law edit must not point at the enacting law
            if target_law and target_law == own_number:
                target_law = None                   # treat as in-law, not a self-loop

            paragraph = _nz(a.get("target_paragraph")) or None
            case = _nz(a.get("target_case")) or None
            law_type = a.get("target_law_type")
            tid = _target_id(target_law, law_type, law.instrument_id,
                             article, paragraph, case)
            ordinal = seen.get(tid, 0)
            seen[tid] = ordinal + 1

            law.amendments.append(AmendmentOp(
                op=_norm_action(a.get("action")),
                scope=_norm_scope(a.get("scope")),
                target_id=tid,
                new_text=new_text or None,
                effective_date=law.fek_date or None,
                sub_edit_ordinal=str(ordinal),
                resolved=bool(target_law is None),  # in-law targets resolve locally
                extraction_method=method,
                source_id=p.canonical_id,           # WHICH article of THIS (new) law
            ))                                       # made the edit -> source_*_number
    # Same hygiene pass the deterministic extractor applies: drop heading-only /
    # empty edits, self-document dumps, unresolved fragments, and exact dupes, so
    # both extractors emit the clean edge set consolidate()/the loader expect.
    law.amendments = _clean_amendments(law.amendments, law.instrument_id)
    log.info("amend_llm %s: %d op(s)", law.instrument_id, len(law.amendments))
    return law
