"""Tests for the offline validation harness (validate.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

pytest.importorskip("pdfplumber")
pytest.importorskip("reportlab")

from tests.make_fixture_pdf import build  # noqa: E402
import validate  # noqa: E402


@pytest.fixture(scope="module")
def fek_pdf(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("pdf") / "fek_sample.pdf")
    build(path)
    return path


def test_validate_known_good_fixture(fek_pdf):
    rec = validate.validate_pdf(fek_pdf, use_azure=False)
    assert rec["status"] == "ok"
    assert rec["instrument_id"] == "ν.5090/2024"
    assert rec["provisions"] >= 2
    assert "masthead_unidentified" not in rec["flags"]
    assert "no_articles" not in rec["flags"]


def test_validate_missing_file_is_extract_error():
    rec = validate.validate_pdf("/nonexistent/whatever.pdf", use_azure=False)
    assert rec["status"] == "extract_error"
    assert "error" in rec


def test_quality_flags_catch_short_bodies():
    # build a Law by hand with mostly-empty article bodies -> many_short_bodies
    from models import Law, Provision, TYPE_NOMOS
    law = Law(instrument_id="ν.1/2020", instrument_key="N1/2020",
              instrument_type=TYPE_NOMOS)
    for i in range(4):
        law.provisions.append(Provision(
            canonical_id=f"ν.1/2020#αρ.{i}", instrument_id="ν.1/2020",
            instrument_key="N1/2020", instrument_type=TYPE_NOMOS,
            article_no=str(i), chunk_type="article", text_in_force="."))
    flags = validate._quality_flags(law, [], identified=True)
    assert any(f.startswith("many_short_bodies") for f in flags)


def test_quality_flags_catch_homoglyph_residue():
    from models import Law, Provision, TYPE_NOMOS
    law = Law(instrument_id="ν.1/2020", instrument_key="N1/2020",
              instrument_type=TYPE_NOMOS)
    # Latin 'o' wedged into a Greek word
    law.provisions.append(Provision(
        canonical_id="ν.1/2020#αρ.1", instrument_id="ν.1/2020",
        instrument_key="N1/2020", instrument_type=TYPE_NOMOS, article_no="1",
        chunk_type="article",
        text_in_force="Το κείμενo αυτό περιέχει λατινικό γράμμα και είναι αρκετά μακρύ."))
    flags = validate._quality_flags(law, [], identified=True)
    assert "possible_homoglyph_residue" in flags


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn() if fn.__code__.co_argcount == 0 else None
            if fn.__code__.co_argcount == 0:
                print(f"PASS {fn.__name__}"); passed += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n(ran arg-free tests only; use pytest for fixtures) {passed} passed")
