"""
table_extractor.py — NumericFact extraction from GROBID TEI table XML.

Reads <figure type="table"> elements from GROBID TEI XML and extracts
structured numeric facts with full scientific provenance.

Every NumericFact carries:
  table_id        — stable identifier  "{paper_id}_tbl_{n:03d}"
  table_label     — human label as printed in the paper ("Table 3 .")
  page            — PDF page number extracted from GROBID coords attribute
  column_context  — full multi-row column header chain  ["Calibration", "NSE"]
  row_context     — non-numeric row label cells to the left of the value  ["W280"]
  raw_cell        — original cell text before float conversion  "0.78"
  metric          — normalised display name  "NSE", "KGE", "RMSE"
  value           — parsed float  0.78
  unit            — unit string if present in header  "%", "mm", None
  confidence      — extraction confidence  0.80 | 0.85

Two table layouts are handled transparently:

  column-labeled  — metric names appear as column headers; values run down rows
                    Example:  | NSE  | PBIAS | R²  |
                              | 0.78 | -4.2  | 0.91|

  row-labeled     — metric label is a cell in the row; values are to its right
                    Example:  [None, None, 'Nash', '0.466', '0.559', ...]
                              [None, 'SCS', 'CN',  '76.41', '79.21', ...]

Usage::

    from src.extraction.table_extractor import extract_numeric_facts

    facts = extract_numeric_facts("data/literature/grobid_xml/0030.tei.xml", "0030")
    for f in facts:
        print(f.metric, f.value, f.column_context, f.row_context, f.raw_cell)
"""
from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from src.extraction.numbers import parse_number

log = logging.getLogger("geohydro.extraction.table_extractor")

_TEI_NS = "http://www.tei-c.org/ns/1.0"


# ── NumericFact dataclass ─────────────────────────────────────────────────────

@dataclass
class NumericFact:
    """
    One numeric measurement extracted from a GROBID-parsed table.

    Designed as a scientific evidentiary object: every field is required for
    downstream reasoning, audit, or display.  Consumers must be able to
    reconstruct exactly WHICH cell was read, in WHICH column hierarchy, from
    WHICH row, on WHICH page, in WHICH table.
    """
    # ── Identity ──────────────────────────────────────────────────────────────
    fact_id:         str       # sha1({paper_id}|{table_id}|{cid}|{value}|{seq})
    paper_id:        str
    table_id:        str       # stable "{paper_id}_tbl_{n:03d}"  (1-based)
    table_label:     str       # "Table 3 ." as printed in the paper

    # ── Source location ───────────────────────────────────────────────────────
    page:            int | None          # PDF page number (from GROBID coords)

    # ── Column provenance ─────────────────────────────────────────────────────
    column_context:  list[str]           # multi-row column header chain
                                         #   ["Calibration", "NSE"] or ["NSE"]
    col_header:      str                 # merged column header text (raw)

    # ── Row provenance ────────────────────────────────────────────────────────
    row_context:     list[str]           # non-numeric row label cells to the left
                                         #   ["W280"] or ["Event_01", "2020-05"]

    # ── Cell ─────────────────────────────────────────────────────────────────
    raw_cell:        str                 # original cell text  "0.78" | "78%"

    # ── Semantic ─────────────────────────────────────────────────────────────
    metric:          str                 # normalised display name  "NSE"
    canonical_id:    str                 # ontology ID  "metric.nse"
    node_label:      str                 # target KG label  "Metric" | "Method"
    value:           float
    unit:            str | None          # "m³/s" | "%" | None
    confidence:      float               # 0.80 (row-labeled) | 0.85 (col-labeled)
    source:          str = "grobid_tei"

    def to_dict(self) -> dict:
        return {
            "fact_id":        self.fact_id,
            "paper_id":       self.paper_id,
            "table_id":       self.table_id,
            "table_label":    self.table_label,
            "page":           self.page,
            "column_context": self.column_context,   # list[str] — Neo4j stores as array
            "col_header":     self.col_header,
            "row_context":    self.row_context,       # list[str]
            "raw_cell":       self.raw_cell,
            "metric":         self.metric,
            "canonical_id":   self.canonical_id,
            "node_label":     self.node_label,
            "value":          self.value,
            "unit":           self.unit,
            "confidence":     self.confidence,
            "source":         self.source,
        }


