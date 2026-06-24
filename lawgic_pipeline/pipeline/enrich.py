"""enrich.py — classification + LLM enrichment.

Domain classification is deterministic and layered:
  1. CODE_DOMAIN — cited-code / framework-law signal (highest precision). When a
     provision cites a named code or a well-known framework law, that is a very
     strong domain signal.
  2. KEYWORD_DOMAIN — accent-folded keyword fallback, applied only when the
     cited-code signal found nothing, so an uncited provision still gets a domain.

domain_dkn (ΔΚΝ / Ραπτάρχης subject volumes) is now classified deterministically
too — classify_dkn maps the same cited-code/keyword signals to canonical
top-level volume names, so the field is populated offline. A trained
GLC/Raptarchis47k model can later augment/replace it behind the same field, and
LLM `dkn` predictions still merge on top when a provider key is set.

Summary / keywords / EUROVOC / extra ΔΚΝ via the configured LLM are real but skip
silently when no provider key is set, so the pipeline never blocks on them.
"""
from __future__ import annotations

import hashlib
import json
import re

import config
import llm
from greek_stem import stem_text
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
    r"Εμπορικ\w+\s+Νόμ\w+|Εμπορικ\w+\s+Κώδικ|5325/1932|αξιόγραφ\w+|2251/1994|"
    r"προστασ\w+\s+(?:του\s+)?καταναλωτ|αγορανομικ\w+|αθέμιτ\w+\s+ανταγωνισμ": "commercial",
    r"\bΣύνταγμα\b|Συντάγματος|συνταγματικ\w+": "constitutional",
    # EU law as a SUBJECT = citing an EU Directive/Regulation or "ενωσιακό δίκαιο".
    # NOT a bare "Ευρωπαϊκή Ένωση" mention — that fires on "προγράμματα της Ε.Ε."
    # (EU programs) and "ενωσιακούς πόρους" (EU funds), which are not EU-law subjects.
    r"Οδηγί\w+\s+\(?(?:ΕΕ|Ε\.Ε\.|ΕΚ|ΕΟΚ)\)?|Κανονισμ\w+\s+\(?(?:ΕΕ|Ε\.Ε\.|ΕΚ)\)?|"
    r"ενωσιακ\w+\s+δίκαι|δίκαι\w+\s+της\s+Ευρωπαϊκ\w+\s+Ένωσ": "eu_law",
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
    # immigration = migration/asylum law. "αλλοδαπ" alone is a false friend: the
    # idiom "ημεδαπής ή αλλοδαπής" means domestic/foreign (jurisdiction, bodies),
    # not aliens — so require migration context (μεταναστ/άσυλο/the cited laws).
    r"4251/2014|3386/2005|μεταναστευτικ\w+|\bάσυλο\b|"
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
    # NOTE: bare "δεδομεν" removed — it matches generic "δεδομένα/δεδομένο" (facts/
    # data) and mislabeled e.g. market-inspection articles as data_protection. Real
    # personal-data law is caught precisely by CODE_DOMAIN (προσωπικά δεδομένα/GDPR).
    "προσωπικα δεδομεν": "data_protection", "απορρητο": "data_protection",
    "μετοχ": "corporate", "εταιρ": "corporate",
    # consumer / market-regulation law (ν.2251/1994 etc.). "καταναλωτ" matches
    # καταναλωτής/καταναλωτικά (consumer), NOT "κατανάλωση" (consumption, folds to
    # καταναλωσ) — so price/market-control articles map to commercial, while
    # energy/water-consumption text does not false-fire here.
    "καταναλωτ": "commercial", "αγορανομ": "commercial", "λιανεμπορ": "commercial",
    "περιβαλλον": "environmental", "ενεργειακ": "energy",
    "συνταγμα": "constitutional", "συνταγματικ": "constitutional",
    # NOTE: bare "ενωσιακ"/"ευρωπαικ" removed — they fire on "ενωσιακούς πόρους"
    # (EU funds) and "προγράμματα της Ε.Ε." (EU programs), not EU law. Real EU law
    # is caught precisely by CODE_DOMAIN (cited Directive/Regulation / ενωσιακό δίκαιο).
    "ασφαλιστικ": "social_security", "συνταξ": "social_security",
    "υγειονομ": "health", "νοσοκομει": "health",
    "εκπαιδευ": "education", "πανεπιστημ": "education",
    # NOTE: bare "αλλοδαπ" removed (false friend "ημεδαπής ή αλλοδαπής" = abroad);
    # real migration law keeps μεταναστ + the cited laws/άσυλο in CODE_DOMAIN.
    "μεταναστ": "immigration",
    "στρατιωτικ": "defense", "ψηφιακ": "digital",
}


