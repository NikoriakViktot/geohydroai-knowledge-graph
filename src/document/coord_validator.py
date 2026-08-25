"""
coord_validator.py — Structural validation of TEIDocument coordinate data.

Validates BoundingBox integrity across a parsed TEIDocument and returns
a structured ValidationReport that callers can log, store, or act on.

Checks performed
----------------
1. NEGATIVE_PAGE     — page < 1 on any box
2. ZERO_DIMENSION    — w ≤ 0 or h ≤ 0 on any box
3. ZERO_POSITION     — x < 0 or y < 0 (can indicate parse error, not failure)
4. MULTI_PAGE_SPAN   — a single Coordinates object spans more than one page
5. EMPTY_TEXT_COORDS — element has coords but empty text (orphaned bbox)
6. BBOX_OVERLAP      — two chunks on the same page with >50 % IoU overlap
                       (signals duplicate or misaligned extraction)

Usage
-----
    from src.document.coord_validator import validate_document
    report = validate_document(doc)
    if report.error_count > 0:
        log.warning("Coord issues in %s: %s", paper_id, report.summary())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.document.models import TEIDocument

log = logging.getLogger(__name__)


# ── Report model ──────────────────────────────────────────────────────────────

@dataclass
class CoordIssue:
    check:   str    # check name (e.g. "NEGATIVE_PAGE")
    element: str    # element description (e.g. "sentence in Introduction")
    detail:  str    # human-readable detail


@dataclass
class ValidationReport:
    paper_id:    str
    issues:      list[CoordIssue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return len(self.issues)

    def summary(self) -> str:
        if not self.issues:
            return "OK"
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.check] = counts.get(issue.check, 0) + 1
        parts = [f"{k}×{v}" for k, v in sorted(counts.items())]
        return "; ".join(parts)

    def to_dict(self) -> dict:
        return {
            "paper_id":    self.paper_id,
            "error_count": self.error_count,
            "summary":     self.summary(),
            "issues": [
                {"check": i.check, "element": i.element, "detail": i.detail}
                for i in self.issues
            ],
        }


# ── Public API ────────────────────────────────────────────────────────────────

def validate_document(doc: "TEIDocument") -> ValidationReport:
    """
    Run all coordinate checks on a parsed TEIDocument.
    Never raises — catches internal errors and adds them as issues.
    """
    report = ValidationReport(paper_id=doc.paper_id)

    try:
        _check_floats(doc, report)
        _check_sections(doc, report)
        _check_overlaps(doc, report)
    except Exception as exc:
        report.issues.append(CoordIssue(
            check="VALIDATOR_ERROR",
            element="document",
            detail=str(exc),
        ))

    if report.error_count:
        log.debug(
            "coord_validator %s: %d issue(s) — %s",
            doc.paper_id, report.error_count, report.summary(),
        )
    return report


# ── Checks ────────────────────────────────────────────────────────────────────

def _check_floats(doc: "TEIDocument", report: ValidationReport) -> None:
    """Validate Figure, Table, Formula coordinate boxes."""
    for fig in doc.figures:
        _check_coords(fig.coords, f"Figure {fig.xml_id}", report)
    for tab in doc.tables:
        _check_coords(tab.coords, f"Table {tab.xml_id}", report)
    for formula in doc.formulas:
        if formula.text and not formula.coords:
            # formulas with text but no coords are common — not an error
            pass
        _check_coords(formula.coords, f"Formula {formula.xml_id}", report)


def _check_sections(doc: "TEIDocument", report: ValidationReport) -> None:
    """Validate sentence and paragraph coords in all sections."""
    for sec in doc.sections:
        _check_section_recursive(sec, report)


def _check_section_recursive(sec, report: ValidationReport) -> None:
    label = f"section '{sec.title}'"
    for para in sec.paragraphs:
        for i, sent in enumerate(para.sentences):
            _check_coords(sent.coords, f"sentence {i} in {label}", report)
            if sent.coords and not sent.text:
                report.issues.append(CoordIssue(
                    check="EMPTY_TEXT_COORDS",
                    element=f"sentence {i} in {label}",
                    detail="has coords but empty text",
                ))
        if not para.sentences:
            _check_coords(para.coords, f"paragraph in {label}", report)
    for sub in sec.subsections:
        _check_section_recursive(sub, report)


def _check_coords(coords, element: str, report: ValidationReport) -> None:
    if coords is None:
        return
    pages_seen: set[int] = set()
    for b in coords.boxes:
        if b.page < 1:
            report.issues.append(CoordIssue(
                check="NEGATIVE_PAGE",
                element=element,
                detail=f"page={b.page}",
            ))
        if b.w <= 0 or b.h <= 0:
            report.issues.append(CoordIssue(
                check="ZERO_DIMENSION",
                element=element,
                detail=f"w={b.w:.1f} h={b.h:.1f}",
            ))
        if b.x < 0 or b.y < 0:
            report.issues.append(CoordIssue(
                check="ZERO_POSITION",
                element=element,
                detail=f"x={b.x:.1f} y={b.y:.1f}",
            ))
        pages_seen.add(b.page)
    if len(pages_seen) > 1:
        report.issues.append(CoordIssue(
            check="MULTI_PAGE_SPAN",
            element=element,
            detail=f"spans pages {sorted(pages_seen)}",
        ))


def _check_overlaps(doc: "TEIDocument", report: ValidationReport) -> None:
    """
    Detect heavily overlapping sentence bboxes on the same page
    (IoU > 0.5 between any two sentences).  Only checks sentences since
    paragraph/section bboxes legitimately contain each other.
    """
    # Collect (page, x, y, w, h, element_label) for all sentences
    boxes: list[tuple[int, float, float, float, float, str]] = []
    for sec in doc.sections:
        _collect_sentence_boxes(sec, sec.title, boxes)

    # Group by page
    by_page: dict[int, list] = {}
    for entry in boxes:
        pg = entry[0]
        by_page.setdefault(pg, []).append(entry)

    for pg, entries in by_page.items():
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                iou = _iou(entries[i][1:5], entries[j][1:5])
                if iou > 0.5:
                    report.issues.append(CoordIssue(
                        check="BBOX_OVERLAP",
                        element=f"page {pg}",
                        detail=(
                            f"IoU={iou:.2f} between "
                            f"'{entries[i][5]}' and '{entries[j][5]}'"
                        ),
                    ))


def _collect_sentence_boxes(
    sec,
    section_title: str,
    out: list,
) -> None:
    for para in sec.paragraphs:
        for i, sent in enumerate(para.sentences):
            if sent.coords and sent.coords.boxes:
                b = sent.coords.boxes[0]
                out.append((b.page, b.x, b.y, b.w, b.h,
                             f"sent {i} in '{section_title}'"))
    for sub in sec.subsections:
        _collect_sentence_boxes(sub, sub.title or section_title, out)


def _iou(a: tuple, b: tuple) -> float:
    """Intersection-over-Union for two (x, y, w, h) boxes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(ax, bx)
    iy = max(ay, by)
    iw = min(ax + aw, bx + bw) - ix
    ih = min(ay + ah, by + bh) - iy
    if iw <= 0 or ih <= 0:
        return 0.0
    intersection = iw * ih
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0
