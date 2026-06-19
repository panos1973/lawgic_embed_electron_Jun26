"""Unit tests for segment.py (pure text, no external deps)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.segment import segment  # noqa: E402
from models import Law, TYPE_NOMOS  # noqa: E402


def test_oversize_article_body_becomes_annex_subchunks():
    # an "article" whose body swallowed a ratified instrument (a treaty κύρωση,
    # ~44k chars here) must NOT become one mega-article (overflows the embedder +
    # spawns self-targeting false amendments). It becomes ANNEX sub-chunks.
    from pipeline.segment import _ENACTED_BODY_MAX
    law = Law(instrument_id="ν.5011/2023", instrument_key="N5011/2023",
              instrument_type=TYPE_NOMOS)
    body = "Κυρώνεται η Συμφωνία ως εξής:\n" + ("Διάταξη της Συμφωνίας. " * 2000)
    assert len(body) > _ENACTED_BODY_MAX
    law = segment("Άρθρο πρώτο\n" + body + "\n", law)
    annex = [p for p in law.provisions if p.chunk_type == "annex"]
    arts = [p for p in law.provisions if p.chunk_type == "article"]
    assert len(annex) > 1 and not arts                       # sub-chunked, no mega-article
    assert all(len(p.text_in_force) <= 4000 for p in annex)  # each fits the embedder
    assert all(p.canonical_id.startswith("ν.5011/2023#παραρτ.πρώτο") for p in annex)


def test_normal_article_unaffected_by_oversize_cap():
    law = Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
              instrument_type=TYPE_NOMOS)
    law = segment("Άρθρο 1\nΚανονικό σύντομο άρθρο με λίγο κείμενο.\n", law)
    assert [p.chunk_type for p in law.provisions] == ["article"]
    assert law.provisions[0].canonical_id == "ν.5090/2024#αρ.1"


def test_drops_bare_annex_divider_articles():
    """A bare 'Παραρτήματα' section header is not a real article — and two-column
    extraction can duplicate it, garbling 'Άρθρο 20' into a merged 'Άρθρο 2200'.
    Both must be dropped; the real annex (ΠΑΡΑΡΤΗΜΑ) and real articles stay."""
    law = Law(instrument_id="Β΄734/2025", instrument_key="x", instrument_type=TYPE_NOMOS)
    text = ("Άρθρο 1\nΣκοπός Ο σκοπός του παρόντος είναι σαφής.\n\n"
            "Άρθρο 20\nΠαραρτήματα\n\n"
            "Άρθρο 2200\nΠαραρτήματα\n\n"
            "ΠΑΡΑΡΤΗΜΑ 1\nΔΙΚΑΙΟΛΟΓΗΤΙΚΑ Οι υποψήφιοι υποβάλλουν τα εξής έγγραφα.\n")
    law = segment(text, law)
    arts = [p.article_no for p in law.provisions if p.chunk_type == "article"]
    assert "20" not in arts and "2200" not in arts          # both divider chunks dropped
    assert "1" in arts                                      # real article kept
    annex = [p for p in law.provisions if p.chunk_type == "annex"]
    assert len(annex) == 1 and "ΔΙΚΑΙΟΛΟΓΗΤΙΚΑ" in annex[0].text_in_force
    assert all(p.text_in_force.strip() != "Παραρτήματα" for p in law.provisions)


def test_short_no_article_document_stays_single_full_chunk():
    # backward-compatible: a short decision (no Άρθρα) is still ONE '#full' chunk
    law = Law(instrument_id="Β΄2/2025", instrument_key="x", instrument_type=TYPE_NOMOS)
    law = segment("Σύντομη απόφαση χωρίς άρθρα, ένα μικρό κείμενο.", law)
    assert len(law.provisions) == 1
    assert law.provisions[0].canonical_id == "Β΄2/2025#full"
    assert law.provisions[0].chunk_type == "document"


def test_long_no_article_document_splits_into_vectorizable_sections():
    # a no-Άρθρο body bigger than one chunk (e.g. a ΥΑ annexing a long UN resolution)
    # must split into article-sized sections so EACH is its own embeddable chunk —
    # not one oversized chunk that fails to vectorize. Language-agnostic.
    from pipeline.segment import _DOC_CHUNK_CHARS
    body = "ΑΠΟΦΑΣΕΙΣ\n" + "\n".join(
        f"{i}. The Security Council " + "decides and reaffirms " * 12
        for i in range(1, 30))
    law = Law(instrument_id="Β΄1/2025", instrument_key="x", instrument_type=TYPE_NOMOS)
    law = segment(body, law)
    secs = law.provisions
    assert len(secs) > 1                                       # split, not one blob
    assert all(p.chunk_type == "section" for p in secs)
    assert [p.canonical_id for p in secs[:2]] == ["Β΄1/2025#τμ.1", "Β΄1/2025#τμ.2"]
    # each section is within the embedder-friendly size budget
    assert all(len(p.text_in_force) <= _DOC_CHUNK_CHARS + 400 for p in secs)
    # nothing is lost: every numbered clause survives across the sections
    joined = "\n".join(p.text_in_force for p in secs)
    assert all(f"{i}. The Security Council" in joined for i in range(1, 30))


def test_chunking_never_splits_a_paragraph():
    from pipeline.segment import _split_document_body
    paras = [f"Παράγραφος {i}: " + "ουσιαστικό περιεχόμενο που συνεχίζεται. " * 20
             for i in range(1, 8)]                          # ~7 paras, ~900 chars each
    body = "\n\n".join(paras)
    secs = _split_document_body(body)
    assert len(secs) > 1                                    # packed into several chunks
    # every original paragraph lands INTACT inside exactly one section (never split)
    for p in paras:
        assert sum(p.strip() in s for s in secs) == 1, f"paragraph split: {p[:30]}"


def test_chunking_keeps_a_markdown_table_whole():
    from pipeline.segment import _split_document_body
    table = "\n".join(["| Κωδικός | Μάθημα | ECTS |", "| --- | --- | --- |"]
                      + [f"| ΒΤ_{i} | Μάθημα {i} | 5 |" for i in range(1, 30)])
    body = ("Πρόλογος της απόφασης. " * 90) + "\n\n" + table + "\n\n" + ("Επίλογος. " * 5)
    secs = _split_document_body(body)
    assert any(table.strip() in s for s in secs)            # table never split mid-row


def test_spelled_ordinal_articles():
    law = Law(instrument_id="Π.Ν.Π.1/2023", instrument_key="x",
              instrument_type=TYPE_NOMOS)
    txt = "Άρθρο πρώτο\nΠρώτη ρύθμιση.\nΆρθρο δεύτερο\nΈναρξη ισχύος.\n"
    law = segment(txt, law)
    assert [p.article_no for p in law.provisions] == ["πρώτο", "δεύτερο"]


def test_roman_numeral_articles():
    law = Law(instrument_id="ν.5011/2023", instrument_key="x",
              instrument_type=TYPE_NOMOS)
    # post-normalization XII->ΧΙΙ, XIII->ΧΙΙΙ (Greek homoglyphs)
    txt = "Άρθρο ΧΙΙ\nΕπίλυση διαφορών.\nΆρθρο ΧΙΙΙ\nΥπογραφή.\n"
    law = segment(txt, law)
    assert [p.article_no for p in law.provisions] == ["ΧΙΙ", "ΧΙΙΙ"]


def test_correspondence_table_artifacts_dropped():
    # codifying π.δ. end with bare "Άρθρο N" pairs (empty body) and repeats of
    # numbers already emitted — these must not become provisions.
    law = Law(instrument_id="π.δ.62/2025", instrument_key="x",
              instrument_type=TYPE_NOMOS)
    txt = ("Άρθρο 1\nΣκοπός του Κώδικα είναι η ρύθμιση.\n"
           "Άρθρο 2\nΟρισμοί κατά την έννοια του παρόντος.\n"
           # end correspondence table: bare pairs, incl. a repeat of Άρθρο 1
           "Άρθρο 1\nΆρθρο 148\nΆρθρο 2\nΆρθρο 149\n")
    law = segment(txt, law)
    assert [p.article_no for p in law.provisions] == ["1", "2"]   # table dropped


def _law():
    return Law(instrument_id="ν.5090/2024", instrument_key="N5090/2024",
               instrument_type=TYPE_NOMOS)


NESTED = """ΝΟΜΟΣ ΥΠ' ΑΡΙΘΜ. 5090
Κάποιος τίτλος.

