"""Unit tests for the deterministic domain classifier (no LLM, no network)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.enrich import classify_domain  # noqa: E402
from models import Law, Provision, TYPE_NOMOS  # noqa: E402


def _law(title, text):
    law = Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
              instrument_type=TYPE_NOMOS, title=title)
    law.provisions.append(Provision(
        canonical_id="ν.5090/2024#αρ.1", instrument_id="ν.5090/2024",
        instrument_key="N5090/2024", instrument_type=TYPE_NOMOS,
        article_no="1", text_in_force=text))
    return law


def test_cited_code_criminal():
    law = classify_domain(_law("Ποινικός Κώδικας", "Τροποποιείται ο Ποινικός Κώδικας."))
    assert "criminal" in law.provisions[0].legal_domain


def test_framework_law_number_procurement():
    law = classify_domain(_law("", "κατά τον ν. 4412/2016 περί δημοσίων συμβάσεων"))
    assert "public_procurement" in law.provisions[0].legal_domain


def test_gdpr_data_protection():
    law = classify_domain(_law("", "σύμφωνα με τον GDPR και τον ν. 4624/2019"))
    assert "data_protection" in law.provisions[0].legal_domain


def test_keyword_fallback_when_no_cited_code():
    # no code/framework cited, but clear labor keywords -> fallback fires
    law = classify_domain(_law("", "Ο μισθός του εργαζομένου και η απόλυση ρυθμίζονται."))
    assert "labor" in law.provisions[0].legal_domain


def test_keyword_fallback_suppressed_when_code_matched():
    # cites the tax code AND has a stray 'συμβαση' keyword; cited-code wins and
    # the civil keyword fallback must NOT be added.
    law = classify_domain(_law("", "Κατά τον Κώδικα Φορολογίας Εισοδήματος, η σύμβαση..."))
    doms = law.provisions[0].legal_domain
    assert "tax" in doms
    assert "civil" not in doms


def test_eu_programs_and_funds_are_not_eu_law():
    # "προγράμματα της Ε.Ε." (EU programs) / "ενωσιακούς πόρους" (EU funds) are
    # incidental mentions, not EU law as a subject.
    p = classify_domain(_law("", "ενημερώνονται για προγράμματα της Ευρωπαϊκής "
                                 "Ένωσης (Ε.Ε.) και διεθνή προγράμματα.")).provisions[0]
    assert "eu_law" not in p.legal_domain
    f = classify_domain(_law("", "αποζημίωση που χρηματοδοτείται από εθνικούς ή "
                                 "ενωσιακούς πόρους.")).provisions[0]
    assert "eu_law" not in f.legal_domain


def test_cited_eu_directive_regulation_is_eu_law():
    # a real EU-law signal (cited Directive/Regulation) must still be detected.
    p = classify_domain(_law("", "κατά τον Κανονισμό (ΕΕ) 609/2013 και την "
                                 "Οδηγία 2009/39/ΕΚ.")).provisions[0]
    assert "eu_law" in p.legal_domain


def test_abroad_idiom_is_not_immigration():
    # "ημεδαπής ή αλλοδαπής" = domestic/foreign (jurisdiction), not aliens.
    p = classify_domain(_law("", "φορείς της ημεδαπής ή της αλλοδαπής "
                                 "συνεργάζονται.")).provisions[0]
    assert "immigration" not in p.legal_domain


def test_real_migration_law_is_immigration():
    p = classify_domain(_law("", "ο αλλοδαπός υπήκοος τρίτης χώρας υποβάλλει "
                                 "αίτηση ασύλου κατά τον ν. 4251/2014.")).provisions[0]
    assert "immigration" in p.legal_domain


def test_no_signal_leaves_empty():
    law = classify_domain(_law("", "Γενική διάταξη χωρίς σαφές αντικείμενο 123."))
    assert law.provisions[0].legal_domain == []


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


def test_title_does_not_leak_greedy_keyword_domain():
    """Regression: a generic title word ('Εκπαίδευσης') must NOT stamp 'education'
    onto an article whose own text is about something else (prices)."""
    from pipeline.enrich import _domains_for
    title = "Ενίσχυση του Εθνικού Συστήματος Επαγγελματικής Εκπαίδευσης"
    price_text = ("Εξορθολογισμός και διαφάνεια τιμών. Οι αρχές έχουν πρόσβαση "
                  "σε κάθε δεδομένο και έγγραφο.")
    doms = _domains_for(price_text, title)
    assert "education" not in doms          # title word must not leak
    assert "data_protection" not in doms    # generic 'δεδομένο' must not match


def test_personal_data_still_detected():
    from pipeline.enrich import _domains_for
    doms = _domains_for("Επεξεργασία προσωπικών δεδομένων κατά τον GDPR.")
    assert "data_protection" in doms
