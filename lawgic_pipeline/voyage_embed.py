"""voyage_embed.py — contextualized embeddings + rerank (voyage-context-3 / rerank-2.5).

Chunks are sent nested so each provision vector carries surrounding context. A
law longer than one context window is split into several windows; chunks share
context within a window. voyage-context-3's window is 32k tokens PER input
document (the list of chunks sent together), so a long law MUST be split or the
API rejects it ("example ... too many tokens ... context window of 32000").
"""
from __future__ import annotations
import time
from typing import Callable, Optional

import voyageai
import config
import logsetup

_client = None
# voyage-context-3 context window: 32k tokens per input document. We pack chunks
# into windows that stay under SAFETY*32k (conservative — token estimate is
# char-based, and Greek tokenizes denser than the estimate assumes).
CONTEXT_WINDOW_TOKENS = 32_000
MAX_CHUNKS = 16_000          # voyage per-request chunk cap
SAFETY = 0.75

log = logsetup.get("embed")


def client():
    global _client
    if _client is None:
        config.require("VOYAGE_API_KEY")
        _client = voyageai.Client(api_key=config.VOYAGE_API_KEY)
    return _client


def _est_tokens(t: str) -> int:
    return max(1, len(t) // 3)


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


def embed_law_chunks(ordered_chunks: list[str],
                     progress: Optional[Callable[[str], None]] = None
                     ) -> list[list[float]]:
    """Embed chunks (whole-law contextual). Logs per-batch telemetry to the file
    log and, if `progress` is given, emits a human line per step for the UI."""
    if not ordered_chunks:
        return []
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
        try:
            r = client().contextualized_embed(inputs=[span], model=config.EMBED_MODEL,
                                              input_type="document",
                                              output_dimension=config.EMBED_DIM)
        except Exception as e:
            log.exception("batch %d/%d FAILED (%d chunks): %s",
                          bi, len(batches), len(span), e)
            if progress:
                progress(f"batch {bi}/{len(batches)} failed: {e}")
            raise
        out.extend(r.results[0].embeddings)
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