# ── Canonical display names ───────────────────────────────────────────────────

_METRIC_DISPLAY: dict[str, str] = {
    # Hydrological efficiency
    "metric.nse":              "NSE",
    "metric.kge":              "KGE",
    "metric.pbias":            "PBIAS",
    "metric.rsr":              "RSR",
    "metric.ioa":              "IOA",
    # Error metrics
    "metric.rmse":             "RMSE",
    "metric.mae":              "MAE",
    "metric.mse":              "MSE",
    "metric.mape":             "MAPE",
    "metric.mbe":              "MBE",
    "metric.nmad":             "NMAD",
    # Regression
    "metric.r2":               "R²",
    "metric.r":                "r",
    # Classification: flood mapping & remote sensing
    "metric.overall_accuracy": "OA",
    "metric.balanced_acc":     "BA",
    "metric.auc":              "AUC",
    "metric.f1_score":         "F1",
    "metric.iou":              "IoU",
    "metric.kappa":            "Kappa",
    "metric.mcc":              "MCC",
    "metric.precision":        "Precision",
    "metric.pod":              "POD",
    "metric.csi":              "CSI",
    "metric.far":              "FAR",
    "metric.specificity":      "Specificity",
    # Model parameters
    "method.scs_cn":           "CN",
}


def _canonical_metric(canonical_id: str) -> str:
    return _METRIC_DISPLAY.get(canonical_id, canonical_id.rsplit(".", 1)[-1].upper())


# ── Column → ontology ID mapping ──────────────────────────────────────────────
# Each entry: (pattern, canonical_id, node_label, unit_hint)
# unit_hint is used when no unit is readable from the header.
# Ordered most-specific → most-general to prevent short-token false matches.

