"""
region_kg_loader.py — Integration bus: nougat_regions → Neo4j.

Reads every data/nougat_regions/{paper_id}/regions.json produced by
nougat_region_pipeline.py and writes:

  ScientificFigure  nodes  (FIGURE_REGION, SCIENTIFIC_DIAGRAM, CHART_REGION,
                             MULTI_PANEL_FIGURE)
  ScientificTable   nodes  (TABLE_REGION)
  Equation          nodes  (FORMULA_REGION)

  Paper -[:HAS_FIGURE]->    ScientificFigure
  Paper -[:HAS_TABLE]->     ScientificTable
  Paper -[:HAS_EQUATION]->  Equation

  ScientificFigure  -[:FIGURE_MENTIONS]->    Method | Sensor | Metric
  ScientificTable   -[:TABLE_REPORTS]->      Metric
  Equation          -[:EQUATION_GROUNDS_TO]-> Method

Quality gate: every Nougat result is scored by NougatQuality.  Objects with
rejected=True are SKIPPED — they are never written to Neo4j.

Entity grounding: caption text + Nougat markdown are scanned for hydrology
method / metric keywords using the ontology registry.  Matches produce
FIGURE_MENTIONS / TABLE_REPORTS / EQUATION_GROUNDS_TO edges.

Usage::

    python -m src.graph.region_kg_loader            # all papers
    python -m src.graph.region_kg_loader 0030       # one paper
    python -m src.graph.region_kg_loader --dry-run  # parse only, no writes
"""

from __future__ import annotations

import json
import os
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from src.document.nougat_gate import FIGURE_FAMILY
from src.document.nougat_quality import score_nougat_output
from src.graph.neo4j_writer import GraphWriter

log = logging.getLogger("geohydro.region_kg_loader")

_PROJECT_ROOT    = Path(__file__).resolve().parents[2]
_NOUGAT_DIR      = _PROJECT_ROOT / "data" / "nougat_regions"
_ONTOLOGY_REG    = _PROJECT_ROOT / "data" / "ontology" / "ontology_registry.json"


# ── Figure type classifier ────────────────────────────────────────────────────
# Maps region_type + caption keywords → semantic figure_type.
# This is the Phase 1 caption-only classifier; FigureRouter (Phase 2) will
# replace this with a visual model.

_REGION_TYPE_DEFAULTS: dict[str, str] = {
    "FIGURE_REGION":     "other",
    "SCIENTIFIC_DIAGRAM": "methodology_diagram",
    "CHART_REGION":      "chart",
    "MULTI_PANEL_FIGURE": "multi_panel",
    "TABLE_REGION":      "other",
    "FORMULA_REGION":    "other",
}

# Caption keyword → specific figure_type (highest-match wins)
_CAPTION_TYPE_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bhydrograph\b",          re.I), "hydrograph"),
    (re.compile(r"\bstreamflow\b|\bdischarge\b",re.I), "hydrograph"),
    (re.compile(r"\bpeak\s+flow\b",         re.I), "hydrograph"),
    (re.compile(r"\bdem\b|\belevation\s+model\b", re.I), "dem_map"),
    (re.compile(r"\bterrain\b|\btopograph", re.I), "dem_map"),
    (re.compile(r"\bland.use\b|\bland.cover\b|\blulc\b", re.I), "lulc_map"),
    (re.compile(r"\bflood\s+(map|extent|inundation)\b", re.I), "flood_map"),
    (re.compile(r"\binundation\b",          re.I), "flood_map"),
    (re.compile(r"\bwatershed\b|\bcatchment\b", re.I), "watershed_map"),
    (re.compile(r"\bbasin\s+(map|delineation)\b", re.I), "watershed_map"),
    (re.compile(r"\bflowchart\b|\bframework\b|\bmethodology\b", re.I), "methodology_diagram"),
    (re.compile(r"\bscatter\b|\bcorrelation\b",  re.I), "scatter_plot"),
    (re.compile(r"\bbar\s+(chart|graph)\b",      re.I), "bar_chart"),
    (re.compile(r"\btime\s+series\b",            re.I), "time_series"),
    (re.compile(r"\bsatellite\b|\bimagery\b|\bcomposite\b", re.I), "satellite_image"),
]


