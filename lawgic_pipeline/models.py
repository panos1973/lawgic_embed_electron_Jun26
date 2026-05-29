"""models.py — canonical IDs + data models (Law / Provision / Amendment).

The canonical provision id is the backbone for pinpoint lookup, amendment
targeting, dedup and idempotent upserts.
    <instrument_id>#<locator>   e.g.  "ν.4675/2024#αρ.24.παρ.2"
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import re
import unicodedata

TYPE_NOMOS, TYPE_PD, TYPE_PNP = "NOMOS", "PD", "PNP"
TYPE_YA, TYPE_KYA, TYPE_PSIFISMA = "YA", "KYA", "PSIFISMA"
# additional FEK Α΄ (enabling) primary legislation
TYPE_AN, TYPE_ND, TYPE_KANVOULIS = "AN", "ND", "KAN_VOULIS"
# additional FEK Β΄ (implementing) regulatory acts
TYPE_KANAP, TYPE_APOF_DIOIK = "KAN_APOFASI", "APOFASI_DIOIKITI"
TYPE_APOF_PERIF, TYPE_APOF_NPDD = "APOFASI_PERIF", "APOFASI_NPDD"

_PREFIX_DISPLAY = {TYPE_NOMOS: "ν.", TYPE_PD: "π.δ.", TYPE_PNP: "Π.Ν.Π.",
                   TYPE_YA: "ΥΑ ", TYPE_KYA: "ΚΥΑ ", TYPE_PSIFISMA: "ψήφισμα ",
                   TYPE_AN: "α.ν.", TYPE_ND: "ν.δ.", TYPE_KANVOULIS: "Καν.Βουλής ",
                   TYPE_KANAP: "καν.απόφ. ", TYPE_APOF_DIOIK: "απόφ.Διοικ. ",
                   TYPE_APOF_PERIF: "απόφ.Περιφ. ", TYPE_APOF_NPDD: "απόφ.ΝΠΔΔ "}
_PREFIX_KEY = {TYPE_NOMOS: "N", TYPE_PD: "PD", TYPE_PNP: "PNP",
               TYPE_YA: "YA", TYPE_KYA: "KYA", TYPE_PSIFISMA: "PS",
               TYPE_AN: "AN", TYPE_ND: "ND", TYPE_KANVOULIS: "KANV",
               TYPE_KANAP: "KANAP", TYPE_APOF_DIOIK: "APD",
               TYPE_APOF_PERIF: "APP", TYPE_APOF_NPDD: "APN"}


def make_instrument_id(t: str, number: int, year: int) -> str:
    return f"{_PREFIX_DISPLAY.get(t, 'ν.')}{number}/{year}"


def make_instrument_key(t: str, number: int, year: int) -> str:
    return f"{_PREFIX_KEY.get(t, 'N')}{number}/{year}"


def make_decision_id(series: str, fek_number: str, year: int,
                     item: Optional[int] = None) -> str:
    """Citable id for FEK decisions, which lack a clean NUM/YEAR instrument number.

    Built from the gazette coordinates (τεύχος + φύλλο + έτος) plus the
    ΠΕΡΙΕΧΟΜΕΝΑ item index when a single FEK issue bundles several acts, e.g.
    'Β΄913/2025#1'. Single-act issues drop the item suffix: 'Β΄734/2025'.
    """
    base = f"{series}΄{fek_number}/{year}"
    return f"{base}#{item}" if item else base


def make_decision_key(series: str, fek_number: str, year: int,
                      item: Optional[int] = None) -> str:
    base = f"{series}{fek_number}/{year}"
    return f"{base}#{item}" if item else base


def make_provision_id(instrument_id: str, article: str,
                      paragraph: Optional[str] = None) -> str:
    loc = f"αρ.{article}"
    if paragraph:
        loc += f".παρ.{paragraph}"
    return f"{instrument_id}#{loc}"


@dataclass
class AmendmentOp:
    op: str                       # repeals|replaces|adds|amends|modifies|renumbers|consolidates
    target_id: str
    scope: str = "article"        # document|article|paragraph|case|subcase
    effective_date: Optional[str] = None
    new_text: Optional[str] = None
    sub_edit_ordinal: Optional[str] = None
    resolved: bool = True


@dataclass
class DelegationEdge:
    """An implementing act (FEK B) exercising authority granted by an enabling
    provision (FEK A), e.g. a ΥΑ/ΚΥΑ issued 'κατ' εξουσιοδότηση' of a law article.
    """
    enabling_id: str                          # canonical id of the enabling provision/law
    implementing_id: str                      # instrument id of the act exercising it
    enabling_law_number: str = ""             # denormalized "4412/2016"
    enabling_article_number: str = ""         # denormalized "5"
    delegated_authority: str = ""             # who received it (best-effort)
    delegation_scope: str = ""                # short description (best-effort)
    resolved: bool = True


@dataclass
class Provision:
    canonical_id: str
    instrument_id: str
    instrument_key: str
    instrument_type: str
    fek_series: str = ""
    fek_number: str = ""
    fek_date: str = ""
    article_no: str = ""
    article_title: str = ""
    paragraph_no: Optional[str] = None
    level: str = "article"
    chunk_type: str = "article"
    hierarchy_path: str = ""
    book: str = ""
    part: str = ""
    chapter: str = ""
    legal_domain: list[str] = field(default_factory=list)
    domain_dkn: list[str] = field(default_factory=list)
    domain_eurovoc: list[str] = field(default_factory=list)
    status: str = "in_force"
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    version: int = 1
    is_current: bool = True
    amends: list[str] = field(default_factory=list)
    amended_by: list[str] = field(default_factory=list)
    cites: list[str] = field(default_factory=list)
    text_in_force: str = ""
    text_as_enacted: str = ""
    text_normalized: str = ""
    table_json: Optional[str] = None
    keywords: list[str] = field(default_factory=list)
    chunk_summary: str = ""
    content_hash: str = ""
    # filled by embed stage
    vector: Optional[list[float]] = None


@dataclass
class Law:
    instrument_id: str
    instrument_key: str
    instrument_type: str
    title: str = ""
    fek_series: str = ""
    fek_number: str = ""
    fek_date: str = ""
    jurisdiction: str = "gr"
    provisions: list[Provision] = field(default_factory=list)
    amendments: list[AmendmentOp] = field(default_factory=list)
    delegations: list["DelegationEdge"] = field(default_factory=list)

    def ordered_texts(self) -> list[str]:
        return [p.text_in_force for p in self.provisions]


# --- citation parser (deterministic pinpoint path; order-independent, bilingual) ---
_ARTICLE_RE = re.compile(
    r"(?:άρθρ(?:ο|ου)|αρθ\.?|αρ\.?|article|art\.?)\s*(?P<article>\d+[α-ωΑ-Ωa-zA-Z]?)",
    re.IGNORECASE)
_PAR_RE = re.compile(
    r"(?:παρ(?:άγραφος|αγράφου)?\.?|paragraph|para\.?)\s*(?P<par>\d+[α-ωΑ-Ω]?)",
    re.IGNORECASE)
_INSTR_RE = re.compile(
    r"(?P<type>ν\.?|νόμ(?:ος|ου|ο)|law|act|π\.?\s*δ\.?|προεδρικ\w*|presidential\s+decree)?"
    r"\s*(?P<number>\d{1,5})\s*/\s*(?P<year>\d{4})", re.IGNORECASE)


def parse_citation(query: str) -> Optional[dict]:
    art, inst = _ARTICLE_RE.search(query), _INSTR_RE.search(query)
    if not art or not inst:
        return None
    t = (inst.group("type") or "").lower()
    itype = TYPE_PD if (t.startswith("π") or t.startswith("προ") or "decree" in t) else TYPE_NOMOS
    number, year = int(inst.group("number")), int(inst.group("year"))
    par = _PAR_RE.search(query)
    return {"article": art.group("article"),
            "paragraph": par.group("par") if par else None,
            "number": number, "year": year, "instrument_type": itype,
            "instrument_key": make_instrument_key(itype, number, year),
            "instrument_id": make_instrument_id(itype, number, year)}
