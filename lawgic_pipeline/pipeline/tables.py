"""tables.py — recover structured tables from rendered markdown.

The sidecar renders each detected table as a GitHub-markdown grid inline in the
page text (sidecar.pdf_extract._table_to_markdown). That markdown survives
normalize (the `|` cell delimiters and the per-row newlines are preserved) and
lands verbatim in a provision's text_in_force. This module parses those grids
back into structured rows so the loader can populate the `table_json` field
(declared "for EXACT cell lookup when chunk_type=table") — while the
human-/BM25-readable markdown stays in chunk_text.

Deterministic, dependency-free, idempotent. Returns None when a chunk has no
table, so the loader writes a real null rather than an empty string.
"""
from __future__ import annotations

import json
from typing import Optional

# A markdown table row is a line that both starts and ends with a pipe.
def _row_cells(line: str) -> Optional[list[str]]:
    s = line.strip()
    if len(s) < 3 or s[0] != "|" or s[-1] != "|":
        return None
    # strip the leading/trailing pipe, then split the interior on the delimiter
    return [c.strip() for c in s[1:-1].split("|")]


def _is_separator(cells: list[str]) -> bool:
    """The GitHub header rule row, e.g. '| --- | --- |' (dashes/colons only)."""
    nonempty = [c for c in cells if c]
    return bool(nonempty) and all(set(c) <= set("-:") for c in nonempty)


def tables_from_text(text: str) -> list[list[list[str]]]:
    """Every markdown table in `text`, as a list of tables (rows of cell strings).

    A run of consecutive pipe rows is one table; the `|---|` separator row is
    dropped. A block only counts as a real table if it has >=2 rows and >=2
    columns, so a stray prose line containing pipes is not mistaken for one.
    """
    if not text or "|" not in text:
        return []
    tables: list[list[list[str]]] = []
    cur: list[list[str]] = []

    def flush():
        if len(cur) >= 2 and max((len(r) for r in cur), default=0) >= 2:
            tables.append([list(r) for r in cur])

    for line in text.splitlines():
        cells = _row_cells(line)
        if cells is None:
            flush()
            cur = []
            continue
        if _is_separator(cells):
            continue
        cur.append(cells)
    flush()
    return tables


def tables_json(text: str) -> Optional[str]:
    """JSON-encode the tables embedded in `text`, or None when there are none."""
    tabs = tables_from_text(text)
    return json.dumps(tabs, ensure_ascii=False) if tabs else None
