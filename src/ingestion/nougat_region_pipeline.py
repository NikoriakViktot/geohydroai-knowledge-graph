"""
nougat_region_pipeline.py — Semantic Scientific Region Constructor.

Pipeline: PDF + GROBID TEI → semantic scientific objects → Nougat inference.

DESIGN PRINCIPLE
────────────────
GROBID coordinates are semantic *anchors*, not final crop regions.
Each raw coordinate block is a fragment. This module reconstructs full
scientific objects — figures with captions/legends, tables with headers and
footnotes, equations with surrounding explanatory text — before handing the
expanded crop to Nougat for multimodal inference.

REGION MERGING
──────────────
Blocks on the same page are merged when:
  • their bboxes overlap OR
  • vertical gap ≤ MERGE_DISTANCE_Y AND horizontal alignment overlaps, OR
  • a caption keyword is found in the text (attaches caption to nearest body)

Merge distances (PDF points = 72 DPI):
  MERGE_DISTANCE_Y = 120   (~1.7 cm)
  MERGE_DISTANCE_X = 80    (~1.1 cm)

CONTEXT EXPANSION (padding added after merge, in PDF points)
──────────────────────────────────────────────────────────────
  FIGURE_REGION   top=15  bottom=25  sides=10
  TABLE_REGION    top=20  bottom=30  sides=10
  FORMULA_REGION  top=35  bottom=35  sides=20

FULL-PAGE FALLBACK
──────────────────
When a merged+expanded region covers ≥ PAGE_FULL_THRESHOLD of the page area,
the entire page is rendered and sent to Nougat instead of a tight crop.
This avoids fragmented inference on pages dominated by figures or charts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable

import pyarrow as pa
import pyarrow.parquet as pq
import ray
from PIL import Image

from src.actors.nougat_actor import NougatActor
from src.analytics.parquet_schema import REGIONS_SCHEMA
from src.document import pdf_io, tei_io
from src.document.nougat_gate import FIGURE_FAMILY, assess_region, snap_bbox, token_budget

# Figure-family regions are not sent to Nougat: it does not read graphics, it returns
# the caption (which GROBID already has) and, on panels, hallucinated loops. Crops are
# still rendered for a later figure/VLM layer. NOUGAT_FIGURES=1 restores inference.
NOUGAT_FIGURES = os.getenv("NOUGAT_FIGURES", "0") == "1"

# Page mode (default): Nougat infers whole pages, as it was trained; regions take
# their block from the page markdown (src/document/nougat_page_mapper.py).
# NOUGAT_MODE=crop restores the old per-region crop inference.
NOUGAT_MODE = os.getenv("NOUGAT_MODE", "page")
PAGE_DPI    = int(os.getenv("NOUGAT_PAGE_DPI", "150"))
# Off by default: on the 2026-10-03 test papers every crop fallback for a table
# returned the paragraph next to it and every formula fallback returned prose.
NOUGAT_CROP_FALLBACK = os.getenv("NOUGAT_CROP_FALLBACK", "0") == "1"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_DIR      = PROJECT_ROOT / "data/literature/pdf"
TEI_DIR      = PROJECT_ROOT / "data/literature/grobid_xml"
OUTPUT_DIR   = PROJECT_ROOT / "data/nougat_regions"
SODB_DIR     = Path(os.getenv("SODB_DIR", str(PROJECT_ROOT / "data/sodb")))

DPI = 300

# ── Merge + expansion constants (PDF points) ──────────────────────────────────
MERGE_DISTANCE_Y   = 120
MERGE_DISTANCE_X   = 80
MIN_BLOCK_AREA     = 50      # filter out noise (width*height in points²)
PAGE_FULL_THRESHOLD = 0.55   # region/page area ratio → use full-page render

_PADDING: dict[str, dict[str, float]] = {
    "FIGURE_REGION":    {"top": 15,  "bottom": 25, "left": 10, "right": 10},
    "TABLE_REGION":     {"top": 20,  "bottom": 30, "left": 10, "right": 10},
    "FORMULA_REGION":   {"top": 35,  "bottom": 35, "left": 20, "right": 20},
    "CHART_REGION":     {"top": 15,  "bottom": 25, "left": 10, "right": 10},
    "MULTI_PANEL_FIGURE": {"top": 10, "bottom": 20, "left": 8,  "right": 8},
    "SCIENTIFIC_DIAGRAM": {"top": 15, "bottom": 25, "left": 10, "right": 10},
}
_DEFAULT_PADDING = {"top": 15, "bottom": 20, "left": 10, "right": 10}

# Caption / legend keyword detection
_CAPTION_KEYWORDS = frozenset(
    ["figure", "fig.", "fig ", "table", "tab.", "tab ", "chart", "scheme",
     "diagram", "equation", "eq.", "eq ", "appendix", "supplementary"]
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("nougat-region-pipeline")


# ── Data models ───────────────────────────────────────────────────────────────

class BlockRole(str, Enum):
    FIGURE_BODY = "figure_body"    # <figure> element coords (graphic + caption boundary)
    GRAPHIC     = "graphic"        # <graphic> child — pure image area
    CAPTION     = "caption"        # <figDesc>, <note type="table">, etc.
    LABEL       = "label"          # figure/table label text ("Fig. 2")
    TABLE_BODY  = "table_body"     # <table> element
    TABLE_HEAD  = "table_head"     # <head> inside table
    FORMULA     = "formula"        # <formula> element
    CONTEXT     = "context"        # surrounding paragraph/sentence providing formula meaning
    UNKNOWN     = "unknown"


@dataclass
class Bbox4:
    """Axis-aligned bounding box in PDF points (origin top-left)."""
    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def from_xywh(cls, x: float, y: float, w: float, h: float) -> "Bbox4":
        return cls(x0=x, y0=y, x1=x + w, y1=y + h)

    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)

    def union(self, other: "Bbox4") -> "Bbox4":
        return Bbox4(
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
        )

    def overlaps(self, other: "Bbox4") -> bool:
        return (self.x0 < other.x1 and other.x0 < self.x1 and
                self.y0 < other.y1 and other.y0 < self.y1)

    def h_overlaps(self, other: "Bbox4") -> bool:
        """Horizontal bands overlap (same column)."""
        return self.x0 < other.x1 and other.x0 < self.x1

    def vertical_gap(self, other: "Bbox4") -> float:
        """Positive gap between two vertically separated boxes; 0 if overlapping."""
        return max(0.0, max(self.y0, other.y0) - min(self.y1, other.y1))

    def horizontal_gap(self, other: "Bbox4") -> float:
        return max(0.0, max(self.x0, other.x0) - min(self.x1, other.x1))

    def pad(
        self,
        top: float,
        bottom: float,
        left: float,
        right: float,
        page_w: float,
        page_h: float,
    ) -> "Bbox4":
        return Bbox4(
            x0=max(0.0,    self.x0 - left),
            y0=max(0.0,    self.y0 - top),
            x1=min(page_w, self.x1 + right),
            y1=min(page_h, self.y1 + bottom),
        )

    def as_dict(self) -> dict:
        return {"x0": round(self.x0, 2), "y0": round(self.y0, 2),
                "x1": round(self.x1, 2), "y1": round(self.y1, 2)}


@dataclass
class PageDimensions:
    width: float
    height: float

    def area(self) -> float:
        return self.width * self.height


@dataclass
class SemanticBlock:
    block_id:   str
    tag:        str
    role:       BlockRole
    page:       int
    bbox:       Bbox4
    text:       str | None = None
    xml_id:     str | None = None


@dataclass
class ScientificRegion:
    paper_id:          str
    page:              int
    region_id:         str
    region_type:       str
    bbox:              Bbox4
    source_blocks:     list[str]   = field(default_factory=list)
    merge_strategy:    list[str]   = field(default_factory=list)
    crop_strategy:     str         = "expanded_context"
    recommended_parser: str        = "nougat"
    semantic_priority:  str        = "high"
    caption_text:       str | None = None
    formula_text:       str | None = None

    def to_json_dict(self) -> dict:
        return {
            "paper_id":           self.paper_id,
            "page":               self.page,
            "region_id":          self.region_id,
            "region_type":        self.region_type,
            "bbox":               self.bbox.as_dict(),
            "source_blocks":      self.source_blocks,
            "merge_strategy":     self.merge_strategy,
            "crop_strategy":      self.crop_strategy,
            "recommended_parser": self.recommended_parser,
            "semantic_priority":  self.semantic_priority,
            "caption_text":       self.caption_text,
            "formula_text":       self.formula_text,
        }


# ── TEI parsing ───────────────────────────────────────────────────────────────

def _iter_text(el) -> str:
    return " ".join(el.itertext()).strip()


def _has_caption_keyword(text: str | None) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(kw in lower for kw in _CAPTION_KEYWORDS)


def parse_page_dimensions(root) -> dict[int, PageDimensions]:
    """Extract page size from <surface> elements (GROBID facsimile section)."""
    dims: dict[int, PageDimensions] = {}
    for surface in root.iter("{*}surface"):
        n_attr = surface.get("n")
        lrx    = surface.get("lrx")
        lry    = surface.get("lry")
        if n_attr and lrx and lry:
            try:
                dims[int(n_attr)] = PageDimensions(float(lrx), float(lry))
            except (ValueError, TypeError):
                pass
    return dims


def _parse_coord_chunks(coords_str: str) -> list[tuple[int, Bbox4]]:
    """Parse GROBID coords string → list of (page, Bbox4)."""
    result = []
    for chunk in coords_str.split(";"):
        parts = chunk.strip().split(",")
        if len(parts) != 5:
            continue
        try:
            page, x, y, w, h = map(float, parts)
        except ValueError:
            continue
        if w * h < MIN_BLOCK_AREA:
            continue
        result.append((int(page), Bbox4.from_xywh(x, y, w, h)))
    return result


def _union_coord_chunks(coords_str: str) -> dict[int, Bbox4]:
    """Merge all coord chunks per page into a single Bbox4 per page."""
    per_page: dict[int, Bbox4] = {}
    for page, bbox in _parse_coord_chunks(coords_str):
        if page in per_page:
            per_page[page] = per_page[page].union(bbox)
        else:
            per_page[page] = bbox
    return per_page


def _xml_id(el) -> str | None:
    return (el.attrib.get("{http://www.w3.org/XML/1998/namespace}id")
            or el.attrib.get("id"))


def extract_semantic_blocks(tei_path: Path) -> tuple[list[SemanticBlock], dict[int, PageDimensions]]:
    """
    Parse GROBID TEI XML into SemanticBlocks + page dimensions.

    Extracts:
      • <figure>      → FIGURE_BODY blocks (per page, union of all coord chunks)
      •   <graphic>   → GRAPHIC blocks (image-only area)
      •   <figDesc>   → CAPTION blocks
      •   <label>     → LABEL blocks
      • <table>       → TABLE_BODY blocks
      •   <head>      → TABLE_HEAD blocks
      • <formula>     → FORMULA blocks + nearby <s> sentences as CONTEXT
    """
    log.info("[TEI] parsing %s", tei_path)
    root = tei_io.parse_file(tei_path, recover=True)

    page_dims = parse_page_dimensions(root)
    blocks: list[SemanticBlock] = []
    counter: dict[str, int] = {}

    def _new_id(prefix: str) -> str:
        counter[prefix] = counter.get(prefix, 0) + 1
        return f"{prefix}_{counter[prefix]}"

    def _emit(tag: str, role: BlockRole, xml_id_val: str | None,
               coords_str: str, text: str | None) -> None:
        prefix = f"{tag}_{role.value}"
        for page, bbox in _union_coord_chunks(coords_str).items():
            blocks.append(SemanticBlock(
                block_id=_new_id(prefix),
                tag=tag,
                role=role,
                page=page,
                bbox=bbox,
                text=text,
                xml_id=xml_id_val,
            ))

    # ── Figures ──────────────────────────────────────────────────────────────
    for fig_el in root.iter("{*}figure"):
        coords = fig_el.attrib.get("coords")
        fig_id = _xml_id(fig_el)
        if coords:
            # GROBID's figure coords are the full bounding box of image+caption
            _emit("figure", BlockRole.FIGURE_BODY, fig_id, coords, None)

        for child in fig_el:
            tag = tei_io.localname(child)
            child_coords = child.attrib.get("coords")
            child_text   = _iter_text(child) or None

            if tag == "graphic" and child_coords:
                _emit("graphic", BlockRole.GRAPHIC, fig_id, child_coords, None)

            elif tag == "figDesc" and child_coords:
                _emit("figDesc", BlockRole.CAPTION, fig_id, child_coords, child_text)

            elif tag == "label" and child_coords:
                _emit("label", BlockRole.LABEL, fig_id, child_coords, child_text)

            elif tag == "head" and child_coords:
                _emit("head", BlockRole.CAPTION, fig_id, child_coords, child_text)

    # ── Tables ───────────────────────────────────────────────────────────────
    for tbl_el in root.iter("{*}table"):
        coords = tbl_el.attrib.get("coords")
        tbl_id = _xml_id(tbl_el)
        if not coords:
            continue
        _emit("table", BlockRole.TABLE_BODY, tbl_id, coords, None)

        head_el = tbl_el.find(".//{*}head")
        if head_el is not None:
            h_coords = head_el.attrib.get("coords")
            h_text   = _iter_text(head_el) or None
            if h_coords:
                _emit("head", BlockRole.TABLE_HEAD, tbl_id, h_coords, h_text)

        # Table caption often lives in a <note> sibling or parent <figure>
        parent = tbl_el.getparent()
        if parent is not None:
            for note in parent.iter("{*}note"):
                n_coords = note.attrib.get("coords")
                n_text   = _iter_text(note) or None
                if n_coords and n_text:
                    _emit("note", BlockRole.CAPTION, tbl_id, n_coords, n_text)

    # ── Formulas ─────────────────────────────────────────────────────────────
    for form_el in root.iter("{*}formula"):
        coords = form_el.attrib.get("coords")
        if not coords:
            continue
        form_id   = _xml_id(form_el)
        form_text = _iter_text(form_el) or None
        _emit("formula", BlockRole.FORMULA, form_id, coords, form_text)

        # Collect surrounding sentences for context (parent <p> children).
        # Deduplicate by (tag, page, rounded bbox) to avoid re-emitting the same
        # sentence for every formula in the same paragraph.
        parent_p = form_el.getparent()
        if parent_p is not None:
            for s_el in parent_p.iter("{*}s"):
                s_coords = s_el.attrib.get("coords")
                s_text   = _iter_text(s_el) or None
                if s_coords and s_text:
                    _emit("s", BlockRole.CONTEXT, form_id, s_coords, s_text)

    # Deduplicate context blocks: same (role, page, bbox rounded to 1pt) → keep one
    _seen_keys: set[tuple] = set()
    deduped: list[SemanticBlock] = []
    for b in blocks:
        key = (b.role, b.page,
               round(b.bbox.x0), round(b.bbox.y0),
               round(b.bbox.x1), round(b.bbox.y1))
        if key in _seen_keys:
            continue
        _seen_keys.add(key)
        deduped.append(b)

    log.info("[TEI] extracted %d semantic blocks (%d after dedup) across %d pages",
             len(blocks), len(deduped), len({b.page for b in deduped}))
    return deduped, page_dims


# ── Region builder (merge + classify + expand) ────────────────────────────────

# Semantic families: blocks within a family may be merged; cross-family merging
# is blocked (a figure must never absorb a formula or a table).
_FAMILY_FIGURE  = frozenset({BlockRole.FIGURE_BODY, BlockRole.GRAPHIC})
_FAMILY_TABLE   = frozenset({BlockRole.TABLE_BODY,  BlockRole.TABLE_HEAD})
_FAMILY_FORMULA = frozenset({BlockRole.FORMULA})


def _family_of(role: BlockRole) -> frozenset:
    if role in _FAMILY_FIGURE:  return _FAMILY_FIGURE
    if role in _FAMILY_TABLE:   return _FAMILY_TABLE
    if role in _FAMILY_FORMULA: return _FAMILY_FORMULA
    return frozenset()           # CAPTION / LABEL / CONTEXT — no own family


class RegionBuilder:
    """
    Groups SemanticBlocks into ScientificRegions via family-isolated merging.

    Algorithm (per page):
      1. Partition anchor blocks into three families: figure, table, formula.
         Merging NEVER crosses family boundaries.
      2. Within each family, greedily merge spatially close blocks.
      3. Attach CAPTION/LABEL blocks to the nearest figure or table region.
         Attach CONTEXT blocks to the nearest formula region.
      4. Re-merge within each family after attachment (captions can extend bbox).
      5. Classify, expand, and decide crop strategy.
    """

    def __init__(self, paper_id: str, page_dims: dict[int, PageDimensions]) -> None:
        self.paper_id  = paper_id
        self.page_dims = page_dims
        self._counter  = 0

    def build(self, blocks: list[SemanticBlock]) -> list[ScientificRegion]:
        by_page: dict[int, list[SemanticBlock]] = {}
        for b in blocks:
            by_page.setdefault(b.page, []).append(b)

        regions: list[ScientificRegion] = []
        for page, page_blocks in sorted(by_page.items()):
            regions.extend(self._build_page(page, page_blocks))
        return regions

    def _build_page(self, page: int, blocks: list[SemanticBlock]) -> list[ScientificRegion]:
        page_dim = self.page_dims.get(page, PageDimensions(595.276, 841.890))

        fig_anchors  = [b for b in blocks if b.role in _FAMILY_FIGURE]
        tbl_anchors  = [b for b in blocks if b.role in _FAMILY_TABLE]
        frm_anchors  = [b for b in blocks if b.role in _FAMILY_FORMULA]
        captions     = [b for b in blocks if b.role in {BlockRole.CAPTION, BlockRole.LABEL}]
        contexts     = [b for b in blocks if b.role == BlockRole.CONTEXT]

        # Build per-family regions (no cross-family merging)
        fig_regions  = self._merge_within(fig_anchors)
        tbl_regions  = self._merge_within(tbl_anchors)
        frm_regions  = self._merge_within(frm_anchors)

        # Attach captions/labels to figure or table regions (whichever is nearer)
        self._attach_to_nearest(fig_regions + tbl_regions, captions)

        # Attach context sentences to formula regions only
        self._attach_to_nearest(frm_regions, contexts)

        # Re-merge within families after attachment expands bboxes
        fig_regions  = self._merge_within_regions(fig_regions)
        tbl_regions  = self._merge_within_regions(tbl_regions)
        # Formulas: merge only if truly touching (inline equation sequences)
        frm_regions  = self._merge_touching_formulas(frm_regions)

        all_regions = fig_regions + tbl_regions + frm_regions
        return [self._finalize(r, page, page_dim) for r in all_regions]

    # ── Within-family merge ───────────────────────────────────────────────────

    def _merge_within(self, blocks: list[SemanticBlock]) -> list["_Region"]:
        """Initial merge: create _Region per block, then merge spatially."""
        return self._merge_within_regions([_Region(b) for b in blocks])

    def _merge_within_regions(self, regions: list["_Region"]) -> list["_Region"]:
        changed = True
        while changed:
            changed = False
            result: list[_Region] = []
            used = [False] * len(regions)
            for i, r1 in enumerate(regions):
                if used[i]:
                    continue
                for j in range(i + 1, len(regions)):
                    if used[j]:
                        continue
                    if _should_merge(r1.bbox, regions[j].bbox):
                        r1.absorb(regions[j], strategy="proximity_merge")
                        used[j] = True
                        changed = True
                result.append(r1)
            regions = result
        return regions

    def _merge_touching_formulas(self, regions: list["_Region"]) -> list["_Region"]:
        """Merge formula regions only if they literally overlap or are tiny (inline)."""
        changed = True
        while changed:
            changed = False
            result: list[_Region] = []
            used = [False] * len(regions)
            for i, r1 in enumerate(regions):
                if used[i]:
                    continue
                for j in range(i + 1, len(regions)):
                    if used[j]:
                        continue
                    b1, b2 = r1.bbox, regions[j].bbox
                    # Only merge if overlapping or within 40pt (very tight gap)
                    if b1.overlaps(b2) or (b1.vertical_gap(b2) <= 40 and b1.h_overlaps(b2)):
                        r1.absorb(regions[j], strategy="formula_inline_merge")
                        used[j] = True
                        changed = True
                result.append(r1)
            regions = result
        return regions

    # ── Caption/context attachment ────────────────────────────────────────────

    def _attach_to_nearest(
        self, regions: list["_Region"], blocks: list[SemanticBlock]
    ) -> None:
        for block in blocks:
            best_region = None
            best_score  = float("inf")
            for r in regions:
                score = _attachment_score(r.bbox, block.bbox, block.text)
                if score < best_score:
                    best_score  = score
                    best_region = r
            if best_region is not None:
                best_region.absorb_block(block, strategy="caption_attachment")

    # ── Finalize → ScientificRegion ───────────────────────────────────────────

    def _finalize(
        self, r: "_Region", page: int, page_dim: PageDimensions
    ) -> ScientificRegion:
        region_type = self._classify(r)
        pad = _PADDING.get(region_type, _DEFAULT_PADDING)
        expanded = r.bbox.pad(
            top=pad["top"], bottom=pad["bottom"],
            left=pad["left"], right=pad["right"],
            page_w=page_dim.width, page_h=page_dim.height,
        )

        page_area = page_dim.area()
        crop_strategy = (
            "full_page"
            if page_area > 0 and expanded.area() / page_area >= PAGE_FULL_THRESHOLD
            else "expanded_context"
        )

        self._counter += 1
        region_id = (f"{self.paper_id}_p{page}_{region_type.lower()[:3]}"
                     f"_{self._counter:03d}")

        return ScientificRegion(
            paper_id           = self.paper_id,
            page               = page,
            region_id          = region_id,
            region_type        = region_type,
            bbox               = expanded,
            source_blocks      = [b.block_id for b in r.blocks],
            merge_strategy     = sorted(r.merge_strategies),
            crop_strategy      = crop_strategy,
            recommended_parser = "nougat",
            semantic_priority  = "high",
            caption_text       = r.caption_text(),
            formula_text       = r.formula_text(),
        )

    def _classify(self, r: "_Region") -> str:
        roles = {b.role for b in r.blocks}

        has_figure  = bool(roles & _FAMILY_FIGURE)
        has_table   = bool(roles & _FAMILY_TABLE)
        has_formula = BlockRole.FORMULA in roles

        if has_formula:
            return "FORMULA_REGION"

        if has_table:
            return "TABLE_REGION"

        if has_figure:
            graphics = [b for b in r.blocks if b.role == BlockRole.GRAPHIC]
            if len(graphics) >= 2:
                return "MULTI_PANEL_FIGURE"
            caption = r.caption_text() or ""
            _chart_keywords = {"chart", "hydrograph", "map", "diagram",
                               "watershed", "basin", "catchment", "dem",
                               "spatial", "contour", "raster", "satellite"}
            if any(kw in caption.lower() for kw in _chart_keywords):
                return "CHART_REGION"
            w = r.bbox.x1 - r.bbox.x0
            h = r.bbox.y1 - r.bbox.y0
            if w > 300 and h > 200:
                return "SCIENTIFIC_DIAGRAM"
            return "FIGURE_REGION"

        return "FIGURE_REGION"


# ── Merge helpers (module-level, pure functions) ───────────────────────────────

def _should_merge(b1: Bbox4, b2: Bbox4) -> bool:
    """True if two bboxes represent the same visual scientific object."""
    if b1.overlaps(b2):
        return True
    if b1.h_overlaps(b2) and b1.vertical_gap(b2) <= MERGE_DISTANCE_Y:
        return True
    # Side-by-side panels (multi-panel figure)
    mid_y1 = (b1.y0 + b1.y1) / 2
    mid_y2 = (b2.y0 + b2.y1) / 2
    if abs(mid_y1 - mid_y2) < 40 and b1.horizontal_gap(b2) <= MERGE_DISTANCE_X:
        return True
    return False


def _attachment_score(region_bbox: Bbox4, block_bbox: Bbox4,
                       block_text: str | None) -> float:
    """Lower is better. inf means 'do not attach'."""
    vgap = region_bbox.vertical_gap(block_bbox)
    hgap = region_bbox.horizontal_gap(block_bbox)

    if region_bbox.overlaps(block_bbox):
        return 0.0

    h_ok = region_bbox.h_overlaps(block_bbox) or hgap <= MERGE_DISTANCE_X

    if h_ok and vgap <= MERGE_DISTANCE_Y:
        return vgap + hgap * 0.5

    # Caption keyword → slightly relaxed threshold
    if _has_caption_keyword(block_text) and vgap <= MERGE_DISTANCE_Y * 1.5:
        return vgap + hgap

    return float("inf")   # don't attach


class _Region:
    """Mutable working region during merge phase."""

    def __init__(self, block: SemanticBlock) -> None:
        self.blocks: list[SemanticBlock] = [block]
        self.merge_strategies: set[str] = set()

    @property
    def bbox(self) -> Bbox4:
        result = self.blocks[0].bbox
        for b in self.blocks[1:]:
            result = result.union(b.bbox)
        return result

    def absorb(self, other: "_Region", strategy: str) -> None:
        self.blocks.extend(other.blocks)
        self.merge_strategies.add(strategy)
        if other.merge_strategies:
            self.merge_strategies.update(other.merge_strategies)

    def absorb_block(self, block: SemanticBlock, strategy: str) -> None:
        self.blocks.append(block)
        self.merge_strategies.add(strategy)

    def caption_text(self) -> str | None:
        texts = [b.text for b in self.blocks
                 if b.role in {BlockRole.CAPTION, BlockRole.LABEL} and b.text]
        return " ".join(texts) if texts else None

    def formula_text(self) -> str | None:
        texts = [b.text for b in self.blocks
                 if b.role == BlockRole.FORMULA and b.text]
        return " ".join(texts) if texts else None


# ── PDF rendering ─────────────────────────────────────────────────────────────

def render_region(pdf_path: Path, region: ScientificRegion, dpi: int = DPI) -> Image.Image:
    """Render the region bbox from the PDF at the specified DPI."""
    b = region.bbox
    # GROBID pages are 1-indexed
    return pdf_io.render_region_image(
        pdf_path, region.page, (b.x0, b.y0, b.x1, b.y1), dpi=dpi)


def render_full_page(pdf_path: Path, page_num: int, dpi: int = DPI) -> Image.Image:
    """Render an entire PDF page (1-indexed) at DPI."""
    return pdf_io.render_page_image(pdf_path, page_num, dpi=dpi)


def get_render_image(pdf_path: Path, region: ScientificRegion) -> Image.Image:
    """Return the image appropriate for the region's crop strategy."""
    if region.crop_strategy == "full_page":
        return render_full_page(pdf_path, region.page)
    return render_region(pdf_path, region)