def _classify_figure_type(region_type: str, caption: str | None) -> str:
    base = _REGION_TYPE_DEFAULTS.get(region_type, "other")
    if not caption:
        return base
    for pattern, fig_type in _CAPTION_TYPE_MAP:
        if pattern.search(caption):
            return fig_type
    return base


# ── Table type classifier ─────────────────────────────────────────────────────

_TABLE_TYPE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bcalibration\b|\bvalidation\b|\bverification\b", re.I), "validation_table"),
    (re.compile(r"\bperformance\b|\baccuracy\b|\bnse\b|\bkge\b|\brmse\b|\bpbias\b", re.I), "validation_table"),
    (re.compile(r"\bparameter\b|\bcoefficient\b|\bvalue\s+range\b", re.I), "parameter_table"),
    (re.compile(r"\bland.use\b|\blulc\b|\bcurve\s+number\b|\bcn\b", re.I), "lulc_table"),
    (re.compile(r"\brainfall\b|\bstorm\b|\bevent\b.*\bdate\b", re.I), "event_table"),
    (re.compile(r"\bcomparison\b|\bmodel\b.*\bvs\b|\bmethod\b.*\bmethod\b", re.I), "comparison_table"),
]


def _classify_table_type(caption: str | None, nougat_text: str | None) -> str:
    text = " ".join(filter(None, [caption, nougat_text]))
    if not text:
        return "other"
    for pattern, tbl_type in _TABLE_TYPE_PATTERNS:
        if pattern.search(text):
            return tbl_type
    return "other"


# ── Equation analysis ─────────────────────────────────────────────────────────

# Extracts the LHS variable from a formula string.
_LHS_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\s*([A-Za-z][A-Za-z_0-9]{0,8}(?:_\{[^}]+\})?)\s*[=\\leq\\geq]+"),
    re.compile(r"^\s*\\(?:alpha|beta|gamma|lambda|mu|sigma|rho|tau|phi|psi|omega|theta)"
               r"(?:_\{[^}]+\})?\s*="),
]

def _extract_lhs(formula_text: str | None, markdown: str | None) -> str | None:
    for src in (formula_text, markdown):
        if not src:
            continue
        clean = src.strip().replace("\\\\", "").replace("\n", " ")
        for pat in _LHS_PATTERNS:
            m = pat.match(clean)
            if m:
                return m.group(1)
    return None


# Known LHS symbols → equation type (hydrology domain)
_LHS_TO_EQ_TYPE: dict[str, str] = {
    "P_e": "loss_function", "Pe": "loss_function", "Q": "routing",
    "S":   "empirical",     "CN": "empirical",      "I_a": "definition",
    "NSE": "objective_function", "KGE": "objective_function",
    "RMSE":"objective_function", "PBIAS": "objective_function",
    "R2":  "objective_function", "MAE":  "objective_function",
    "q":   "routing",       "Q_p": "transform",    "q_p": "transform",
    "t_p": "transform",     "T_p": "transform",
}

_EQ_TYPE_CONTEXT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bloss\b|\brunoff\b|\babstraction\b|\bprecipitation excess\b", re.I), "loss_function"),
    (re.compile(r"\bNash[\-–]Sutcliffe\b|\befficiency\b|\bRMSE\b|\bPBIAS\b", re.I), "objective_function"),
    (re.compile(r"\brouting\b|\bMuskingum\b|\bstorage\b|\btranslation\b",    re.I), "routing"),
    (re.compile(r"\bunit hydrograph\b|\btransform\b|\blag time\b",            re.I), "transform"),
    (re.compile(r"\bcurve number\b|\bCN\b|\brational\b",                     re.I), "empirical"),
    (re.compile(r"\bwhere\b.*\bis\s+the\b|\bdefined as\b",                   re.I), "definition"),
]

