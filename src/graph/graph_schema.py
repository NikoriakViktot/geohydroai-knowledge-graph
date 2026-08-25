"""
graph_schema.py  —  Dataclass definitions for every node and relationship type
                    in the GeoHydroAI Stage 5 knowledge graph.

Data flows IN from:
  - data/enriched/*.json          (normalized_entities: methods, satellites, metrics)
  - data/analytics/*.parquet      (papers, authors, institutions, topics, …)
  - data/nougat_regions/*/        (visual layer: ScientificFigure, ScientificTable,
                                   Equation — written by region_kg_loader.py)

Neo4j node labels and relationship types are declared here as constants so
that the writer, loader, and query modules all reference the same strings.

Visual layer (Stage 5b):
  ScientificFigure  — figure regions from nougat_region_pipeline, classified by type
  ScientificTable   — table regions with column/row schema metadata
  Equation          — formula regions with LHS symbol and equation type
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ── Node label constants ───────────────────────────────────────────────────────
L_PAPER            = "Paper"
L_AUTHOR           = "Author"
L_INSTITUTION      = "Institution"
L_TOPIC            = "Topic"
L_METHOD           = "Method"
L_SENSOR           = "Sensor"
L_METRIC           = "Metric"
L_COUNTRY          = "Country"
L_FLOOD_EVENT      = "FloodEvent"
# Visual layer
L_SCI_FIGURE       = "ScientificFigure"
L_SCI_TABLE        = "ScientificTable"
L_EQUATION         = "Equation"
# Numeric facts (GROBID table extraction)
L_NUMERIC_FACT     = "NumericFact"

# ── Relationship type constants ────────────────────────────────────────────────
R_USES_METHOD         = "USES_METHOD"
R_USES_SENSOR         = "USES_SENSOR"
R_REPORTS_METRIC      = "REPORTS_METRIC"
R_HAS_TOPIC           = "HAS_TOPIC"
R_FROM_COUNTRY        = "FROM_COUNTRY"
R_REFERENCES          = "REFERENCES"
R_AUTHORED            = "AUTHORED"
R_AFFILIATED_WITH     = "AFFILIATED_WITH"
R_LOCATED_IN          = "LOCATED_IN"
R_CO_OCCURS_WITH      = "CO_OCCURS_WITH"
R_COMMONLY_USED_WITH  = "COMMONLY_USED_WITH"
R_INVESTIGATES        = "INVESTIGATES"
# Visual layer relationships
R_HAS_FIGURE          = "HAS_FIGURE"          # Paper → ScientificFigure
R_HAS_TABLE           = "HAS_TABLE"           # Paper → ScientificTable
R_HAS_EQUATION        = "HAS_EQUATION"        # Paper → Equation
R_FIGURE_MENTIONS     = "FIGURE_MENTIONS"     # ScientificFigure → Method/Metric/Sensor
R_TABLE_REPORTS       = "TABLE_REPORTS"       # ScientificTable  → Metric (with value)
R_EQUATION_GROUNDS_TO = "EQUATION_GROUNDS_TO" # Equation → Method
# NumericFact relationships
R_HAS_NUMERIC_FACT = "HAS_NUMERIC_FACT"    # Paper → NumericFact
R_MEASURES         = "MEASURES"             # NumericFact → Metric | Method


# ── Node dataclasses ───────────────────────────────────────────────────────────

@dataclass
class PaperNode:
    paper_id:       str
    title:          Optional[str]  = None
    doi:            Optional[str]  = None
    year:           Optional[int]  = None
    cited_by_count: Optional[int]  = None
    openalex_id:    Optional[str]  = None
    journal:        Optional[str]  = None
    study_type:     Optional[str]  = None
    primary_country:Optional[str]  = None


@dataclass
class AuthorNode:
    author_id:   str
    display_name:str
    orcid:       Optional[str] = None
    paper_count: Optional[int] = None


@dataclass
class InstitutionNode:
    institution_id:   str
    display_name:     str
    country_code:     Optional[str] = None
    institution_type: Optional[str] = None
    paper_count:      Optional[int] = None


@dataclass
class TopicNode:
    topic_id:    str
    topic_name:  str
    paper_count: Optional[int]   = None
    avg_score:   Optional[float] = None


@dataclass
class MethodNode:
    canonical_id: str
    display_name: str
    family:       Optional[str] = None   # derived from ontology domain
    type_group:   str           = "method"
    paper_count:  Optional[int] = None


@dataclass
class SensorNode:
    canonical_id: str
    display_name: str
    family:       Optional[str] = None   # derived from ontology domain
    type_group:   str           = "sensor"
    paper_count:  Optional[int] = None


@dataclass
class MetricNode:
    canonical_id: str
    display_name: str
    metric_type:  Optional[str] = None
    paper_count:  Optional[int] = None


@dataclass
class CountryNode:
    name:     str
    iso_code: Optional[str]   = None
    lat:      Optional[float] = None
    lon:      Optional[float] = None


@dataclass
class FloodEventNode:
    name:    str
    country: str
    year:    Optional[int]   = None
    lat:     Optional[float] = None
    lon:     Optional[float] = None


# ── Relationship dataclasses ───────────────────────────────────────────────────

@dataclass
class PaperMethodEdge:
    paper_id:     str
    canonical_id: str
    confidence:   float = 1.0


@dataclass
class PaperSensorEdge:
    paper_id:     str
    canonical_id: str
    confidence:   float = 1.0


@dataclass
class PaperMetricEdge:
    paper_id:     str
    canonical_id: str
    confidence:   float = 1.0


@dataclass
class PaperTopicEdge:
    paper_id: str
    topic_id: str
    score:    float = 0.0


@dataclass
class AuthorPaperEdge:
    author_id:       str
    paper_id:        str
    position:        Optional[str] = None
    is_corresponding:bool          = False


@dataclass
class AuthorInstitutionEdge:
    author_id:      str
    institution_id: str


@dataclass
class InstitutionCountryEdge:
    institution_id: str
    country_name:   str


@dataclass
class PaperCountryEdge:
    paper_id:     str
    country_name: str


@dataclass
class PaperReferenceEdge:
    source_paper_id: str
    target_paper_id: str


@dataclass
class CoOccurrenceEdge:
    id_a:  str
    id_b:  str
    count: int
    kind:  str   # "method", "sensor", "method_sensor"


@dataclass
class PaperFloodEventEdge:
    paper_id:   str
    event_name: str


# ── Known flood events (curated; linked to papers by country + year window) ───

FLOOD_EVENTS: list[FloodEventNode] = [
    FloodEventNode("Kakhovka Dam Breach", "Ukraine",    2023, 46.75, 33.37),
    FloodEventNode("Tisza Valley Floods", "Ukraine",    None, 48.10, 23.50),
    FloodEventNode("Prut Basin Floods",   "Romania",    2008, 47.50, 27.90),
    FloodEventNode("Danube Delta Floods", "Romania",    None, 45.10, 29.50),
    FloodEventNode("Jakarta Monsoon Floods","Indonesia",None,-6.19,106.83),
    FloodEventNode("Cyclone Idai Floods", "Mozambique", 2019,-19.80, 34.85),
    FloodEventNode("Bangladesh Monsoon",  "Bangladesh", None, 23.70, 90.40),
    FloodEventNode("Po Valley Floods",    "Italy",      2023, 44.80, 11.30),
    FloodEventNode("Ahr River Flash Flood","Germany",   2021, 50.90,  7.50),
    FloodEventNode("Mumbai Urban Floods", "India",      None, 19.07, 72.88),
    FloodEventNode("Mekong Delta Floods", "Vietnam",    None, 11.50,105.80),
    FloodEventNode("Rhine Flash Floods",  "Germany",    2021, 51.50,  8.20),
]


# ── Visual layer node dataclasses (Stage 5b) ───────────────────────────────────

@dataclass
class ScientificFigureNode:
    """
    A figure extracted from a paper via the Nougat region pipeline.

    figure_type:
        "hydrograph"          — discharge vs time chart
        "dem_map"             — digital elevation model visualization
        "lulc_map"            — land use / land cover classified map
        "flood_map"           — inundation extent map
        "methodology_diagram" — flowchart, framework, system diagram
        "scatter_plot"        — accuracy / correlation scatter
        "bar_chart"           — categorical bar chart
        "time_series"         — general time-series chart
        "multi_panel"         — figure with ≥ 2 sub-panels
        "other"               — unclassified
    """
    fig_id:           str
    paper_id:         str
    page:             int
    region_id:        str              # links to nougat_region_pipeline output
    figure_type:      str              # see above
    caption:          str | None
    nougat_markdown:  str | None
    nougat_quality:   float            # 0.0–1.0 from NougatQuality.score
    crop_strategy:    str              # "expanded_context" | "full_page"
    bbox_json:        str              # JSON of {x0,y0,x1,y1} in PDF points
    merge_strategy:   str              # comma-joined merge strategies used
    semantic_priority: str = "high"


@dataclass
class ScientificTableNode:
    """
    A table extracted from a paper via the Nougat region pipeline.

    table_type:
        "validation_table"  — NSE / PBIAS / R² calibration/validation results
        "parameter_table"   — model parameters with values / ranges
        "lulc_table"        — LULC class descriptions and CN values
        "event_table"       — rainfall events with dates and depths
        "comparison_table"  — method or model comparison
        "other"             — unclassified
    """
    table_id:        str
    paper_id:        str
    page:            int
    region_id:       str
    caption:         str | None
    table_type:      str               # see above
    nougat_markdown: str | None
    nougat_quality:  float
    crop_strategy:   str
    bbox_json:       str
    row_count:       int = 0
    column_count:    int = 0


@dataclass
class EquationNode:
    """
    A formula / equation extracted from a paper.

    equation_type:
        "loss_function"       — rainfall-runoff loss (SCS-CN, Green-Ampt)
        "objective_function"  — calibration metric (NSE, KGE, RMSE)
        "routing"             — channel routing (Muskingum, Saint-Venant)
        "transform"           — rainfall → runoff transform (unit hydrograph)
        "empirical"           — empirical relationship (rational method, CN)
        "definition"          — symbol definition ("where X is the …")
        "other"               — unclassified
    """
    eq_id:                str
    paper_id:             str
    page:                 int
    region_id:            str
    latex_raw:            str | None    # Nougat or GROBID LaTeX text
    lhs_symbol:           str | None    # e.g. "P_e", "Q", "NSE", "CN"
    equation_type:        str           # see above
    equation_number:      str | None    # "(1)", "(2a)"
    nougat_quality:       float
    context_text:         str | None    # surrounding sentences from CONTEXT blocks
    canonical_eq_id:      str | None    # future: link to canonical equation library


# ── Visual layer edge dataclasses ─────────────────────────────────────────────

@dataclass
class PaperFigureEdge:
    paper_id: str
    fig_id:   str

@dataclass
class PaperTableEdge:
    paper_id: str
    table_id: str

@dataclass
class PaperEquationEdge:
    paper_id: str
    eq_id:    str

@dataclass
class FigureMentionsEdge:
    """Figure caption or Nougat text references a method/sensor/metric."""
    fig_id:       str
    canonical_id: str
    node_label:   str    # "Method" | "Sensor" | "Metric"
    evidence:     str    # text snippet that triggered the match

@dataclass
class TableReportsEdge:
    """Table contains a column reporting a specific metric."""
    table_id:     str
    canonical_id: str    # ontology metric id
    confidence:   float = 1.0

@dataclass
class EquationGroundsToEdge:
    """Equation is grounded to a method ontology entry."""
    eq_id:        str
    canonical_id: str    # ontology method id
    confidence:   float = 1.0


# ── NumericFact node and edge dataclasses (Stage 5c) ─────────────────────────

@dataclass
class NumericFactNode:
    """
    One numeric measurement extracted from a GROBID-parsed table.
    Full scientific provenance: table, page, column chain, row labels, raw cell.

    canonical_id → Metric node (metric.nse, metric.kge, metric.pbias, …)
                or Method node (method.scs_cn)
    node_label   → "Metric" | "Method" — determines MEASURES edge target
    """
    fact_id:         str
    paper_id:        str
    table_id:        str                # "{paper_id}_tbl_{n:03d}"
    table_label:     str                # "Table 3 ."
    page:            Optional[int]      # PDF page from GROBID coords
    column_context:  list               # multi-row column header chain ["Cal.", "NSE"]
    col_header:      str                # merged column header text
    row_context:     list               # non-numeric row label cells ["W280"]
    raw_cell:        str                # original cell text "0.78"
    metric:          str                # display name "NSE"
    canonical_id:    str                # ontology ID "metric.nse"
    node_label:      str                # "Metric" | "Method"
    value:           float
    unit:            Optional[str]  = None
    confidence:      float          = 0.85
    source:          str            = "grobid_tei"


@dataclass
class PaperNumericFactEdge:
    paper_id: str
    fact_id:  str


@dataclass
class NumericFactMeasuresEdge:
    """NumericFact → Metric | Method."""
    fact_id:      str
    canonical_id: str
    node_label:   str    # "Metric" | "Method"