_COLUMN_PATTERNS: list[tuple[re.Pattern, str, str, str | None]] = [
    # ── Hydrological efficiency (most specific patterns first) ────────────────
    (re.compile(r"\bnse\b|\bnash\b|nash[\s\-]?sutcliffe|nash[\s\-]?eff(?:iciency)?", re.I),
        "metric.nse",    "Metric", None),
    (re.compile(r"\bkge\b|kling[\s\-]?gupta", re.I),
        "metric.kge",    "Metric", None),
    (re.compile(r"\bpbias\b|percent[\s\-]?bias|p[\s\.]?bias\b", re.I),
        "metric.pbias",  "Metric", "%"),
    (re.compile(r"\brsr\b", re.I),
        "metric.rsr",    "Metric", None),
    (re.compile(r"\bioa\b|index[\s\-]?of[\s\-]?agreement", re.I),
        "metric.ioa",    "Metric", None),
    # ── Error metrics ─────────────────────────────────────────────────────────
    (re.compile(r"\brmse\b|root[\s\-]?mean[\s\-]?sq(?:uare[d]?)?[\s\-]?err", re.I),
        "metric.rmse",   "Metric", None),
    # MBE before MAE to prevent 'mean bias error' matching MAE pattern
    (re.compile(r"\bmbe\b|mean[\s\-]?bias[\s\-]?err(?:or)?", re.I),
        "metric.mbe",    "Metric", None),
    (re.compile(r"\bmae\b|mean[\s\-]?abs(?:olute)?[\s\-]?err", re.I),
        "metric.mae",    "Metric", None),
    (re.compile(r"\bmse\b", re.I),
        "metric.mse",    "Metric", None),
    (re.compile(r"\bmape\b|mean[\s\-]?abs(?:olute)?[\s\-]?perc", re.I),
        "metric.mape",   "Metric", "%"),
    (re.compile(r"\bnmad\b|norm(?:alized)?[\s\-]?median[\s\-]?abs(?:olute)?[\s\-]?dev", re.I),
        "metric.nmad",   "Metric", None),
    # ── Regression — R² before bare r to prevent substring collision ──────────
    (re.compile(r"\br[\^²2]\b|\br\s*squared\b|\bcoeff(?:icient)?[\s\-]?(?:of[\s\-]?)?determ", re.I),
        "metric.r2",     "Metric", None),
    (re.compile(r"\bpearson[\s\-]?r\b|correlation[\s\-]?coeff(?:icient)?\b|\bcc\b", re.I),
        "metric.r",      "Metric", None),
    # ── Classification: flood mapping, ML, remote sensing ─────────────────────
    # Long-form before short abbreviations
    (re.compile(r"balanced[\s\-]?acc(?:uracy)?", re.I),
        "metric.balanced_acc",     "Metric", None),
    (re.compile(r"overall[\s\-]?acc(?:uracy)?|\baccuracy\b", re.I),
        "metric.overall_accuracy", "Metric", None),
    (re.compile(r"\boa\b", re.I),
        "metric.overall_accuracy", "Metric", None),
    (re.compile(r"\bauc[\s\-]?roc\b|\broc[\s\-]?auc\b|\bauc\b"
                r"|area[\s\-]?under[\s\-]?(?:the[\s\-]?)?(?:roc[\s\-]?)?curve", re.I),
        "metric.auc",    "Metric", None),
    (re.compile(r"\bf1[\s\-]?(?:score)?\b|\bf[\s\-]?measure\b"
                r"|dice[\s\-]?(?:score|coeff(?:icient)?)", re.I),
        "metric.f1_score", "Metric", None),
    (re.compile(r"\biou\b|intersection[\s\-]?over[\s\-]?union|jaccard", re.I),
        "metric.iou",    "Metric", None),
    (re.compile(r"\bkappa\b|cohen[\s\-]?kappa", re.I),
        "metric.kappa",  "Metric", None),
    (re.compile(r"\bmcc\b|matthews[\s\-]?corr(?:elation)?", re.I),
        "metric.mcc",    "Metric", None),
    (re.compile(r"\bprecision\b|\bppv\b|positive[\s\-]?predictive[\s\-]?value", re.I),
        "metric.precision", "Metric", None),
    # POD / recall / sensitivity / hit rate — all map to same canonical ID
    (re.compile(r"\bpod\b|prob(?:ability)?[\s\-]?of[\s\-]?det(?:ection)?"
                r"|hit[\s\-]?rate\b|\brecall\b|\bsensitivity\b|\btpr\b", re.I),
        "metric.pod",    "Metric", None),
    (re.compile(r"\bcsi\b|critical[\s\-]?success[\s\-]?index|threat[\s\-]?score", re.I),
        "metric.csi",    "Metric", None),
    (re.compile(r"\bfar\b|false[\s\-]?alarm[\s\-]?rat(?:e|io)?", re.I),
        "metric.far",    "Metric", None),
    (re.compile(r"\bspecificity\b|\btnr\b|true[\s\-]?neg(?:ative)?[\s\-]?rat(?:e|io)?", re.I),
        "metric.specificity", "Metric", None),
    # ── Model parameters ──────────────────────────────────────────────────────
    (re.compile(r"(?<![a-z])\bcn\b(?![a-z])|curve[\s\-]?num(?:ber)?", re.I),
        "method.scs_cn", "Method", None),
]


def _map_column(header: str) -> tuple[str, str, str | None] | None:
    """
    Match header text against _COLUMN_PATTERNS.
    Returns (canonical_id, node_label, unit_hint) or None.
    """
    h = header.strip()
    for pat, cid, label, unit_hint in _COLUMN_PATTERNS:
        if pat.search(h):
            return cid, label, unit_hint
    return None


# ── Cell / header utilities ───────────────────────────────────────────────────

_UNIT_RE = re.compile(r"\(([^)]{1,25})\)\s*$")


def _split_unit(text: str) -> tuple[str, str | None]:
    """'RMSE (m³/s)' → ('RMSE', 'm³/s');  'NSE' → ('NSE', None)."""
    m = _UNIT_RE.search(text)
    if m:
        return text[: m.start()].strip(), m.group(1).strip()
    return text, None


def _parse_float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        # Unicode minus, thin spaces, decimal comma and a trailing % are accepted.
        return parse_number(text)
    except ValueError:
        return None


def _is_numeric(text: str | None) -> bool:
    return _parse_float(text) is not None