# ── SODB regions.parquet writer ───────────────────────────────────────────────

def _write_regions_parquet(
    results: list[dict],
    paper_id: str,
    pipeline_hash: str,
    sodb_dir: Path = SODB_DIR,
) -> Path:
    """
    Write regions.parquet to data/sodb/{paper_id}/regions.parquet.

    Atomic: writes to .parquet.tmp then renames.
    Keeps the existing regions.json output untouched.
    """
    out_dir  = sodb_dir / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "regions.parquet"

    rows: list[dict] = []
    now = datetime.now(timezone.utc)

    for item in results:
        region      = item.get("region", {})
        nougat      = item.get("nougat_result") or {}
        gate        = item.get("gate") or {}
        crop_abs    = item.get("crop_path")
        bbox        = region.get("bbox", {})

        # Store crop_path relative to project root for portability
        crop_rel: str | None = None
        if crop_abs:
            try:
                crop_rel = str(Path(crop_abs).relative_to(PROJECT_ROOT))
            except ValueError:
                crop_rel = crop_abs

        # For formula regions, the markdown is the LaTeX content.
        # For all others, visual_text is the readable content.
        # Normalise: old pipeline stored lowercase "formula"; new one stores "FORMULA_REGION".
        region_type  = region.get("region_type", "UNKNOWN")
        is_formula   = "FORMULA" in region_type.upper()
        nougat_latex = nougat.get("markdown_text") if is_formula else None
        nougat_text  = nougat.get("visual_text")

        # Page may be at region["page"] (new format) or region["bbox"]["page"] (old format).
        page_val = region.get("page") or bbox.get("page") or 0

        rows.append({
            "region_id":      region.get("region_id", ""),
            "paper_id":       paper_id,
            "page":           int(page_val),
            # Handle both coordinate formats:
            #   new (_Region.to_json_dict): x0/y0/x1/y1
            #   old (regions.json legacy):  x/y/width/height
            "bbox_x0":        float(bbox.get("x0") or bbox.get("x") or 0.0),
            "bbox_y0":        float(bbox.get("y0") or bbox.get("y") or 0.0),
            "bbox_x1":        float(bbox.get("x1") or (bbox.get("x", 0) + bbox.get("width", 0))),
            "bbox_y1":        float(bbox.get("y1") or (bbox.get("y", 0) + bbox.get("height", 0))),
            "region_type":    region_type,
            "source_parser":  "HYBRID",  # GROBID coords + Nougat content
            "nougat_text":    nougat_text,
            "nougat_latex":   nougat_latex,
            "confidence":     gate.get("confidence"),
            "crop_path":      crop_rel,
            "merge_group_id": None,
            "pipeline_hash":  pipeline_hash,
            "created_at":     now,
            "nougat_status":     gate.get("nougat_status"),
            "nougat_flags":      gate.get("nougat_flags"),
            "grounding_words":   gate.get("grounding_words"),
            "grounding_numbers": gate.get("grounding_numbers"),
            "crop_strategy":     region.get("crop_strategy"),
        })

    table   = pa.Table.from_pylist(rows, schema=REGIONS_SCHEMA)
    tmp     = out_path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="snappy")
    tmp.rename(out_path)
    log.info("[SODB] regions.parquet written → %s  (%d rows)", out_path, len(rows))
    return out_path


