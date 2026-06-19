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


def test_enrich_llm_retries_once_on_bad_json():
    # a single garbled/truncated JSON used to silently zero a provision's whole
    # enrichment; enrich_llm must retry once and recover on the second answer.
    import json as _json
    from pipeline import enrich as _enrich
    calls = {"n": 0}

    def flaky(system, user, want_json=True, max_tokens=1024):
        calls["n"] += 1
        if calls["n"] == 1:
            return "not valid json {{{"          # first attempt -> json.loads fails
        return _json.dumps({"summary": "ΠΕΡΙΛΗΨΗ", "keywords": ["κλειδί"],
                            "eurovoc": [], "dkn": []})

    law = _law("τίτλος", "Κείμενο διάταξης προς περίληψη.")
    orig = _enrich.llm.complete
    _enrich.llm.complete = flaky
    try:
        _enrich.enrich_llm(law)
    finally:
        _enrich.llm.complete = orig
    assert calls["n"] == 2                        # retried after the first bad JSON
    assert law.provisions[0].chunk_summary == "ΠΕΡΙΛΗΨΗ"
    assert "κλειδί" in law.provisions[0].keywords


def test_enrich_llm_gives_up_after_two_failures():
    # after two failures it must move on without crashing, leaving the fields empty.
    from pipeline import enrich as _enrich
    calls = {"n": 0}

    def always_bad(system, user, want_json=True, max_tokens=1024):
        calls["n"] += 1
        return "still not json"

    law = _law("τίτλος", "Κείμενο.")
    orig = _enrich.llm.complete
    _enrich.llm.complete = always_bad
    try:
        _enrich.enrich_llm(law)                   # must not raise
    finally:
        _enrich.llm.complete = orig
    assert calls["n"] == 2                        # exactly two attempts, then give up
    assert law.provisions[0].chunk_summary == ""  # stayed empty, no crash


def _law_with_summaries(*summaries, title="Νόμος δοκιμής", cat="NOMOS_SUBSTANTIVE"):
    law = Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
              instrument_type=TYPE_NOMOS, title=title, document_category=cat)
    for i, s in enumerate(summaries, 1):
        law.provisions.append(Provision(
            canonical_id=f"ν.5090/2024#αρ.{i}", instrument_id="ν.5090/2024",
            instrument_key="N5090/2024", instrument_type=TYPE_NOMOS,
            article_no=str(i), text_in_force="…", chunk_type="article",
            chunk_summary=s))
    return law


def test_summarize_law_synthesizes_from_article_summaries():
    from pipeline.enrich import summarize_law
    law = _law_with_summaries("Το άρθρο 1 ορίζει τον σκοπό.", "Το άρθρο 2 συστήνει αρχή.")
    seen = {}

    def fake(system, user, want_json=True, max_tokens=400):
        seen["json"] = want_json
        seen["user"] = user
        return "Ο νόμος ορίζει τον σκοπό και συστήνει αρχή."

    summarize_law(law, complete=fake)
    assert law.summary == "Ο νόμος ορίζει τον σκοπό και συστήνει αρχή."
    assert seen["json"] is False                      # prose overview, not JSON
    assert "Το άρθρο 1" in seen["user"]               # built FROM the article summaries


def test_summarize_law_deterministic_fallback_without_article_summaries():
    from pipeline.enrich import summarize_law
    law = _law_with_summaries("")                     # provision present but no summary
    calls = {"n": 0}

    def fake(*a, **k):
        calls["n"] += 1
        return "unused"

    summarize_law(law, complete=fake)
    assert calls["n"] == 0                             # nothing to synthesize -> no call
    assert "Νόμος δοκιμής" in law.summary and "NOMOS_SUBSTANTIVE" in law.summary


def test_summarize_law_falls_back_without_llm_key():
    from pipeline.enrich import summarize_law
    law = _law_with_summaries("Το άρθρο 1 ορίζει κάτι.")

    def no_key(*a, **k):
        raise SystemExit("no LLM key")

    summarize_law(law, complete=no_key)               # must not crash
    assert law.summary.startswith("Νόμος δοκιμής")    # deterministic fallback used


def test_consumer_protection_law_maps_to_commercial():
    # ν.2251/1994 (consumer protection) + market-control text must classify as
    # 'commercial' (and thus ΔΚΝ ΕΜΠΟΡΙΚΗ ΝΟΜΟΘΕΣΙΑ) — the αρ.38 price-rationalisation
    # gap where both legal_domain and domain_dkn came back empty.
    from pipeline.enrich import _domains_for
    text = ("Εξορθολογισμός τιμών. Για τα καταναλωτικά προϊόντα δεν επιτρέπεται "
            "προωθητική ενέργεια. Κυρώσεις κατά την παρ. 5 του άρθρου 13α του "
            "ν. 2251/1994.")
    assert "commercial" in _domains_for(text)


