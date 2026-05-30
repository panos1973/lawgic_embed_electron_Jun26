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
    # environment / energy split out as the old app's taxonomy keeps them distinct
    r"1650/1986|4014/2011|4685/2020|περιβάλλον\w*|περιβαλλοντικ\w+": "environmental",
    r"4001/2011|4951/2022|ενεργειακ\w+|ΑΠΕ\b|ανανεώσιμ\w+\s+πηγ": "energy",
    # broader subject domains ported from the old app's 17-label set
    r"2690/1999|Κώδικα?\s+Διοικητικής\s+Διαδικασίας|2717/1999|"
    r"Κώδικα?\s+Διοικητικής\s+Δικονομίας|ΚΔΔ\b": "administrative",
    r"Εμπορικ\w+\s+Νόμ\w+|Εμπορικ\w+\s+Κώδικ|5325/1932|αξιόγραφ\w+": "commercial",
    r"\bΣύνταγμα\b|Συντάγματος|συνταγματικ\w+": "constitutional",
    r"Οδηγί\w+\s+\(?(?:ΕΕ|Ε\.Ε\.|ΕΚ)\)?|Κανονισμ\w+\s+\(?(?:ΕΕ|Ε\.Ε\.)\)?|"
    r"ενωσιακ\w+\s+δίκαι|Ευρωπαϊκ\w+\s+Ένωσ": "eu_law",
    r"4387/2016|ασφαλιστικ\w+\s+(?:φορέ|νομοθεσ)|κοινωνικ\w+\s+ασφάλισ|"
    r"συνταξιοδοτικ\w+": "social_security",
    r"4600/2019|4512/2018|υγειονομικ\w+|δημόσι\w+\s+υγεί|\bΕΣΥ\b": "health",
    r"4957/2022|4547/2018|τριτοβάθμι\w+\s+εκπαίδευσ|πανεπιστήμι\w+|"
    r"σχολικ\w+\s+μονάδ": "education",
    r"\bΚΟΚ\b|Κώδικα?\s+Οδικής\s+Κυκλοφορίας|αεροπορικ\w+\s+μεταφορ|"
    r"ναυτιλιακ\w+|σιδηροδρομικ\w+": "transport",
    r"4727/2020|ψηφιακ\w+\s+διακυβέρν|ηλεκτρονικ\w+\s+διακυβέρν|"
    r"Κώδικα?\s+Ψηφιακής": "digital",
    r"3883/2010|ένοπλ\w+\s+δυνάμ|στρατιωτικ\w+\s+προσωπικ|εθνικ\w+\s+άμυν": "defense",
    r"4251/2014|3386/2005|μεταναστευτικ\w+|αλλοδαπ\w+|\bάσυλο\b|"
    r"αιτ\w+\s+ασύλου": "immigration",
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
    "περιβαλλον": "environmental", "ενεργειακ": "energy",
    "συνταγμα": "constitutional", "συνταγματικ": "constitutional",
    "ενωσιακ": "eu_law", "ευρωπαικ": "eu_law",
    "ασφαλιστικ": "social_security", "συνταξ": "social_security",
    "υγειονομ": "health", "νοσοκομει": "health",
    "εκπαιδευ": "education", "πανεπιστημ": "education",
    "μεταναστ": "immigration", "αλλοδαπ": "immigration",
    "στρατιωτικ": "defense", "ψηφιακ": "digital",
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


# ── document-category taxonomy (ported from the old app's detection-schema) ──
# A function taxonomy that sub-classifies the coarse instrument_type (already on
# the Law from the masthead) into the 20 categories the old TS app routed on.
# Deterministic: instrument_type + accent-folded title signals (+ deep-hierarchy
# evidence from segmentation for codifications). No LLM, no network.