# Ministry / general-secretariat PORTFOLIO names enumerate other policy areas
# ("εκπρόσωπος του Υπουργείου Ναυτιλίας …", "ο Υπουργός Αγροτικής Ανάπτυξης …")
# that are NOT the article's own subject. A vocational-education council article
# listing those ministries was getting stamped ΕΜΠΟΡΙΚΗ ΝΑΥΤΙΛΙΑ / ΓΕΩΡΓΙΚΗ /
# ΔΙΕΘΝΕΙΣ. We strip the ministry head + its trailing Capitalized portfolio run
# from the keyword-fallback haystack ONLY (CODE_DOMAIN cited-code patterns are
# precise and keep the full text). Subject mentions in ordinary prose ("γεωργικές
# εκμεταλλεύσεις", "νηολόγηση πλοίου") are untouched — they don't follow Υπουργ-.
_ORG_PORTFOLIO = re.compile(
    r"(?:Υπουργ\w*|Γενικ\w+\s+Γραμματ\w+|Γραμματ(?:έας|είας|έα))\s+"
    r"(?:[Α-ΩΆ-Ώ][α-ωά-ώϊϋΐΰ]+\s*|και\s+|,\s*)+")


def _strip_portfolios(text: str) -> str:
    """Remove ministry/secretariat portfolio enumerations for keyword matching."""
    return _ORG_PORTFOLIO.sub(" ", text)


def _add(p, dom):
    if dom not in p.legal_domain:
        p.legal_domain.append(dom)


def _domains_for(text: str, title: str = "") -> list[str]:
    """Return the ordered legal_domain labels a provision matches.

    Layer 1 = cited-code/framework patterns (high precision) — scanned over the
    provision text PLUS the law title (a code named in the title is a real
    signal). Layer 2 = greedy folded-keyword fallback — scanned over the
    PROVISION TEXT ONLY, never the title: a generic title word like
    "Επαγγελματικής Εκπαίδευσης" would otherwise stamp 'education' onto every
    article of the law (the ν.5082/2024 bug). Fallback fires only when Layer 1
    found nothing. Shared by classify_domain and the ΔΚΝ classifier.
    """
    doms: list[str] = []
    matched = False
    hay = f"{title}\n{text}" if title else text
    for pat, dom in CODE_DOMAIN.items():
        if re.search(pat, hay, re.IGNORECASE):
            if dom not in doms:
                doms.append(dom)
            matched = True
    if not matched:
        # text only — NOT the title; and with ministry portfolio enumerations
        # stripped so a listed ministry's policy area doesn't leak in as a subject
        folded = fold_for_bm25(_strip_portfolios(text))
        for kw, dom in KEYWORD_DOMAIN.items():
            if kw in folded and dom not in doms:
                doms.append(dom)
    return doms


def classify_domain(law: Law) -> Law:
    """Layer 1 (cited-code) with a folded-keyword fallback per provision."""
    for p in law.provisions:
        for dom in _domains_for(p.text_in_force, law.title):
            _add(p, dom)
    return law


# ── ΔΚΝ (Ραπτάρχης) subject taxonomy — deterministic top-level volumes ──
# The Διαρκής Κώδικας Νομοθεσίας (Ραπτάρχης) is the official thematic index of
# Greek law. This populates domain_dkn with its canonical top-level volume names
# (Greek), deterministically and offline — the same layered approach as
# legal_domain. A trained model on the GLC/Raptarchis47k corpus can later augment
# or replace this behind the same field; until that corpus is available, this is
# the real signal (and LLM `dkn` predictions still merge on top in enrich_llm).