Ο ΠΡΟΕΔΡΟΣ ΤΗΣ ΕΛΛΗΝΙΚΗΣ ΔΗΜΟΚΡΑΤΙΑΣ

ΜΕΡΟΣ ΠΡΩΤΟ
ΓΕΝΙΚΕΣ ΔΙΑΤΑΞΕΙΣ

ΚΕΦΑΛΑΙΟ Α
ΣΚΟΠΟΣ ΚΑΙ ΟΡΙΣΜΟΙ

Άρθρο 1
Σκοπός
Σκοπός του παρόντος νόμου είναι η ρύθμιση.

Άρθρο 2
Ορισμοί
Για την εφαρμογή ισχύουν οι ακόλουθοι ορισμοί.

ΚΕΦΑΛΑΙΟ Β
ΟΥΣΙΑΣΤΙΚΕΣ ΔΙΑΤΑΞΕΙΣ

Άρθρο 3
Πεδίο εφαρμογής
Ο παρών νόμος εφαρμόζεται σε όλους.

ΜΕΡΟΣ ΔΕΥΤΕΡΟ
ΤΕΛΙΚΕΣ ΔΙΑΤΑΞΕΙΣ

Άρθρο 4
Έναρξη ισχύος
Η ισχύς αρχίζει από τη δημοσίευση.

