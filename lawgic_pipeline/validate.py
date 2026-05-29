"""validate.py — offline accuracy harness for the extraction → segmentation →
amendment → classification stages.

Runs the *deterministic* half of the pipeline (everything before embed/load) over
a folder of real FEK PDFs and emits a structured per-document report plus an
aggregate summary. No Voyage, no Weaviate, no network, no API keys — so it can be
pointed at real gazette PDFs to find where the parsers break before anything is
embedded or loaded.

Each document is scored against deterministic quality heuristics (not ground
truth — we have none yet), surfaced as `flags`. Flags are signals to eyeball,
not hard failures: e.g. masthead unidentified, zero provisions, suspiciously
short provision bodies, unresolved amendment targets, mojibake/homoglyph residue.

Usage:
    python validate.py <folder> [--json] [--no-azure] [--limit N]

    --json      one JSON object per document + a final summary object (for tooling)
    --no-azure  force pdfplumber-only extraction even if DI is configured
    --limit N   process at most N PDFs (quick smoke run)

Exit code is 0 when every document was at least *identified* (masthead resolved),
1 otherwise — so CI can gate on "the parser still recognises real FEKs".
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

from normalize import normalize_display
import pipeline.extract as extract
import pipeline.segment as segment
import pipeline.enrich as enrich
import pipeline.amend as amend
import pipeline.delegate as delegate
import pipeline.refs as refs
from models import Law, make_instrument_id, make_instrument_key

# A provision body shorter than this is suspicious (segmentation likely split on a
# false anchor — e.g. the word "Άρθρο" inside a sentence).
_MIN_BODY_CHARS = 25
# Latin letters surrounded by Greek text suggest homoglyph/OCR residue.
_LATIN_IN_GREEK = re.compile(r"[Α-Ωα-ω][A-Za-z]|[A-Za-z][Α-Ωα-ω]")


def _quality_flags(law: Law, ex_warnings: list[str], identified: bool) -> list[str]:
    flags: list[str] = []
    if not identified:
        flags.append("masthead_unidentified")

    arts = [p for p in law.provisions if p.chunk_type == "article"]
    if not arts:
        flags.append("no_articles")

    # non-monotonic / duplicate article numbers hint at mis-segmentation
    nums = [p.article_no for p in arts]
    if len(nums) != len(set(nums)):
        flags.append("duplicate_article_numbers")

    short = sum(1 for p in arts if len(p.text_in_force) < _MIN_BODY_CHARS)
    if arts and short / len(arts) > 0.3:
        flags.append(f"many_short_bodies({short}/{len(arts)})")

    # homoglyph / mojibake residue in the body of the first few provisions
    sample = " ".join(p.text_in_force for p in arts[:5])
    if _LATIN_IN_GREEK.search(sample):
        flags.append("possible_homoglyph_residue")

    unresolved = sum(1 for op in law.amendments if not op.resolved)
    if unresolved:
        flags.append(f"unresolved_amendments({unresolved}/{len(law.amendments)})")

    # propagate the most relevant extract/masthead warnings
    if any("Azure DI" in w for w in ex_warnings):
        flags.append("azure_di_degraded")
    if any("scanned" in w or "mixed" in w for w in ex_warnings):
        flags.append("not_clean_text_layer")
    return flags


def validate_pdf(path: str, use_azure: bool = True) -> dict:
    """Run the offline spine on one PDF and return a report dict (never raises)."""
    rec: dict = {"file": os.path.basename(path)}
    try:
        ex = extract.extract_pdf(path, use_azure=use_azure)
    except Exception as e:  # noqa: BLE001
        rec.update(status="extract_error", error=f"{type(e).__name__}: {e}")
        return rec

    mh = ex.masthead or {}
    itype, number, year = mh.get("instrument_type"), mh.get("number"), mh.get("year")
    identified = bool(itype) and number is not None and year is not None

    rec.update(
        classification=ex.classification,
        table_pages=ex.table_pages,
        instrument_type=itype, number=number, year=year,
        fek=f"{mh.get('fek_series','')} {mh.get('fek_number','')} "
            f"{mh.get('fek_date') or ''}".strip(),
        title=(mh.get("title") or "")[:120],
    )

    if not identified:
        rec.update(status="review", instrument_id=None, provisions=0, annexes=0,
                   amendments=0, flags=_quality_flags(Law("", "", ""), ex.warnings, False))
        return rec

    text = normalize_display(ex.text)
    law = Law(instrument_id=make_instrument_id(itype, number, year),
              instrument_key=make_instrument_key(itype, number, year),
              instrument_type=itype, title=mh.get("title", ""),
              fek_series=mh.get("fek_series", ""), fek_number=mh.get("fek_number", ""),
              fek_date=mh.get("fek_date") or "")
    law = segment.segment(text, law)
    law = amend.extract_amendments(law)
    law = amend.consolidate(law)
    law = delegate.extract_delegations(law)
    law = refs.extract_external_refs(law)
    law = enrich.classify_domain(law)

    arts = [p for p in law.provisions if p.chunk_type == "article"]
    annexes = [p for p in law.provisions if p.chunk_type == "annex"]
    domains = sorted({d for p in arts for d in p.legal_domain})
    rec.update(
        status="ok",
        instrument_id=law.instrument_id,
        provisions=len(arts),
        annexes=len(annexes),
        amendments=len(law.amendments),
        resolved_amendments=sum(1 for op in law.amendments if op.resolved),
        delegations=len(law.delegations),
        domains=domains,
        flags=_quality_flags(law, ex.warnings, True),
    )
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline FEK extraction accuracy harness")
    ap.add_argument("folder")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-azure", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    pdfs = sorted(glob.glob(os.path.join(args.folder, "**", "*.pdf"), recursive=True))
    if args.limit:
        pdfs = pdfs[:args.limit]

    reports = []
    for path in pdfs:
        rec = validate_pdf(path, use_azure=not args.no_azure)
        reports.append(rec)
        if args.json:
            sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        else:
            _print_human(rec)

    n = len(reports)
    identified = sum(1 for r in reports if r.get("status") in ("ok",))
    flagged = sum(1 for r in reports if r.get("flags"))
    errored = sum(1 for r in reports if r.get("status") == "extract_error")
    total_prov = sum(r.get("provisions", 0) for r in reports)
    summary = {"type": "summary", "documents": n, "identified": identified,
               "review": n - identified - errored, "extract_errors": errored,
               "flagged": flagged, "total_provisions": total_prov}
    if args.json:
        sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")
    else:
        print(f"\n{'='*60}")
        print(f"{n} documents | identified={identified} review={summary['review']} "
              f"extract_errors={errored} | flagged={flagged} | "
              f"provisions={total_prov}")

    # gate: every processable doc must at least be identified
    return 0 if (identified + errored == n or identified == n) else 1


def _print_human(rec: dict) -> None:
    status = rec.get("status")
    head = f"[{status:13}] {rec['file']}"
    if status == "extract_error":
        print(f"{head}  ! {rec.get('error')}")
        return
    print(f"{head}  {rec.get('instrument_id') or '(unidentified)':<14} "
          f"class={rec.get('classification')} "
          f"prov={rec.get('provisions', 0)} annex={rec.get('annexes', 0)} "
          f"amend={rec.get('amendments', 0)}")
    if rec.get("title"):
        print(f"                 title: {rec['title']}")
    if rec.get("domains"):
        print(f"                 domains: {', '.join(rec['domains'])}")
    if rec.get("flags"):
        print(f"                 ⚑ {', '.join(rec['flags'])}")


if __name__ == "__main__":
    sys.exit(main())
