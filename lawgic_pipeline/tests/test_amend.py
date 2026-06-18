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


def _law_with_chunk(text, chunk_type):
    law = Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
              instrument_type=TYPE_NOMOS)
    law.provisions.append(Provision(
        canonical_id=("ν.5090/2024#παραρτ.1" if chunk_type == "annex"
                      else "ν.5090/2024#αρ.1"),
        instrument_id="ν.5090/2024", instrument_key="N5090/2024",
        instrument_type=TYPE_NOMOS, article_no="1",
        chunk_type=chunk_type, text_in_force=text))
    return law


# a ratified treaty's OWN amending language ("Article X is replaced …") must not be
# mined as amendments OF the enacting Greek law (the self-targeting false edges).
_TREATY_EDIT = "Το άρθρο 1 της Συμφωνίας αντικαθίσταται ως εξής: «νέο κείμενο της Συμφωνίας»."


def test_amend_skips_annex_ratified_content():
    law = _law_with_chunk(_TREATY_EDIT, "annex")
    extract_amendments(law)
    assert law.amendments == []          # annex = verbatim ratified text, not mined


def test_amend_still_mines_non_annex_provisions():
    # control: the SAME text in a normal article DOES yield an edge — proving the
    # skip is annex-specific, not the text being filtered for some other reason.
    law = _law_with_chunk(_TREATY_EDIT, "article")
    extract_amendments(law)
    assert len(law.amendments) >= 1


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


def test_inserted_article_attributed_to_target_law_not_enacting():
    """Heading declares the target law; an inserted article must attach there,
    not to the enacting law (Bug 1)."""
    from models import Law, Provision
    law = Law(instrument_id="ν.5082/2024", instrument_key="N5082/2024",
              instrument_type=TYPE_NOMOS)
    law.provisions.append(Provision(
        canonical_id="ν.5082/2024#αρ.43", instrument_id="ν.5082/2024",
        instrument_key="N5082/2024", instrument_type=TYPE_NOMOS, article_no="43",
        text_in_force=("Αρχηγείο - Τροποποίηση άρθρου 82 ν. 4662/2020\n"
                       "Στο άρθρο 82 προστίθεται παρ. 3 ως εξής: «3. Νέο.»")))
    extract_amendments(law)
    tgts = [op.target_id for op in law.amendments]
    assert any(t.startswith("ν.4662/2020#αρ.82") for t in tgts)   # right law
    assert not any(t.startswith("ν.5082/2024#αρ.82") for t in tgts)  # not enacting


def test_no_self_consolidate_for_own_restated_article():
    """A substantive article restating its own text (no external law in heading)
    must not produce a self-consolidates edge (Bug 2)."""
    from models import Law, Provision
    law = Law(instrument_id="ν.5082/2024", instrument_key="N5082/2024",
              instrument_type=TYPE_NOMOS)
    law.provisions.append(Provision(
        canonical_id="ν.5082/2024#αρ.18", instrument_id="ν.5082/2024",
        instrument_key="N5082/2024", instrument_type=TYPE_NOMOS, article_no="18",
        text_in_force="Άρθρο 18 Φοίτηση\nδιαμορφώνεται ως εξής: «πλήρες κείμενο.»"))
    extract_amendments(law)
    self_consol = [op for op in law.amendments
                   if op.op == "consolidates" and op.target_id.startswith("ν.5082/2024#")]
    assert self_consol == []


def test_clean_drops_self_dumps_and_fragments_keeps_real():
    from pipeline.amend import _clean_amendments
    from models import AmendmentOp
    own = "ν.5082/2024"
    ops = [
        AmendmentOp(op="adds", target_id="ν.5082/2024", scope="document",
                    new_text="Άρθρο 18 ..." * 6, resolved=False),          # self-dump
        AmendmentOp(op="adds", target_id="ν.4763/2020", scope="document",
                    new_text="Εφαρμογής", resolved=False),                  # fragment
        AmendmentOp(op="adds", target_id="ν.4763/2020#αρ.40Α", scope="article",
                    new_text="3α. Με κοινή απόφαση ορίζεται.", resolved=True),  # good (short, lower-case body)
        AmendmentOp(op="replaces", target_id="ν.4186/2013#αρ.9", scope="article",
                    new_text="Άρθρο 9 ..." * 3, resolved=True),            # good
    ]
    out = _clean_amendments(ops, own)
    tids = [o.target_id for o in out]
    assert tids == ["ν.4763/2020#αρ.40Α", "ν.4186/2013#αρ.9"]


def test_clean_drops_heading_banners_but_keeps_empty_text_with_precise_target():
    """A NON-EMPTY heading/banner quoted-span is noise and is dropped. But an EMPTY
    new_text WITH a pinpoint #αρ. target is a genuine edit whose replacement text we
    couldn't capture (e.g. a restatement too large to echo, recovered structure-only,
    or a no-text validity extension) — its edge (action+target) is the graph fact and
    must be kept, consistent with how empty 'amends'/'repeals' edges are treated."""
    from pipeline.amend import _clean_amendments
    from models import AmendmentOp
    own = "ν.5082/2024"
    ops = [
        AmendmentOp(op="adds", target_id="ν.4763/2020#αρ.40Ι", scope="article",
                    new_text="Κεφάλαιο ΣΤ2", resolved=True),               # heading insert -> drop
        AmendmentOp(op="adds", target_id="ν.4763/2020#αρ.40Α", scope="article",
                    new_text="ΚΕΝΤΡΑ ΕΠΑΓΓΕΛΜΑΤΙΚΗΣ ΕΚΠΑΙΔΕΥΣΗΣ ΚΑΙ ΚΑΤΑΡΤΙΣΗΣ",
                    resolved=True),                                         # all-caps banner -> drop
        AmendmentOp(op="replaces", target_id="ν.4368/2016#αρ.90.παρ.7.περ.γ",
                    scope="case", new_text="", resolved=True),              # empty + precise -> KEEP
        AmendmentOp(op="replaces", target_id="ν.4186/2013#αρ.9", scope="article",
                    new_text="Άρθρο 9 Πρόγραμμα σπουδών. 1. Τα προγράμματα.",
                    resolved=True),                                         # real -> keep
    ]
    out = _clean_amendments(ops, own)
    assert [o.target_id for o in out] == [
        "ν.4368/2016#αρ.90.παρ.7.περ.γ", "ν.4186/2013#αρ.9"]