def _cells_of(row_elem) -> list[str | None]:
    return [c.text for c in row_elem.findall(f"{{{_TEI_NS}}}cell")]


# ── Value plausibility ────────────────────────────────────────────────────────
# Generous hard bounds to reject parser artifacts (CN optimizer step 0.01,
# misaligned multi-header columns giving NSE > 1).  Not a scientific gate.

_VALUE_BOUNDS: dict[str, tuple[float, float]] = {
    # Hydrological efficiency
    "metric.nse":              (-10.0, 1.0),
    "metric.kge":              (-10.0, 1.0),
    "metric.pbias":            (-200.0, 200.0),
    "metric.rsr":              (0.0, 1e4),
    "metric.ioa":              (0.0, 1.0),
    # Error metrics (unbounded positive)
    "metric.rmse":             (0.0, 1e9),
    "metric.mae":              (0.0, 1e9),
    "metric.mse":              (0.0, 1e9),
    "metric.mape":             (0.0, 1e4),
    "metric.mbe":              (-1e9, 1e9),
    "metric.nmad":             (0.0, 1e9),
    # Regression
    "metric.r2":               (-10.0, 1.0),
    "metric.r":                (-1.0, 1.0),
    # Classification [0, 1]
    "metric.overall_accuracy": (0.0, 1.0),
    "metric.balanced_acc":     (0.0, 1.0),
    "metric.auc":              (0.0, 1.0),
    "metric.f1_score":         (0.0, 1.0),
    "metric.iou":              (0.0, 1.0),
    "metric.kappa":            (-1.0, 1.0),
    "metric.mcc":              (-1.0, 1.0),
    "metric.precision":        (0.0, 1.0),
    "metric.pod":              (0.0, 1.0),
    "metric.csi":              (0.0, 1.0),
    "metric.far":              (0.0, 1.0),
    "metric.specificity":      (0.0, 1.0),
    # Model parameters
    "method.scs_cn":           (1.0, 100.0),
}


def _value_plausible(canonical_id: str, value: float) -> bool:
    bounds = _VALUE_BOUNDS.get(canonical_id)
    if bounds is None:
        return True
    lo, hi = bounds
    return lo <= value <= hi


# ── Provenance helpers ────────────────────────────────────────────────────────

def _make_table_id(paper_id: str, table_idx: int) -> str:
    """Stable, human-readable table identifier (1-based index)."""
    return f"{paper_id}_tbl_{table_idx:03d}"


def _make_fact_id(table_id: str, canonical_id: str, value: float, seq: int) -> str:
    raw = f"{table_id}|{canonical_id}|{value:.8f}|{seq}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _page_from_coords(fig_elem) -> int | None:
    """Extract PDF page number from GROBID coords attribute 'page,x,y,w,h'."""
    coords = fig_elem.get("coords", "")
    if not coords:
        return None
    try:
        return int(coords.split(",")[0])
    except (ValueError, IndexError):
        return None


def _build_column_contexts(
    header_rows: list[list[str | None]],
    n_cols:      int,
) -> list[list[str]]:
    """
    For each column index, collect non-empty cells across all header rows.

    Input:   header_rows = [["Calibration", None], ["NSE", "PBIAS"]]
    Returns: [["Calibration", "NSE"], ["PBIAS"]]
    """
    contexts: list[list[str]] = []
    for ci in range(n_cols):
        ctx = [
            hr[ci].strip()
            for hr in header_rows
            if ci < len(hr) and hr[ci] is not None and hr[ci].strip()
        ]
        contexts.append(ctx)
    return contexts


def _row_labels_left_of(row: list[str | None], ci: int) -> list[str]:
    """
    All non-numeric, non-None cells to the LEFT of column ci.
    These are the row label / group context for the value at ci.
    """
    return [
        row[j].strip()
        for j in range(min(ci, len(row)))
        if row[j] is not None and row[j].strip() and not _is_numeric(row[j])
    ]


# ── Column-labeled extraction ─────────────────────────────────────────────────

