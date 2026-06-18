"""orchestrator.py — runs the pipeline per document, updates state, emits progress.

Stage order: extract -> normalize -> segment -> classify -> amend -> embed -> load.
Hard stages (extract full, enrich LLM, amend resolution) currently raise/stub;
the spine, state tracking and idempotency are real.
"""
from __future__ import annotations
import hashlib
import os
from collections import Counter
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
from errors import FatalIngestError, looks_fatal, as_fatal  # noqa: F401 (as_fatal used by cli)

log = logsetup.get("orchestrator")


def _stage(provider: str, stage: str, fn):
    """Run one external-call stage. Convert a credential/endpoint failure into a
    FatalIngestError (which stops the whole run) while letting per-document and
    transient errors propagate unchanged."""
    try:
        return fn()
    except FatalIngestError:
        raise
    except SystemExit as e:               # config.require(): a REQUIRED key is missing
        raise FatalIngestError(provider, stage, str(e)) from e
    except Exception as e:                # noqa: BLE001
        if looks_fatal(e):
            raise FatalIngestError(provider, stage, f"{type(e).__name__}: {e}") from e
        raise


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
        # say WHY it could not be identified — the missing field is what the
        # operator needs to see in the log (vs a bare "could not be identified").
        emit("review", "act not identified — "
             f"type={seg.instrument_type or mh.get('instrument_type')}, "
             f"number={mh.get('number')}, year={mh.get('year')}, "
             f"FEK={mh.get('fek_series') or '?'}{mh.get('fek_number') or '?'}")
        return "review", None
    law = segment.segment(seg.text, law)
    _ct = Counter(p.chunk_type for p in law.provisions)
    emit("segment", f"{law.instrument_id}: {len(law.provisions)} provision(s)"
         + (" — " + ", ".join(f"{n} {t}" for t, n in _ct.items()) if _ct else ""))
    # Amend BEFORE classify/embed: consolidation rewrites text_in_force to the
    # in-force version, which is what domain signal, summary and vectors must use.
    if config.AMEND_EXTRACTOR == "llm":
        try:
            import pipeline.amend_llm as amend_llm
            law = amend_llm.extract_amendments_llm(law)
        except SystemExit as e:
            # no LLM key (or provider misconfig) -> deterministic, but say so:
            # otherwise the operator thinks the LLM extractor ran when it didn't.
            log.warning("AMEND_EXTRACTOR=llm but LLM unavailable (%s); "
                        "falling back to deterministic extractor for %s",
                        e, law.instrument_id)
            law = amend.extract_amendments(law)
        except Exception as e:            # noqa: BLE001
            # a WRONG key/endpoint (vs a missing one) recurs on every file -> stop.
            if looks_fatal(e):
                raise FatalIngestError(f"LLM ({config.LLM_PROVIDER})", "amend",
                                       f"{type(e).__name__}: {e}") from e
            raise
    else:
        law = amend.extract_amendments(law)
    law = amend.consolidate(law)
    law = delegate.extract_delegations(law, full_text=seg.text)
    law = refs.extract_external_refs(law)
    emit("amend", f"{law.instrument_id}: {len(law.amendments)} amendment edge(s), "
         f"{len(law.delegations)} delegation(s)")
    emit("classify")
    law = enrich.classify_domain(law)
    law = enrich.classify_document_category(law)       # function taxonomy (deterministic)
    law = enrich.classify_dkn(law)                     # ΔΚΝ/Ραπτάρχης volumes (deterministic)
    _domains = sorted({d for p in law.provisions for d in (p.legal_domain or [])})
    emit("classify", f"{law.instrument_id}: {law.document_category or 'uncategorized'}"
         + (f" · {', '.join(_domains)}" if _domains else ""))
    # enrich_llm is one LLM call per provision — the slowest stage on a long law.
    # Emit per-provision progress so the UI never looks frozen here.
    emit("enrich", f"{law.instrument_id}: LLM enrichment ({len(law.provisions)} provisions)")
    law = _stage(f"LLM ({config.LLM_PROVIDER})", "enrich (summaries)",
                 lambda: enrich.enrich_llm(law, progress=lambda m: emit("enrich", m)))
    # whole-law overview synthesized from the article summaries -> document node only
    # (not per-chunk vectors). Cheap (one small call); deterministic fallback w/o key.
    law = _stage(f"LLM ({config.LLM_PROVIDER})", "summarize (document)",
                 lambda: enrich.summarize_law(law))
    chunks = law.ordered_texts()
    emit("embed", f"{law.instrument_id}: embedding {len(chunks)} chunk(s)")
    log.info("embed %s: %d chunk(s)", law.instrument_id, len(chunks))
    vectors = _stage("Voyage", "embed",
                     lambda: ve.embed_law_chunks(chunks, progress=lambda m: emit("embed", m)))
    emit("load")

    def _load():
        wio.load_document(client, law)
        wio.load_law(client, law, vectors)
        wio.load_amendments(client, law.amendments, source_law=law)
        wio.load_delegations(client, law.delegations, source_law=law)
    _stage("Weaviate", "load", _load)
    emit("load", f"{law.instrument_id}: document + {len(law.provisions)} chunk(s)"
         + (f" + {len(law.amendments)} amendment(s)" if law.amendments else "")
         + (f" + {len(law.delegations)} delegation(s)" if law.delegations else ""))
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
    # Atomically claim the doc: returns False if already done (dedup/resume) or
    # being processed by another worker — so the parallel pool never double-embeds
    # one law. claim() also marks it 'processing'.
    if not st.claim(doc_id, path, chash):
        emit("dedup", "unchanged or already in progress — skipped")
        log.info("dedup/claim skip: %s", doc_id)
        return "done"

    try:
        st.set_status(doc_id, "processing", stage="extract")
        emit("extract")
        ex = extract.extract_pdf(path)
        text = normalize_display(ex.text)
        mh = ex.masthead or {}
        # detail: how the PDF came in (text/scanned/mixed, pages, OCR) + what the
        # masthead resolved to — the line that makes a mis-identification obvious.
        emit("extract", f"{ex.classification}, {len(ex.pages_markdown)} page(s)"
             + (f", {len(ex.ocr_pages)} OCR" if ex.ocr_pages else "")
             + (f", {len(ex.table_pages)} table" if ex.table_pages else "")
             + f" · masthead: {mh.get('instrument_type') or 'unknown'}"
             + (f" {mh.get('number')}" if mh.get('number') else "")
             + f", FEK {mh.get('fek_series') or '?'} {mh.get('fek_number') or '?'}"
             + f"/{mh.get('year') or '?'}")
        for w in (ex.warnings or [])[:3]:
            emit("extract", f"warning: {w}")

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
        emit("segment", f"{len(acts)} act(s) identified")

        # Process each act through the full spine. Amend runs before embed so the
        # consolidated (in-force) text is what gets vectorised. Per-act detail
        # (provisions, edges, classification, load) is emitted inside _process_act.
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

    except FatalIngestError as e:
        # Credential/endpoint failure: release this doc back to 'pending' so a resume
        # retries it after the fix, and propagate so the BATCH stops here instead of
        # marking every remaining file 'error'.
        st.set_status(doc_id, "pending", error=str(e))
        emit("paused", str(e))
        log.error("process PAUSED (fatal): %s — %s", doc_id, e)
        raise
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