ΠΑΡΑΡΤΗΜΑ Ι
Πίνακας αντιστοιχίσεων
γραμμή πίνακα
"""


def test_article_count_and_ids():
    law = segment(NESTED, _law())
    arts = [p for p in law.provisions if p.chunk_type == "article"]
    assert [p.article_no for p in arts] == ["1", "2", "3", "4"]
    assert arts[0].canonical_id == "ν.5090/2024#αρ.1"


def test_titles_captured():
    law = segment(NESTED, _law())
    by_no = {p.article_no: p for p in law.provisions if p.chunk_type == "article"}
    assert by_no["1"].article_title == "Σκοπός"
    assert by_no["4"].article_title == "Έναρξη ισχύος"


def test_hierarchy_context():
    law = segment(NESTED, _law())
    by_no = {p.article_no: p for p in law.provisions if p.chunk_type == "article"}
    # article 1: ΜΕΡΟΣ ΠΡΩΤΟ / ΚΕΦΑΛΑΙΟ Α
    assert by_no["1"].part == "ΠΡΩΤΟ"
    assert by_no["1"].chapter == "Α"
    assert by_no["1"].hierarchy_path == \
        "ν.5090/2024 > ΜΕΡΟΣ ΠΡΩΤΟ > ΚΕΦΑΛΑΙΟ Α > Άρθρο 1"
    # article 3: chapter advanced to Β, still ΜΕΡΟΣ ΠΡΩΤΟ
    assert by_no["3"].chapter == "Β"
    assert by_no["3"].part == "ΠΡΩΤΟ"
    # article 4: ΜΕΡΟΣ ΔΕΥΤΕΡΟ resets chapter
    assert by_no["4"].part == "ΔΕΥΤΕΡΟ"
    assert by_no["4"].chapter == ""
    assert by_no["4"].hierarchy_path == \
        "ν.5090/2024 > ΜΕΡΟΣ ΔΕΥΤΕΡΟ > Άρθρο 4"


def test_annex_detected():
    law = segment(NESTED, _law())
    annexes = [p for p in law.provisions if p.chunk_type == "annex"]
    assert len(annexes) == 1
    assert annexes[0].canonical_id == "ν.5090/2024#παραρτ.Ι"
    assert "Πίνακας" in annexes[0].article_title


def test_masthead_not_emitted():
    law = segment(NESTED, _law())
    # nothing before Άρθρο 1 (masthead / promulgation) becomes a provision
    assert all("ΠΡΟΕΔΡΟΣ" not in p.text_in_force.split("\n")[0]
               for p in law.provisions)


def test_article_only_text_still_works():
    simple = "Άρθρο 1\nΜόνος\nΚείμενο.\n\nΆρθρο 2\nΔεύτερο\nΚι άλλο."
    law = segment(simple, _law())
    arts = [p for p in law.provisions if p.chunk_type == "article"]
    assert len(arts) == 2
    assert arts[0].part == "" and arts[0].chapter == ""
    assert arts[0].hierarchy_path == "ν.5090/2024 > Άρθρο 1"


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


def test_inserted_articles_in_guillemets_not_emitted_as_own():
    """Articles quoted inside « » (inserted into another law) must NOT become
    articles of THIS law, and must not fragment the host article."""
    law = Law(instrument_id="ν.5082/2024", instrument_key="N5082/2024",
              instrument_type=TYPE_NOMOS)
    txt = (
        "Άρθρο 13\n"
        "Κέντρα - Προσθήκη Κεφαλαίου ΣΤ1 και άρθρων 40Α έως 40ΙΑ στον ν. 4763/2020\n"
        "Μετά το άρθρο 40 του ν. 4763/2020 προστίθενται άρθρα 40Α έως 40ΙΑ ως εξής:\n"
        "«ΚΕΦΑΛΑΙΟ ΣΤ1\n"
        "Άρθρο 40Α\nΑποστολή.\n"
        "Άρθρο 40Β\nΠροϋποθέσεις.»\n\n"
        "Άρθρο 14\nΕπόμενο\nΚείμενο."
    )
    law = segment(txt, law)
    nums = [p.article_no for p in law.provisions]
    assert nums == ["13", "14"]                      # only real articles
    assert "40Α" not in nums and "40Β" not in nums   # inserted ones masked
    a13 = next(p for p in law.provisions if p.article_no == "13")
    assert "Άρθρο 40Α" in a13.text_in_force          # host article stays whole


def test_unclosed_guillemet_masks_to_end():
    law = Law(instrument_id="ν.1/2024", instrument_key="x",
              instrument_type=TYPE_NOMOS)
    txt = "Άρθρο 1\nΕισαγωγή.\nπροστίθεται ως εξής:\n«Άρθρο 5\nΞένο.\nΆρθρο 6\nΚι άλλο."
    law = segment(txt, law)
    assert [p.article_no for p in law.provisions] == ["1"]   # 5,6 stay masked


def test_markdown_article_heading_anchor_no_bleed():
    """Azure DI emits '# Άρθρο 42 Τίτλος...' on one line; it must start a new
    article so the previous one does not swallow it (art.41/42 bleed)."""
    law = Law(instrument_id="ν.5082/2024", instrument_key="N",
              instrument_type=TYPE_NOMOS)
    txt = ("Άρθρο 41\nΠαράταση.\nΚείμενο 41.\n"
           "# Άρθρο 42 Οργανικές - Τροποποίηση άρθρου 77 ν. 4662/2020\n"
           "Κείμενο 42.\nΆρθρο 43\nΕπόμενο.")
    law = segment(txt, law)
    assert [p.article_no for p in law.provisions] == ["41", "42", "43"]
    a41 = next(p for p in law.provisions if p.article_no == "41")
    assert "Άρθρο 42" not in a41.text_in_force


def test_enacted_body_quote_is_segmented_not_masked():
    """A codification/ratification quotes its WHOLE enacted body inside one outer
    « » ('Κυρώνεται ο Κώδικας ... ως εξής: «Άρθρο 1 ... Άρθρο N ...»'). That dominant
    quote is the instrument's own law, not an amendment insertion, so its articles
    must be segmented — not masked away to a single 'document' fallback chunk (the
    π.δ.62/2025 Κώδικας Εργατικού Δικαίου bug: a clean text layer -> 0 articles)."""
    law = Law(instrument_id="π.δ.99/2025", instrument_key="x",
              instrument_type=TYPE_NOMOS)
    body = "\n".join(f"Άρθρο {i}\nΤίτλος {i}\nΟυσιαστική διάταξη {i} με κείμενο."
                     for i in range(1, 9))
    txt = "ΠΡΟΕΔΡΙΚΟ ΔΙΑΤΑΓΜΑ\nΚυρώνεται ο Κώδικας που έχει ως εξής:\n«\n" + body + "\n»\n"
    law = segment(txt, law)
    arts = [p for p in law.provisions if p.chunk_type == "article"]
    assert [p.article_no for p in arts] == [str(i) for i in range(1, 9)]
    assert all(p.chunk_type != "document" for p in law.provisions)   # no fallback
    assert "Ουσιαστική διάταξη 1" in arts[0].text_in_force            # real body, not masked


def test_large_single_replacement_quote_still_masked():
    """A big quoted REPLACEMENT (one article's worth, few headers) is an insertion,
    not an enacted body — it must stay masked even when it is most of a short
    amending law. Guards the enacted-body heuristic against false positives."""
    law = Law(instrument_id="ν.9/2025", instrument_key="x",
              instrument_type=TYPE_NOMOS)
    inserted = "Άρθρο 5\n" + ("Νέο εκτενές κείμενο της αντικατάστασης. " * 30)
    txt = "Άρθρο 1\nΑντικατάσταση\nΤο άρθρο 5 αντικαθίσταται ως εξής:\n«" + inserted + "»\n"
    law = segment(txt, law)
    nums = [p.article_no for p in law.provisions if p.chunk_type == "article"]
    assert nums == ["1"]                          # the quoted Άρθρο 5 stays masked


def test_correspondence_table_reference_body_dropped():
    """A bare 'Άρθρο N' whose body is itself an article reference ('Άρθρο M, όπως
    ...') is a codification correspondence/derivation-table row (new article ->
    source provision), not substantive law — it must not become a provision."""
    law = Law(instrument_id="π.δ.62/2025", instrument_key="x",
              instrument_type=TYPE_NOMOS)
    txt = ("Άρθρο 1\nΣκοπός\nΟ Κώδικας ρυθμίζει τις σχέσεις εργασίας με σαφήνεια.\n"
           "Άρθρο 678\nΆρθρο 679, όπως διαμορφώθηκε και ισχύει\n"
           "Άρθρο 680\nΆρθρο 681 ΑΚ\n")
    law = segment(txt, law)
    nums = [p.article_no for p in law.provisions if p.chunk_type == "article"]
    assert nums == ["1"]                          # table rows 678/680 dropped