# coarse legal_domain label -> ΔΚΝ top-level volume (canonical Greek name)
_DOMAIN_TO_DKN = {
    "criminal": "ΠΟΙΝΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "criminal_procedure": "ΠΟΙΝΙΚΗ ΔΙΚΟΝΟΜΙΑ",
    "civil": "ΑΣΤΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "civil_procedure": "ΠΟΛΙΤΙΚΗ ΔΙΚΟΝΟΜΙΑ",
    "labor": "ΕΡΓΑΤΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "tax": "ΦΟΡΟΛΟΓΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "public_procurement": "ΔΗΜΟΣΙΕΣ ΣΥΜΒΑΣΕΙΣ",
    "data_protection": "ΠΡΟΣΤΑΣΙΑ ΠΡΟΣΩΠΙΚΩΝ ΔΕΔΟΜΕΝΩΝ",
    "corporate": "ΕΜΠΟΡΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "insolvency": "ΕΜΠΟΡΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "customs": "ΤΕΛΩΝΕΙΑΚΗ ΝΟΜΟΘΕΣΙΑ",
    "environmental": "ΠΕΡΙΒΑΛΛΟΝΤΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "energy": "ΕΝΕΡΓΕΙΑ",
    "administrative": "ΔΙΟΙΚΗΤΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "commercial": "ΕΜΠΟΡΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "constitutional": "ΣΥΝΤΑΓΜΑΤΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "eu_law": "ΔΙΕΘΝΕΙΣ ΣΧΕΣΕΙΣ",
    "social_security": "ΚΟΙΝΩΝΙΚΗ ΑΣΦΑΛΙΣΗ",
    "health": "ΥΓΕΙΟΝΟΜΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "education": "ΕΚΠΑΙΔΕΥΤΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "transport": "ΜΕΤΑΦΟΡΕΣ ΚΑΙ ΕΠΙΚΟΙΝΩΝΙΕΣ",
    "digital": "ΗΛΕΚΤΡΟΝΙΚΗ ΔΙΑΚΥΒΕΡΝΗΣΗ",
    "defense": "ΕΘΝΙΚΗ ΑΜΥΝΑ",
    "immigration": "ΑΛΛΟΔΑΠΟΙ ΚΑΙ ΜΕΤΑΝΑΣΤΕΥΣΗ",
}

# ΔΚΝ volumes with no legal_domain counterpart — matched directly by folded
# keyword. Keys are folded at module load (accents stripped, ς->σ) so they are
# written here in natural Greek spelling.
_DKN_EXTRA_RAW = {
    "γεωργικ": "ΓΕΩΡΓΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "αγροτικ": "ΓΕΩΡΓΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    # NOTE: "καλλιεργ" removed — it is a false friend matching "καλλιέργεια
    # δεξιοτήτων" (cultivation of *skills*), which stamped ΓΕΩΡΓΙΚΗ on education
    # articles. Real farming is caught by γεωργικ/αγροτικ.
    "ναυτιλ": "ΕΜΠΟΡΙΚΗ ΝΑΥΤΙΛΙΑ",
    "πλοίο": "ΕΜΠΟΡΙΚΗ ΝΑΥΤΙΛΙΑ",
    "λιμεν": "ΕΜΠΟΡΙΚΗ ΝΑΥΤΙΛΙΑ",
    "εκκλησ": "ΕΚΚΛΗΣΙΑΣΤΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "μητροπόλ": "ΕΚΚΛΗΣΙΑΣΤΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "ιερά σύνοδο": "ΕΚΚΛΗΣΙΑΣΤΙΚΗ ΝΟΜΟΘΕΣΙΑ",
    "δημόσια έργα": "ΔΗΜΟΣΙΑ ΕΡΓΑ",
    "οδοποιί": "ΔΗΜΟΣΙΑ ΕΡΓΑ",
    "τοπικής αυτοδιοίκησης": "ΟΡΓΑΝΙΣΜΟΙ ΤΟΠΙΚΗΣ ΑΥΤΟΔΙΟΙΚΗΣΗΣ",
    "δημοτικό συμβούλιο": "ΟΡΓΑΝΙΣΜΟΙ ΤΟΠΙΚΗΣ ΑΥΤΟΔΙΟΙΚΗΣΗΣ",
    "περιφερειάρχ": "ΟΡΓΑΝΙΣΜΟΙ ΤΟΠΙΚΗΣ ΑΥΤΟΔΙΟΙΚΗΣΗΣ",
}
_DKN_EXTRA = {fold_for_bm25(k): v for k, v in _DKN_EXTRA_RAW.items()}

