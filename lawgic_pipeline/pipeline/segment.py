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
from greek_stem import stem_text

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

# A provision body that opens with a bare "Άρθρο N" reference is a correspondence/
# derivation-table row, not a real article (real bodies open with the title text).
_REF_BODY = re.compile(r"Άρθρο\s+\d")


def _first_line(block: str) -> str:
    for line in block.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


# A no-Άρθρο document (most FEK decisions, and acts whose body is a verbatim
# foreign-language annex like a ratified UN resolution) must still be chunked into
# article-sized, individually embeddable sections — NOT one giant document chunk
# that overflows the embedder and leaves the text un-vectorized. Language-agnostic:
# Greek preamble, English body and Greek translation are all packed the same way.
_DOC_CHUNK_CHARS = 2000


# A numbered/lettered clause start ("1.", "12)", "α)", "Α.") — a paragraph boundary
# in flat decisions, so each clause becomes its own packable unit.
_CLAUSE_START = re.compile(r"^\s*(?:\d{1,3}|[Α-Ωα-ωA-Za-z])[.)]\s")


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return len(s) >= 3 and s[0] == "|" and s[-1] == "|"


def _paragraphs(body: str) -> list[str]:
    """Split a body into atomic blocks: blank-line-separated paragraphs, each
    numbered clause, and each markdown table (kept whole). Content-lossless."""
    paras: list[str] = []
    cur: list[str] = []

    def flush():
        if cur:
            j = "\n".join(cur).strip()
            if j:
                paras.append(j)
            cur.clear()

    prev_table = False
    for line in body.split("\n"):
        if not line.strip():                          # blank line -> boundary
            flush()
            prev_table = False
            continue
        is_tbl = _is_table_row(line)
        # boundary: a table<->text transition, or a new (non-table) numbered clause
        if cur and (is_tbl != prev_table
                    or (not is_tbl and _CLAUSE_START.match(line))):
            flush()
        cur.append(line)
        prev_table = is_tbl
    flush()
    return paras


def _hard_wrap(text: str, target: int) -> list[str]:
    """Last resort: slice a single over-budget paragraph into target-sized pieces."""
    return [text[i:i + target] for i in range(0, len(text), target)]


def _split_document_body(body: str, target: int = _DOC_CHUNK_CHARS) -> list[str]:
    """Pack a no-Άρθρο body into <=~target-char sections at PARAGRAPH boundaries —
    never splitting a paragraph, numbered clause, or markdown table mid-way (only a
    single paragraph FAR larger than the budget is hard-wrapped, as a last resort,
    so a chunk never overflows the embedder). Content-lossless: every non-whitespace
    character lands in exactly one section. Returns [body] when it fits in one chunk
    (a short decision stays whole)."""
    body = body.strip()
    if len(body) <= target:
        return [body]
    sections: list[str] = []
    cur: list[str] = []
    cur_len = 0

    def flush():
        nonlocal cur, cur_len
        if cur:
            joined = "\n\n".join(cur).strip()
            if joined:
                sections.append(joined)
            cur, cur_len = [], 0

    for p in _paragraphs(body):
        if len(p) > target:
            # a single paragraph over budget: emit on its own — kept WHOLE unless it
            # alone overflows badly, then hard-wrapped so the embedder never chokes.
            flush()
            sections.extend(_hard_wrap(p, target) if len(p) > int(target * 1.5) else [p])
            continue
        if cur_len and cur_len + len(p) + 2 > target:
            flush()                                   # adding p would overflow -> close
        cur.append(p)
        cur_len += len(p) + 2
    flush()
    return [s for s in sections if s] or [body]


# A quoted span longer than this fraction of the whole instrument, AND carrying a
# run of article headers, is not a local insertion — it is the enacted/ratified
# body itself. A codification/ratification quotes its entire code in one outer span
# ("Κυρώνεται ο Κώδικας ... ως εξής: «Άρθρο 1 ... Άρθρο 587 ...»"); masking it would
# blank the whole document (π.δ.62/2025 -> 0 articles, one 'document' chunk). The
# article-count floor keeps a single large *replacement* insertion (one article's
# worth of quoted text) masked, so only genuine enacted bodies are segmented.
_ENACTED_BODY_FRACTION = 0.6
_ENACTED_MIN_ARTICLES = 5
_ARTICLE_HEADER_HINT = re.compile(r"(?m)^[^\S\r\n]*#{0,6}[^\S\r\n]*Άρθρο[^\S\r\n]+\d")