def test_clean_still_drops_empty_text_without_a_target():
    """An empty/heading text op with NO precise #αρ. target stays noise -> dropped."""
    from pipeline.amend import _clean_amendments
    from models import AmendmentOp
    ops = [
        AmendmentOp(op="adds", target_id="ν.4763/2020", scope="document",
                    new_text="", resolved=False),                           # empty, no article
        AmendmentOp(op="replaces", target_id="ν.4186/2013#αρ.9", scope="article",
                    new_text="Άρθρο 9 Πρόγραμμα σπουδών. 1. Τα προγράμματα.", resolved=True),
    ]
    out = _clean_amendments(ops, "ν.5082/2024")
    assert [o.target_id for o in out] == ["ν.4186/2013#αρ.9"]


def test_clean_keeps_textless_repeal_and_renumber():
    """repeals/renumbers carry no quoted text by nature — never treat their
    empty new_text as heading-only noise."""
    from pipeline.amend import _clean_amendments
    from models import AmendmentOp
    ops = [
        AmendmentOp(op="repeals", target_id="ν.1234/2000", scope="document",
                    new_text=None, resolved=True),
        AmendmentOp(op="renumbers", target_id="ν.4763/2020#αρ.40", scope="article",
                    new_text=None, resolved=True),
    ]
    out = _clean_amendments(ops, "ν.5082/2024")
    assert [o.op for o in out] == ["repeals", "renumbers"]


def test_clean_dedups_exact_duplicates():
    from pipeline.amend import _clean_amendments
    from models import AmendmentOp
    op = AmendmentOp(op="replaces", target_id="ν.4186/2013#αρ.9", scope="article",
                     new_text="Άρθρο 9 ..." * 3, resolved=True)
    op2 = AmendmentOp(op="replaces", target_id="ν.4186/2013#αρ.9", scope="article",
                      new_text="Άρθρο 9 ..." * 3, resolved=True)
    assert len(_clean_amendments([op, op2], "ν.5082/2024")) == 1


def test_drop_phantom_repeal_contradicted_by_same_article_restatement():
    """ν.5086 art.19/29/31 each emitted a bare 'repeals' alongside the real
    text-bearing restatement of the SAME target. The law amends (not deletes), so
    the phantom repeal — which would falsely flip the provision to 'repealed' — is
    dropped; the restatement is kept."""
    from pipeline.amend import _clean_amendments
    from models import AmendmentOp
    src = "ν.5086/2024#αρ.19"
    ops = [
        AmendmentOp(op="replaces", target_id="ν.5002/2022#αρ.23.παρ.1", scope="paragraph",
                    new_text="«1. Η Επιτροπή Συντονισμού αποτελείται από έξι (6) μέλη.»",
                    resolved=True, source_id=src),
        AmendmentOp(op="repeals", target_id="ν.5002/2022#αρ.23.παρ.1", scope="paragraph",
                    new_text=None, resolved=True, source_id=src),
    ]
    out = _clean_amendments(ops, "ν.5086/2024")
    assert [o.op for o in out] == ["replaces"]
    # an 'adds' restatement contradicts a sibling repeal the same way (art.31 case)
    src2 = "ν.5086/2024#αρ.31"
    ops2 = [
        AmendmentOp(op="adds", target_id="ν.5005/2022#αρ.28.παρ.2", scope="paragraph",
                    new_text="«2. Για την πρώτη εφαρμογή του παρόντος ...»",
                    resolved=True, source_id=src2),
        AmendmentOp(op="repeals", target_id="ν.5005/2022#αρ.28.παρ.2", scope="paragraph",
                    new_text=None, resolved=True, source_id=src2),
    ]
    assert [o.op for o in _clean_amendments(ops2, "ν.5086/2024")] == ["adds"]


def test_keep_repeal_when_restatement_is_from_a_different_article():
    """Scoped per (source, target): a repeal from one article survives even if a
    DIFFERENT article restates the same target — two distinct legislative acts are
    never merged. (A genuine standalone repeal is never dropped.)"""
    from pipeline.amend import _clean_amendments
    from models import AmendmentOp
    ops = [
        AmendmentOp(op="repeals", target_id="ν.4635/2019#αρ.50", scope="article",
                    new_text=None, resolved=True, source_id="ν.5086/2024#αρ.22"),
        AmendmentOp(op="replaces", target_id="ν.4635/2019#αρ.50", scope="article",
                    new_text="«Άρθρο 50 εντελώς νέο και εκτενές κείμενο διάταξης.»",
                    resolved=True, source_id="ν.5086/2024#αρ.40"),
    ]
    out = _clean_amendments(ops, "ν.5086/2024")
    assert {o.op for o in out} == {"repeals", "replaces"} and len(out) == 2