# all volume labels the deterministic classifier can emit (for validation/tests)
DKN_VOLUMES = set(_DOMAIN_TO_DKN.values()) | set(_DKN_EXTRA.values())


def classify_dkn(law: Law) -> Law:
    """Populate domain_dkn with ΔΚΝ (Ραπτάρχης) top-level subject volumes.

    Deterministic and offline: maps the cited-code/keyword signals (shared with
    classify_domain) to their canonical Greek volume name, then adds ΔΚΝ-only
    volumes (agriculture, shipping, ecclesiastical, public works, local
    government) matched by folded keyword. The extra-volume pass always runs, so
    a provision can carry both a mapped volume and an extra one.

    A trained GLC/Raptarchis47k model would slot in here behind the same field;
    LLM `dkn` predictions (enrich_llm) still merge on top when a key is set.
    """
    for p in law.provisions:
        for dom in _domains_for(p.text_in_force, law.title):
            vol = _DOMAIN_TO_DKN.get(dom)
            if vol and vol not in p.domain_dkn:
                p.domain_dkn.append(vol)
        # text only (not the title), ministry portfolio enumerations stripped
        folded = fold_for_bm25(_strip_portfolios(p.text_in_force))
        for kw, vol in _DKN_EXTRA.items():
            if kw in folded and vol not in p.domain_dkn:
                p.domain_dkn.append(vol)
    return law


# ── document-category taxonomy (ported from the old app's detection-schema) ──
# A function taxonomy that sub-classifies the coarse instrument_type (already on
# the Law from the masthead) into the 20 categories the old TS app routed on.
# Deterministic: instrument_type + accent-folded title signals (+ deep-hierarchy
# evidence from segmentation for codifications). No LLM, no network.

# title-signal keyword groups, most-specific intent first. Folded through
# fold_for_bm25 at module load so they match the folded title regardless of
# accents / final sigma (ς->σ) — keys written here in their natural spelling.
_CAT_SIGNALS_RAW = {
    "amendment":      ("τροποποίηση", "τροποποιείται", "τροποποιούνται",
                       "αντικατάσταση", "αντικαθίσταται", "καταργείται"),
    "codification":   ("κωδικοποίηση", "κωδικοποιητικ", "κωδικοποιούμεν",
                       "κώδικας"),
    "ratification":   ("κύρωση", "επικύρωση", "κυρώνεται", "συνθήκη",
                       "πρωτόκολλο", "διεθν συμβ"),
    "organizational": ("οργανισμός", "διάρθρωση", "σύσταση", "οργάνωση",
                       "κατανομή θέσε"),
    "regulatory":     ("κανονισμός", "κανονιστικ", "ρύθμιση", "καθορισμός",
                       "καθορισμ"),
    "enforcement":    ("εφαρμογή", "εκτέλεση", "εφαρμοστικ"),
}
_CAT_SIGNALS = {k: tuple(fold_for_bm25(s) for s in v)
                for k, v in _CAT_SIGNALS_RAW.items()}

