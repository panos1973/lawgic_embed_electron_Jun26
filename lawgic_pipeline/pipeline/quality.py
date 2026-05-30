"""quality.py — extracted-text quality scoring (port of the proven TS detector).

Greek FEK PDFs fail extraction in characteristic ways that a single signal misses:
  * custom font encodings map Greek glyphs into Latin-1/Extended ranges
  * CID-keyed fonts with no ToUnicode map yield (cid:NNN) tokens
  * image/scanned pages yield box-drawing/block glyphs or almost nothing
  * ASCII-substitution renders Greek as '?' runs

score_text() returns a 0-100 quality score plus the component ratios and a
human-readable failure reason, mirroring the old Electron app's
text-quality.util.ts so the new pipeline inherits its hard-won thresholds.
A page scoring below the threshold has no usable text layer and should be routed
to OCR (Azure DI) rather than embedded.

All character-class patterns use \\u escapes so this source stays byte-clean
(literal box-drawing / control characters would otherwise embed NUL bytes).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- character classes ----------------------------------------------------
_GREEK = re.compile("[Ͱ-Ͽἀ-῿]")        # Greek + polytonic
_LATIN = re.compile(r"[A-Za-z]")
_NUM = re.compile(r"[0-9]")
_VALID_PUNCT = re.compile(
    "[.,;:!?'\"()\\-–—/\\[\\]{}«»"
    "°%&@#\\s·΄΅]")
# garbled-Greek: glyphs that appear when a Greek font is mis-decoded
# Latin-1 Supplement, Extended-A, Extended-B, IPA, modifiers, Extended-Additional
_GARBLED = re.compile(
    "[À-ÿĀ-ſƀ-ɏ"
    "ɐ-ʯʰ-˿Ḁ-ỿ]")
# box-drawing / block / geometric shapes — image-extraction failure
_BOX = re.compile("[─-╿▀-▟■-◿]")
# symbol garbage: letterlike, math operators, technical, dingbats, specials
_SYMBOL = re.compile(
    "[℀-⅏∀-⋿⌀-⏿✀-➿￰-￿]")
# control + invisible characters (always garbage)
_CONTROL = re.compile("[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]")
_CID = re.compile(r"\(cid:\d+\)")


@dataclass
class Quality:
    score: int
    greek_ratio: float
    garbage_ratio: float
    box_ratio: float
    cid_ratio: float
    reason: str = ""

    @property
    def is_valid(self) -> bool:
        return self.score >= 40


def _ratio(rx: "re.Pattern[str]", text: str, total: int) -> float:
    return len(rx.findall(text)) / total if total else 0.0


def score_text(text: str, min_score: int = 40) -> Quality:
    """Score extracted page text 0-100. Below min_score => route to OCR."""
    if not text or len(text.strip()) < 50:
        return Quality(0, 0.0, 1.0, 0.0, 0.0, "text too short (<50 chars)")

    total = len(text)
    greek = _ratio(_GREEK, text, total)
    latin = _ratio(_LATIN, text, total)
    garbled = _ratio(_GARBLED, text, total)
    box = _ratio(_BOX, text, total)
    symbol = _ratio(_SYMBOL, text, total)
    control = _ratio(_CONTROL, text, total)
    cid = len(_CID.findall(text)) / max(1, len(text.split()))

    garbage = (len(_GARBLED.findall(text)) + len(_BOX.findall(text))
               + len(_SYMBOL.findall(text)) + len(_CONTROL.findall(text)))
    garbage_ratio = garbage / total

    words = [w for w in text.split() if w]
    avg_word = sum(len(w) for w in words) / len(words) if words else 0
    q_ratio = text.count("?") / total

    score = 100.0
    score -= garbled * 80
    score -= box * 60
    score -= symbol * 50
    score -= control * 40
    score -= garbage_ratio * 30
    score -= min(cid, 1.0) * 80          # CID-keyed font with no ToUnicode map
    if greek < 0.05 and latin < 0.05:
        score -= 25
    if avg_word > 25:
        score -= 25
    elif avg_word > 15:
        score -= 10
    if q_ratio > 0.08:
        score -= 40                      # ASCII-substitution garble
    if greek > 0.3:
        score += 10
    score = max(0.0, min(100.0, score))

    reason = ""
    if score < min_score:
        if cid > 0.15:
            reason = f"CID-keyed font, no ToUnicode map ({cid*100:.0f}% cid tokens)"
        elif q_ratio > 0.08:
            reason = f"ASCII-substitution garble ({q_ratio*100:.0f}% '?')"
        elif garbled > 0.15:
            reason = f"font-encoding garble ({garbled*100:.0f}% extended-Latin)"
        elif box > 0.08:
            reason = f"image-based content ({box*100:.0f}% box/block glyphs)"
        elif greek < 0.05 and latin < 0.05:
            reason = "insufficient readable text"
        else:
            reason = f"low quality score {score:.0f}"

    return Quality(round(score), round(greek, 3), round(garbage_ratio, 3),
                   round(box, 3), round(cid, 3), reason)


def needs_ocr(text: str, min_score: int = 40) -> bool:
    """True when the page text layer is too poor to embed and should go to OCR."""
    return not score_text(text, min_score).is_valid
