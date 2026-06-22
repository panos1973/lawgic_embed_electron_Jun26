"""voyage_embed.py — contextualized embeddings + rerank (voyage-context-3 / rerank-2.5).

Chunks are sent nested so each provision vector carries surrounding context. A
law longer than one context window is split into several windows; chunks share
context within a window. voyage-context-3's window is 32k tokens PER input
document (the list of chunks sent together), so a long law MUST be split or the
API rejects it ("example ... too many tokens ... context window of 32000").
"""
from __future__ import annotations
import math
import re
import time
from typing import Callable, Optional

import voyageai
import config
import logsetup
import ratelimit

_client = None
# voyage-context-3 context window: 32k tokens per input document (the list of
# chunks sent together). We pack chunks into windows under SAFETY*32k.
CONTEXT_WINDOW_TOKENS = 32_000
MAX_CHUNKS = 16_000          # voyage per-request chunk cap
SAFETY = 0.75
# Oversize chunks are split, embedded as SEPARATE documents, and pooled. Keep each
# sub-segment well under the window: the char→token estimate under-counts dense
# OCR'd Greek/English, so a piece we measured at ~24k tokens measured ~32k+ at the
# API and was rejected. 0.45 leaves ~2.2x head-room against that under-count.
OVERSIZE_SAFETY = 0.45
# Greek legal text tokenizes DENSELY: ~1.5 chars/token (the old embedder's
# proven value). Using 3 here under-counted by ~2x, so a window we estimated at
# 23k was really ~46k tokens and Voyage rejected it (>32k). Stay conservative.
CHARS_PER_TOKEN = 1.5
# voyage-context-3 rejects an empty / whitespace-only input. A blank provision (an
# image- or drawing-only page Azure DI returned empty for, a structure-only section)
# must not fail the WHOLE document, so blanks are sent as this placeholder instead.
_BLANK_PLACEHOLDER = "[χωρίς κείμενο]"

log = logsetup.get("embed")


def client():
    global _client
    if _client is None:
        config.require("VOYAGE_API_KEY")
        _client = voyageai.Client(api_key=config.VOYAGE_API_KEY)
    return _client


def _est_tokens(t: str) -> int:
    return max(1, int(len(t) / CHARS_PER_TOKEN))


