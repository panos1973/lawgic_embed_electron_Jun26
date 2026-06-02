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


def enrich_llm(law: Law, progress=None) -> Law:
    """Per-provision summary + keywords + taxonomy via the configured LLM.
    Skips silently if no provider key is set, so the pipeline never blocks on it.

    Each provision is one LLM call, so for a long law this is the slowest stage.
    `progress(msg)` (optional) is invoked per provision so the UI shows it is
    alive instead of looking frozen between classify and embed."""
    total = len(law.provisions)
    for i, p in enumerate(law.provisions, 1):
        if progress:
            progress(f"{law.instrument_id}: provision {i}/{total}")
        # Retry once: a single truncated/garbled JSON (or a transient provider
        # hiccup) used to silently zero a provision's whole enrichment. The budget
        # is 1024 — long articles (e.g. a 13-member council) blew past 700 once the
        # summary + keywords + EUROVOC + ΔΚΝ lists were emitted, truncating the JSON.
        data = None
        for _attempt in range(2):
            try:
                out = llm.complete(_SYSTEM, p.text_in_force, want_json=True,
                                   max_tokens=1024)
                data = json.loads(out)
                break
            except SystemExit:
                if progress:
                    progress("no LLM key — skipping enrichment")
                return law        # no API key configured — skip enrichment
            except Exception:
                continue          # bad JSON / transient error -> retry once
        if data is None:
            continue              # one bad provision shouldn't fail the law
        p.chunk_summary = data.get("summary", p.chunk_summary)
        kw = list(data.get("keywords") or [])
        ev = data.get("eurovoc") or []
        dkn = data.get("dkn") or []
        # domain_dkn is a CONTROLLED vocabulary (the canonical Ραπτάρχης volumes).
        # The LLM tends to also return free-form topical phrases ("Πρακτική
        # άσκηση", "Χρεόγραφα"); keep only entries that are real ΔΚΝ volumes and
        # route the rest to keywords so the field stays clean and filterable.
        canon = [d for d in dkn if d in DKN_VOLUMES]
        noncanon = [d for d in dkn if d not in DKN_VOLUMES]
        p.domain_eurovoc = list(dict.fromkeys(p.domain_eurovoc + ev))
        p.domain_dkn = list(dict.fromkeys(p.domain_dkn + canon))
        p.keywords = list(dict.fromkeys((kw or p.keywords) + noncanon)) or p.keywords
    return law