# ── Pipeline orchestrator ─────────────────────────────────────────────────────

class NougatRegionPipeline:
    def __init__(self) -> None:
        log.info("[RAY] init")
        ray.init(ignore_reinit_error=True)

        log.info("[ACTOR] starting NougatActor")
        self.actor = NougatActor.remote()

        info = ray.get(self.actor.model_info.remote())
        log.info("[ACTOR] model_info=%s", info)

        # Stable hash identifying this model + pipeline version.
        # Changes whenever the model or code version changes, which causes
        # SODBManifest to mark cached stages as stale.
        # mode and guard version are part of the hash: results of the old crop mode
        # must not count as done for page mode
        from src.document.nougat_parser import REGION_GUARD_VERSION
        _hash_src = (f"{info.get('model_name', '')}:{info.get('model_version', '')}:"
                     f"{NOUGAT_MODE}:{REGION_GUARD_VERSION}").encode()
        self.pipeline_hash = "sha256:" + hashlib.sha256(_hash_src).hexdigest()[:16]
        log.info("[PIPELINE_HASH] %s", self.pipeline_hash)

    def process_pdf(self, pdf_path: Path, tei_path: Path) -> dict:
        if NOUGAT_MODE == "page":
            return self.process_pdf_pages(pdf_path, tei_path)
        return self.process_pdf_crops(pdf_path, tei_path)

    # ── page mode ─────────────────────────────────────────────────────────────

    def process_pdf_pages(self, pdf_path: Path, tei_path: Path) -> dict:
        """Infer each page holding a table or formula once, then give every region
        its block of the page markdown, grounded in the PDF text layer."""
        from src.document.nougat_gate import loop_flags
        from src.document.nougat_page_mapper import assign_regions

        paper_id  = pdf_path.stem
        out_dir   = OUTPUT_DIR / paper_id
        crops_dir = out_dir / "crops"
        pages_dir = out_dir / "pages"
        for d in (crops_dir, pages_dir):
            d.mkdir(parents=True, exist_ok=True)
        log.info("[PDF] %s  (page mode)", pdf_path.name)

        blocks, page_dims = extract_semantic_blocks(tei_path)
        regions = RegionBuilder(paper_id, page_dims).build(blocks)
        log.info("[REGIONS] %d scientific regions constructed from %d blocks",
                 len(regions), len(blocks))

        # crops are kept for human verification and a later figure/VLM layer
        crop_of: dict[str, str] = {}
        for region in regions:
            crop_path = crops_dir / f"{region.region_id}.png"
            try:
                render_region(pdf_path, region).save(crop_path)
                crop_of[region.region_id] = str(crop_path)
            except Exception:
                log.warning("[crop] %s: render failed", region.region_id)

        by_page: dict[int, list[ScientificRegion]] = {}
        for region in regions:
            if region.region_type not in FIGURE_FAMILY:
                by_page.setdefault(region.page, []).append(region)

        futures = {}
        for page in sorted(by_page):
            try:
                image = pdf_io.render_page_image(pdf_path, page, dpi=PAGE_DPI)
                futures[page] = self.actor.parse_image.remote(image, f"{paper_id}_p{page}")
            except Exception as exc:
                log.warning("[page] %s p%d: render failed: %s", paper_id, page, exc)

        results: list[dict] = []
        actor_failures = 0
        for region in regions:
            if region.region_type in FIGURE_FAMILY:
                region.crop_strategy = "not_inferred"
                results.append({"region": region.to_json_dict(),
                                "crop_path": crop_of.get(region.region_id),
                                "gate": assess_region(None, region.region_type).as_row()})

        for page, page_regions in sorted(by_page.items()):
            fut = futures.get(page)
            parsed, page_md, page_flags = {}, "", []
            if fut is None:
                page_flags.append("RENDER_FAILURE")
            else:
                try:
                    parsed = ray.get(fut)
                    page_md = parsed.get("markdown_text") or ""
                except Exception:
                    actor_failures += 1
                    log.exception("[PAGE FAILED nougat] %s p%d", paper_id, page)
                    page_flags.append("ACTOR_FAILURE")
            (pages_dir / f"p{page:03d}.md").write_text(page_md, encoding="utf-8")
            # A collapse is cut off by the parser (the prefix is kept, as in Nougat's
            # own inference); regions after the cut stay unmatched. Only a loop that
            # survives in the text, or an exhausted budget, rejects the page.
            note = ["PAGE_TRUNCATED_AT_LOOP"] if parsed.get("guard_stopped") else []
            if parsed.get("hit_token_budget"):
                page_flags.append("PAGE_TOKEN_BUDGET_EXHAUSTED")
            if page_md and loop_flags(page_md):
                page_flags.append("PAGE_LOOP")

            words = None
            try:
                words = pdf_io.extract_page_words(pdf_path, page)
            except Exception:
                pass
            assigned = assign_regions(
                page_md,
                [{"region_id": r.region_id, "region_type": r.region_type,
                  "bbox": (r.bbox.x0, r.bbox.y0, r.bbox.x1, r.bbox.y1),
                  "formula_text": r.formula_text} for r in page_regions],
                words,
            ) if page_md and not page_flags else {}

            for region in page_regions:
                region.crop_strategy = "page_inference"
                a = assigned.get(region.region_id)
                text = a.text if a else None
                if not text and not page_flags:
                    fb = self._crop_fallback(pdf_path, region, words)
                    if fb is not None:
                        fb["crop_path"] = crop_of.get(region.region_id)
                        results.append(fb)
                        continue
                if page_flags:
                    gate = {"nougat_status": "failed" if "ACTOR_FAILURE" in page_flags
                            or "RENDER_FAILURE" in page_flags else "rejected",
                            "nougat_flags": ",".join(page_flags)}
                elif not text:
                    gate = {"nougat_status": "failed",
                            "nougat_flags": ",".join(note + [f"UNMATCHED_ON_PAGE:{a.method if a else 'unmatched'}"])}
                else:
                    v = assess_region(text, region.region_type, (0.0, 0.0, 1e5, 1e5), words)
                    v.flags += note + [f"MATCH:{a.method}:{a.score:.2f}"]
                    gate = v.as_row()
                results.append({
                    "region":        region.to_json_dict(),
                    "crop_path":     crop_of.get(region.region_id),
                    "nougat_result": {"markdown_text": text, "visual_text": text,
                                      "page_markdown": f"pages/p{page:03d}.md"},
                    "gate":          gate,
                })

        final = {"paper_id": paper_id, "pdf_path": str(pdf_path), "tei_path": str(tei_path),
                 "blocks_extracted": len(blocks), "regions_count": len(regions),
                 "pages_inferred": len(by_page), "mode": "page", "regions": results}
        with open(out_dir / "regions.json", "w", encoding="utf-8") as f:
            json.dump(final, f, indent=2, ensure_ascii=False)
        _write_regions_parquet(results, paper_id, self.pipeline_hash)

        if actor_failures:
            log.error("[INCOMPLETE] %s: %d page(s) failed in the Nougat actor; "
                      "region_extract is NOT marked done", paper_id, actor_failures)
            final["actor_failures"] = actor_failures
            return final
        try:
            from src.document.sodb_manifest import SODBManifest
            SODBManifest(paper_id, self.pipeline_hash, SODB_DIR).mark_done("region_extract")
        except Exception as _exc:
            log.debug("[manifest] region_extract mark failed: %s", _exc)
        n_acc = sum(1 for r in results if (r.get("gate") or {}).get("nougat_status") == "accepted")
        log.info("[PAGES] %s: %d pages inferred, %d regions accepted", paper_id, len(by_page), n_acc)
        return final

    def _crop_fallback(self, pdf_path: Path, region: "ScientificRegion",
                       words: list | None) -> dict | None:
        """A table or formula the page inference did not produce (Nougat skipped the
        table, or collapsed before reaching it) is inferred once more from its crop.
        Crops are out of distribution, so the result goes through the same gate with
        grounding against the crop's own text layer, and is marked CROP_FALLBACK."""
        if NOUGAT_CROP_FALLBACK is False or not words:
            return None
        pad = _PADDING.get(region.region_type, _DEFAULT_PADDING)
        b = region.bbox
        nb = snap_bbox((b.x0, b.y0, b.x1, b.y1), words,
                       (pad["top"], pad["bottom"], pad["left"], pad["right"]))
        region.bbox = Bbox4(*nb)
        region.crop_strategy = "crop_fallback"
        try:
            image = render_region(pdf_path, region)
            parsed = ray.get(self.actor.parse_image.remote(image, region.region_id,
                                                            token_budget(nb)))
        except Exception as exc:
            log.warning("[fallback] %s: %s", region.region_id, exc)
            return None
        text = parsed.get("markdown_text") or parsed.get("visual_text") or ""
        v = assess_region(text, region.region_type, nb, words)
        v.flags.append("CROP_FALLBACK")
        if parsed.get("guard_stopped") or parsed.get("hit_token_budget"):
            v.flags.append("GENERATION_LOOP_STOPPED")
            v.status = "rejected"
        return {"region": region.to_json_dict(), "crop_path": None,
                "nougat_result": {"markdown_text": text, "visual_text": text},
                "gate": v.as_row()}

    # ── crop mode (legacy) ────────────────────────────────────────────────────

    def process_pdf_crops(self, pdf_path: Path, tei_path: Path) -> dict:
        paper_id  = pdf_path.stem
        out_dir   = OUTPUT_DIR / paper_id
        crops_dir = out_dir / "crops"
        out_dir.mkdir(parents=True, exist_ok=True)
        crops_dir.mkdir(parents=True, exist_ok=True)

        log.info("[PDF] %s", pdf_path.name)

        # 1. Parse TEI → semantic blocks + page dimensions
        blocks, page_dims = extract_semantic_blocks(tei_path)

        # 2. Build scientific regions (merge + expand)
        builder = RegionBuilder(paper_id, page_dims)
        regions = builder.build(blocks)

        log.info("[REGIONS] %d scientific regions constructed from %d blocks",
                 len(regions), len(blocks))

        results = []
        futures = []
        region_meta = []
        page_words: dict[int, list | None] = {}

        def words_of(page: int):
            if page not in page_words:
                try:
                    page_words[page] = pdf_io.extract_page_words(pdf_path, page)
                except Exception as exc:          # scanned page or broken stream
                    log.debug("[words] %s p%d: %s", paper_id, page, exc)
                    page_words[page] = None
            return page_words[page]

        # 3. Render crops and dispatch to NougatActor (concurrent)
        for i, region in enumerate(regions, start=1):
            crop_path = crops_dir / f"{region.region_id}.png"

            # Do not cut a text line at the top or bottom edge of a table/formula crop.
            if region.crop_strategy != "full_page" and region.region_type not in FIGURE_FAMILY:
                w = words_of(region.page)
                if w:
                    pad = _PADDING.get(region.region_type, _DEFAULT_PADDING)
                    b = region.bbox
                    nb = snap_bbox((b.x0, b.y0, b.x1, b.y1), w,
                                   (pad["top"], pad["bottom"], pad["left"], pad["right"]))
                    region.bbox = Bbox4(*nb)

            log.info(
                "[REGION %d/%d] type=%-22s strategy=%-16s page=%d  bbox=(%.0f,%.0f,%.0f,%.0f)",
                i, len(regions),
                region.region_type, region.crop_strategy, region.page,
                region.bbox.x0, region.bbox.y0, region.bbox.x1, region.bbox.y1,
            )

            try:
                image = get_render_image(pdf_path, region)
                image.save(crop_path)
                if region.region_type in FIGURE_FAMILY and not NOUGAT_FIGURES:
                    futures.append(None)
                    region_meta.append((region, str(crop_path)))
                    results.append({
                        "region":    region.to_json_dict(),
                        "crop_path": str(crop_path),
                        "gate":      assess_region(None, region.region_type).as_row(),
                    })
                    continue
                b = region.bbox
                future = self.actor.parse_image.remote(
                    image, region.region_id,
                    token_budget((b.x0, b.y0, b.x1, b.y1)) if region.crop_strategy != "full_page"
                    else None)
                futures.append(future)
                region_meta.append((region, str(crop_path)))
            except Exception as exc:
                log.exception("[REGION FAILED render] %s", region.region_id)
                results.append({
                    "region":     region.to_json_dict(),
                    "crop_path":  str(crop_path),
                    "error":      str(exc),
                    "gate":       {"nougat_status": "failed", "nougat_flags": "RENDER_FAILURE"},
                })
                futures.append(None)
                region_meta.append((region, str(crop_path)))

        # 4. Collect results
        actor_failures = 0      # ray.get raised: the actor died (OOM kill, restart), not a bad crop
        for idx, (region, crop_path) in enumerate(region_meta):
            future = futures[idx]
            if future is None:
                continue
            try:
                parsed = ray.get(future)
                text = parsed.get("markdown_text") or parsed.get("visual_text") or ""
                b = region.bbox
                gbox = (0.0, 0.0, 1e5, 1e5) if region.crop_strategy == "full_page" \
                    else (b.x0, b.y0, b.x1, b.y1)
                verdict = assess_region(text, region.region_type, gbox, words_of(region.page))
                if parsed.get("guard_stopped"):
                    verdict.flags.append("GENERATION_LOOP_STOPPED")
                    verdict.status = "rejected"
                if parsed.get("hit_token_budget"):
                    verdict.flags.append("TOKEN_BUDGET_EXHAUSTED")
                    verdict.status = "rejected"
                if parsed.get("error"):
                    verdict.status = "failed"
                results.append({
                    "region":       region.to_json_dict(),
                    "crop_path":    crop_path,
                    "nougat_result": parsed,
                    "gate":         verdict.as_row(),
                })
                if parsed.get("error"):
                    log.warning("[REGION ERROR] %s  err=%s",
                                region.region_id, parsed["error"])
                else:
                    log.info("[REGION OK] %s  md_len=%s",
                             region.region_id,
                             len(parsed.get("markdown_text") or ""))
            except Exception as exc:
                actor_failures += 1
                log.exception("[REGION FAILED nougat] %s", region.region_id)
                results.append({
                    "region":    region.to_json_dict(),
                    "crop_path": crop_path,
                    "error":     str(exc),
                    "gate":      {"nougat_status": "failed", "nougat_flags": "ACTOR_FAILURE"},
                })

        final = {
            "paper_id":        paper_id,
            "pdf_path":        str(pdf_path),
            "tei_path":        str(tei_path),
            "blocks_extracted": len(blocks),
            "regions_count":   len(regions),
            "regions":         results,
        }

        output_path = out_dir / "regions.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(final, f, indent=2, ensure_ascii=False)

        log.info("[SAVED] %s", output_path)

        _write_regions_parquet(results, paper_id, self.pipeline_hash)

        # Mark region_extract done in SODB manifest for idempotency — but not when the
        # actor failed under some regions: on 2026-10-03 Ray killed NougatActor (node
        # memory > 95 %), 12 papers were written with empty regions and marked done, and
        # the next run skipped them. Without the mark the next run redoes the paper.
        if actor_failures:
            log.error("[INCOMPLETE] %s: %d of %d regions failed in the Nougat actor; "
                      "region_extract is NOT marked done, rerun to redo this paper",
                      paper_id, actor_failures, len(region_meta))
            final["actor_failures"] = actor_failures
            return final
        try:
            from src.document.sodb_manifest import SODBManifest
            SODBManifest(paper_id, self.pipeline_hash, SODB_DIR).mark_done("region_extract")
        except Exception as _exc:
            log.debug("[manifest] region_extract mark failed: %s", _exc)

        return final


