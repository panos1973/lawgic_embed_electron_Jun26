"""extract.py — text-layer + table-page Azure DI hybrid (the shipping technique).

Flow:
  1. Run the pdfplumber sidecar (sidecar/pdf_extract.py) to get per-page text,
     a text|scanned|mixed classification and the list of table pages.
  2. For pages that contain tables, optionally upgrade them with Azure Document
     Intelligence prebuilt-layout (markdown output) for high-fidelity tables, and
     splice the upgraded markdown back in place of the pdfplumber text.
     - Whole-doc Azure DI is used when the doc is scanned/mixed (no usable text
       layer) and DI is configured.
  3. Parse the masthead so the document arrives downstream already *identified*
     (instrument type / number / year + FEK reference) — this is what makes the
     canonical id, and therefore the idempotent UUID, unique per law.

Azure DI is optional: if DI_ENDPOINT / DI_KEY are unset we degrade gracefully to
the pdfplumber text (tables already rendered as markdown by the sidecar) and add a
warning, so the pipeline still runs end-to-end without Azure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config
from pipeline.masthead import parse_masthead


@dataclass
class ExtractResult:
    text: str
    classification: str          # text | scanned | mixed
    table_pages: list[int]
    pages_markdown: list[str]
    warnings: list[str]
    masthead: dict = field(default_factory=dict)
    ocr_pages: list[int] = field(default_factory=list)


def _azure_layout_markdown(path: str, pages: list[int] | None = None) -> str:
    """Run Azure DI (config.AZURE_DI_MODEL, default prebuilt-layout) and return markdown.

    `pages` (1-based) restricts analysis to specific pages (the table-page upgrade);
    None analyses the whole document. Raises if DI is not configured — callers guard.
    """
    if not (config.AZURE_DI_ENDPOINT and config.AZURE_DI_KEY):
        raise RuntimeError("Azure DI not configured (DI_ENDPOINT / DI_KEY)")

    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import (
        AnalyzeDocumentRequest, DocumentContentFormat)
    from azure.core.credentials import AzureKeyCredential

    client = DocumentIntelligenceClient(
        endpoint=config.AZURE_DI_ENDPOINT,
        credential=AzureKeyCredential(config.AZURE_DI_KEY))
    with open(path, "rb") as f:
        body = AnalyzeDocumentRequest(bytes_source=f.read())
    page_arg = ",".join(str(p) for p in pages) if pages else None
    poller = client.begin_analyze_document(
        config.AZURE_DI_MODEL or "prebuilt-layout", body,
        output_content_format=DocumentContentFormat.MARKDOWN,
        pages=page_arg)
    return poller.result().content or ""


def extract_pdf(path: str, use_azure: bool = True) -> ExtractResult:
    """Extract a FEK PDF to full-document text + table map + masthead identity."""
    from sidecar.pdf_extract import detect

    det = detect(path)
    warnings = list(det.get("warnings", []))
    classification = det["pdf_classification"]
    table_pages = det.get("table_pages", [])
    ocr_pages = det.get("ocr_pages", [])      # text layer too poor -> needs OCR
    pages_meta = det.get("pages_meta", [])
    pages_markdown = [p["markdown"] for p in pages_meta]

    di_available = use_azure and bool(config.AZURE_DI_ENDPOINT and config.AZURE_DI_KEY)

    if classification == "scanned":
        # No usable text layer anywhere: whole-doc DI is the only option.
        if di_available:
            try:
                text = _azure_layout_markdown(path)
            except Exception as e:  # noqa: BLE001 — fall back to pdfplumber text
                warnings.append(f"Azure DI whole-doc failed, using text layer ({e})")
                text = det["markdown"]
        else:
            warnings.append(
                "document is 'scanned' (no usable text layer) but Azure DI is not "
                "configured; cannot OCR — output will be empty/low quality")
            text = det["markdown"]
    else:
        # 'text' or 'mixed': keep the good pdfplumber text and upgrade ONLY the
        # pages that need it (tables for fidelity, OCR pages for missing text)
        # with selective per-page DI. This is the cost-conscious path: clean
        # pages stay free, only the exceptions hit the paid OCR service.
        upgrade_pages = sorted(set(table_pages) | set(ocr_pages))
        if di_available and upgrade_pages:
            for pg in upgrade_pages:
                try:
                    md = _azure_layout_markdown(path, pages=[pg])
                    if md.strip() and 1 <= pg <= len(pages_markdown):
                        pages_markdown[pg - 1] = md
                except Exception as e:  # noqa: BLE001
                    warnings.append(f"Azure DI page {pg} upgrade failed ({e})")
        elif upgrade_pages and not di_available:
            if ocr_pages:
                warnings.append(
                    f"{len(ocr_pages)} page(s) need OCR (poor text layer) but Azure "
                    "DI is not configured; those pages will be missing/low quality")
            if table_pages:
                warnings.append(
                    f"{len(table_pages)} table page(s) detected; Azure DI not "
                    "configured, using pdfplumber markdown tables")
        # Optional: READ each table OR scanned/figure page as an image with the
        # multimodal LLM (source-agnostic — digital or photocopied; table, seal or
        # figure) and splice in a faithful transcription + Greek narration. Gated by
        # TABLE_VISION; runs ONLY on the flagged pages (tables ∪ poor-OCR pages);
        # degrades to the existing extracted text if it returns None.
        if getattr(config, "TABLE_VISION", False) and upgrade_pages:
            from pipeline.table_vision import read_page_vision
            for pg in upgrade_pages:
                r, why = read_page_vision(path, pg)
                if r and 1 <= pg <= len(pages_markdown):
                    pages_markdown[pg - 1] = r
                else:
                    warnings.append(
                        f"page vision: page {pg} not read ({why}); kept extracted text")
        text = "\n\n".join(m for m in pages_markdown if m)

    # Masthead is parsed on the full text (it needs the cover block); the body
    # text handed downstream then has the repeating page furniture stripped.
    masthead = parse_masthead(text)
    warnings.extend(f"masthead: {w}" for w in masthead.get("warnings", []))
    from pipeline.normalize import strip_furniture
    text = strip_furniture(text)

    return ExtractResult(
        text=text,
        classification=classification,
        table_pages=table_pages,
        pages_markdown=pages_markdown,
        warnings=warnings,
        masthead=masthead,
        ocr_pages=ocr_pages,
    )