# every category string the classifier can emit (mirrors the old DocumentCategory)
DOCUMENT_CATEGORIES = {
    "NOMOS_AMENDMENT", "NOMOS_CODIFICATION", "NOMOS_RATIFICATION",
    "NOMOS_SUBSTANTIVE", "PD_ORGANIZATIONAL", "PD_REGULATORY",
    "PD_CODIFICATION", "PD_AMENDMENT", "PD_ENFORCEMENT", "PNP", "PSIFISMA",
    "KYA", "YA_REGULATORY", "YA_INDIVIDUAL", "YA_ORGANIZATIONAL",
    "EGKYKLIOS", "GNOMODOSIA", "ANAKOINOSI", "UNKNOWN",
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
                        TYPE_APOF_DIOIK, TYPE_APOF_PERIF, TYPE_APOF_NPDD,
                        TYPE_ANAKOINOSI)

    folded = fold_for_bm25(law.title or "")
    # ΒΙΒΛΙΟ/ΜΕΡΟΣ deep hierarchy is only WEAK codification evidence: almost every
    # modern multi-article law uses ΜΕΡΟΣ/ΚΕΦΑΛΑΙΟ, so it must not by itself force
    # NOMOS_CODIFICATION (that mislabeled amending laws like ν.5082/2024). Require
    # an explicit codification keyword in the title; use `deep` only as a weak
    # tiebreak when no amendment/ratification signal is present.
    deep = any(p.book for p in law.provisions)   # ΒΙΒΛΙΟ only — the real codex marker
    # an amending law cites many "Τροποποίηση/Προσθήκη ... ν. XXXX" in its article
    # headings; count them to distinguish amendment-heavy laws from substantive.
    # The heading sits in the first line of the body (article_title is truncated),
    # so scan the body head, not the stored title.
    amend_titles = sum(
        1 for p in law.provisions
        if _signal(fold_for_bm25((p.text_in_force or "")[:160]), "amendment"))
    t = law.instrument_type

    if t in (TYPE_NOMOS, TYPE_AN, TYPE_ND):
        if _signal(folded, "ratification"):
            cat = "NOMOS_RATIFICATION"
        elif _signal(folded, "codification"):
            cat = "NOMOS_CODIFICATION"
        elif _signal(folded, "amendment") or amend_titles >= 3:
            cat = "NOMOS_AMENDMENT"
        elif deep:
            cat = "NOMOS_CODIFICATION"        # ΒΙΒΛΙΟ-level structure, no amend signal
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
    elif t == TYPE_ANAKOINOSI:
        cat = "ANAKOINOSI"
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


def _enrich_one(p) -> dict | None:
    """One provision's enrichment LLM call (with a single retry). Returns the parsed
    dict, or None if it stayed unparseable. Raises SystemExit (no key) / a fatal error
    (wrong key) to the caller. Retry once: a single truncated/garbled JSON used to
    silently zero a provision's whole enrichment (the 1024-token budget can truncate a
    long article's summary+keywords+EUROVOC+ΔΚΝ lists)."""
    for _attempt in range(2):
        try:
            out = llm.complete(_SYSTEM, p.text_in_force, want_json=True, max_tokens=1024)
            return json.loads(out)
        except SystemExit:
            raise                 # no API key configured
        except Exception as e:
            from errors import looks_fatal
            if looks_fatal(e):
                raise             # WRONG key/endpoint -> stop the run, don't swallow
            continue              # bad JSON / transient error -> retry once
    return None                   # one bad provision shouldn't fail the law


def _apply_enrichment(p, data: dict) -> None:
    p.chunk_summary = data.get("summary", p.chunk_summary)
    kw = list(data.get("keywords") or [])
    ev = data.get("eurovoc") or []
    dkn = data.get("dkn") or []
    # domain_dkn is a CONTROLLED vocabulary (the canonical Ραπτάρχης volumes). The LLM
    # tends to also return free-form topical phrases ("Πρακτική άσκηση", "Χρεόγραφα");
    # keep only entries that are real ΔΚΝ volumes and route the rest to keywords so the
    # field stays clean and filterable.
    canon = [d for d in dkn if d in DKN_VOLUMES]
    noncanon = [d for d in dkn if d not in DKN_VOLUMES]
    p.domain_eurovoc = list(dict.fromkeys(p.domain_eurovoc + ev))
    p.domain_dkn = list(dict.fromkeys(p.domain_dkn + canon))
    p.keywords = list(dict.fromkeys((kw or p.keywords) + noncanon)) or p.keywords