# ── Discovery + entry point ───────────────────────────────────────────────────

def iter_pdf_pairs(
    limit:       int | None = None,
    skip_done:   bool = True,
    sodb_root:   Path = SODB_DIR,
    pipeline_hash: str | None = None,
) -> Iterable[tuple[Path, Path]]:
    """
    Yield (pdf_path, tei_path) pairs eligible for Nougat processing.

    Skip logic (when skip_done=True):
      - Skip if data/sodb/{paper_id}/regions.parquet already exists AND
        the SODB manifest records region_extract=done under the current
        pipeline_hash.  This makes the runner idempotent on re-runs.
    """
    from src.document.sodb_manifest import SODBManifest

    pdf_files = sorted(PDF_DIR.glob("*.pdf"))
    log.info("[DISCOVERY] PDF_DIR=%s  found=%d", PDF_DIR, len(pdf_files))

    count = skipped = 0
    for pdf_path in pdf_files:
        tei_path = TEI_DIR / f"{pdf_path.stem}.tei.xml"
        if not tei_path.exists():
            log.debug("[SKIP no-tei] %s", pdf_path.name)
            continue

        if skip_done and pipeline_hash:
            paper_id = pdf_path.stem
            parquet_path = sodb_root / paper_id / "regions.parquet"
            if parquet_path.exists():
                manifest = SODBManifest(paper_id, pipeline_hash, sodb_root)
                if manifest.is_done("region_extract"):
                    skipped += 1
                    continue

        yield pdf_path, tei_path
        count += 1
        if limit is not None and count >= limit:
            break

    if skipped:
        log.info("[SKIP pre-filter] %d already done (regions.parquet + manifest)", skipped)


