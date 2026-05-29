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


def _table_to_markdown(rows: list[list]) -> str:
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
    warnings: list[str] = []
    text_pages = 0

    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as e:  # noqa: BLE001 — one bad page must not abort
                text = ""
                warnings.append(f"page {i}: text extraction failed ({e})")

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
        "warnings": warnings,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python -m sidecar.pdf_extract <path-to-pdf>")
    print(json.dumps(detect(sys.argv[1]), ensure_ascii=False))
