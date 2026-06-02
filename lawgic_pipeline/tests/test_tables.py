"""Tables: sidecar markdown rendering + structured recovery for table_json,
plus the Greek/Latin language tag. No network, no PDF."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sidecar.pdf_extract import _table_to_markdown  # noqa: E402
from pipeline.tables import tables_from_text, tables_json  # noqa: E402
from pipeline.normalize import language_of  # noqa: E402


def test_table_to_markdown_renders_grid():
    # regression: _table_to_markdown was undefined (NameError swallowed by the
    # extractor), so every table silently failed to render. It must produce a
    # GitHub-markdown grid now.
    md = _table_to_markdown([["Κλίμακα", "Ποσό"], ["1", "100"], ["2", "200"]])
    lines = md.splitlines()
    assert lines[0] == "| Κλίμακα | Ποσό |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == "| 1 | 100 |"


def test_render_then_recover_roundtrips():
    rows = [["Κλίμακα", "Συντελεστής"], ["0-10000", "9%"], ["10001+", "22%"]]
    recovered = tables_from_text(_table_to_markdown(rows))
    assert recovered == [rows]


def test_tables_json_is_none_without_a_table():
    assert tables_json("Άρθρο 1. Καμία στήλη εδώ, απλό κείμενο.") is None
    # a single stray pipe in prose must not be mistaken for a table
    assert tables_json("κόστος 1,00 € | ανά σελίδα") is None


def test_tables_json_encodes_embedded_table():
    text = ("Άρθρο 5 Πίνακας τελών\n"
            "| Υπηρεσία | Τέλος |\n| --- | --- |\n| Α | 10 |\n| Β | 20 |\n"
            "Τα ανωτέρω ισχύουν από 1.1.2024.")
    js = tables_json(text)
    assert js is not None
    assert json.loads(js) == [[["Υπηρεσία", "Τέλος"], ["Α", "10"], ["Β", "20"]]]


def test_two_tables_in_one_chunk():
    text = ("| a | b |\n| --- | --- |\n| 1 | 2 |\n"
            "ενδιάμεσο κείμενο\n"
            "| c | d |\n| --- | --- |\n| 3 | 4 |")
    assert len(tables_from_text(text)) == 2


def test_language_of_greek_english_mixed():
    assert language_of("Άρθρο 1. Ο παρών νόμος τίθεται σε ισχύ.") == "el"
    assert language_of("The Security Council, acting under Chapter VII, decides.") == "en"
    # a ratified resolution: Greek wrapper + substantial verbatim English body
    mixed = ("Κυρώνεται η απόφαση. The Security Council, Acknowledging the report, "
             "Affirms its commitment and Decides to extend the mandate of BINUH.")
    assert language_of(mixed) == "mixed"
    assert language_of("") == "el"            # no letters -> Greek default
    assert language_of("123 / 456 — €") == "el"


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
