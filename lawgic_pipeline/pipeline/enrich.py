"""enrich.py — classification + LLM enrichment.

Domain classification is deterministic and layered:
  1. CODE_DOMAIN — cited-code / framework-law signal (highest precision). When a
     provision cites a named code or a well-known framework law, that is a very
     strong domain signal.
  2. KEYWORD_DOMAIN — accent-folded keyword fallback, applied only when the
     cited-code signal found nothing, so an uncited provision still gets a domain.

The trained domain_dkn classifier (GLC / Raptarchis47k Ραπτάρχης labels) is the
documented next layer; it requires the labelled corpus and is wired through
enrich_llm's `dkn` field today. The hook `classify_dkn` below is the explicit
extension point for a local trained model when that dataset is available.

Summary / keywords / EUROVOC / ΔΚΝ via the configured LLM are real but skip
silently when no provider key is set, so the pipeline never blocks on them.
"""
from __future__ import annotations

import json
import re

import llm
from models import Law
from normalize import fold_for_bm25

# cited-code / framework-law -> domain (highest-precision signal). Patterns are
# matched case-insensitively against title + provision text.
CODE_DOMAIN = {
    r"Ποινικ\w+\s+Κώδικ": "criminal",
    r"Κώδικα?\s+Ποινικής\s+Δικονομίας|ΚΠΔ\b": "criminal_procedure",
    r"Αστικ\w+\s+Κώδικ": "civil",
    r"Κώδικα?\s+Πολιτικής\s+Δικονομίας|ΚΠολΔ\b": "civil_procedure",
    r"4808/2021|Εργατικ\w+|Κώδικα?\s+Εργασίας": "labor",
    r"Κώδικα?\s+Φορολογ\w*|\bΦΠΑ\b|4172/2013|4174/2013|ΚΦΕ\b|ΚΦΔ\b": "tax",
    r"4412/2016|4413/2016|δημόσι\w+\s+συμβάσε\w+": "public_procurement",
    r"4624/2019|2016/679|\bGDPR\b|προσωπικ\w+\s+δεδομέν\w+": "data_protection",
    r"4548/2018|4072/2012|ανώνυμ\w+\s+εταιρ\w+|\bΕΠΕ\b|\bΙΚΕ\b": "corporate",
    r"4738/2020|πτωχευτικ\w+|αφερεγγυότητ\w+": "insolvency",
    r"2960/2001|τελωνειακ\w+|δασμ\w+": "customs",
    r"4001/2011|4685/2020|περιβάλλον\w*|ενεργειακ\w+": "energy_environment",
}

# Accent-folded keyword fallback (applied only when CODE_DOMAIN matched nothing).
# Keys are folded so they match regardless of accents / final sigma.
KEYWORD_DOMAIN = {
    "ποινη": "criminal", "εγκλημα": "criminal", "κακουργημα": "criminal",
    "διαζυγιο": "civil", "κληρονομια": "civil", "συμβαση": "civil",
    "μισθος": "labor", "εργαζομεν": "labor", "απολυση": "labor",
    "φορος": "tax", "φορολογ": "tax", "εισοδημα": "tax",
    "διαγωνισμος": "public_procurement", "αναθετουσα": "public_procurement",
    "δεδομεν": "data_protection", "απορρητο": "data_protection",
    "μετοχ": "corporate", "εταιρ": "corporate",
}


def _add(p, dom):
    if dom not in p.legal_domain:
        p.legal_domain.append(dom)


def classify_domain(law: Law) -> Law:
    """Layer 1 (cited-code) with a folded-keyword fallback per provision."""
    for p in law.provisions:
        hay = f"{law.title}\n{p.text_in_force}"
        matched = False
        for pat, dom in CODE_DOMAIN.items():
            if re.search(pat, hay, re.IGNORECASE):
                _add(p, dom)
                matched = True
        if not matched:
            folded = fold_for_bm25(hay)
            for kw, dom in KEYWORD_DOMAIN.items():
                if kw in folded:
                    _add(p, dom)
    return law


def classify_dkn(law: Law) -> Law:
    """Extension point for the trained ΔΚΝ (Ραπτάρχης) classifier.

    No-op today: the labelled GLC/Raptarchis47k corpus is not bundled, so domain_dkn
    is populated via the LLM (`dkn` field) in enrich_llm. When a local trained model
    is available, load it here and append predictions to p.domain_dkn.
    """
    return law

# STABLE prefix — byte-identical across every call so the provider caches it and
# bills subsequent calls at the cache-hit rate. Only the provision text varies.
_SYSTEM = (
    "You are a Greek legal metadata extractor. For the single legal provision in "
    "the user message, return ONLY a JSON object with keys: "
    "summary (2-3 sentence Greek summary), keywords (5-10 Greek keywords), "
    "eurovoc (EUROVOC descriptor strings), dkn (Ραπτάρχης ΔΚΝ subject labels). "
    "No prose, no markdown, JSON only."
)


def enrich_llm(law: Law) -> Law:
    """Per-provision summary + keywords + taxonomy via the configured LLM.
    Skips silently if no provider key is set, so the pipeline never blocks on it."""
    for p in law.provisions:
        try:
            out = llm.complete(_SYSTEM, p.text_in_force, want_json=True, max_tokens=700)
            data = json.loads(out)
        except SystemExit:
            return law            # no API key configured — skip enrichment
        except Exception:
            continue              # one bad provision shouldn't fail the law
        p.chunk_summary = data.get("summary", p.chunk_summary)
        p.keywords = data.get("keywords", p.keywords) or p.keywords
        ev = data.get("eurovoc") or []
        dkn = data.get("dkn") or []
        p.domain_eurovoc = list(dict.fromkeys(p.domain_eurovoc + ev))
        p.domain_dkn = list(dict.fromkeys(p.domain_dkn + dkn))
    return law
