"""Unit tests for amend.py (pure text, no external deps)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.amend import extract_amendments, consolidate, _resolve_reference  # noqa: E402
from models import Law, Provision, TYPE_NOMOS  # noqa: E402


def _law_with(text):
    law = Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
              instrument_type=TYPE_NOMOS)
    law.provisions.append(Provision(
        canonical_id="ν.5090/2024#αρ.1", instrument_id="ν.5090/2024",
        instrument_key="N5090/2024", instrument_type=TYPE_NOMOS,
        article_no="1", text_in_force=text))
    return law


def test_resolve_full_nested_reference():
    tid, scope, resolved = _resolve_reference(
        "Η παρ. 2 του άρθρου 24 του ν. 4675/2024 ", "ν.5090/2024")
    assert tid == "ν.4675/2024#αρ.24.παρ.2"
    assert scope == "paragraph"
    assert resolved is True


def test_resolve_article_only_external():
    tid, scope, _ = _resolve_reference("Το άρθρο 5 του ν. 4412/2016 ", "ν.5090/2024")
    assert tid == "ν.4412/2016#αρ.5"
    assert scope == "article"


def test_resolve_case_level():
    tid, scope, _ = _resolve_reference(
        "Η περ. α της παρ. 1 του άρθρου 3 του ν. 4412/2016 ", "ν.5090/2024")
    assert tid == "ν.4412/2016#αρ.3.παρ.1.περ.α"
    assert scope == "case"


def test_resolve_self_reference_defaults_to_current_law():
    tid, scope, resolved = _resolve_reference("Το άρθρο 2 ", "ν.5090/2024")
    assert tid == "ν.5090/2024#αρ.2"
    assert resolved is True


def test_resolve_pd_instrument():
    tid, _, _ = _resolve_reference("του άρθρου 3 του π.δ. 80/2021 ", "ν.5090/2024")
    assert tid == "π.δ.80/2021#αρ.3"


def test_detect_replace_with_target_and_text():
    law = _law_with(
        "Η παρ. 2 του άρθρου 24 του ν. 4675/2024 αντικαθίσταται ως εξής: "
        "«Το νέο κείμενο της παραγράφου.»")
    extract_amendments(law)
    assert len(law.amendments) == 1
    op = law.amendments[0]
    assert op.op == "replaces"
    assert op.target_id == "ν.4675/2024#αρ.24.παρ.2"
    assert op.scope == "paragraph"
    assert op.new_text == "Το νέο κείμενο της παραγράφου."
    assert op.resolved is True


def test_detect_repeal_document_scope():
    law = _law_with("Ο ν. 1234/2000 καταργείται.")
    extract_amendments(law)
    ops = [o for o in law.amendments if o.op == "repeals"]
    assert ops and ops[0].target_id == "ν.1234/2000"
    assert ops[0].scope == "document"


def test_consolidate_in_law_edit_rewrites_text_in_force():
    # an article that replaces article 1 of the SAME law
    law = Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
              instrument_type=TYPE_NOMOS)
    target = Provision(canonical_id="ν.5090/2024#αρ.1",
                       instrument_id="ν.5090/2024", instrument_key="N5090/2024",
                       instrument_type=TYPE_NOMOS, article_no="1",
                       text_in_force="Παλιό κείμενο.")
    amender = Provision(canonical_id="ν.5090/2024#αρ.9",
                        instrument_id="ν.5090/2024", instrument_key="N5090/2024",
                        instrument_type=TYPE_NOMOS, article_no="9",
                        text_in_force="Το άρθρο 1 αντικαθίσταται ως εξής: «Νέο κείμενο.»")
    law.provisions += [target, amender]
    extract_amendments(law)
    consolidate(law)
    assert target.text_in_force == "Νέο κείμενο."
    assert target.text_as_enacted == "Παλιό κείμενο."
    assert target.version == 2


def test_consolidate_leaves_external_targets_untouched():
    law = _law_with(
        "Η παρ. 1 του άρθρου 3 του ν. 4412/2016 αντικαθίσταται ως εξής: «Άλλο.»")
    extract_amendments(law)
    before = law.provisions[0].text_in_force
    consolidate(law)
    # the only provision targets an EXTERNAL law, so its own text is unchanged
    assert law.provisions[0].text_in_force == before


def test_fek_reference_decision_target():
    # target is a prior decision named only by its gazette ref + date
    w = "του άρθρου 18 της υπ’ αρ. 3/100/21.12.2023 (Β΄ 7738) απόφασης"
    tid, scope, resolved = _resolve_reference(w, "Β΄879/2024")
    assert tid == "Β΄7738/2023#αρ.18"
    assert resolved is True


def test_fek_reference_needs_date_for_year():
    # without a date we cannot derive the year -> fall back to self (default)
    w = "του άρθρου 5 (Β΄ 7738) απόφασης"
    tid, scope, resolved = _resolve_reference(w, "Β΄879/2024")
    assert tid.startswith("Β΄879/2024")        # default instrument, not the FEK ref


def test_law_fek_in_parens_not_treated_as_target():
    # "ν. 4622/2019 ... (Α' 133)": the law is the target, (Α'133) is just its FEK
    w = "του άρθρου 67 του ν. 4622/2019 (Α’ 133)"
    tid, scope, resolved = _resolve_reference(w, "x")
    assert tid == "ν.4622/2019#αρ.67"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}"); passed += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
