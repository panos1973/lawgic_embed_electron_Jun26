"""refs.py — external (non-Greek-instrument) reference tagging.

EU legislation is cited heavily in Greek law ("κατ' εφαρμογή του Κανονισμού (ΕΕ)
2022/868", "Οδηγία 2011/83/ΕΕ"). We record these as external references on each
provision (Provision.cites -> stored as `external_law_references`) so the graph
captures the EU dependency. They are NOT resolved to a node — there is no EU law
collection — they are recorded verbatim in a normalized form.
"""
from __future__ import annotations

import re

from models import Law

# "Κανονισμός/Κανονισμού (ΕΕ) 2022/868", "(ΕΚ) αριθ. 1234/2007"
_EU_PRE = re.compile(
    r"(?P<kind>Κανονισμ\w*|Οδηγί\w*|Απόφασ\w*)\s*\(?\s*(?P<org>ΕΕ|ΕΚ|ΕΟΚ)\s*\)?\s*"
    r"(?:αριθ\.?\s*)?(?P<num>\d{1,4}/\d{2,4})", re.IGNORECASE)
# "Οδηγία 2011/83/ΕΕ" — number precedes the org tag
_EU_POST = re.compile(
    r"(?P<kind>Κανονισμ\w*|Οδηγί\w*|Απόφασ\w*)\s*(?P<num>\d{4}/\d{1,4})\s*/\s*"
    r"(?P<org>ΕΕ|ΕΚ|ΕΟΚ)", re.IGNORECASE)

_KIND = {"κ": "Κανονισμός", "ο": "Οδηγία", "α": "Απόφαση"}


def _norm(kind: str, org: str, num: str) -> str:
    k = _KIND.get(kind[:1].lower(), kind)
    return f"{org.upper()}:{k} {num}"


def extract_external_refs(law: Law) -> Law:
    """Append normalized EU-law references to each provision's cites (deduped)."""
    for p in law.provisions:
        found = []
        for rx in (_EU_PRE, _EU_POST):
            for m in rx.finditer(p.text_in_force):
                tok = _norm(m.group("kind"), m.group("org"), m.group("num"))
                if tok not in p.cites and tok not in found:
                    found.append(tok)
        p.cites.extend(found)
    return law