def _classify_eq_type(lhs: str | None, context: str | None) -> str:
    if lhs and lhs in _LHS_TO_EQ_TYPE:
        return _LHS_TO_EQ_TYPE[lhs]
    if context:
        for pat, eq_type in _EQ_TYPE_CONTEXT_PATTERNS:
            if pat.search(context):
                return eq_type
    return "other"


# ── Entity grounding ──────────────────────────────────────────────────────────

@dataclass
class GroundedEntity:
    canonical_id: str
    node_label:   str    # "Method" | "Sensor" | "Metric"
    evidence:     str


def _load_ontology_keywords() -> list[tuple[re.Pattern, str, str]]:
    """
    Build a lightweight keyword matcher from the ontology registry.

    Returns list of (compiled_pattern, canonical_id, node_label).
    Only includes high-signal entities to avoid false positives in
    short caption/markdown text.
    """
    if not _ONTOLOGY_REG.exists():
        log.warning("Ontology registry not found: %s", _ONTOLOGY_REG)
        return []

    with open(_ONTOLOGY_REG) as f:
        registry = json.load(f)

    rules: list[tuple[re.Pattern, str, str]] = []

    for entry in registry.get("entities", []):
        cid   = entry.get("canonical_id", "")
        etype = entry.get("type", "")
        if not cid:
            continue

        if etype in {"method", "process", "model", "software"}:
            label = "Method"
        elif etype in {"sensor", "satellite", "satellite_mission"}:
            label = "Sensor"
        elif etype in {"metric"}:
            label = "Metric"
        else:
            continue

        # Build pattern from acronym + display_name + aliases
        terms: list[str] = []
        for key in ("acronym", "display_name", "full_name"):
            val = entry.get(key, "")
            if val and len(val) >= 3:
                terms.append(re.escape(val))
        for alias in entry.get("aliases", []):
            if len(alias) >= 3:
                terms.append(re.escape(alias))

        if not terms:
            continue

        pattern_str = r"\b(?:" + "|".join(terms) + r")\b"
        try:
            rules.append((re.compile(pattern_str, re.I), cid, label))
        except re.error:
            pass

    log.info("[grounding] loaded %d entity patterns from ontology", len(rules))
    return rules


# Load once at module import time (cached in module scope)
_ONTOLOGY_RULES: list[tuple[re.Pattern, str, str]] | None = None


def _get_ontology_rules() -> list[tuple[re.Pattern, str, str]]:
    global _ONTOLOGY_RULES
    if _ONTOLOGY_RULES is None:
        _ONTOLOGY_RULES = _load_ontology_keywords()
    return _ONTOLOGY_RULES


def _ground_entities(text: str | None) -> list[GroundedEntity]:
    """Run ontology keyword matching on text, return matched entities."""
    if not text or len(text) < 5:
        return []
    entities: list[GroundedEntity] = []
    seen: set[str] = set()
    for pattern, cid, label in _get_ontology_rules():
        m = pattern.search(text)
        if m and cid not in seen:
            seen.add(cid)
            snippet = text[max(0, m.start() - 30) : m.end() + 30].strip()
            entities.append(GroundedEntity(cid, label, snippet))
    return entities


# ── Nougat result extraction ──────────────────────────────────────────────────

def _extract_markdown(nougat_result: dict) -> str | None:
    """Pull markdown text from NougatActor result dict."""
    if not nougat_result or nougat_result.get("error"):
        return None
    return nougat_result.get("markdown_text") or None


def _estimate_table_dimensions(nougat_result: dict) -> tuple[int, int]:
    """Rough row/column count from nougat_result tables list."""
    tables = nougat_result.get("tables", [])
    if not tables:
        return 0, 0
    t = tables[0]
    rows = t.get("rows", [])
    cols = len(rows[0]) if rows else 0
    return len(rows), cols


# ── Per-region processing ─────────────────────────────────────────────────────

@dataclass
class _LoadResult:
    figure_rows:       list[dict] = field(default_factory=list)
    table_rows:        list[dict] = field(default_factory=list)
    equation_rows:     list[dict] = field(default_factory=list)
    paper_fig_edges:   list[dict] = field(default_factory=list)
    paper_tbl_edges:   list[dict] = field(default_factory=list)
    paper_eq_edges:    list[dict] = field(default_factory=list)
    fig_mention_edges: list[dict] = field(default_factory=list)
    tbl_report_edges:  list[dict] = field(default_factory=list)
    eq_grounds_edges:  list[dict] = field(default_factory=list)
    stats:             dict       = field(default_factory=dict)


