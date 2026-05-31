"""segment.py — morphology segmentation into article-level provisions.

Stores one Provision per Άρθρο (the embed/retrieval unit), but each provision
carries its full structural context — ΒΙΒΛΙΟ / ΜΕΡΟΣ / ΚΕΦΑΛΑΙΟ / ΤΜΗΜΑ — in
book/part/chapter plus a human-readable hierarchy_path. ΠΑΡΑΡΤΗΜΑ (annex) blocks
become annex provisions. Anything before the first structural anchor (masthead,
promulgation) is not emitted as a provision.

The parser is anchor-based: it finds every structural/article/annex header line,
walks them in document order while maintaining the current hierarchy, and slices
each article body up to the next anchor of any kind (so a new ΚΕΦΑΛΑΙΟ correctly
terminates the preceding article).
"""
from __future__ import annotations

import hashlib
import re

from models import Law, Provision, make_provision_id
from normalize import fold_for_bm25

# Structural + leaf anchors. All require the keyword at the start of a line
# (headers in FEK sit on their own line, usually upper-case). The label group is
# the ordinal word or letter that follows (ΠΡΩΤΟ, Α΄, 2, ...).
_LABEL = r"([A-Za-zΑ-Ωα-ω0-9]+['΄ʼ’]?)"
_ANCHORS = [
    ("book",    re.compile(r"(?m)^\s*ΒΙΒΛΙΟ\s+" + _LABEL + r"\s*$")),
    ("part",    re.compile(r"(?m)^\s*ΜΕΡΟΣ\s+" + _LABEL + r"\s*$")),
    ("chapter", re.compile(r"(?m)^\s*ΚΕΦΑΛΑΙΟ\s+" + _LABEL + r"\s*$")),
    ("section", re.compile(r"(?m)^\s*ΤΜΗΜΑ\s+" + _LABEL + r"\s*$")),
    ("article", re.compile(r"(?m)^\s*Άρθρο\s+(\d+[Α-Ωα-ω]?)\.?\s*$")),
    # markdown-heading article (Azure DI path): "# Άρθρο 42 Τίτλος..." — heading
    # and title share the line. Unambiguous (a bare 'Άρθρο 42' cross-reference in
    # body text never starts with '#'), so this safely bounds the next article
    # and stops the previous one from swallowing it (the art.41/42 bleed).
    ("article", re.compile(r"(?m)^\s*#{1,6}\s*Άρθρο\s+(\d+[Α-Ωα-ω]?)\b.*$")),
    # spelled-out ordinals (κύρωση/ΠΝΠ/short laws): "Άρθρο πρώτο", "Άρθρο μόνο"
    ("article", re.compile(
        r"(?im)^\s*Άρθρο\s+(πρώτο|δεύτερο|τρίτο|τέταρτο|πέμπτο|έκτο|έβδομο|όγδοο|"
        r"ένατο|δέκατο|μόνο)\.?\s*$")),
    # Roman numerals (international-treaty articles): "Άρθρο XII" — post-glyph
    # normalization these mix Greek (Ι Χ Μ) and Latin (V L C D) homoglyphs.
    ("article", re.compile(r"(?m)^\s*Άρθρο\s+([ΙΧΜIVXLCDM]{2,7})\.?\s*$")),
    ("annex",   re.compile(r"(?m)^\s*ΠΑΡΑΡΤΗΜΑ\s*" + _LABEL + r"?\s*$")),
]

# Hierarchy nesting order (shallow -> deep) and the keyword shown in the path.
_ORDER = ["book", "part", "chapter", "section"]
_KEYWORD = {"book": "ΒΙΒΛΙΟ", "part": "ΜΕΡΟΣ", "chapter": "ΚΕΦΑΛΑΙΟ",
            "section": "ΤΜΗΜΑ"}


def _first_line(block: str) -> str:
    for line in block.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    """Char ranges enclosed in Greek guillemets « … » (depth-aware).

    Amending laws quote the text they insert/replace inside « », and that quoted
    block frequently contains its OWN 'Άρθρο 40Α', 'ΚΕΦΑΛΑΙΟ ΣΤ1' headers — but
    those are articles of the TARGET law, not of this enacting law. We must not
    segment on headers inside these spans; they belong to the host (amending)
    article and travel with it. Returns top-level spans only (handles nesting).
    """
    spans: list[tuple[int, int]] = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "«":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "»" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                spans.append((start, i))
                start = -1
    # an unclosed « (truncated/missing close): treat to end of text so a dangling
    # quote can't let inserted headers leak back in as real articles
    if depth > 0 and start >= 0:
        spans.append((start, len(text)))
    return spans


def _in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a < pos < b for a, b in spans)


