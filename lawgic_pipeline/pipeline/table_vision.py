"""table_vision.py — read a table page as an IMAGE with a multimodal LLM.

Source-agnostic table handling: rasterize the page (PyMuPDF — pure-Python wheel,
no poppler/system deps) and let the model READ it, returning a faithful markdown
transcription (-> table_json for exact lookup, via tables.py) PLUS a Greek
narration (-> embedded, for semantic retrieval). This removes the dependency on
fragile pdfplumber table detection and works whether the table is digital text or
a photocopy.

Gated by config.TABLE_VISION; the caller runs it ONLY on detected table pages.
Degrades gracefully: any failure (no PyMuPDF, no LLM key, vision/JSON error)
returns None so extraction keeps the existing markdown — it can never break a run.
"""
from __future__ import annotations

import base64
import json
from typing import Optional

import config
import logsetup

log = logsetup.get("table_vision")

# Stable instruction prefix (cacheable). Faithfulness is the priority — exact cell
# values feed table_json; the narration is only a retrieval aid.
_SYSTEM = (
    "You are given a single page from a Greek official gazette (ΦΕΚ) that contains "
    "a table. Do two things, FAITHFULLY — transcribe every row and column with exact "
    "values, never invent or omit a row:\n"
    "1) `markdown`: the table as a GitHub-markdown grid (a header row, a |---| "
    "separator, then one row per line).\n"
    "2) `narration`: a concise GREEK prose explanation, roughly one short clause per "
    "row, so the table can be found by meaning.\n"
    'Return ONLY JSON: {"markdown": "...", "narration": "..."}.'
)
_USER = "Read the table on this page."


def _render_png(path: str, page_no: int, dpi: int = 200) -> Optional[bytes]:
    """Render 1-based page `page_no` of the PDF to PNG bytes via PyMuPDF. None on
    any failure (missing dep, bad page, render error)."""
    try:
        import fitz  # PyMuPDF
    except Exception as e:                                # noqa: BLE001
        log.warning("PyMuPDF (fitz) not available — table vision disabled: %s", e)
        return None
    doc = None
    try:
        doc = fitz.open(path)
        if not (1 <= page_no <= doc.page_count):
            return None
        return doc.load_page(page_no - 1).get_pixmap(dpi=dpi).tobytes("png")
    except Exception as e:                                # noqa: BLE001
        log.warning("rasterize page %d failed: %s", page_no, e)
        return None
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:                             # noqa: BLE001
                pass


def narrate_table_page(path: str, page_no: int) -> Optional[str]:
    """Read the table on `page_no` with the multimodal LLM and return
    'narration\\n\\nmarkdown' (narration first so the vector is semantic; the
    faithful markdown follows so tables.py can still recover table_json and the
    grid shows for display). Returns None on any failure — the caller then keeps
    the existing extracted text."""
    png = _render_png(path, page_no)
    if not png:
        return None
    import llm
    b64 = base64.b64encode(png).decode("ascii")
    try:
        out = llm.complete_vision(_SYSTEM, _USER, b64, want_json=True, max_tokens=4096)
        data = json.loads(out)
    except SystemExit:                                    # no LLM key configured
        log.warning("no LLM key — table vision skipped (page %d)", page_no)
        return None
    except Exception as e:                                # noqa: BLE001
        log.warning("table vision failed on page %d: %s", page_no, str(e)[:160])
        return None
    md = (data.get("markdown") or "").strip()
    narr = (data.get("narration") or "").strip()
    if not (md or narr):
        return None
    log.info("table vision page %d: narration=%dc markdown=%dc", page_no, len(narr), len(md))
    return "\n\n".join(x for x in (narr, md) if x)
