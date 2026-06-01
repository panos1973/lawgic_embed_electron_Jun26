"""greek_stem.py — Greek morphological stemming for the BM25 search field.

Why this exists
---------------
Greek is heavily inflected: one lemma surfaces as νόμος / νόμου / νόμων / νόμο,
or τροποποίηση / τροποποιείται / τροποποιήσεις. Plain accent-folding
(``normalize.fold_for_bm25``) lowercases and de-accents but leaves these as
DISTINCT tokens, so a BM25 search for "τροποποίηση" misses an article that only
says "τροποποιείται" — the classic "missing Greek word endings" recall hole.
The old (TS/Electron) embedder closed it with a Snowball Greek stemmer; this is
the Python equivalent, applied to BOTH the stored search text AND the query,
identically (the same non-negotiable that governs ``fold_for_bm25``).

Design — self-contained first
-----------------------------
The shipping artifact is a frozen Windows .exe, and the managed build env is
externally-managed, so we MUST NOT hard-depend on a C/WASM stemmer wheel being
present. Therefore the primary path is a deterministic, pure-Python inflectional
suffix stemmer (no dependencies). If the optional ``snowballstemmer`` package
*is* importable we prefer it (more linguistically complete), but everything
degrades cleanly to the built-in — ingest never hard-fails, and the field is
always populated.

Both stemmers operate on FOLDED text (lowercase, de-accented, final-sigma → σ),
so stemming is a strict refinement of ``fold_for_bm25``; the two stay consistent
and the analyzer sees the same terms on query and ingest sides.
"""
from __future__ import annotations

import re

from normalize import fold_for_bm25

# Below this length we don't stem (ν, αρ, ωσ, και, του…) and we never strip a
# token below this many residual characters — over-stemming 2–3-char tokens
# costs more precision than the recall it buys.
_MIN_STEM = 3

# Greek word run in FOLDED text (lowercase, de-accented, final-sigma already
# normalized to σ by fold_for_bm25). ς is included defensively.
_GREEK_WORD = re.compile(r"[α-ωϊϋ]+")

# Inflectional suffixes to strip, in the FOLDED alphabet, ordered longest-first
# so the longest legitimate ending wins (e.g. -ηση beats -η on τροποποιηση).
# Curated for the dominant noun/adjective/verb paradigms in legal Greek; the
# goal is that inflectional variants of one lemma collapse to one index term,
# applied identically to ingest text and queries.
_SUFFIXES = sorted(
    [
        # neuter -μα paradigm (γραμμα, καθεστωσ → -ματ-): keep the -ματ stem
        "ματων", "ματοσ", "ματα",
        # verbal-noun / 3rd-declension -ση/-ξη families and their genitives
        "ησεων", "ησεωσ", "ησεισ", "ξεων", "ξεωσ", "ξεισ",
        # mediopassive verb endings — τροποποιειται / καταργειται / -ουνται …
        "ουνται", "ονται", "ομαι", "ειται", "εται", "ηκαν", "θηκε",
        # active verb endings — -ουμε / -ουν / -ετε / -εισ / -ω …
        "ουμε", "ουν", "ετε", "εισ", "ησε", "ασε",
        # verbal nouns -ηση / -ξη
        "ηση", "ξη",
        # 3rd-declension genitives -εων / -εωσ
        "εων", "εωσ",
        # plural / genitive endings shared across declensions
        "ουσ", "οισ", "ων", "εσ", "οι", "ασ", "ησ",
        # singular case endings
        "οσ", "ου", "ο", "α", "η", "ε", "ω", "υ", "ι",
    ],
    key=len, reverse=True,
)

_snowball = None
_backend: str | None = None       # "snowball" | "builtin" | None(=uninit)


def _init_backend():
    global _snowball, _backend
    if _backend is not None:
        return
    try:
        import snowballstemmer
        if "greek" in snowballstemmer.algorithms():
            _snowball = snowballstemmer.stemmer("greek")
            _backend = "snowball"
            return
    except Exception:                          # pragma: no cover - env-dependent
        pass
    _backend = "builtin"


def backend() -> str:
    """Which stemmer is active: 'snowball' (optional) or 'builtin' (default)."""
    _init_backend()
    return _backend


def _builtin_stem(tok: str) -> str:
    """Strip inflectional suffixes to a fixed point, guarding stem length.

    Strips the longest matching suffix and repeats until no rule applies, so the
    result is a terminal stem. Iterating (rather than a single pass) is what makes
    the transform idempotent — re-stemming a stem is a no-op — and it lets every
    inflection of a lemma converge to the SAME index term even when the residual
    stem itself still ends in a case vowel (e.g. διαταξεων → διατα → διατ, the
    same fixed point reached by διαταξη → διατα → διατ). Each strip shortens the
    token and never goes below _MIN_STEM, so the loop always terminates.
    """
    while len(tok) > _MIN_STEM:
        for suf in _SUFFIXES:
            if tok.endswith(suf) and len(tok) - len(suf) >= _MIN_STEM:
                tok = tok[: -len(suf)]
                break
        else:
            break
    return tok


def _stem_token(tok: str) -> str:
    if _backend == "snowball":
        return _snowball.stemWord(tok)
    return _builtin_stem(tok)


def stem_text(text):
    """Fold, then stem each Greek token. Idempotent; identical on query+ingest.

    Non-Greek runs (numbers, Latin, punctuation, whitespace) pass through the
    fold unchanged. None/empty pass straight through.
    """
    if not text:
        return text
    _init_backend()
    folded = fold_for_bm25(text)
    return _GREEK_WORD.sub(lambda m: _stem_token(m.group(0)), folded)


def stem_query(text):
    """Alias for the retrieval side — identical stemming to ingest."""
    return stem_text(text)
