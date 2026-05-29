"""Integration test: sidecar + extract + masthead + segment on a generated PDF.

Skips automatically if pdfplumber / reportlab are not installed, so the pure-text
unit tests can still run in a minimal environment.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

pytest.importorskip("pdfplumber")
pytest.importorskip("reportlab")

from tests.make_fixture_pdf import build  # noqa: E402
import pipeline.extract as extract  # noqa: E402
import pipeline.segment as segment  # noqa: E402
from normalize import normalize_display  # noqa: E402
from models import Law, make_instrument_id, make_instrument_key, TYPE_NOMOS  # noqa: E402


@pytest.fixture(scope="module")
def fek_pdf(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("pdf") / "fek_sample.pdf")
    build(path)
    return path


def test_sidecar_detects_text_and_table(fek_pdf):
    from sidecar.pdf_extract import detect
    det = detect(fek_pdf)
    assert det["pdf_classification"] in ("text", "mixed")
    assert det["table_pages"], "expected at least one table page"
    assert "ΝΟΜΟΣ" in det["markdown"]


def test_extract_parses_masthead(fek_pdf):
    # use_azure=False so the test never reaches for the network
    ex = extract.extract_pdf(fek_pdf, use_azure=False)
    assert ex.masthead["instrument_type"] == TYPE_NOMOS
    assert ex.masthead["number"] == 5090
    assert ex.masthead["year"] == 2024
    assert ex.masthead["fek_series"] == "Α"
    assert ex.masthead["fek_date"] == "2024-03-26"


def test_full_chain_builds_identified_law(fek_pdf):
    ex = extract.extract_pdf(fek_pdf, use_azure=False)
    mh = ex.masthead
    text = normalize_display(ex.text)
    law = Law(
        instrument_id=make_instrument_id(mh["instrument_type"], mh["number"], mh["year"]),
        instrument_key=make_instrument_key(mh["instrument_type"], mh["number"], mh["year"]),
        instrument_type=mh["instrument_type"], title=mh["title"],
        fek_series=mh["fek_series"], fek_number=mh["fek_number"], fek_date=mh["fek_date"])
    law = segment.segment(text, law)

    assert law.instrument_id == "ν.5090/2024"   # real id, not a placeholder
    assert len(law.provisions) >= 2
    # every provision id is namespaced under the real instrument id -> unique UUIDs
    assert all(p.canonical_id.startswith("ν.5090/2024#") for p in law.provisions)
    assert len({p.canonical_id for p in law.provisions}) == len(law.provisions)
