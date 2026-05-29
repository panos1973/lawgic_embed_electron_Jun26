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
    anchors = []
    for kind, rx in _ANCHORS:
        for m in rx.finditer(text):
            anchors.append((m.start(), m.end(), kind, m))
    anchors.sort(key=lambda a: a[0])

    state = {lvl: "" for lvl in _ORDER}        # current structural tokens

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
