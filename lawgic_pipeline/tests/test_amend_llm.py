"""Tests for the LLM-assisted amendment extractor (no network — LLM is stubbed).

Verifies parsing, the new_text capture, multi-target splitting, the source==target
self-loop guard, the fabrication guard, and the cheap no-verb gate.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.amend_llm import extract_amendments_llm, _AMEND_STEMS  # noqa: E402
from models import Law, Provision, TYPE_NOMOS  # noqa: E402


def _law(*texts, number="5090/2024"):
    law = Law(instrument_id=f"ν.{number}", instrument_key="N5090/2024",
              instrument_type=TYPE_NOMOS, title="t", fek_date="2024-05-01")
    for i, t in enumerate(texts, 1):
        law.provisions.append(Provision(
            canonical_id=f"ν.{number}#αρ.{i}", instrument_id=f"ν.{number}",
            instrument_key="N5090/2024", instrument_type=TYPE_NOMOS,
            article_no=str(i), text_in_force=t))
    return law


def _fake(payload):
    """Return a complete() stub that always answers with `payload` (a dict)."""
    return lambda system, user, want_json=True, max_tokens=1500: json.dumps(payload)


def test_replaces_with_new_text_and_nested_scope():
    law = _law("Η περ. α΄ της παρ. 1 του άρθρου 60 του ν. 4172/2013 "
               "αντικαθίσταται ως εξής: «Ειδικά, η δήλωση υποβάλλεται…»")
    extract_amendments_llm(law, complete=_fake({"amendments": [{
        "action": "replaces", "scope": "case", "target_law_number": "4172/2013",
        "target_article_number": "60", "target_paragraph": "1", "target_case": "α",
        "new_text": "Ειδικά, η δήλωση υποβάλλεται…", "position": ""}]}))
    assert len(law.amendments) == 1
    op = law.amendments[0]
    assert op.op == "replaces" and op.scope == "case"
    # the case (.περ.α) must be preserved in the canonical id, not dropped
    assert op.target_id == "ν.4172/2013#αρ.60.παρ.1.περ.α"
    assert op.new_text.startswith("Ειδικά")
    assert op.resolved is False          # cross-law target, not resolved locally


def test_pd_target_keeps_pd_instrument_type():
    # a π.δ. target must become 'π.δ.NUM/YEAR', not 'ν.NUM/YEAR' (the 10a6adc8 bug).
    law = _law("Η παρ. 2 του άρθρου 14 του π.δ. 77/2023 αντικαθίσταται ως εξής: "
               "«νέο κείμενο της παραγράφου με πεζά.»")
    extract_amendments_llm(law, complete=_fake({"amendments": [{
        "action": "consolidates", "scope": "paragraph", "target_law_number": "77/2023",
        "target_law_type": "pd", "target_article_number": "14", "target_paragraph": "2",
        "new_text": "νέο κείμενο της παραγράφου με πεζά."}]}))
    assert law.amendments[0].target_id == "π.δ.77/2023#αρ.14.παρ.2"


def test_law_target_defaults_to_nomos_when_type_absent():
    # backward-compatible: no target_law_type -> ν. (the common case)
    law = _law("Το άρθρο 5 του ν. 4412/2016 αντικαθίσταται ως εξής: "
               "«νέο κείμενο του άρθρου με πεζά γράμματα.»")
    extract_amendments_llm(law, complete=_fake({"amendments": [{
        "action": "replaces", "scope": "article", "target_law_number": "4412/2016",
        "target_article_number": "5",
        "new_text": "νέο κείμενο του άρθρου με πεζά γράμματα."}]}))
    assert law.amendments[0].target_id == "ν.4412/2016#αρ.5"


def test_source_id_records_the_amending_article_of_this_law():
    # the edge must record WHICH article of the enacting (new) law made the change,
    # so the loader writes source_canonical_id / source_article_number (and the graph
    # can answer "ν.5090/2024 art.1 amends ν.4412/2016 art.5"), not just the target.
    law = _law("Το άρθρο 5 του ν. 4412/2016 αντικαθίσταται ως εξής: "
               "«νέο κείμενο του άρθρου με πεζά γράμματα.»")
    extract_amendments_llm(law, complete=_fake({"amendments": [{
        "action": "replaces", "scope": "article", "target_law_number": "4412/2016",
        "target_article_number": "5",
        "new_text": "νέο κείμενο του άρθρου με πεζά γράμματα."}]}))
    op = law.amendments[0]
    assert op.source_id == "ν.5090/2024#αρ.1"      # the amending article of THIS law
    assert op.target_id == "ν.4412/2016#αρ.5"      # ... altering art.5 of the older law


def test_diamorfonetai_restatement_collapses_to_full_text():
    # "διαμορφώνεται ως εξής": a VERBATIM micro-edit + the full restated paragraph
    # that CONTAINS it -> one edge (the restatement). Modeled on the real
    # ν.4934/2022 art 22 §4 case found in the cluster (short restatement, 74 chars).
    law = _law("Στην παρ. 4 του άρθρου 22 του ν. 4934/2022 η φράση «εκκινεί την "
               "31η.12.2023» αντικαθίσταται και η παρ. 4 διαμορφώνεται ως εξής: «...»")
    extract_amendments_llm(law, complete=_fake({"amendments": [
        {"action": "replaces", "scope": "phrase", "target_law_number": "4934/2022",
         "target_article_number": "22", "target_paragraph": "4",
         "new_text": "εκκινεί την 30ή.9.2024"},
        {"action": "amends", "scope": "paragraph", "target_law_number": "4934/2022",
         "target_article_number": "22", "target_paragraph": "4",
         "new_text": "4. Η παραγωγική λειτουργία της ως άνω πλατφόρμας εκκινεί την 30ή.9.2024."},
    ]}))
    assert len(law.amendments) == 1                       # micro subsumed by restatement
    op = law.amendments[0]
    assert op.target_id == "ν.4934/2022#αρ.22.παρ.4"
    assert "παραγωγική λειτουργία" in (op.new_text or "")  # the full restatement kept


def test_redundant_empty_amends_edge_dropped():
    # a bare "amends" with empty new_text is contentless; when a sibling on the SAME
    # target carries the real text it is dropped (the spurious art-24 edge in the
    # cluster). repeals/renumbers with empty new_text are NOT affected.
    law = _law("Στην παρ. 4 του άρθρου 22 του ν. 4934/2022 διαμορφώνεται ως εξής: «4. …»")
    extract_amendments_llm(law, complete=_fake({"amendments": [
        {"action": "amends", "scope": "paragraph", "target_law_number": "4934/2022",
         "target_article_number": "22", "target_paragraph": "4", "new_text": ""},
        {"action": "replaces", "scope": "paragraph", "target_law_number": "4934/2022",
         "target_article_number": "22", "target_paragraph": "4",
         "new_text": "4. Η παραγωγική λειτουργία εκκινεί την 30ή.9.2024."},
    ]}))
    assert len(law.amendments) == 1
    assert law.amendments[0].op == "replaces" and law.amendments[0].new_text


def test_distinct_short_edits_to_same_target_are_kept():
    # guard: two SHORT edits to the same target with NO full restatement must NOT be
    # collapsed (we'd lose a genuine edit). Both are kept.
    law = _law("Στο άρθρο 5 του ν. 4412/2016 η λέξη Α αντικαθίσταται από Β και "
               "προστίθεται εδάφιο.")
    extract_amendments_llm(law, complete=_fake({"amendments": [
        {"action": "replaces", "scope": "phrase", "target_law_number": "4412/2016",
         "target_article_number": "5", "new_text": "η λέξη «Α» αντικαθίσταται από «Β»."},
        {"action": "adds", "scope": "phrase", "target_law_number": "4412/2016",
         "target_article_number": "5", "new_text": "προστίθεται νέο τρίτο εδάφιο σύντομο."},
    ]}))
    assert len(law.amendments) == 2                       # both short -> both kept


def test_multi_target_split_into_two_ops():
    law = _law("Οι παρ. 8 και 9 του άρθρου 64 του ν. 4172/2013 "
               "αντικαθίστανται ως εξής: «8. … 9. …»")
    extract_amendments_llm(law, complete=_fake({"amendments": [
        {"action": "replaces", "scope": "paragraph", "target_law_number": "4172/2013",
         "target_article_number": "64", "target_paragraph": "8",
         "new_text": "8. Η παράγραφος όγδοη αντικαθίσταται με νέο κείμενο."},
        {"action": "replaces", "scope": "paragraph", "target_law_number": "4172/2013",
         "target_article_number": "64", "target_paragraph": "9",
         "new_text": "9. Η παράγραφος ένατη αντικαθίσταται με νέο κείμενο."},
    ]}))
    assert len(law.amendments) == 2
    assert {op.target_id for op in law.amendments} == {
        "ν.4172/2013#αρ.64.παρ.8", "ν.4172/2013#αρ.64.παρ.9"}


def test_adds_with_position():
    law = _law("Στο τέλος του άρθρου 13, μετά την παρ. 17, προστίθενται "
               "παράγραφοι 18 έως 22, ως εξής: «18. …»")
    extract_amendments_llm(law, complete=_fake({"amendments": [{
        "action": "adds", "scope": "paragraph", "target_law_number": None,
        "target_article_number": "13",
        "new_text": "18. Προστίθεται νέα παράγραφος με ειδικές ρυθμίσεις.",
        "position": "στο τέλος, μετά την παρ. 17"}]}))
    op = law.amendments[0]
    assert op.op == "adds"
    assert op.target_id == "ν.5090/2024#αρ.13"     # in-law (target_law null)
    assert op.resolved is True
    assert op.new_text.startswith("18.")


def test_self_loop_guard_demotes_to_in_law():
    # model wrongly returns the enacting law as the target -> must NOT self-loop
    law = _law("Η παρ. 1 του άρθρου 7 αντικαθίσταται ως εξής: «…»", number="346/2025")
    extract_amendments_llm(law, complete=_fake({"amendments": [{
        "action": "replaces", "scope": "paragraph", "target_law_number": "346/2025",
        "target_article_number": "7", "target_paragraph": "1",
        "new_text": "1. Η παράγραφος πρώτη αντικαθίσταται με το νέο κείμενο."}]}))
    op = law.amendments[0]
    assert op.target_id == "ν.346/2025#αρ.7.παρ.1"
    assert op.resolved is True          # demoted to in-law, not a cross-law self-loop


def test_fabrication_guard_drops_empty_edges():
    law = _law("τροποποιείται κάτι ασαφές")
    extract_amendments_llm(law, complete=_fake({"amendments": [
        {"action": "amends", "scope": "article"}]}))   # no article, no new_text
    assert law.amendments == []


def test_clean_pass_applies_to_llm_output():
    # the LLM may return a heading-only banner with a valid target — the shared
    # _clean_amendments hygiene must drop it just as on the deterministic path,
    # while keeping the real lower-case edit.
    law = _law("Στον ν. 4763/2020 προστίθεται κεφάλαιο και αντικαθίσταται άρθρο.")
    extract_amendments_llm(law, complete=_fake({"amendments": [
        {"action": "adds", "scope": "article", "target_law_number": "4763/2020",
         "target_article_number": "40Α",
         "new_text": "ΚΕΝΤΡΑ ΕΠΑΓΓΕΛΜΑΤΙΚΗΣ ΕΚΠΑΙΔΕΥΣΗΣ ΚΑΙ ΚΑΤΑΡΤΙΣΗΣ"},  # banner -> drop
        {"action": "replaces", "scope": "article", "target_law_number": "4186/2013",
         "target_article_number": "9",
         "new_text": "Άρθρο 9 Πρόγραμμα σπουδών. 1. Τα προγράμματα διδασκαλίας."},  # keep
    ]}))
    assert [op.target_id for op in law.amendments] == ["ν.4186/2013#αρ.9"]


def test_extraction_method_records_llm_provider_and_model():
    # the loader denormalizes op.extraction_method into Weaviate; an LLM edge must
    # carry "llm:<provider>:<model>", not the AmendmentOp default "pattern_matching",
    # so the embedded data tells the truth about which extractor (and which model
    # version) produced the edge — provider alone can't tell V4 Pro from V4 Flash.
    import config
    model = config.LLM_MODEL or config.PROVIDERS[config.LLM_PROVIDER]["default_model"]
    law = _law("Το άρθρο 5 του ν. 4412/2016 αντικαθίσταται ως εξής: "
               "«νέο κείμενο του άρθρου με πεζά γράμματα.»")
    extract_amendments_llm(law, complete=_fake({"amendments": [{
        "action": "replaces", "scope": "article", "target_law_number": "4412/2016",
        "target_article_number": "5",
        "new_text": "νέο κείμενο του άρθρου με πεζά γράμματα."}]}))
    method = law.amendments[0].extraction_method
    assert method == f"llm:{config.LLM_PROVIDER}:{model}"
    assert method.startswith(f"llm:{config.LLM_PROVIDER}:")
    assert method != "pattern_matching"


def test_literal_null_case_is_not_appended_to_target_id():
    # the model sometimes echoes a JSON null as the string "null" for target_case;
    # it must NOT leak into the canonical id as '.περ.null' (observed in a live run).
    law = _law("Στην παρ. 4 του άρθρου 18 του ν. 4763/2020 προστίθεται εδάφιο: "
               "«νέο τρίτο εδάφιο με πεζά γράμματα.»")
    extract_amendments_llm(law, complete=_fake({"amendments": [{
        "action": "adds", "scope": "paragraph", "target_law_number": "4763/2020",
        "target_article_number": "18", "target_paragraph": "4", "target_case": "null",
        "new_text": "νέο τρίτο εδάφιο με πεζά γράμματα."}]}))
    assert len(law.amendments) == 1
    tid = law.amendments[0].target_id
    assert tid == "ν.4763/2020#αρ.18.παρ.4"
    assert "null" not in tid and ".περ." not in tid


def test_no_verb_provision_is_not_sent_to_llm():
    calls = []
    def spy(system, user, want_json=True, max_tokens=1500):
        calls.append(user)
        return json.dumps({"amendments": []})
    law = _law("Γενική διάταξη χωρίς καμία μεταβολή.")
    extract_amendments_llm(law, complete=spy)
    assert calls == []                   # cheap gate skipped the call entirely


def test_missing_key_propagates_systemexit():
    def no_key(system, user, want_json=True, max_tokens=1500):
        raise SystemExit("Missing DEEPSEEK_API_KEY")
    law = _law("Το άρθρο 5 αντικαθίσταται ως εξής: «…»")
    try:
        extract_amendments_llm(law, complete=no_key)
        assert False, "should have propagated SystemExit"
    except SystemExit:
        pass


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
