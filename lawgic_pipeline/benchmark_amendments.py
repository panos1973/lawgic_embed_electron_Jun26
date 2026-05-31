"""benchmark_amendments.py — score the amendment extractor against gold edges.

Answers the question "are the amendments good enough?" empirically, on real data,
before committing to a full re-embed. It runs the configured extractor over each
source law and diffs the predicted edges against a gold file (e.g. the existing
Gemini-extracted edges), reporting precision / recall / F1 plus new_text coverage.

Usage:
    # deterministic (no key needed):
    python benchmark_amendments.py --gold gold_amendments.sample.json --laws-dir ./goldlaws

    # LLM extractor (set provider + key first, e.g. DeepSeek V4 Pro non-thinking):
    AMEND_EXTRACTOR=llm LLM_PROVIDER=deepseek DEEPSEEK_API_KEY=... \
      python benchmark_amendments.py --gold gold_amendments.sample.json \
        --laws-dir ./goldlaws --extractor llm

Each source law in the gold file is matched to a text in --laws-dir named
'<source_law>.pdf' or '<source_law>.txt' (slashes -> underscores, e.g.
'104_2018.txt'). Laws without a matching text are skipped (and reported).

A predicted edge matches a gold edge when (target_law, target_article, action)
agree after normalization. This isolates extraction quality from downstream
resolution.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import config
from models import Law, TYPE_NOMOS
import pipeline.segment as segment


def _safe(name: str) -> str:
    return name.replace("/", "_")


def _load_text(laws_dir: str, source_law: str) -> str | None:
    base = _safe(source_law)
    txt = os.path.join(laws_dir, base + ".txt")
    pdf = os.path.join(laws_dir, base + ".pdf")
    if os.path.exists(txt):
        return open(txt, encoding="utf-8").read()
    if os.path.exists(pdf):
        import pdfplumber
        with pdfplumber.open(pdf) as p:
            return "\n".join((pg.extract_text() or "") for pg in p.pages)
    return None


def _norm_law(s: str) -> str:
    """'4172/2013' or '4172' -> '4172'; keeps the numeric core for matching."""
    s = (s or "").strip()
    m = re.match(r"(\d+)", s)
    return m.group(1) if m else s


def _key(edge: dict) -> tuple:
    return (_norm_law(edge.get("target_law", edge.get("target_law_number", ""))),
            (edge.get("target_article", edge.get("target_article_number", "")) or "").strip(),
            (edge.get("action", edge.get("op", "")) or "").strip().lower())


def _predict(source_law: str, text: str, extractor: str) -> list[dict]:
    law = Law(instrument_id=f"ν.{source_law}", instrument_key="N",
              instrument_type=TYPE_NOMOS, title="", fek_date="")
    law = segment.segment(text, law)
    if extractor == "llm":
        import pipeline.amend_llm as amend_llm
        law = amend_llm.extract_amendments_llm(law)
    else:
        import pipeline.amend as amend
        law = amend.extract_amendments(law)
    out = []
    for op in law.amendments:
        # target_id like 'ν.3852/2010#αρ.140.παρ.x' -> recover law + article
        tl = re.search(r"ν\.([\d/]+)#", op.target_id or "")
        ta = re.search(r"#αρ\.(\d+[Α-Ωα-ω]?)", op.target_id or "")
        out.append({"target_law": tl.group(1) if tl else "",
                    "target_article": ta.group(1) if ta else "",
                    "action": op.op, "new_text": op.new_text})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--laws-dir", required=True)
    ap.add_argument("--extractor", choices=["deterministic", "llm"],
                    default=config.AMEND_EXTRACTOR)
    args = ap.parse_args()

    gold = {k: v for k, v in json.load(open(args.gold, encoding="utf-8")).items()
            if not k.startswith("_")}

    tp = fp = fn = 0
    pred_total = newtext = 0
    scored, skipped = [], []

    for source_law, gold_edges in gold.items():
        text = _load_text(args.laws_dir, source_law)
        if not text:
            skipped.append(source_law)
            continue
        preds = _predict(source_law, text, args.extractor)
        pred_total += len(preds)
        newtext += sum(1 for e in preds if (e.get("new_text") or "").strip())

        gold_keys = {_key(e) for e in gold_edges}
        pred_keys = {_key(e) for e in preds}
        law_tp = len(gold_keys & pred_keys)
        tp += law_tp
        fp += len(pred_keys - gold_keys)
        fn += len(gold_keys - pred_keys)
        scored.append((source_law, law_tp, len(gold_keys), len(pred_keys)))

    print(f"\nExtractor: {args.extractor}")
    print(f"Scored laws: {len(scored)}   Skipped (no text): {skipped or 'none'}")
    for sl, t, g, p in scored:
        print(f"  {sl:<12} matched {t}/{g} gold   (predicted {p})")
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    cov = newtext / pred_total if pred_total else 0.0
    print(f"\nPrecision {prec:.2f}  Recall {rec:.2f}  F1 {f1:.2f}")
    print(f"new_text coverage: {cov:.0%} of predicted edges carry replacement text")
    if not scored:
        print("\n(no source-law texts found in --laws-dir; drop them in to score)")


if __name__ == "__main__":
    main()
