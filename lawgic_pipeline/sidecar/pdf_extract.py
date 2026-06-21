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


def _detect_gutter(words: list[dict], width: float) -> float:
    """Find the real two-column gutter empirically.

    FEK columns are frequently OFFSET from the page centre (the right column's left
    margin sits a few points off width/2). A fixed width/2 split then makes every
    right-column word 'cross' the assumed gutter, inflating the crossing ratio so the
    clean two-column path is rejected and the columns get line-interleaved — welding a
    header into its neighbour's text ("Άρθρο 12 <bled-in column text>"). Instead, test
    each column-start (word left-edge) in the central band as a candidate gutter and
    return the one crossed by the FEWEST words (ties -> nearest the centre); the right
    column's left margin, where its lines all begin, wins. Falls back to width/2.
    """
    if not words:
        return width / 2.0
    mid = width / 2.0
    lo, hi = width * 0.35, width * 0.65
    cands = sorted({w["x0"] for w in words if lo <= w["x0"] <= hi})
    best_g = mid
    best_c = sum(1 for w in words if w["x0"] < mid < w["x1"])
    for g in cands:
        c = sum(1 for w in words if w["x0"] < g < w["x1"])
        if c < best_c or (c == best_c and abs(g - mid) < abs(best_g - mid)):
            best_g, best_c = g, c
    # Only trust an off-centre gutter when it yields a real two-column split: CLEAN
    # (few words cross it — 0.04 mirrors _column_text's acceptance threshold) AND
    # BALANCED (both columns hold a substantial share of the words). On irregular /
    # bilingual / single-column pages even the best candidate is either crossed by many
    # words or sits near the margin (a lopsided split) — there is no real gutter, so
    # keep width/2 and let the downstream crossing-ratio test classify the page;
    # moving the split off-centre there would mis-band the page and weld lines.
    n = len(words)
    left = sum(1 for w in words if w["x1"] <= best_g)
    right = sum(1 for w in words if w["x0"] >= best_g)
    # A real gutter is the right column's LEFT MARGIN: its lines all begin there, so a
    # substantial share of words START at best_g. A spurious low-crossing x on an
    # irregular / bilingual page lacks this — only a stray word or two starts there
    # (treaty annex pages: 0-2% vs a true two-column body's ~5%+).
    margin = sum(1 for w in words if abs(w["x0"] - best_g) <= 2.0)
    if (best_c <= 0.04 * n and min(left, right) >= 0.3 * n
            and margin >= 0.04 * n):
        return best_g
    return mid


def _column_text(page) -> str:
    """Reading-order text with two-column reconstruction when a gutter exists.

    FEK body pages are symmetric two-column; pdfplumber's extract_text() reads
    line-by-line and so interleaves the columns. We detect the gutter (the vertical
    band that almost no word crosses — found empirically, since columns are often
    offset from centre) and, when found, emit the whole left column then the whole
    right column. Full-width lines (mastheads, titles, tables) keep ~all words crossing
    the gutter, so such pages stay single-flow.
    """
    try:
        words = page.extract_words(use_text_flow=False)
    except Exception:
        return page.extract_text() or ""
    if not words:
        return page.extract_text() or ""
    g = _detect_gutter(words, page.width)
    crossing = sum(1 for w in words if w["x0"] < g < w["x1"])
    left = [w for w in words if w["x1"] <= g]
    right = [w for w in words if w["x0"] >= g]
    # Primary path (unchanged): a clean central gutter across the whole page ->
    # emit the whole left column then the whole right column.
    if (crossing / len(words) < 0.04 and len(left) >= 8 and len(right) >= 8):
        return "\n".join(_group_lines(left) + _group_lines(right))
    # Fallback. A MIXED page — a two-column block (e.g. a FEK decision's Greek
    # preamble) followed by full-width single-column text (e.g. an annexed
    # foreign-language resolution) — fails the whole-page test because the
    # single-column lines cross the gutter. Line-by-line extract_text() would then
    # interleave the two-column block. If the page actually contains a two-column
    # band, reconstruct it band-aware; otherwise keep plain extract_text().
    rows = _rows(words)
    twocol_rows = sum(
        1 for r in rows
        if not _spans_gutter(r, g)
        and any(w["x1"] <= g for w in r) and any(w["x0"] >= g for w in r))
    if twocol_rows >= 3:
        return _banded_text(rows, g)
    return page.extract_text() or ""


