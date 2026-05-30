"""sidecar/pdf_extract.py — pdfplumber detector (real implementation).

Two jobs, both cheap and deterministic:
  1. Classify the document: text | scanned | mixed (drives whether the caller
     needs OCR / whole-doc Azure DI).
  2. Per page: pull the text layer (reading order) and flag pages that contain
     tables, so the caller can send *only those pages* to Azure DI for a
     high-fidelity table render (the "table-page upgrade" technique).

Usable two ways:
  - in-process:  from sidecar.pdf_extract import detect; detect(path)
  - subprocess:  python -m sidecar.pdf_extract <path>   (prints JSON on stdout)

Output shape (stable contract):
  {
    "markdown": str,                         # whole-doc text, pages joined
    "pdf_classification": "text|scanned|mixed",
    "pages_meta": [{"page": int, "markdown": str, "has_tables": bool,
                    "char_count": int}],
    "table_pages": [int, ...],               # 1-based page numbers with tables
    "warnings": [str, ...],
  }
"""
from __future__ import annotations

import json
import sys

# A page with at least this many extracted characters is considered to have a
# real text layer (vs. a scanned image with only noise / nothing).
_TEXT_CHAR_THRESHOLD = 80


def _line_text(words: list[dict]) -> str:
    """Join words on one visual line (already x-sorted) into a string."""
    return " ".join(w["text"] for w in words)


def _group_lines(words: list[dict], ytol: float = 3.0) -> list[str]:
    """Group words into visual lines by 'top', preserving reading order."""
    ws = sorted(words, key=lambda w: (round(w["top"] / ytol), w["x0"]))
    lines, cur, cur_top = [], [], None
    for w in ws:
        if cur_top is None or abs(w["top"] - cur_top) <= ytol:
            cur.append(w)
            cur_top = w["top"] if cur_top is None else cur_top
        else:
            lines.append(_line_text(sorted(cur, key=lambda x: x["x0"])))
            cur, cur_top = [w], w["top"]
    if cur:
        lines.append(_line_text(sorted(cur, key=lambda x: x["x0"])))
    return lines


def _column_text(page) -> str:
    """Reading-order text with two-column reconstruction when a gutter exists.

    FEK body pages are symmetric two-column; pdfplumber's extract_text() reads
    line-by-line and so interleaves the columns. We detect a central gutter (a
    vertical band that almost no word crosses) and, when found, emit the whole
    left column then the whole right column. Full-width lines (mastheads, titles,
    tables) keep ~all words crossing the gutter, so such pages stay single-flow.
    """
    try:
        words = page.extract_words(use_text_flow=False)
    except Exception:
        return page.extract_text() or ""
    if not words:
        return page.extract_text() or ""
    g = page.width / 2.0
    crossing = sum(1 for w in words if w["x0"] < g < w["x1"])
    left = [w for w in words if w["x1"] <= g]
    right = [w for w in words if w["x0"] >= g]
    # Two-column only if the gutter is genuinely clear and both sides populated.
    if (crossing / len(words) < 0.04 and len(left) >= 8 and len(right) >= 8):
        return "\n".join(_group_lines(left) + _group_lines(right))
    return page.extract_text() or ""
    """Render a pdfplumber table (list of row-lists) as a GitHub markdown table.

    pdfplumber yields None for empty cells; normalize to "" and collapse internal
    newlines so the row stays on one markdown line.
    """
    def cell(x):
        return (str(x) if x is not None else "").replace("\n", " ").strip()

    rows = [r for r in rows if r]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [[cell(c) for c in r] + [""] * (width - len(r)) for r in rows]
    header, body = norm[0], norm[1:]
    out = ["| " + " | ".join(header) + " |",
           "| " + " | ".join(["---"] * width) + " |"]
    for r in body:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def detect(path: str) -> dict:
    """Classify a PDF and return per-page text + table-page map (see module doc)."""
    import pdfplumber

    pages_meta: list[dict] = []
    table_pages: list[int] = []
    ocr_pages: list[int] = []
    warnings: list[str] = []
    text_pages = 0

    try:
        from pipeline.normalize import normalize_glyphs
        from pipeline.quality import score_text
    except Exception:  # noqa: BLE001 — sidecar must run even if import path differs
        normalize_glyphs = lambda t: t            # noqa: E731
        score_text = None                         # noqa: E731

    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            try:
                raw = _column_text(page)
            except Exception as e:  # noqa: BLE001 — one bad page must not abort
                raw = ""
                warnings.append(f"page {i}: text extraction failed ({e})")

            # Multi-signal quality gate (CID, garbled-Greek, box glyphs, '?' garble).
            # Score the RAW page text — before normalize_glyphs strips (cid:NNN)
            # tokens — so the CID signal is still visible. A page whose text is
            # *corrupted* (not merely short) has no usable text layer and is routed
            # to OCR rather than embedded; a short but clean page (small table,
            # signature) is kept as-is.
            if score_text is not None and len(raw.strip()) >= _TEXT_CHAR_THRESHOLD \
                    and not score_text(raw).is_valid:
                ocr_pages.append(i)
                warnings.append(
                    f"page {i}: low text quality -> OCR ({score_text(raw).reason})")
                text = ""
            else:
                text = normalize_glyphs(raw)

            # table detection — guarded; find_tables can throw on odd geometry
            tables = []
            try:
                tables = page.find_tables()
            except Exception as e:  # noqa: BLE001
                warnings.append(f"page {i}: table detection failed ({e})")
            has_tables = bool(tables)

            md_parts = [text] if text else []
            if has_tables:
                table_pages.append(i)
                for t in tables:
                    try:
                        md = _table_to_markdown(t.extract())
                        if md:
                            md_parts.append(md)
                    except Exception as e:  # noqa: BLE001
                        warnings.append(f"page {i}: table render failed ({e})")

            char_count = len(text.strip())
            if char_count >= _TEXT_CHAR_THRESHOLD:
                text_pages += 1

            pages_meta.append({
                "page": i,
                "markdown": "\n\n".join(md_parts),
                "has_tables": has_tables,
                "char_count": char_count,
            })

    total = len(pages_meta)
    if total == 0:
        classification = "scanned"
        warnings.append("no pages found")
    elif text_pages == 0:
        classification = "scanned"
    elif text_pages == total:
        classification = "text"
    else:
        classification = "mixed"

    markdown = "\n\n".join(p["markdown"] for p in pages_meta if p["markdown"])
    return {
        "markdown": markdown,
        "pdf_classification": classification,
        "pages_meta": pages_meta,
        "table_pages": table_pages,
        "ocr_pages": ocr_pages,
        "warnings": warnings,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python -m sidecar.pdf_extract <path-to-pdf>")
    print(json.dumps(detect(sys.argv[1]), ensure_ascii=False))
