"""orchestrator.py — runs the pipeline per document, updates state, emits progress.

Stage order: extract -> normalize -> segment -> classify -> amend -> embed -> load.
Hard stages (extract full, enrich LLM, amend resolution) currently raise/stub;
the spine, state tracking and idempotency are real.
"""
from __future__ import annotations
import hashlib
import os
from typing import Callable, Optional

import config
from state import State
from models import (Law, make_instrument_id, make_instrument_key,
                    make_decision_id, make_decision_key)
from normalize import normalize_display
import pipeline.extract as extract
import pipeline.segment as segment
import pipeline.enrich as enrich
import pipeline.amend as amend
import pipeline.delegate as delegate
import pipeline.multiact as multiact
import pipeline.refs as refs
import voyage_embed as ve
import weaviate_io as wio
import logsetup

log = logsetup.get("orchestrator")


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_law(seg, mh) -> Optional[Law]:
    """Construct an identified Law from an act segment + the gazette masthead.

    Returns None when the act cannot be given a stable canonical id (so the
    caller routes it to review rather than inventing a colliding placeholder).
    """
    year = mh.get("year")
    if seg.is_decision:
        series = mh.get("fek_series") or ""
        fek_no = mh.get("fek_number") or ""
        if not (series and fek_no and year and seg.instrument_type):
            return None
        iid = make_decision_id(series, fek_no, year, seg.item)
        ikey = make_decision_key(series, fek_no, year, seg.item)
    else:
        itype, number = seg.instrument_type, mh.get("number")
        if not itype or number is None or year is None:
            return None
        iid = make_instrument_id(itype, number, year)
        ikey = make_instrument_key(itype, number, year)
    return Law(instrument_id=iid, instrument_key=ikey,
               instrument_type=seg.instrument_type,
               jurisdiction=config.DEFAULT_TENANT,
               title=seg.title or mh.get("title", ""),
               fek_series=mh.get("fek_series", ""),
               fek_number=mh.get("fek_number", ""),
               fek_date=mh.get("fek_date") or "")


def _process_act(client, seg, mh, emit=lambda *a: None) -> tuple[str, Optional[Law]]:
    """Run the full per-instrument spine for one act. Returns (status, law)."""
    law = _build_law(seg, mh)
    if law is None:
        return "review", None
    law = segment.segment(seg.text, law)
    # Amend BEFORE classify/embed: consolidation rewrites text_in_force to the
    # in-force version, which is what domain signal, summary and vectors must use.
    law = amend.extract_amendments(law)
    law = amend.consolidate(law)
    law = delegate.extract_delegations(law, full_text=seg.text)
    law = refs.extract_external_refs(law)
    emit("classify")
    law = enrich.classify_domain(law)
    law = enrich.classify_document_category(law)       # function taxonomy (deterministic)
    law = enrich.classify_dkn(law)                     # ΔΚΝ/Ραπτάρχης volumes (deterministic)
    law = enrich.enrich_llm(law)                       # no-op without LLM key; merges extra dkn
    chunks = law.ordered_texts()
    emit("embed", f"{law.instrument_id}: embedding {len(chunks)} chunk(s)")
    log.info("embed %s: %d chunk(s)", law.instrument_id, len(chunks))
    vectors = ve.embed_law_chunks(chunks, progress=lambda m: emit("embed", m))
    emit("load")
    wio.load_document(client, law)
    wio.load_law(client, law, vectors)
    wio.load_amendments(client, law.amendments, source_law=law)
    wio.load_delegations(client, law.delegations, source_law=law)
    return "done", law


def process_document(client, st: State, path: str,
                     progress: Optional[Callable[[str, str], None]] = None) -> str:
    """Returns final status: done | review | error. progress(stage, msg) optional."""
    def emit(stage, msg=""):
        if progress:
            progress(stage, msg)

    chash = _hash_file(path)
    doc_id = os.path.basename(path)
    log.info("process start: %s", doc_id)
    st.upsert(doc_id, path, chash)
    if st.seen_hash(chash):
        st.set_status(doc_id, "done", stage="dedup")
        emit("dedup", "unchanged — skipped")
        log.info("dedup skip (unchanged): %s", doc_id)
        return "done"

    try:
        st.set_status(doc_id, "processing", stage="extract")
        emit("extract")
        ex = extract.extract_pdf(path)
        text = normalize_display(ex.text)
        mh = ex.masthead or {}

        # One gazette PDF may contain N instruments: primary legislation is a
        # single act, but a decision issue (any Β΄, or an Α΄ ministerial section)
        # bundles several, each split out and identified by its issuer.
        emit("segment")
        acts = multiact.split_acts(text, mh)
        if not acts:
            reason = ("could not identify any instrument from masthead "
                      f"(type={mh.get('instrument_type')}, "
                      f"number={mh.get('number')}, year={mh.get('year')})")
            st.set_status(doc_id, "review", stage="extract", error=reason)
            emit("review", reason)
            return "review"

        # Process each act through the full spine. Amend runs before embed so the
        # consolidated (in-force) text is what gets vectorised.
        emit("amend")
        done, review, total_prov = 0, 0, 0
        for seg in acts:
            status, law = _process_act(client, seg, mh, emit)
            if status == "done":
                done += 1
                total_prov += len(law.provisions)
            else:
                review += 1

        if done == 0:
            reason = f"{review} act(s) could not be identified for ingestion"
            st.set_status(doc_id, "review", stage="load", error=reason)
            emit("review", reason)
            return "review"

        emit("done", f"{done} instrument(s), {total_prov} provisions"
                     + (f", {review} to review" if review else ""))
        st.set_status(doc_id, "done", stage="load",
                      confidence=1.0 if review == 0 else 0.8)
        log.info("process done: %s — %d instrument(s), %d provisions, %d review",
                 doc_id, done, total_prov, review)
        return "done"

    except NotImplementedError as e:
        st.set_status(doc_id, "review", stage="extract", error=str(e))
        emit("review", str(e))
        log.warning("process review: %s — %s", doc_id, e)
        return "review"
    except Exception as e:
        st.set_status(doc_id, "error", error=str(e))
        emit("error", str(e))
        log.exception("process error: %s — %s", doc_id, e)
        return "error"