def _process_region(region_entry: dict, paper_id: str) -> tuple[str, dict | None]:
    """
    Process one region entry from regions.json.

    Returns (node_type, row_dict) or (node_type, None) if rejected/skipped.
    node_type: "figure" | "table" | "equation" | "skip"
    """
    region       = region_entry.get("region", {})
    nougat_result= region_entry.get("nougat_result") or {}
    error        = region_entry.get("error") or nougat_result.get("error")

    if error:
        return "skip", None

    region_type  = region.get("region_type", "")
    region_id    = region.get("region_id", "")
    page         = region.get("page", 0)
    caption      = region.get("caption_text")
    formula_text = region.get("formula_text")
    crop_strategy= region.get("crop_strategy", "")
    merge_strats = ",".join(region.get("merge_strategy") or [])
    bbox_json    = json.dumps(region.get("bbox", {}))

    markdown     = _extract_markdown(nougat_result)
    quality      = score_nougat_output(markdown, region_type)

    # Nougat acceptance gate (src/document/nougat_gate.py): figure-family regions
    # keep their node but never their Nougat text (it is the caption plus page
    # text, or a hallucinated loop); tables and formulas need status "accepted".
    status = (region_entry.get("gate") or {}).get("nougat_status")
    if region_type in FIGURE_FAMILY:
        markdown = None
    elif status != "accepted":
        log.debug("[skip] %s  gate=%s", region_id, status)
        return "skip", None

    if markdown is not None and quality.rejected:
        log.debug("[skip] %s  reason=%s  score=%.2f",
                  region_id, quality.reason, quality.score)
        return "skip", None

    # ── FORMULA_REGION → Equation node ───────────────────────────────────────
    if region_type == "FORMULA_REGION":
        lhs     = _extract_lhs(formula_text, markdown)
        context = region_entry.get("region", {}).get("caption_text")  # context_text = caption slot
        eq_type = _classify_eq_type(lhs, context or markdown)
        eq_number = None
        if markdown:
            m = re.search(r'\((\d+[a-z]?)\)', markdown)
            if m:
                eq_number = m.group(1)

        row = dict(
            eq_id           = region_id,
            paper_id        = paper_id,
            page            = page,
            region_id       = region_id,
            latex_raw       = formula_text or (markdown[:400] if markdown else None),
            lhs_symbol      = lhs,
            equation_type   = eq_type,
            equation_number = eq_number,
            nougat_quality  = quality.score,
            context_text    = (markdown[:500] if markdown else None),
            canonical_eq_id = None,
        )
        return "equation", row

    # ── TABLE_REGION → ScientificTable node ──────────────────────────────────
    if region_type == "TABLE_REGION":
        tbl_type        = _classify_table_type(caption, markdown)
        n_rows, n_cols  = _estimate_table_dimensions(nougat_result)
        row = dict(
            table_id        = region_id,
            paper_id        = paper_id,
            page            = page,
            region_id       = region_id,
            caption         = caption,
            table_type      = tbl_type,
            nougat_markdown = markdown[:2000] if markdown else None,
            nougat_quality  = quality.score,
            crop_strategy   = crop_strategy,
            bbox_json       = bbox_json,
            row_count       = n_rows,
            column_count    = n_cols,
        )
        return "table", row

    # ── All figure types → ScientificFigure node ─────────────────────────────
    fig_type = _classify_figure_type(region_type, caption)
    row = dict(
        fig_id          = region_id,
        paper_id        = paper_id,
        page            = page,
        region_id       = region_id,
        figure_type     = fig_type,
        caption         = caption,
        nougat_markdown = markdown[:2000] if markdown else None,
        nougat_quality  = quality.score,
        crop_strategy   = crop_strategy,
        bbox_json       = bbox_json,
        merge_strategy  = merge_strats,
        semantic_priority = region.get("semantic_priority", "high"),
    )
    return "figure", row