def _quote_forest(text: str) -> list[dict]:
    """Parse balanced « » into a nesting forest of {start, end, children} nodes.

    An unclosed « (truncated/missing close) is closed at end of text, so a dangling
    quote can't let inserted headers leak back in as real articles.
    """
    roots: list[dict] = []
    stack: list[dict] = []
    for i, ch in enumerate(text):
        if ch == "«":
            stack.append({"start": i, "children": []})
        elif ch == "»" and stack:
            node = stack.pop()
            node["end"] = i
            (stack[-1]["children"] if stack else roots).append(node)
    while stack:                                   # unclosed -> close to end
        node = stack.pop()
        node["end"] = len(text)
        (stack[-1]["children"] if stack else roots).append(node)
    return roots


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    """Char ranges that must be EXCLUDED from segmentation (depth-aware).

    Amending laws quote the text they insert/replace inside « », and that quoted
    block frequently contains its OWN 'Άρθρο 40Α', 'ΚΕΦΑΛΑΙΟ ΣΤ1' headers — articles
    of the TARGET law, not of this enacting law. We must not segment on headers
    inside these spans; they belong to the host (amending) article and travel with
    it. Returns top-level spans (handles nesting).

    Exception: a quote that dominates the instrument and itself holds a run of
    article headers is the enacted body of a codification/ratification, not an
    insertion — segment INSIDE it and mask only its nested children (the foreign
    quotes inside the code's own articles).
    """
    n = len(text)
    masks: list[tuple[int, int]] = []

    def visit(nodes: list[dict]) -> None:
        for nd in nodes:
            inner = text[nd["start"]:nd["end"]]
            enacted_body = (
                len(inner) > _ENACTED_BODY_FRACTION * n
                and len(_ARTICLE_HEADER_HINT.findall(inner)) >= _ENACTED_MIN_ARTICLES)
            if enacted_body:
                visit(nd["children"])              # unmask self, mask foreign quotes
            else:
                masks.append((nd["start"], nd["end"]))

    visit(_quote_forest(text))
    masks.sort()
    return masks


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


# A single article/annex body larger than this is not one provision — it is a
# ratified/enacted instrument (an international treaty, a whole code) that the
# article merely introduces ("Κυρώνεται … ως εξής: <TREATY>"). Sliced whole into
# one chunk it overflows the 32k embedder (pooled into a blurry mega-vector) AND
# its internal "Article X is replaced …" lines get mis-mined as amendments to THIS
# law (self-targeting false edges). So emit such a body as ANNEX sub-chunks:
# retrievable at sub-chunk granularity, and excluded from amendment extraction
# (annex = verbatim ratified text, not amending provisions of the enacting law).
# The threshold sits far above any real single article, so normal laws are
# untouched (it only fires on a body that would itself overflow the embedder).
_ENACTED_BODY_MAX = 30_000


def _annex_subchunks(law: Law, state: dict, base_loc: str, body: str) -> None:
    """Emit `body` as one or more ANNEX provisions, sub-chunked under base_loc."""
    secs = _split_document_body(body)
    for i, sec in enumerate(secs, 1):
        loc = base_loc if len(secs) == 1 else f"{base_loc}.τμ.{i}"
        path = " > ".join([law.instrument_id, *_structural_path(state), loc])
        law.provisions.append(Provision(
            canonical_id=f"{law.instrument_id}#{loc}",
            instrument_id=law.instrument_id, instrument_key=law.instrument_key,
            instrument_type=law.instrument_type, fek_series=law.fek_series,
            fek_number=law.fek_number, fek_date=law.fek_date,
            article_no=loc, article_title=_first_line(sec),
            level="annex", chunk_type="annex",
            book=state["book"], part=state["part"], chapter=state["chapter"],
            hierarchy_path=path, text_in_force=sec,
            text_normalized=fold_for_bm25(sec), text_stemmed=stem_text(sec),
            content_hash=hashlib.sha256(sec.encode()).hexdigest()))


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
            if len(body) > _ENACTED_BODY_MAX:      # huge annex -> retrievable sub-chunks
                _annex_subchunks(law, state, loc, body)
                continue
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
                text_stemmed=stem_text(body),
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
        # A body that is ITSELF a bare article reference ("Άρθρο 679, όπως ...") is
        # a correspondence/derivation-table row — codifications close with tables
        # mapping each codified article to its source provision — not substantive
        # law. Skip without claiming the number, so a real header for it elsewhere
        # can still be emitted.
        if _REF_BODY.match(body):
            continue
        seen_articles.add(art_no)
        if len(body) > _ENACTED_BODY_MAX:
            # this "article" body swallowed a ratified/enacted instrument (e.g. a
            # treaty κύρωση whose verbatim text has no «»/Άρθρο structure we could
            # split on) -> emit it as annex sub-chunks instead of one mega-article.
            _annex_subchunks(law, state, f"παραρτ.{art_no}", body)
            continue
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
            text_stemmed=stem_text(body),
            content_hash=hashlib.sha256(body.encode()).hexdigest()))

    # Fallback: instruments with no internal Άρθρο/ΠΑΡΑΡΤΗΜΑ structure (most FEK
    # decisions) must still yield one embeddable provision covering the whole act.
    if not law.provisions:
        body = text.strip()
        if body:
            sections = _split_document_body(body)
            n = len(sections)
            for i, sec in enumerate(sections):
                # one section -> keep the historical '#full' id and 'document' type
                # (byte-identical to the pre-split behaviour for short decisions);
                # many -> '#τμ.N' sections so each is its own vectorized chunk.
                cid = (f"{law.instrument_id}#full" if n == 1
                       else f"{law.instrument_id}#τμ.{i + 1}")
                law.provisions.append(Provision(
                    canonical_id=cid, instrument_id=law.instrument_id,
                    instrument_key=law.instrument_key,
                    instrument_type=law.instrument_type,
                    fek_series=law.fek_series, fek_number=law.fek_number,
                    fek_date=law.fek_date,
                    article_no="" if n == 1 else str(i + 1),
                    article_title=_first_line(sec),
                    level="document", chunk_type="document" if n == 1 else "section",
                    hierarchy_path=law.instrument_id,
                    text_in_force=sec, text_normalized=fold_for_bm25(sec),
                    text_stemmed=stem_text(sec),
                    content_hash=hashlib.sha256(sec.encode()).hexdigest()))
    return law
