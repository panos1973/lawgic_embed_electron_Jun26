"""Unit tests for the deterministic document-category classifier (no LLM, no network).

Covers the function taxonomy ported from the old app's detection-schema:
laws split into amendment/codification/ratification/substantive; π.δ. and ΥΑ
split by structural intent; decisions/PNP/resolutions mapped directly.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.enrich import classify_document_category, DOCUMENT_CATEGORIES  # noqa: E402
from models import (Law, Provision, TYPE_NOMOS, TYPE_PD, TYPE_YA, TYPE_KYA,  # noqa: E402
                    TYPE_PNP, TYPE_PSIFISMA, TYPE_KANAP, TYPE_KANVOULIS,
                    TYPE_ANAKOINOSI, TYPE_PYS)


def test_anakoinosi_notice_category():
    # a FEK announcement/notice is tagged with its own category (filterable, not UNKNOWN)
    assert _cat(TYPE_ANAKOINOSI, "Θέση σε ισχύ της Συμφωνίας ...") == "ANAKOINOSI"
    assert "ANAKOINOSI" in DOCUMENT_CATEGORIES


def test_pys_cabinet_act_category():
    assert _cat(TYPE_PYS, "Συμμετοχή της Ελλάδας στην αύξηση του μετοχικού κεφαλαίου") == "PYS"
    assert "PYS" in DOCUMENT_CATEGORIES


def _law(itype, title, book="", part=""):
    law = Law(instrument_id="x", instrument_key="x", instrument_type=itype, title=title)
    law.provisions.append(Provision(
        canonical_id="x#1", instrument_id="x", instrument_key="x",
        instrument_type=itype, article_no="1", book=book, part=part,
        text_in_force=title))
    return law


def _cat(itype, title, **kw):
    return classify_document_category(_law(itype, title, **kw)).document_category


def test_nomos_amendment():
    assert _cat(TYPE_NOMOS, "Τροποποίηση διατάξεων του Ποινικού Κώδικα") == "NOMOS_AMENDMENT"


def test_nomos_ratification_beats_other_signals():
    # ratification is checked first: a "Κύρωση" law that also amends stays ratification
    assert _cat(TYPE_NOMOS, "Κύρωση της Διεθνούς Σύμβασης και τροποποίηση συναφών διατάξεων") \
        == "NOMOS_RATIFICATION"


def test_nomos_codification_by_title():
    assert _cat(TYPE_NOMOS, "Κωδικοποίηση της νομοθεσίας περί προσωπικού") == "NOMOS_CODIFICATION"


def test_nomos_codification_by_deep_hierarchy():
    # no title signal, but ΒΙΒΛΙΟ/ΜΕΡΟΣ structure -> codification
    assert _cat(TYPE_NOMOS, "Νέος Κώδικας", book="ΒΙΒΛΙΟ ΠΡΩΤΟ") == "NOMOS_CODIFICATION"


def test_nomos_substantive_fallback():
    assert _cat(TYPE_NOMOS, "Μέτρα για την ενίσχυση της ανάπτυξης") == "NOMOS_SUBSTANTIVE"


def test_pd_organizational():
    assert _cat(TYPE_PD, "Οργανισμός του Υπουργείου Υγείας") == "PD_ORGANIZATIONAL"


def test_pd_amendment():
    assert _cat(TYPE_PD, "Τροποποίηση του π.δ. 18/2018") == "PD_AMENDMENT"


def test_pd_regulatory_fallback():
    assert _cat(TYPE_PD, "Προσόντα διορισμού σε θέσεις") == "PD_REGULATORY"


def test_ya_organizational():
    assert _cat(TYPE_YA, "Διάρθρωση και κατανομή θέσεων προσωπικού") == "YA_ORGANIZATIONAL"


def test_ya_regulatory_by_signal():
    assert _cat(TYPE_YA, "Καθορισμός των όρων και προϋποθέσεων") == "YA_REGULATORY"


def test_ya_individual_fallback():
    assert _cat(TYPE_YA, "Διορισμός μελών επιτροπής") == "YA_INDIVIDUAL"


def test_kanonistiki_apofasi_is_regulatory():
    # a regulatory act type defaults to YA_REGULATORY even without a title signal
    assert _cat(TYPE_KANAP, "Απόφαση της Αρχής") == "YA_REGULATORY"


def test_kya_direct():
    assert _cat(TYPE_KYA, "Κοινή ρύθμιση δύο υπουργείων") == "KYA"


def test_pnp_direct():
    assert _cat(TYPE_PNP, "Κατεπείγοντα μέτρα αντιμετώπισης") == "PNP"


def test_psifisma_direct():
    assert _cat(TYPE_PSIFISMA, "Ψήφισμα της Βουλής") == "PSIFISMA"


def test_type_outside_taxonomy_is_unknown():
    # Κανονισμός Βουλής is not one of the 20 routed categories -> UNKNOWN
    assert _cat(TYPE_KANVOULIS, "Κανονισμός της Βουλής") == "UNKNOWN"


def test_every_result_is_in_the_taxonomy():
    for itype in (TYPE_NOMOS, TYPE_PD, TYPE_YA, TYPE_KYA, TYPE_PNP,
                  TYPE_PSIFISMA, TYPE_KANAP, TYPE_KANVOULIS):
        assert _cat(itype, "κάποιος τίτλος") in DOCUMENT_CATEGORIES


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


def test_amending_law_with_meros_is_not_codification():
    """Regression: a law with deep ΜΕΡΟΣ/ΚΕΦΑΛΑΙΟ structure but whose articles are
    overwhelmingly 'Τροποποίηση/Προσθήκη ... ν. XXXX' must be NOMOS_AMENDMENT, not
    NOMOS_CODIFICATION (the ν.5082/2024 bug: ΜΕΡΟΣ-level depth wrongly forced
    codification)."""
    law = Law(instrument_id="ν.5082/2024", instrument_key="N5082/2024",
              instrument_type=TYPE_NOMOS,
              title="Ενίσχυση του Εθνικού Συστήματος Επαγγελματικής Εκπαίδευσης και άλλες διατάξεις")
    for i in range(6):
        law.provisions.append(Provision(
            canonical_id=f"x#{i}", instrument_id="ν.5082/2024", instrument_key="N5082/2024",
            instrument_type=TYPE_NOMOS, article_no=str(i), part="ΜΕΡΟΣ Α'",
            text_in_force=f"Τροποποίηση άρθρου {i} ν. 4763/2020. Στο άρθρο {i}..."))
    classify_document_category(law)
    assert law.document_category == "NOMOS_AMENDMENT"


def test_meros_only_no_amend_signal_is_substantive_not_codification():
    """ΜΕΡΟΣ depth alone (no ΒΙΒΛΙΟ, no codification/amend signal) -> substantive."""
    law = Law(instrument_id="ν.1/2024", instrument_key="x", instrument_type=TYPE_NOMOS,
              title="Ρυθμίσεις για την ψηφιακή οικονομία")
    law.provisions.append(Provision(
        canonical_id="x#1", instrument_id="ν.1/2024", instrument_key="x",
        instrument_type=TYPE_NOMOS, article_no="1", part="ΜΕΡΟΣ Α'",
        text_in_force="Σκοπός του παρόντος είναι..."))
    classify_document_category(law)
    assert law.document_category == "NOMOS_SUBSTANTIVE"