def _extract_column_labeled(
    paper_id:    str,
    table_id:    str,
    table_label: str,
    page:        int | None,
    all_rows:    list[list[str | None]],
) -> list[NumericFact]:
    """
    Handle tables where metric names appear as column headers.

    Header detection: rows where fewer than half of non-None cells are numeric
    are treated as header rows.  Up to 4 consecutive header rows are merged
    vertically per column to build the full column_context chain.
    """
    if not all_rows:
        return []

    # Detect header depth (max 4 rows)
    header_depth = 0
    for i, row in enumerate(all_rows[:4]):
        non_none = [c for c in row if c is not None]
        if not non_none:
            header_depth = i + 1
            continue
        if sum(1 for c in non_none if _is_numeric(c)) / len(non_none) > 0.5:
            break
        header_depth = i + 1

    if header_depth == 0 or header_depth >= len(all_rows):
        return []

    header_rows = all_rows[:header_depth]
    n_cols      = max((len(r) for r in header_rows), default=0)

    # Build per-column context chains and merged header strings
    col_contexts = _build_column_contexts(header_rows, n_cols)
    merged_headers: list[str] = [" ".join(ctx) for ctx in col_contexts]

    # Map each column to an ontology entry
    col_mappings: list[tuple[str, str, str | None] | None] = []
    for h in merged_headers:
        clean_h, unit_from_header = _split_unit(h)
        m = _map_column(clean_h)
        if m:
            cid, label, unit_hint = m
            col_mappings.append((cid, label, unit_from_header or unit_hint))
        else:
            col_mappings.append(None)

    if not any(col_mappings):
        return []

    # Extract data rows
    facts: list[NumericFact] = []
    seq = 0
    for row in all_rows[header_depth:]:
        for ci, mapping in enumerate(col_mappings):
            if mapping is None or ci >= len(row):
                continue
            cid, label, unit = mapping
            raw_cell = (row[ci] or "").strip()
            v = _parse_float(raw_cell)
            if v is None or not _value_plausible(cid, v):
                continue
            facts.append(NumericFact(
                fact_id        = _make_fact_id(table_id, cid, v, seq),
                paper_id       = paper_id,
                table_id       = table_id,
                table_label    = table_label,
                page           = page,
                column_context = col_contexts[ci],
                col_header     = merged_headers[ci],
                row_context    = _row_labels_left_of(row, ci),
                raw_cell       = raw_cell,
                metric         = _canonical_metric(cid),
                canonical_id   = cid,
                node_label     = label,
                value          = v,
                unit           = unit,
                confidence     = 0.85,
            ))
            seq += 1
    return facts


# ── Row-labeled extraction ─────────────────────────────────────────────────────

def _extract_row_labeled(
    paper_id:    str,
    table_id:    str,
    table_label: str,
    page:        int | None,
    all_rows:    list[list[str | None]],
) -> list[NumericFact]:
    """
    Handle tables where a metric label appears in any cell of a row and the
    cells to its right are the values.

    GROBID often places the metric label at column 2 when the table has a
    multi-level row-group structure:

        [None,  None,  'Nash', '0.466', '0.559', ...]   ← label at col 2
        [None,  'SCS', 'CN',   '76.41', '79.21', ...]   ← label at col 2

    column_context: cells BEFORE the label (the hierarchical group headers).
    row_context:    empty — the entire row IS the labelled metric row.
    """
    facts: list[NumericFact] = []
    seq = 0
    for row in all_rows:
        if not row:
            continue

        # Scan left-to-right for the first cell that matches a metric keyword
        match_idx: int | None = None
        mapping: tuple[str, str, str | None] | None = None
        for idx, cell in enumerate(row):
            m = _map_column(cell or "")
            if m is not None:
                match_idx = idx
                mapping = m
                break
        if mapping is None or match_idx is None:
            continue

        cid, label, unit_hint = mapping
        label_text = (row[match_idx] or "").strip()
        _, unit_from_cell = _split_unit(label_text)
        unit = unit_from_cell or unit_hint

        # Cells BEFORE the label are hierarchical group headers (column context)
        pre_label_ctx = [
            row[j].strip()
            for j in range(match_idx)
            if row[j] is not None and row[j].strip()
        ]

        for cell in row[match_idx + 1 :]:
            raw_cell = (cell or "").strip()
            v = _parse_float(raw_cell)
            if v is None or not _value_plausible(cid, v):
                continue
            facts.append(NumericFact(
                fact_id        = _make_fact_id(table_id, cid, v, seq),
                paper_id       = paper_id,
                table_id       = table_id,
                table_label    = table_label,
                page           = page,
                column_context = pre_label_ctx + [label_text],
                col_header     = label_text,
                row_context    = [],
                raw_cell       = raw_cell,
                metric         = _canonical_metric(cid),
                canonical_id   = cid,
                node_label     = label,
                value          = v,
                unit           = unit,
                confidence     = 0.80,
            ))
            seq += 1
    return facts