def _rows(words: list[dict], ytol: float = 3.0) -> list[list[dict]]:
    """Group words into visual rows by 'top' (each row sorted left-to-right)."""
    ws = sorted(words, key=lambda w: (round(w["top"] / ytol), w["x0"]))
    rows, cur, cur_top = [], [], None
    for w in ws:
        if cur_top is None or abs(w["top"] - cur_top) <= ytol:
            cur.append(w)
            cur_top = w["top"] if cur_top is None else cur_top
        else:
            rows.append(sorted(cur, key=lambda x: x["x0"]))
            cur, cur_top = [w], w["top"]
    if cur:
        rows.append(sorted(cur, key=lambda x: x["x0"]))
    return rows


def _spans_gutter(row: list[dict], g: float) -> bool:
    """A row is full-width (single-column) when a word straddles the centre gutter;
    a genuine two-column row keeps every word on one side, leaving the gutter clear."""
    return any(w["x0"] < g < w["x1"] for w in row)


def _banded_text(rows: list[list[dict]], g: float) -> str:
    """Reconstruct a mixed page. Walk rows top->bottom: buffer two-column rows (split
    at the gutter) and flush the buffer — whole left column, then whole right column —
    whenever a full-width line interrupts, so a two-column block is never read across
    the gutter. Pure single-column input degrades to plain top-to-bottom order."""
    left: list[str] = []
    right: list[str] = []
    out: list[str] = []

    def flush():
        out.extend(left)
        out.extend(right)
        left.clear()
        right.clear()

    for row in rows:
        if _spans_gutter(row, g):
            flush()
            out.append(_line_text(row))
        else:
            lw = [w for w in row if w["x1"] <= g]
            rw = [w for w in row if w["x0"] >= g]
            if lw:
                left.append(_line_text(lw))
            if rw:
                right.append(_line_text(rw))
    flush()
    return "\n".join(out)


def _table_to_markdown(rows) -> str:
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


def _page_needs_ocr(raw: str, score_text) -> tuple[bool, bool, str]:
    """Decide whether one page must be routed to OCR (Azure DI).

    A page needs OCR in two distinct situations, and the caller treats them
    differently:

      1. NO usable text layer — a scanned image or a blank page yields almost
         nothing (a few chars of header noise). needs_ocr=True, is_corrupt=False:
         the little clean text is kept as a fallback for when DI is not
         configured (DI, when present, replaces the page outright).
      2. CORRUPTED text layer — enough characters came out, but they are garble
         (mis-decoded font, (cid:NNN) tokens, box glyphs). needs_ocr=True,
         is_corrupt=True: that garbage text is blanked so it never reaches the
         embedder; only DI's render should stand in for it.

    Returns (needs_ocr, is_corrupt, reason). This runs per page, so scanned
    pages scattered anywhere in a long document — page 43 of 100, or a handful
    spread through 500 — are each caught independently, not just whole-doc scans.
    """
    stripped = (raw or "").strip()
    if len(stripped) < _TEXT_CHAR_THRESHOLD:
        return True, False, "no usable text layer (image/blank page)"
    if score_text is not None:
        q = score_text(raw)
        if not q.is_valid:
            return True, True, q.reason
    return False, False, ""


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

            needs, corrupt, reason = _page_needs_ocr(raw, score_text)
            if needs:
                ocr_pages.append(i)
                warnings.append(f"page {i}: -> OCR ({reason})")
                # blank only corrupted text; keep the little clean text of an image
                # page as a fallback for when DI is not configured (DI replaces it).
                text = "" if corrupt else normalize_glyphs(raw)
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