def enrich_llm(law: Law, progress=None) -> Law:
    """Per-provision summary + keywords + taxonomy via the configured LLM.
    Skips silently if no provider key is set, so the pipeline never blocks on it.

    Each provision is one LLM call — the slowest stage for a long law. The calls are
    independent and I/O-bound, so they fan out across config.LLM_CONCURRENCY workers
    (the rate limiter still caps the real request rate); results are APPLIED in the main
    thread so each provision is written exactly once with no races. `progress(msg)` is
    invoked per completed provision so the UI shows it is alive."""
    total = len(law.provisions)
    if total == 0:
        return law
    conc = max(1, getattr(config, "LLM_CONCURRENCY", 8))

    from concurrent.futures import ThreadPoolExecutor, as_completed
    done = 0
    with ThreadPoolExecutor(max_workers=min(conc, total)) as ex:
        futs = {ex.submit(_enrich_one, p): p for p in law.provisions}
        for fut in as_completed(futs):
            try:
                data = fut.result()
            except SystemExit:
                if progress:
                    progress("no LLM key — skipping enrichment")
                return law        # no API key configured — skip enrichment entirely
            done += 1
            if progress:
                progress(f"{law.instrument_id}: provision {done}/{total}")
            if data is not None:
                _apply_enrichment(futs[fut], data)
    return law


# Whole-law overview, synthesized ONCE from the per-article summaries. Stored on the
# document node only (NOT prepended to per-chunk vectors — voyage-context-3 already
# embeds each chunk with whole-law context, so gluing a summary on every chunk would
# only blur precision).
_LAW_SUMMARY_SYSTEM = (
    "You are a Greek legal analyst. From the law's title and the per-article "
    "summaries in the user message, write a concise 3-5 sentence Greek overview of "
    "what the whole law does: its purpose, the main subject areas it covers, and any "
    "notable measures. Return ONLY the overview text — no JSON, no headings, no preamble."
)


def _law_summary_fallback(law: Law) -> str:
    """Deterministic overview when no LLM key / no article summaries exist."""
    base = (law.title or law.instrument_id).strip()
    n = sum(1 for p in law.provisions if p.chunk_type == "article")
    bits = [b for b in (law.document_category, f"{n} άρθρα" if n else "") if b]
    return f"{base} — {', '.join(bits)}" if bits else base


def summarize_law(law: Law, complete=None) -> Law:
    """Populate law.summary with a whole-law overview (document node only).

    Synthesizes from the per-article summaries enrich_llm produced — a small, cheap
    call, not a re-read of the full law. Falls back to a deterministic line without
    an LLM key (so the field is never empty); a WRONG key still raises (fatal)."""
    parts = [(p.chunk_summary or "").strip()
             for p in law.provisions
             if p.chunk_type in ("article", "section") and (p.chunk_summary or "").strip()]
    if not parts:
        law.summary = _law_summary_fallback(law)[:1200]
        return law
    if complete is None:
        complete = llm.complete
    user = (f"Τίτλος: {law.title}\nΚατηγορία: {law.document_category}\n\n"
            "Περίληψη ανά άρθρο:\n" + "\n".join(f"- {s}" for s in parts[:60]))
    try:
        out = complete(_LAW_SUMMARY_SYSTEM, user, want_json=False, max_tokens=400)
        law.summary = (out or "").strip() or _law_summary_fallback(law)[:1200]
    except SystemExit:
        law.summary = _law_summary_fallback(law)[:1200]      # no key -> deterministic
    except Exception as e:                                   # noqa: BLE001
        from errors import looks_fatal
        if looks_fatal(e):
            raise                                            # wrong key/endpoint -> stop
        law.summary = _law_summary_fallback(law)[:1200]      # transient -> fallback
    return law


