"""
nougat_quality.py — Quality gate for Nougat inference output.

Nougat is a generative model. Its failure modes are concrete and detectable:

  REPETITION_LOOP   — decoder stuck in a loop, produces "The The The The..."
                      or longer repeated phrases.  Most common failure.
  LOW_ENTROPY       — output text is abnormally uniform (same chars/words over
                      and over).  Shannon entropy < ENTROPY_THRESHOLD.
  EMPTY             — blank or whitespace-only output.
  TOO_SHORT         — < MIN_CHARS characters after stripping.  Often means
                      the crop was blank or a header-only region.
  HALLUCINATION     — known Nougat artifacts: [MISSING], [ILLEGIBLE], spurious
                      LaTeX overflow on non-formula content.
  LATEX_OVERFLOW    — for non-equation regions, high backslash/dollar density
                      signals Nougat treating cartographic or table content
                      as mathematical notation.

Each flag carries a score penalty.  Final score < REJECT_THRESHOLD → rejected.
Rejected output must NOT be written to the knowledge graph.

Usage::

    from src.document.nougat_quality import score_nougat_output, REJECT_THRESHOLD

    quality = score_nougat_output(markdown_text, region_type="FIGURE_REGION")
    if quality.rejected:
        log.warning("rejected %s: %s", region_id, quality.reason)
    else:
        write_to_kg(...)
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

# ── Thresholds ────────────────────────────────────────────────────────────────

REJECT_THRESHOLD   = 0.35   # score below this → rejected
ENTROPY_THRESHOLD  = 3.0    # bits/char; English text ≈ 4.2, pure repetition < 2.0
MIN_CHARS          = 15     # stripped length below this → TOO_SHORT
NGRAM_REPEAT_RATIO = 0.15   # if any 5-gram's count / total_ngrams > this → loop

# For non-formula regions: latex symbol ratio above this is suspicious
LATEX_OVERFLOW_RATIO = 0.25  # fraction of chars that are \, $, {, }

# Formula region types — these legitimately contain lots of LaTeX
_FORMULA_REGION_TYPES = frozenset({"FORMULA_REGION"})

# Nougat hallucination markers
_HALLUCINATION_PATTERNS: list[re.Pattern] = [
    re.compile(r'\[MISSING\]',    re.IGNORECASE),
    re.compile(r'\[ILLEGIBLE\]',  re.IGNORECASE),
    re.compile(r'\[UNK\]',        re.IGNORECASE),
    re.compile(r'\\text\{[^}]{0,3}\}\s*\\text\{', re.IGNORECASE),  # cascading \text{}
    re.compile(r'(?:\.\.\.\s*){3,}'),    # excessive ellipsis
    re.compile(r'(?:#+\s*){4,}'),        # run of empty headers
]


# ── Output model ──────────────────────────────────────────────────────────────

@dataclass
class NougatQuality:
    score:       float             # 0.0 (rejected) → 1.0 (perfect)
    flags:       list[str]         = field(default_factory=list)
    rejected:    bool              = False
    reason:      str | None        = None   # human-readable primary failure
    char_count:  int               = 0
    entropy:     float             = 0.0
    region_type: str               = ""


# ── Internal helpers ──────────────────────────────────────────────────────────

def _shannon_entropy(text: str) -> float:
    """Character-level Shannon entropy in bits."""
    if not text:
        return 0.0
    counts = Counter(text)
    total  = len(text)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def _repetition_ratio(text: str, n: int = 5) -> float:
    """
    Fraction of n-grams occupied by the single most common n-gram.

    A value near 1.0 means the text is almost entirely one repeated phrase.
    Normal text: < 0.05 for n=5.  Nougat loop: easily > 0.30.
    """
    words = text.split()
    if len(words) < n + 1:
        return 0.0
    ngrams  = [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]
    counts  = Counter(ngrams)
    top_cnt = counts.most_common(1)[0][1]
    return top_cnt / len(ngrams)


def _latex_symbol_ratio(text: str) -> float:
    """Fraction of characters that are LaTeX structural symbols."""
    if not text:
        return 0.0
    latex_chars = sum(1 for c in text if c in r"\${}[]_^")
    return latex_chars / len(text)


def _has_hallucination_marker(text: str) -> bool:
    return any(p.search(text) for p in _HALLUCINATION_PATTERNS)


def _detect_char_loop(text: str) -> bool:
    """
    Fast check: if the first 80 characters appear verbatim ≥ 4 times in the
    full text, the decoder is looping on a prefix.
    """
    if len(text) < 160:
        return False
    prefix = text[:80]
    return text.count(prefix) >= 4


# ── Public API ────────────────────────────────────────────────────────────────

def score_nougat_output(
    text:        str | None,
    region_type: str = "",
) -> NougatQuality:
    """
    Score Nougat markdown output for a single region.

    Parameters
    ----------
    text        : raw markdown string from NougatParser / NougatActor
    region_type : ScientificRegion.region_type ("FORMULA_REGION", etc.)
                  Used to calibrate expected content characteristics.

    Returns
    -------
    NougatQuality dataclass.  Check `.rejected` before writing to KG.
    """
    flags:  list[str] = []
    score:  float     = 1.0
    reason: str | None = None

    # ── 1. Empty ──────────────────────────────────────────────────────────────
    if not text or not text.strip():
        return NougatQuality(
            score       = 0.0,
            flags       = ["EMPTY"],
            rejected    = True,
            reason      = "empty output",
            char_count  = 0,
            entropy     = 0.0,
            region_type = region_type,
        )

    stripped    = text.strip()
    char_count  = len(stripped)
    entropy     = _shannon_entropy(stripped)

    # ── 2. Too short ──────────────────────────────────────────────────────────
    if char_count < MIN_CHARS:
        flags.append("TOO_SHORT")
        score -= 0.40
        if reason is None:
            reason = f"output too short ({char_count} chars)"

    # ── 3. Repetition loop (character prefix) ─────────────────────────────────
    if _detect_char_loop(stripped):
        flags.append("REPETITION_LOOP")
        score -= 0.55
        reason = "character-level repetition loop detected"

    # ── 4. Repetition loop (n-gram) ───────────────────────────────────────────
    rep_ratio = _repetition_ratio(stripped)
    if rep_ratio > NGRAM_REPEAT_RATIO:
        if "REPETITION_LOOP" not in flags:
            flags.append("REPETITION_LOOP")
            score -= 0.55
            reason = f"n-gram repetition ratio={rep_ratio:.2f}"
        # If already flagged, only add additional penalty for very severe cases
        elif rep_ratio > 0.40:
            score -= 0.20

    # ── 5. Low entropy ────────────────────────────────────────────────────────
    if entropy < ENTROPY_THRESHOLD:
        flags.append("LOW_ENTROPY")
        penalty = max(0.10, 0.30 * (1 - entropy / ENTROPY_THRESHOLD))
        score  -= penalty
        if reason is None:
            reason = f"low Shannon entropy ({entropy:.2f} bits/char)"

    # ── 6. Hallucination markers ──────────────────────────────────────────────
    if _has_hallucination_marker(stripped):
        flags.append("HALLUCINATION")
        score -= 0.25
        if reason is None:
            reason = "contains hallucination markers ([MISSING], [ILLEGIBLE], …)"

    # ── 7. LaTeX overflow (non-formula regions) ───────────────────────────────
    if region_type not in _FORMULA_REGION_TYPES:
        latex_ratio = _latex_symbol_ratio(stripped)
        if latex_ratio > LATEX_OVERFLOW_RATIO:
            flags.append("LATEX_OVERFLOW")
            score -= 0.20
            if reason is None:
                reason = f"unexpected LaTeX density ({latex_ratio:.0%}) in {region_type}"

    score    = max(0.0, min(1.0, score))
    rejected = score < REJECT_THRESHOLD

    if rejected and reason is None:
        reason = f"composite quality score {score:.2f} below threshold {REJECT_THRESHOLD}"

    return NougatQuality(
        score       = round(score, 3),
        flags       = flags,
        rejected    = rejected,
        reason      = reason,
        char_count  = char_count,
        entropy     = round(entropy, 3),
        region_type = region_type,
    )


def is_acceptable(text: str | None, region_type: str = "") -> bool:
    """Convenience predicate — True if output passes the quality gate."""
    return not score_nougat_output(text, region_type).rejected