def _process_file(regions_json: Path) -> _LoadResult:
    """Process one paper's regions.json into KG rows."""
    result = _LoadResult()

    try:
        with open(regions_json) as f:
            data = json.load(f)
    except Exception as exc:
        log.error("Failed to read %s: %s", regions_json, exc)
        return result

    paper_id = data.get("paper_id", "")
    if not paper_id:
        log.warning("No paper_id in %s", regions_json)
        return result

    total = skipped = figures = tables = equations = 0

    # gate verdicts: in regions.json for new runs, in regions.parquet after a rescore
    gates: dict[str, dict] = {}
    pq_path = regions_json.parents[2] / "sodb" / paper_id / "regions.parquet"
    if not pq_path.exists():
        pq_path = Path(os.getenv("SODB_DIR", "data/sodb")) / paper_id / "regions.parquet"
    try:
        import pandas as pd
        rdf = pd.read_parquet(pq_path)
        if "nougat_status" in rdf.columns:
            gates = {r.region_id: {"nougat_status": r.nougat_status} for r in rdf.itertuples()}
    except Exception:
        pass

    for entry in data.get("regions", []):
        total += 1
        rid = (entry.get("region") or {}).get("region_id")
        if "gate" not in entry and rid in gates:
            entry = dict(entry, gate=gates[rid])
        node_type, row = _process_region(entry, paper_id)

        if node_type == "skip" or row is None:
            skipped += 1
            continue

        grounding_text = " ".join(filter(None, [
            row.get("caption"),
            row.get("nougat_markdown") or row.get("context_text") or row.get("latex_raw"),
        ]))

        if node_type == "figure":
            figures += 1
            result.figure_rows.append(row)
            result.paper_fig_edges.append({"paper_id": paper_id, "fig_id": row["fig_id"]})
            # Entity grounding → FIGURE_MENTIONS edges
            for ent in _ground_entities(grounding_text):
                result.fig_mention_edges.append({
                    "fig_id":       row["fig_id"],
                    "canonical_id": ent.canonical_id,
                    "node_label":   ent.node_label,
                    "evidence":     ent.evidence[:200],
                })

        elif node_type == "table":
            tables += 1
            result.table_rows.append(row)
            result.paper_tbl_edges.append({"paper_id": paper_id, "table_id": row["table_id"]})
            # Metric grounding → TABLE_REPORTS edges
            for ent in _ground_entities(grounding_text):
                if ent.node_label == "Metric":
                    result.tbl_report_edges.append({
                        "table_id":     row["table_id"],
                        "canonical_id": ent.canonical_id,
                        "confidence":   0.85,
                    })

        elif node_type == "equation":
            equations += 1
            result.equation_rows.append(row)
            result.paper_eq_edges.append({"paper_id": paper_id, "eq_id": row["eq_id"]})
            # Method grounding → EQUATION_GROUNDS_TO edges
            for ent in _ground_entities(grounding_text):
                if ent.node_label == "Method":
                    result.eq_grounds_edges.append({
                        "eq_id":        row["eq_id"],
                        "canonical_id": ent.canonical_id,
                        "confidence":   0.80,
                    })

    result.stats = {
        "paper_id":  paper_id,
        "total":     total,
        "skipped":   skipped,
        "figures":   figures,
        "tables":    tables,
        "equations": equations,
    }
    return result


# ── Main loader ───────────────────────────────────────────────────────────────

def iter_region_files(
    nougat_dir: Path,
    paper_filter: str | None = None,
) -> Iterator[Path]:
    """Yield all regions.json files under nougat_dir."""
    for path in sorted(nougat_dir.glob("*/regions.json")):
        if paper_filter and paper_filter not in path.parent.name:
            continue
        yield path