def iter_listed_pairs(list_file: Path) -> Iterable[tuple[Path, Path]]:
    """(pdf, tei) for every paper_id listed in list_file; PDFs are looked up in all
    corpus PDF folders. Missing PDFs or TEI are logged and skipped."""
    pdf_dirs = [PDF_DIR, PROJECT_ROOT / "data/literature/pdf_oa",
                PROJECT_ROOT / "data/literature/pdf_missing"]
    for line in Path(list_file).read_text().splitlines():
        pid = line.strip()
        if not pid or pid.startswith("#"):
            continue
        pdf = next((d / f"{pid}.pdf" for d in pdf_dirs if (d / f"{pid}.pdf").exists()), None)
        tei = TEI_DIR / f"{pid}.tei.xml"
        if pdf is None or not tei.exists():
            log.warning("[PAPER-LIST] %s: pdf=%s tei=%s — skipped", pid, pdf, tei.exists())
            continue
        yield pdf, tei


def _run_sequential(pipeline: "NougatRegionPipeline", pairs) -> tuple[int, int]:
    processed = errors = 0
    for pdf_path, tei_path in pairs:
        log.info("=" * 72)
        log.info("[PROCESS] %s", pdf_path.name)
        try:
            result = pipeline.process_pdf(pdf_path, tei_path)
            log.info("[DONE] paper=%s  blocks=%d  regions=%d",
                     result["paper_id"], result["blocks_extracted"], result["regions_count"])
            processed += 1
        except Exception as exc:
            log.exception("[ERROR] %s: %s", pdf_path.name, exc)
            errors += 1
    return processed, errors