# A raw markdown table embeds as ONE diluted vector — broad queries find it, but a
# specific-cell or paraphrased question ("the protein-structure course") misses,
# because the value is averaged across every row. So we ADD a faithful Greek prose
# narration (one clause per row) to the chunk: the verbatim markdown + structured
# table_json stay for exact lookup, while the narration makes each row retrievable
# by meaning. Instruction in English (reliable), output in Greek (the corpus).
_TABLE_NARRATION_SYSTEM = (
    "You are given one or more tables extracted from a Greek government gazette "
    "(ΦΕΚ), as JSON: a list of tables; each table a list of rows; each row a list of "
    "cell strings. Write a faithful narration IN GREEK: one short sentence per DATA "
    "row, weaving in the value of each column so the row is findable by meaning "
    "(e.g. «Το μάθημα Δομική Βιολογία (BT_1.3) είναι μάθημα επιλογής, 5 ECTS, στο Α' "
    "εξάμηνο.»). Do NOT invent, omit, or alter any number or code; skip header rows. "
    "Return ONLY the narration text, with no preamble."
)


def _narrate_one(tabs, complete):
    """One table-bearing provision's narration call. Returns the narration text, or
    None on an empty/transient result. Raises SystemExit (no key) / a fatal error
    (wrong key) to the caller — consistent with _enrich_one."""
    try:
        out = complete(_TABLE_NARRATION_SYSTEM, json.dumps(tabs, ensure_ascii=False),
                       want_json=False, max_tokens=1500)
    except SystemExit:
        raise
    except Exception as e:                                   # noqa: BLE001
        from errors import looks_fatal
        if looks_fatal(e):
            raise                                            # wrong key/endpoint -> stop
        return None                                          # transient -> leave as-is
    return (out or "").strip()


def narrate_tables(law: Law, progress=None, complete=None) -> Law:
    """Append a faithful Greek narration (one clause per data row) to every provision
    whose text contains a table, so the EMBEDDED vector carries the table's meaning
    — not just a diluted markdown grid. The verbatim markdown and the structured
    table_json are left untouched (exact lookup still works); the narration only adds
    semantic, paraphrase-friendly text (also indexed for BM25). One cheap text-LLM
    call per table-bearing chunk. The calls are independent and I/O-bound, so they fan
    out across config.LLM_CONCURRENCY workers (the rate limiter still caps the real
    request rate) and results are applied in the main thread — table-heavy gazettes
    (hundreds of tables) used to narrate one-at-a-time, minutes per document. Skips
    silently without an LLM key; a wrong key is fatal (consistent with enrich_llm), a
    transient error leaves that table as-is."""
    from pipeline.tables import tables_from_text
    if complete is None:
        complete = llm.complete
    targets = [(p, t) for p in law.provisions
               if (t := tables_from_text(p.text_in_force or ""))]
    if not targets:
        return law
    conc = max(1, getattr(config, "LLM_CONCURRENCY", 8))

    from concurrent.futures import ThreadPoolExecutor, as_completed
    narrated = 0
    with ThreadPoolExecutor(max_workers=min(conc, len(targets))) as ex:
        futs = {ex.submit(_narrate_one, tabs, complete): p for p, tabs in targets}
        for fut in as_completed(futs):
            try:
                narration = fut.result()
            except SystemExit:
                if progress and not narrated:
                    progress("no LLM key — skipping table narration")
                return law                                   # no key -> skip entirely
            p = futs[fut]
            if not narration or narration in (p.text_in_force or ""):
                continue
            body = f"{p.text_in_force}\n\n[Πίνακας — αφήγηση περιεχομένου]\n{narration}"
            p.text_in_force = body
            p.text_normalized = fold_for_bm25(body)          # keep BM25 fields in sync
            p.text_stemmed = stem_text(body)
            p.content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            narrated += 1
            if progress:
                where = f"Άρθρο {p.article_no}" if p.article_no else (p.chunk_type or "chunk")
                progress(f"{law.instrument_id}: table narrated — {where}")
    return law