def load_all_regions(
    nougat_dir:   Path        = _NOUGAT_DIR,
    paper_filter: str | None  = None,
    dry_run:      bool        = False,
    writer:       GraphWriter | None = None,
) -> dict:
    """
    Main entry point.  Processes all regions.json files and writes to Neo4j.

    Parameters
    ----------
    nougat_dir   : root of nougat output (default: data/nougat_regions/)
    paper_filter : optional paper_id substring filter
    dry_run      : if True, parse only — no Neo4j writes
    writer       : existing GraphWriter (created internally if None)

    Returns
    -------
    Summary statistics dict.
    """
    files = list(iter_region_files(nougat_dir, paper_filter))
    if not files:
        log.warning("No regions.json files found in %s", nougat_dir)
        return {}

    log.info("[RegionToKG] processing %d papers", len(files))

    # Accumulate all rows before writing (single batch write per label)
    all_figure_rows:       list[dict] = []
    all_table_rows:        list[dict] = []
    all_equation_rows:     list[dict] = []
    all_paper_fig_edges:   list[dict] = []
    all_paper_tbl_edges:   list[dict] = []
    all_paper_eq_edges:    list[dict] = []
    all_fig_mention_edges: list[dict] = []
    all_tbl_report_edges:  list[dict] = []
    all_eq_grounds_edges:  list[dict] = []

    total_stats: dict = {"papers": 0, "total": 0, "skipped": 0,
                         "figures": 0, "tables": 0, "equations": 0}

    for path in files:
        log.info("[file] %s", path)
        r = _process_file(path)
        all_figure_rows.extend(r.figure_rows)
        all_table_rows.extend(r.table_rows)
        all_equation_rows.extend(r.equation_rows)
        all_paper_fig_edges.extend(r.paper_fig_edges)
        all_paper_tbl_edges.extend(r.paper_tbl_edges)
        all_paper_eq_edges.extend(r.paper_eq_edges)
        all_fig_mention_edges.extend(r.fig_mention_edges)
        all_tbl_report_edges.extend(r.tbl_report_edges)
        all_eq_grounds_edges.extend(r.eq_grounds_edges)
        for k, v in r.stats.items():
            if k != "paper_id":
                total_stats[k] = total_stats.get(k, 0) + v
        total_stats["papers"] += 1
        log.info("[stats] %s", r.stats)

    log.info(
        "[RegionToKG] accumulated: figures=%d  tables=%d  equations=%d  "
        "fig_mentions=%d  tbl_reports=%d  eq_grounds=%d",
        len(all_figure_rows), len(all_table_rows), len(all_equation_rows),
        len(all_fig_mention_edges), len(all_tbl_report_edges), len(all_eq_grounds_edges),
    )

    if dry_run:
        log.info("[dry-run] no Neo4j writes.")
        return total_stats

    # ── Write to Neo4j ────────────────────────────────────────────────────────
    _own_writer = writer is None
    if _own_writer:
        writer = GraphWriter()

    try:
        # Ensure visual-layer constraints exist before first write
        writer.create_constraints()

        log.info("[write] nodes …")
        writer.write_figures(all_figure_rows)
        writer.write_tables(all_table_rows)
        writer.write_equations(all_equation_rows)

        log.info("[write] Paper edges …")
        writer.write_paper_figure_edges(all_paper_fig_edges)
        writer.write_paper_table_edges(all_paper_tbl_edges)
        writer.write_paper_equation_edges(all_paper_eq_edges)

        log.info("[write] grounding edges …")
        writer.write_figure_mentions_edges(all_fig_mention_edges)
        writer.write_table_reports_edges(all_tbl_report_edges)
        writer.write_equation_grounds_to_edges(all_eq_grounds_edges)

        counts = writer.node_counts()
        log.info("[RegionToKG] Neo4j node counts: %s", counts)

    finally:
        if _own_writer:
            writer.close()

    return total_stats


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    paper_filter = None
    dry_run      = False

    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        elif not arg.startswith("--"):
            paper_filter = arg

    log.info("[boot] RegionToKG loader")
    log.info("[nougat_dir] %s", _NOUGAT_DIR)
    log.info("[filter] %s", paper_filter or "all")
    log.info("[dry_run] %s", dry_run)

    stats = load_all_regions(
        paper_filter=paper_filter,
        dry_run=dry_run,
    )
    log.info("[done] %s", stats)


if __name__ == "__main__":
    main()