# title-signal keyword groups (accent-folded), most-specific intent first
_CAT_SIGNALS = {
    "amendment":      ("τροποποιησ", "τροποποιειται", "τροποποιουνται",
                       "αντικατασταση", "αντικαθισταται", "καταργειται"),
    "codification":   ("κωδικοποιησ", "κωδικοποιητικ", "κωδικοποιουμεν",
                       "κωδικας"),
    "ratification":   ("κυρωση", "επικυρωση", "κυρωνεται", "συνθηκη",
                       "πρωτοκολλο", "διεθν συμβ"),
    "organizational": ("οργανισμος", "διαρθρωση", "συσταση", "οργανωση",
                       "κατανομη θεσε"),
    "regulatory":     ("κανονισμος", "κανονιστικ", "ρυθμιση", "καθορισμος",
                       "καθορισμ"),
    "enforcement":    ("εφαρμογη", "εκτελεση", "εφαρμοστικ"),
}

# every category string the classifier can emit (mirrors the old DocumentCategory)
DOCUMENT_CATEGORIES = {
    "NOMOS_AMENDMENT", "NOMOS_CODIFICATION", "NOMOS_RATIFICATION",
    "NOMOS_SUBSTANTIVE", "PD_ORGANIZATIONAL", "PD_REGULATORY",
    "PD_CODIFICATION", "PD_AMENDMENT", "PD_ENFORCEMENT", "PNP", "PSIFISMA",
    "KYA", "YA_REGULATORY", "YA_INDIVIDUAL", "YA_ORGANIZATIONAL",
    "EGKYKLIOS", "GNOMODOSIA", "UNKNOWN",
}


def _signal(folded: str, kind: str) -> bool:
    return any(k in folded for k in _CAT_SIGNALS[kind])


def classify_document_category(law: Law) -> Law:
    """Set law.document_category from instrument_type + title signals.

    Mirrors the old app's Stage-1 routing: laws split into amendment /
    codification / ratification / substantive; π.δ. and ΥΑ split by their
    structural intent. Best-effort and deterministic — when no signal fits, it
    falls back to the type's most common category (or UNKNOWN for types outside
    the taxonomy) so the field is always populated.
    """
    from models import (TYPE_NOMOS, TYPE_AN, TYPE_ND, TYPE_PD, TYPE_PNP,
                        TYPE_YA, TYPE_KYA, TYPE_PSIFISMA, TYPE_KANAP,
                        TYPE_APOF_DIOIK, TYPE_APOF_PERIF, TYPE_APOF_NPDD)

    folded = fold_for_bm25(law.title or "")
    # deep hierarchy (ΒΙΒΛΙΟ/ΜΕΡΟΣ/ΚΕΦΑΛΑΙΟ) is strong codification evidence
    deep = any(p.book or p.part for p in law.provisions)
    t = law.instrument_type

    if t in (TYPE_NOMOS, TYPE_AN, TYPE_ND):
        if _signal(folded, "ratification"):
            cat = "NOMOS_RATIFICATION"
        elif _signal(folded, "codification") or deep:
            cat = "NOMOS_CODIFICATION"
        elif _signal(folded, "amendment"):
            cat = "NOMOS_AMENDMENT"
        else:
            cat = "NOMOS_SUBSTANTIVE"
    elif t == TYPE_PD:
        if _signal(folded, "organizational"):
            cat = "PD_ORGANIZATIONAL"
        elif _signal(folded, "codification") or deep:
            cat = "PD_CODIFICATION"
        elif _signal(folded, "amendment"):
            cat = "PD_AMENDMENT"
        elif _signal(folded, "enforcement"):
            cat = "PD_ENFORCEMENT"
        else:
            cat = "PD_REGULATORY"
    elif t == TYPE_PNP:
        cat = "PNP"
    elif t == TYPE_PSIFISMA:
        cat = "PSIFISMA"
    elif t == TYPE_KYA:
        cat = "KYA"
    elif t in (TYPE_YA, TYPE_KANAP, TYPE_APOF_DIOIK, TYPE_APOF_PERIF,
               TYPE_APOF_NPDD):
        if _signal(folded, "organizational"):
            cat = "YA_ORGANIZATIONAL"
        elif _signal(folded, "regulatory") or t == TYPE_KANAP:
            cat = "YA_REGULATORY"
        else:
            cat = "YA_INDIVIDUAL"
    else:
        cat = "UNKNOWN"

    law.document_category = cat
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
