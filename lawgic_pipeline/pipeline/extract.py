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


def _azure_layout_markdown(path: str, pages: list[int] | None = None) -> str:
    """Run Azure DI prebuilt-layout and return markdown.

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
        "prebuilt-layout", body,
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
    pages_meta = det.get("pages_meta", [])
    pages_markdown = [p["markdown"] for p in pages_meta]

    di_available = use_azure and bool(config.AZURE_DI_ENDPOINT and config.AZURE_DI_KEY)

    if classification in ("scanned", "mixed"):
        # No reliable text layer for at least some pages: prefer whole-doc DI.
        if di_available:
            try:
                text = _azure_layout_markdown(path)
            except Exception as e:  # noqa: BLE001 — fall back to pdfplumber text
                warnings.append(f"Azure DI whole-doc failed, using text layer ({e})")
                text = det["markdown"]
        else:
            warnings.append(
                f"document is '{classification}' but Azure DI is not configured; "
                "using pdfplumber text layer (table/scan fidelity may be reduced)")
            text = det["markdown"]
    else:
        # Clean text layer everywhere: keep pdfplumber text, upgrade only the
        # table pages with DI for high-fidelity tables (the splice technique).
        if di_available and table_pages:
            for pg in table_pages:
                try:
                    md = _azure_layout_markdown(path, pages=[pg])
                    if md.strip() and 1 <= pg <= len(pages_markdown):
                        pages_markdown[pg - 1] = md
                except Exception as e:  # noqa: BLE001
                    warnings.append(f"Azure DI page {pg} upgrade failed ({e})")
        elif table_pages and not di_available:
            warnings.append(
                f"{len(table_pages)} table page(s) detected; Azure DI not "
                "configured, using pdfplumber markdown tables")
        text = "\n\n".join(m for m in pages_markdown if m)

    masthead = parse_masthead(text)
    warnings.extend(f"masthead: {w}" for w in masthead.get("warnings", []))

    return ExtractResult(
        text=text,
        classification=classification,
        table_pages=table_pages,
        pages_markdown=pages_markdown,
        warnings=warnings,
        masthead=masthead,
    )