def _structural_path(state: dict) -> list[str]:
    """Human-readable structural prefix, e.g. ['ΜΕΡΟΣ ΠΡΩΤΟ', 'ΚΕΦΑΛΑΙΟ Α']."""
    out = []
    for lvl in _ORDER:
        tok = state.get(lvl)
        if tok:
            out.append(f"{_KEYWORD[lvl]} {tok}")
    return out


def segment(text: str, law: Law) -> Law:
    """Populate law.provisions (article-level) with full hierarchy context."""
    # Headers inside « » quoted blocks are inserted/restated text of ANOTHER law
    # (the amendment target), not articles of this one — exclude them so e.g.
    # 'Άρθρο 40Α' quoted inside an amending article does not become ν.<this>#αρ.40Α
    # and does not fragment the host article.
    quoted = _quoted_spans(text)

    anchors = []
    for kind, rx in _ANCHORS:
        for m in rx.finditer(text):
            if _in_spans(m.start(), quoted):
                continue
            anchors.append((m.start(), m.end(), kind, m))
    anchors.sort(key=lambda a: a[0])

    state = {lvl: "" for lvl in _ORDER}        # current structural tokens
    seen_articles: set[str] = set()            # de-dup repeated Άρθρο headers

    for idx, (start, end, kind, m) in enumerate(anchors):
        nxt = anchors[idx + 1][0] if idx + 1 < len(anchors) else len(text)
        body = text[end:nxt].strip()

        if kind in _ORDER:
            token = (m.group(1) or "").strip()
            state[kind] = token
            # entering a shallower level resets everything deeper
            for deeper in _ORDER[_ORDER.index(kind) + 1:]:
                state[deeper] = ""
            continue

        if kind == "annex":
            token = (m.group(1) or "").strip()
            loc = f"παραρτ.{token}" if token else "παραρτ"
            cid = f"{law.instrument_id}#{loc}"
            path = " > ".join([law.instrument_id, *_structural_path(state),
                               f"ΠΑΡΑΡΤΗΜΑ {token}".strip()])
            law.provisions.append(Provision(
                canonical_id=cid, instrument_id=law.instrument_id,
                instrument_key=law.instrument_key,
                instrument_type=law.instrument_type,
                fek_series=law.fek_series, fek_number=law.fek_number,
                fek_date=law.fek_date,
                article_no=token, article_title=_first_line(body),
                level="annex", chunk_type="annex",
                book=state["book"], part=state["part"], chapter=state["chapter"],
                hierarchy_path=path, text_in_force=body,
                text_normalized=fold_for_bm25(body),
                content_hash=hashlib.sha256(body.encode()).hexdigest()))
            continue

        # kind == "article"
        art_no = m.group(1)
        # An "Άρθρο N" line with no body, or a repeat of an article number already
        # emitted, is not a real header — it is a correspondence-table / index
        # artifact (codifying π.δ. end up with hundreds of bare "Άρθρο X" pairs).
        # Within one instrument an article number is unique, so keep the first.
        if not body or art_no in seen_articles:
            continue
        seen_articles.add(art_no)
        title = _first_line(body)
        cid = make_provision_id(law.instrument_id, art_no)
        path = " > ".join([law.instrument_id, *_structural_path(state),
                           f"Άρθρο {art_no}"])
        law.provisions.append(Provision(
            canonical_id=cid, instrument_id=law.instrument_id,
            instrument_key=law.instrument_key,
            instrument_type=law.instrument_type,
            fek_series=law.fek_series, fek_number=law.fek_number,
            fek_date=law.fek_date,
            article_no=art_no, article_title=title,
            level="article", chunk_type="article",
            book=state["book"], part=state["part"], chapter=state["chapter"],
            hierarchy_path=path, text_in_force=body,
            text_normalized=fold_for_bm25(body),
            content_hash=hashlib.sha256(body.encode()).hexdigest()))

    # Fallback: instruments with no internal Άρθρο/ΠΑΡΑΡΤΗΜΑ structure (most FEK
    # decisions) must still yield one embeddable provision covering the whole act.
    if not law.provisions:
        body = text.strip()
        if body:
            cid = f"{law.instrument_id}#full"
            law.provisions.append(Provision(
                canonical_id=cid, instrument_id=law.instrument_id,
                instrument_key=law.instrument_key,
                instrument_type=law.instrument_type,
                fek_series=law.fek_series, fek_number=law.fek_number,
                fek_date=law.fek_date,
                article_no="", article_title=_first_line(body),
                level="document", chunk_type="document",
                hierarchy_path=law.instrument_id,
                text_in_force=body, text_normalized=fold_for_bm25(body),
                content_hash=hashlib.sha256(body.encode()).hexdigest()))
    return law