def _run_parallel(pipeline: "NougatRegionPipeline", pairs, n_workers: int) -> tuple[int, int]:
    """
    Process N PDFs concurrently via ThreadPoolExecutor.

    CPU work (TEI parse + PDF render) for later papers overlaps with
    GPU inference for earlier papers, increasing overall GPU utilisation.
    ray.get() and actor.remote() are both thread-safe in Ray 2.x.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    pair_list = list(pairs)
    log.info("[PARALLEL] %d PDFs  workers=%d", len(pair_list), n_workers)

    processed = errors = 0

    def _work(pdf_path: Path, tei_path: Path) -> dict:
        log.info("[PROCESS] %s", pdf_path.name)
        return pipeline.process_pdf(pdf_path, tei_path)

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        future_map = {pool.submit(_work, pdf, tei): pdf for pdf, tei in pair_list}
        for fut in as_completed(future_map):
            pdf_path = future_map[fut]
            try:
                result = fut.result()
                log.info("[DONE] paper=%s  blocks=%d  regions=%d",
                         result["paper_id"], result["blocks_extracted"], result["regions_count"])
                processed += 1
            except Exception as exc:
                log.exception("[ERROR] %s: %s", pdf_path.name, exc)
                errors += 1

    return processed, errors


def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="GeoHydroAI — Nougat semantic region extraction",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Maximum number of papers to process (default: all)",
    )
    parser.add_argument(
        "--overwrite", action="store_true", default=False,
        help="Re-process papers whose regions.parquet already exists",
    )
    parser.add_argument(
        "--pdf-dir", type=Path, default=PDF_DIR,
        help="Directory containing PDF files",
    )
    parser.add_argument(
        "--tei-dir", type=Path, default=TEI_DIR,
        help="Directory containing GROBID TEI XML files",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="PDFs processed concurrently (>1 overlaps CPU+GPU work, raises GPU utilisation)",
    )
    parser.add_argument(
        "--paper-list", type=Path, default=None,
        help="File with one paper_id per line: (re)process exactly these papers, "
             "looking for the PDF in pdf/, pdf_oa/ and pdf_missing/ (implies --overwrite)",
    )
    args = parser.parse_args()

    # Override module-level constants if flags given
    if args.pdf_dir != PDF_DIR:
        globals()["PDF_DIR"] = args.pdf_dir
    if args.tei_dir != TEI_DIR:
        globals()["TEI_DIR"] = args.tei_dir

    log.info("[BOOT] Nougat semantic region extraction via Ray actor")
    log.info("[BOOT] workers=%d  gpu_fraction=%s",
             args.workers, os.getenv("NOUGAT_GPU_FRACTION", "0.5"))
    log.info("[PROJECT_ROOT] %s", PROJECT_ROOT)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    pipeline = NougatRegionPipeline()

    if args.paper_list:
        pairs = list(iter_listed_pairs(args.paper_list))[: args.limit]
        log.info("[PAPER-LIST] %d papers from %s", len(pairs), args.paper_list)
    else:
        pairs = iter_pdf_pairs(
            limit=args.limit,
            skip_done=not args.overwrite,
            sodb_root=SODB_DIR,
            pipeline_hash=pipeline.pipeline_hash,
        )

    if args.workers > 1:
        processed, errors = _run_parallel(pipeline, pairs, args.workers)
    else:
        processed, errors = _run_sequential(pipeline, pairs)

    log.info("=" * 72)
    log.info("[SUMMARY] processed=%d  errors=%d", processed, errors)


if __name__ == "__main__":
    main()