# ── Per-table dispatcher ──────────────────────────────────────────────────────

def _extract_from_table_elem(
    fig_elem,
    paper_id:    str,
    table_id:    str,
    table_label: str,
    page:        int | None,
) -> list[NumericFact]:
    tbl = fig_elem.find(f"{{{_TEI_NS}}}table")
    if tbl is None:
        return []

    all_rows = [_cells_of(r) for r in tbl.findall(f"{{{_TEI_NS}}}row")]
    if not all_rows:
        return []

    # Column-labeled wins if it produces any results; fall back to row-labeled.
    facts = _extract_column_labeled(paper_id, table_id, table_label, page, all_rows)
    if not facts:
        facts = _extract_row_labeled(paper_id, table_id, table_label, page, all_rows)
    return facts


# ── Public API ────────────────────────────────────────────────────────────────

def extract_numeric_facts(tei_path: str | Path, paper_id: str) -> list[NumericFact]:
    """
    Parse all tables in a GROBID TEI XML file and return NumericFact records.

    Every returned fact carries full scientific provenance: table, page,
    column context chain, row label chain, and raw cell text.

    Parameters
    ----------
    tei_path  : path to .tei.xml produced by GROBID
    paper_id  : corpus paper identifier propagated into every fact

    Returns
    -------
    List of NumericFact dataclass instances.
    """
    tei_path = Path(tei_path)
    if not tei_path.exists():
        log.warning("TEI XML not found: %s", tei_path)
        return []

    try:
        tree = ET.parse(tei_path)
    except ET.ParseError as exc:
        log.error("TEI XML parse error %s: %s", tei_path.name, exc)
        return []

    root = tree.getroot()
    facts: list[NumericFact] = []
    table_idx = 0   # 1-based, incremented per table element

    for fig_elem in root.iter(f"{{{_TEI_NS}}}figure"):
        if fig_elem.get("type") != "table":
            continue
        table_idx += 1

        label_elem = fig_elem.find(f"{{{_TEI_NS}}}label")
        head_elem  = fig_elem.find(f"{{{_TEI_NS}}}head")
        label_text = (label_elem.text or "").strip() if label_elem is not None else ""
        head_text  = (head_elem.text  or "").strip() if head_elem  is not None else ""
        table_label = head_text or (f"Table {label_text}" if label_text else "Table ?")

        table_id = _make_table_id(paper_id, table_idx)
        page     = _page_from_coords(fig_elem)

        tbl_facts = _extract_from_table_elem(
            fig_elem, paper_id, table_id, table_label, page,
        )
        if tbl_facts:
            log.debug(
                "  %s  %s  page=%s  facts=%d",
                table_id, table_label, page, len(tbl_facts),
            )
        facts.extend(tbl_facts)

    log.info(
        "extract_numeric_facts: paper=%-20s  tables=%d  facts=%d",
        paper_id, table_idx, len(facts),
    )
    return facts


# ── Period-type detection (shared with process_paper and table_kg_loader) ────

_CALIBRATION_TERMS = frozenset({"calibration", "calib", "cal", "training", "fitting"})
_VALIDATION_TERMS  = frozenset({"validation", "valid", "val", "test", "verification"})


def detect_period_type(row_context: list[str], col_header: str | None) -> str | None:
    """Return CALIBRATION, VALIDATION, or None based on context tokens."""
    combined = (" ".join(row_context) + " " + (col_header or "")).lower()
    tokens   = set(combined.split())
    if tokens & _CALIBRATION_TERMS:
        return "CALIBRATION"
    if tokens & _VALIDATION_TERMS:
        return "VALIDATION"
    return None