def _split_oversized(text: str, budget: int) -> list[str]:
    """Split one over-budget chunk into <=budget sub-segments WITHOUT losing text.

    Greedy, boundary-aware: accumulate paragraphs/sentences until the next piece
    would exceed the budget. Falls back to a hard character cut only for a single
    piece that is itself larger than the budget (e.g. a giant unbroken table row).
    Used only for the rare provision that alone exceeds the 32k window — a normal
    article never triggers this. Every character ends up in exactly one segment.
    """
    max_chars = int(budget * CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return [text]
    # prefer splitting on paragraph, then sentence, then whitespace boundaries
    pieces = re.split(r"(\n\n+|(?<=[.;·])\s+)", text)
    pieces = [p for p in pieces if p and not p.isspace()]
    segments: list[str] = []
    cur = ""
    for p in pieces:
        # a single piece bigger than the budget: hard-cut it into max_chars slices
        if len(p) > max_chars:
            if cur:
                segments.append(cur)
                cur = ""
            for i in range(0, len(p), max_chars):
                segments.append(p[i:i + max_chars])
            continue
        if cur and len(cur) + len(p) > max_chars:
            segments.append(cur)
            cur = p
        else:
            cur += p
    if cur:
        segments.append(cur)
    return segments or [text[:max_chars]]


def _pool(vectors: list[list[float]]) -> list[float]:
    """Mean-pool sub-segment vectors back into one, re-normalized to unit length.

    voyage-context-3 returns unit vectors and the index uses DOT distance, so the
    combined vector must also be unit length. The mean direction is the standard,
    cheap way to represent a chunk that had to be embedded in pieces.
    """
    if len(vectors) == 1:
        return vectors[0]
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v in vectors:
        for j in range(dim):
            acc[j] += v[j]
    norm = math.sqrt(sum(x * x for x in acc)) or 1.0
    return [x / norm for x in acc]


def _plan_batches(chunks: list[str]) -> list[list[int]]:
    """Group chunk indices into context windows that respect the token/chunk budget.

    Pure (no network) so it can be unit-tested: a single window when everything
    fits the budget, otherwise greedy packing by estimated tokens / chunk count.
    A single chunk larger than the budget is sent in its own window (best effort;
    logged) rather than silently dropped or merged.
    """
    if not chunks:
        return []
    budget = int(CONTEXT_WINDOW_TOKENS * SAFETY)
    if sum(_est_tokens(c) for c in chunks) <= budget and len(chunks) <= MAX_CHUNKS:
        return [list(range(len(chunks)))]
    batches: list[list[int]] = []
    cur: list[int] = []
    tok = 0
    for i, c in enumerate(chunks):
        t = _est_tokens(c)
        if t > budget:
            log.warning("chunk %d alone ~%d tok exceeds window budget %d — sending solo",
                        i, t, budget)
        if cur and (tok + t > budget or len(cur) >= MAX_CHUNKS):
            batches.append(cur)
            cur, tok = [], 0
        cur.append(i)
        tok += t
    if cur:
        batches.append(cur)
    return batches


def _embed_one(chunk: str) -> list[float]:
    """Embed ONE chunk to ONE vector. A chunk that alone exceeds the context window is
    split into sub-window pieces, each embedded as its OWN document, then mean-pooled
    (preserving the 1-vector-per-chunk contract). Used for single-chunk windows and as
    the per-chunk fallback when a whole multi-chunk window is rejected."""
    def _call(text: str) -> list[float]:
        rr = ratelimit.with_retry(
            lambda: client().contextualized_embed(
                inputs=[[text]], model=config.EMBED_MODEL,
                input_type="document", output_dimension=config.EMBED_DIM),
            provider="voyage")
        return list(rr.results[0].embeddings[0])

    if _est_tokens(chunk) <= int(CONTEXT_WINDOW_TOKENS * SAFETY):
        return _call(chunk)
    # Oversized: the 32k limit is per input document (the SUM of its chunks), so
    # sending the sub-segments together would still be oversize. Embed each separately
    # and mean-pool to one vector.
    segs = _split_oversized(chunk, int(CONTEXT_WINDOW_TOKENS * OVERSIZE_SAFETY))
    log.warning("oversized chunk ~%d tok -> %d sub-segment(s), embedded separately + pooled",
                _est_tokens(chunk), len(segs))
    return _pool([_call(seg) for seg in segs])


def embed_law_chunks(ordered_chunks: list[str],
                     progress: Optional[Callable[[str], None]] = None
                     ) -> list[list[float]]:
    """Embed chunks (whole-law contextual). Logs per-batch telemetry to the file
    log and, if `progress` is given, emits a human line per step for the UI."""
    if not ordered_chunks:
        return []
    # voyage-context-3 rejects an empty / whitespace-only input ("the example at index
    # 0 in your batch has ..."), which fails the WHOLE document. A blank provision (an
    # image/drawing-only page DI returned empty for, a structure-only section) carries
    # no text -> substitute a placeholder so the document still embeds and loads.
    blanks = sum(1 for c in ordered_chunks if not (c or "").strip())
    if blanks:
        log.warning("%d/%d chunk(s) blank (no extractable text — e.g. image-only page); "
                    "using a placeholder so the document is not lost", blanks, len(ordered_chunks))
        if progress:
            progress(f"{blanks} blank chunk(s) → placeholder (kept document embeddable)")
        ordered_chunks = [c if (c or "").strip() else _BLANK_PLACEHOLDER
                          for c in ordered_chunks]

    batches = _plan_batches(ordered_chunks)
    est = sum(_est_tokens(c) for c in ordered_chunks)
    log.info("start: %d chunks, ~%d tok, %d batch(es), model=%s dim=%d",
             len(ordered_chunks), est, len(batches), config.EMBED_MODEL, config.EMBED_DIM)
    if progress:
        progress(f"{len(ordered_chunks)} chunks → {len(batches)} batch(es), ~{est // 1000}k tok")

    out: list[list[float]] = []
    t0 = time.perf_counter()
    for bi, idxs in enumerate(batches, 1):
        span = [ordered_chunks[i] for i in idxs]
        bt = time.perf_counter()

        # A single-chunk window (incl. an oversized one) is embedded on its own.
        if len(span) == 1:
            out.append(_embed_one(span[0]))
            dt = time.perf_counter() - bt
            log.info("batch %d/%d: 1 chunk in %.2fs", bi, len(batches), dt)
            if len(batches) > 1 and progress:
                progress(f"batch {bi}/{len(batches)}: 1 chunk, {dt:.1f}s")
            continue

        try:
            r = ratelimit.with_retry(
                lambda span=span: client().contextualized_embed(
                    inputs=[span], model=config.EMBED_MODEL,
                    input_type="document", output_dimension=config.EMBED_DIM),
                provider="voyage")
            out.extend(r.results[0].embeddings)
        except Exception as e:                    # noqa: BLE001
            if ratelimit._is_retryable(e):        # network/429 that survived retries -> fatal
                log.exception("batch %d/%d FAILED (%d chunks): %s",
                              bi, len(batches), len(span), e)
                if progress:
                    progress(f"batch {bi}/{len(batches)} failed: {e}")
                raise
            # The whole-window request was content-rejected (e.g. a chunk the char→token
            # estimate under-counted). Don't lose the document: embed each chunk on its
            # own so only a genuinely unembeddable chunk (if any) could be affected.
            log.warning("batch %d/%d window rejected (%s) — falling back to per-chunk",
                        bi, len(batches), e)
            if progress:
                progress(f"batch {bi}/{len(batches)}: window rejected, embedding per-chunk")
            for c in span:
                out.append(_embed_one(c))
        dt = time.perf_counter() - bt
        log.info("batch %d/%d: %d chunks in %.2fs", bi, len(batches), len(span), dt)
        if len(batches) > 1 and progress:
            progress(f"batch {bi}/{len(batches)}: {len(span)} chunks, {dt:.1f}s")

    total = time.perf_counter() - t0
    log.info("done: %d vectors in %.2fs", len(out), total)
    if progress:
        progress(f"done: {len(out)} vectors in {total:.1f}s")
    return out


def embed_query(q: str) -> list[float]:
    r = client().contextualized_embed(inputs=[[q]], model=config.EMBED_MODEL,
                                      input_type="query",
                                      output_dimension=config.EMBED_DIM)
    return list(r.results[0].embeddings[0])


def rerank(query: str, documents: list[str], top_k: int | None = None,
           instruction: str | None = None) -> list[tuple[int, float]]:
    if not documents:
        return []
    q = f"{instruction}\n{query}" if instruction else query
    r = client().rerank(query=q, documents=documents, model=config.RERANK_MODEL,
                        top_k=top_k, truncation=True)
    return [(x.index, x.relevance_score) for x in r.results]