def test_consumer_keyword_maps_to_commercial_without_cited_law():
    # even without the cited law, a consumer-facing market article should map via
    # the keyword fallback.
    from pipeline.enrich import _domains_for
    assert "commercial" in _domains_for("Προστασία του καταναλωτή και έλεγχος αγοράς.")


def test_consumption_is_not_commercial():
    # guard: "κατανάλωση" (consumption) folds to καταναλωσ, not καταναλωτ — an
    # energy-consumption clause must NOT be tagged commercial by the fallback.
    from pipeline.enrich import _domains_for
    doms = _domains_for("Μέτρα για τη μείωση της κατανάλωσης ηλεκτρικής ενέργειας.")
    assert "commercial" not in doms


def test_article38_like_fills_dkn_volume():
    # end-to-end: the deterministic ΔΚΝ classifier must now stamp ΕΜΠΟΡΙΚΗ
    # ΝΟΜΟΘΕΣΙΑ on an αρ.38-style article (was empty in the live Gemini run).
    from pipeline.enrich import classify_dkn
    law = _law("", "Διαφάνεια τιμών στα καταναλωτικά προϊόντα, ν. 2251/1994.")
    classify_dkn(law)
    assert "ΕΜΠΟΡΙΚΗ ΝΟΜΟΘΕΣΙΑ" in law.provisions[0].domain_dkn


# ── table narration (makes a table's rows retrievable by meaning) ──
from pipeline.enrich import narrate_tables  # noqa: E402
from pipeline.tables import tables_json      # noqa: E402
from models import Provision  # noqa: E402


def _table_law():
    md = ("Πρόγραμμα Μαθημάτων\n\n"
          "| Κωδικός | Τίτλος | ECTS |\n| --- | --- | --- |\n"
          "| BT_1.3 | Δομική Βιολογία | 5 |\n")
    law = Law(instrument_id="Β΄734/2025", instrument_key="x", instrument_type=TYPE_NOMOS)
    law.provisions.append(Provision(
        canonical_id="Β΄734/2025#αρ.6", instrument_id="Β΄734/2025", instrument_key="x",
        instrument_type=TYPE_NOMOS, article_no="6", chunk_type="article",
        text_in_force=md, text_normalized="orig", text_stemmed="orig", content_hash="orig"))
    return law, md


def test_narrate_tables_appends_narration_and_resyncs_bm25():
    law, _ = _table_law()
    p = law.provisions[0]
    fake = lambda system, user, want_json=True, max_tokens=1024: \
        "Το μάθημα Δομική Βιολογία (BT_1.3) αξίζει 5 ECTS."
    narrate_tables(law, complete=fake)
    assert p.text_in_force.startswith("Πρόγραμμα Μαθημάτων")        # original kept
    assert "| Κωδικός | Τίτλος | ECTS |" in p.text_in_force         # verbatim markdown intact
    assert "Δομική Βιολογία (BT_1.3) αξίζει 5 ECTS" in p.text_in_force   # narration appended
    assert p.content_hash != "orig"                                # derived fields re-synced
    assert "αξιζει" in p.text_normalized                           # narration folded into BM25
    assert tables_json(p.text_in_force) is not None                # table_json still recoverable


def test_narrate_tables_skips_chunks_without_tables():
    law = Law(instrument_id="ν.1/2024", instrument_key="x", instrument_type=TYPE_NOMOS)
    law.provisions.append(Provision(
        canonical_id="ν.1/2024#αρ.1", instrument_id="ν.1/2024", instrument_key="x",
        instrument_type=TYPE_NOMOS, article_no="1", text_in_force="Απλό κείμενο χωρίς πίνακα."))
    calls = []
    narrate_tables(law, complete=lambda *a, **k: calls.append(1) or "x")
    assert not calls and law.provisions[0].text_in_force == "Απλό κείμενο χωρίς πίνακα."


def test_narrate_tables_degrades_without_llm_key():
    law, md = _table_law()
    def no_key(*a, **k):
        raise SystemExit("no key")
    narrate_tables(law, complete=no_key)               # must not raise
    assert law.provisions[0].text_in_force == md       # left exactly as-is
