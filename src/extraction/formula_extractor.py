"""
formula_extractor.py — Extract and classify Formula objects from TEIDocument.

Two classification passes:
  Pass 1 (structural): LaTeX/text pattern matching — NSE, KGE, PBIAS, RMSE, etc.
  Pass 2 (contextual): surrounding section title (methods → hydro efficiency formula)

Formula records carry region_id=None at extraction time; the FK to regions.parquet
is populated in Phase 3 when region matching is implemented.

Usage::

    from src.extraction.formula_extractor import extract_formulas

    records = extract_formulas(tei_doc)
    for r in records:
        print(r.formula_id, r.formula_class, r.latex or r.text)
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.document.models import TEIDocument

# ── Classification patterns (most-specific → most-general) ───────────────────
# Each: (compiled regex, class_label)
# Applied to the formula text (LaTeX or plain).  First match wins.

_CLASSIFY: list[tuple[re.Pattern, str]] = [
    # NSE / Nash-Sutcliffe efficiency
    (re.compile(r"nash|NSE|E_?\{?n\}?|\\text\{NSE\}|nash[\s\-]?sutcliffe", re.I), "NSE"),
    # KGE / Kling-Gupta
    (re.compile(r"KGE|kling[\s\-]?gupta|\\text\{KGE\}", re.I), "KGE"),
    # PBIAS / percent bias
    (re.compile(r"PBIAS|percent[\s\-]?bias|P[\s\.]?BIAS", re.I), "PBIAS"),
    # RMSE / root-mean-square error
    (re.compile(r"RMSE|\\sqrt\s*\{.*\\sum|root[\s\-]?mean[\s\-]?sq", re.I), "RMSE"),
    # MAE / mean absolute error
    (re.compile(r"\bMAE\b|mean[\s\-]?abs(?:olute)?[\s\-]?err", re.I), "MAE"),
    # R² / coefficient of determination
    (re.compile(r"R\^2|R\^\\{?2\}?|r[\s\-]?squared|coeff.*determ", re.I), "R2"),
    # Manning's n (roughness coefficient)
    (re.compile(r"manning|roughness[\s\-]?coeff", re.I), "MANNING"),
    # Continuity / mass balance
    (re.compile(r"continuity|mass[\s\-]?balance|water[\s\-]?balance", re.I), "CONTINUITY"),
    # Saint-Venant / shallow water equations
    (re.compile(r"saint[\s\-]?venant|shallow[\s\-]?water|\\frac\{\\partial Q", re.I), "SHALLOW_WATER"),
    # Richards equation (unsaturated flow)
    (re.compile(r"richard|unsaturated|soil[\s\-]?moisture[\s\-]?flow", re.I), "RICHARDS"),
    # Rational method / peak flow
    (re.compile(r"rational[\s\-]?method|Q\s*=\s*C\s*i\s*A|peak[\s\-]?flow", re.I), "RATIONAL"),
]


def _classify(text: str | None) -> str | None:
    if not text:
        return None
    for pat, label in _CLASSIFY:
        if pat.search(text):
            return label
    return None


# ── FormulaRecord ─────────────────────────────────────────────────────────────

@dataclass
class FormulaRecord:
    formula_id:    str
    paper_id:      str
    region_id:     str | None     # FK → regions.parquet (None until Phase 3 matching)
    xml_id:        str | None
    page:          int | None
    text:          str | None     # plain-text fallback (GROBID)
    latex:         str | None     # LaTeX content (Nougat-enriched if available)
    formula_class: str | None     # NSE | KGE | RMSE | ...
    confidence:    float | None
    source_parser: str

    def to_row(self) -> dict:
        return {
            "formula_id":    self.formula_id,
            "paper_id":      self.paper_id,
            "region_id":     self.region_id,
            "xml_id":        self.xml_id,
            "page":          self.page,
            "text":          self.text,
            "latex":         self.latex,
            "formula_class": self.formula_class,
            "confidence":    self.confidence,
            "source_parser": self.source_parser,
        }


# ── Public API ────────────────────────────────────────────────────────────────

def extract_formulas(tei_doc: "TEIDocument") -> list[FormulaRecord]:
    """
    Extract all Formula objects from a TEIDocument and return FormulaRecords.

    For GROBID-parsed docs: Formula.text is plain text (e.g. "E n = 1 - SS res …").
    For Nougat-enriched docs: Formula.text may contain LaTeX fragments; doc.markdown_text
    holds the full Nougat output — individual formula LaTeX is not yet per-formula
    until region matching is wired (Phase 3).

    The formula_class is derived from whatever text is available.
    """
    records: list[FormulaRecord] = []
    parser_kind = tei_doc.parser_kind.value

    for formula in tei_doc.formulas:
        page = formula.coords.primary_page if formula.coords else None
        text = formula.text or None

        # For Nougat-parsed docs, text may already be LaTeX
        # For GROBID docs, text is OCR-degraded plain text
        is_likely_latex = text and ("\\" in text or "^" in text or "_{" in text)
        latex = text if is_likely_latex else None

        cls = _classify(text)

        records.append(FormulaRecord(
            formula_id    = _stable_id(tei_doc.paper_id, formula.xml_id, text),
            paper_id      = tei_doc.paper_id,
            region_id     = None,
            xml_id        = formula.xml_id or None,
            page          = page,
            text          = text if not is_likely_latex else None,
            latex         = latex,
            formula_class = cls,
            confidence    = 0.90 if is_likely_latex else 0.70,
            source_parser = parser_kind,
        ))

    return records


def _stable_id(paper_id: str, xml_id: str | None, text: str | None) -> str:
    """Deterministic ID: prefer xml_id-based hash, fall back to UUID."""
    if xml_id:
        import hashlib
        raw = f"{paper_id}:{xml_id}"
        return "f_" + hashlib.sha1(raw.encode()).hexdigest()[:14]
    return "f_" + uuid.uuid4().hex[:14]
