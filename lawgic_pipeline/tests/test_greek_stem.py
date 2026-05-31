"""Tests for greek_stem — Greek morphological stemming for the BM25 field.

The contract is recall across inflection: inflectional variants of one lemma
must collapse to a single index term, and the SAME transform must apply to
ingest text and queries. Tests run against the dependency-free built-in backend
(the shipping default) so they are deterministic regardless of whether the
optional Snowball wheel is present.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import greek_stem  # noqa: E402
from greek_stem import stem_text, stem_query  # noqa: E402
from normalize import fold_for_bm25  # noqa: E402


@pytest.fixture(autouse=True)
def _force_builtin(monkeypatch):
    # Pin the deterministic, dependency-free backend for every test.
    monkeypatch.setattr(greek_stem, "_backend", "builtin")
    monkeypatch.setattr(greek_stem, "_snowball", None)
    yield


def _s(word: str) -> str:
    return stem_text(word)


def test_noun_inflection_collapses():
    # νόμος / νόμου / νόμων / νόμο -> one stem (the core recall win).
    assert len({_s(w) for w in ["νόμος", "νόμου", "νόμων", "νόμο"]}) == 1


def test_article_inflection_collapses():
    assert _s("άρθρο") == _s("άρθρου") == _s("άρθρα")


def test_verb_and_noun_family_collapse():
    # noun τροποποίηση and verb τροποποιείται share a stem -> a query for the
    # noun finds an article that only uses the verb form (and vice-versa).
    assert _s("τροποποίηση") == _s("τροποποιείται")
    assert _s("κατάργηση") == _s("καταργείται")


def test_genitive_plurals_collapse():
    # διάταξη / διατάξεις / διατάξεων -> one stem.
    assert _s("διάταξη") == _s("διατάξεις") == _s("διατάξεων")


def test_query_equals_ingest():
    s = "Η παράγραφος 2 του άρθρου 24 τροποποιείται"
    assert stem_query(s) == stem_text(s)


def test_idempotent():
    once = stem_text("τροποποιήσεις των διατάξεων")
    assert stem_text(once) == once


def test_numbers_and_citation_atoms_preserved():
    out = stem_text("ν. 4675/2024")
    assert "4675" in out and "2024" in out


def test_latin_left_alone():
    assert "covid" in stem_text("COVID-19").lower()


def test_short_tokens_not_overstemmed():
    # 3-char and shorter tokens are left intact (no stripping below _MIN_STEM).
    assert stem_text("και") == "και"
    assert stem_text("ως") == fold_for_bm25("ως")


def test_empty_and_none():
    assert stem_text("") == ""
    assert stem_text(None) is None


def test_is_refinement_of_fold():
    # Stemming must not reintroduce case/accents: the output is still folded.
    out = stem_text("ΤΡΟΠΟΠΟΙΗΣΗ Άρθρου")
    assert out == out.lower()
    assert "ά" not in out and "ή" not in out


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q"]))
