"""
graph_constraints.py  —  Uniqueness constraints and indexes for the
                          GeoHydroAI Stage 5 knowledge graph.

Run once on a fresh or wiped database.  All statements are idempotent
(IF NOT EXISTS) so safe to re-run on an already-constrained database.
"""
from __future__ import annotations

# ── Uniqueness constraints ─────────────────────────────────────────────────────
# Each property listed here must be unique across all nodes of that label.

CONSTRAINTS: list[str] = [
    # Core corpus nodes
    "CREATE CONSTRAINT paper_id         IF NOT EXISTS FOR (n:Paper)       REQUIRE n.paper_id       IS UNIQUE",
    "CREATE CONSTRAINT author_id        IF NOT EXISTS FOR (n:Author)      REQUIRE n.author_id      IS UNIQUE",
    "CREATE CONSTRAINT institution_id   IF NOT EXISTS FOR (n:Institution) REQUIRE n.institution_id IS UNIQUE",
    "CREATE CONSTRAINT topic_id         IF NOT EXISTS FOR (n:Topic)       REQUIRE n.topic_id       IS UNIQUE",
    # Canonical entity nodes (normalised by Stage 2/3 ontology)
    "CREATE CONSTRAINT method_cid       IF NOT EXISTS FOR (n:Method)      REQUIRE n.canonical_id   IS UNIQUE",
    "CREATE CONSTRAINT sensor_cid       IF NOT EXISTS FOR (n:Sensor)      REQUIRE n.canonical_id   IS UNIQUE",
    "CREATE CONSTRAINT metric_cid       IF NOT EXISTS FOR (n:Metric)      REQUIRE n.canonical_id   IS UNIQUE",
    # Lookup nodes
    "CREATE CONSTRAINT country_name     IF NOT EXISTS FOR (n:Country)     REQUIRE n.name           IS UNIQUE",
    "CREATE CONSTRAINT flood_event_name IF NOT EXISTS FOR (n:FloodEvent)  REQUIRE n.name           IS UNIQUE",
    # Visual layer nodes (Stage 5b)
    "CREATE CONSTRAINT sci_figure_id    IF NOT EXISTS FOR (n:ScientificFigure) REQUIRE n.fig_id   IS UNIQUE",
    "CREATE CONSTRAINT sci_table_id     IF NOT EXISTS FOR (n:ScientificTable)  REQUIRE n.table_id IS UNIQUE",
    "CREATE CONSTRAINT equation_id      IF NOT EXISTS FOR (n:Equation)         REQUIRE n.eq_id    IS UNIQUE",
    # Numeric facts (Stage 5c)
    "CREATE CONSTRAINT numeric_fact_id  IF NOT EXISTS FOR (n:NumericFact)      REQUIRE n.fact_id  IS UNIQUE",
]

# ── Range indexes (for property lookups and ORDER BY) ─────────────────────────

INDEXES: list[str] = [
    "CREATE INDEX paper_year        IF NOT EXISTS FOR (n:Paper)   ON (n.year)",
    # Title-only CITES stubs MERGE on title; without a range index each row scans
    # every Paper node (the full-text index below is not used by MERGE).
    "CREATE INDEX paper_title       IF NOT EXISTS FOR (n:Paper)   ON (n.title)",
    "CREATE INDEX paper_doi         IF NOT EXISTS FOR (n:Paper)   ON (n.doi)",
    "CREATE INDEX paper_citations   IF NOT EXISTS FOR (n:Paper)   ON (n.cited_by_count)",
    "CREATE INDEX paper_country     IF NOT EXISTS FOR (n:Paper)   ON (n.primary_country)",
    "CREATE INDEX paper_openalex    IF NOT EXISTS FOR (n:Paper)   ON (n.openalex_id)",
    "CREATE INDEX paper_stub        IF NOT EXISTS FOR (n:Paper)   ON (n.is_reference_stub)",
    "CREATE INDEX method_family     IF NOT EXISTS FOR (n:Method)  ON (n.family)",
    "CREATE INDEX sensor_family     IF NOT EXISTS FOR (n:Sensor)  ON (n.family)",
    "CREATE INDEX author_name_idx   IF NOT EXISTS FOR (n:Author)  ON (n.display_name)",
    "CREATE INDEX institution_cc    IF NOT EXISTS FOR (n:Institution) ON (n.country_code)",
    "CREATE INDEX topic_name_idx    IF NOT EXISTS FOR (n:Topic)   ON (n.topic_name)",
    # Visual layer indexes
    "CREATE INDEX fig_paper_idx     IF NOT EXISTS FOR (n:ScientificFigure) ON (n.paper_id)",
    "CREATE INDEX fig_type_idx      IF NOT EXISTS FOR (n:ScientificFigure) ON (n.figure_type)",
    "CREATE INDEX fig_quality_idx   IF NOT EXISTS FOR (n:ScientificFigure) ON (n.nougat_quality)",
    "CREATE INDEX tbl_paper_idx     IF NOT EXISTS FOR (n:ScientificTable)  ON (n.paper_id)",
    "CREATE INDEX tbl_type_idx      IF NOT EXISTS FOR (n:ScientificTable)  ON (n.table_type)",
    "CREATE INDEX eq_paper_idx      IF NOT EXISTS FOR (n:Equation)         ON (n.paper_id)",
    "CREATE INDEX eq_type_idx       IF NOT EXISTS FOR (n:Equation)         ON (n.equation_type)",
    "CREATE INDEX eq_lhs_idx        IF NOT EXISTS FOR (n:Equation)         ON (n.lhs_symbol)",
    # Numeric fact indexes (Stage 5c)
    "CREATE INDEX nf_paper_idx      IF NOT EXISTS FOR (n:NumericFact)      ON (n.paper_id)",
    "CREATE INDEX nf_table_idx      IF NOT EXISTS FOR (n:NumericFact)      ON (n.table_id)",
    "CREATE INDEX nf_cid_idx        IF NOT EXISTS FOR (n:NumericFact)      ON (n.canonical_id)",
    "CREATE INDEX nf_metric_idx     IF NOT EXISTS FOR (n:NumericFact)      ON (n.metric)",
    "CREATE INDEX nf_value_idx      IF NOT EXISTS FOR (n:NumericFact)      ON (n.value)",
    "CREATE INDEX nf_page_idx       IF NOT EXISTS FOR (n:NumericFact)      ON (n.page)",
]

# ── Full-text index (for Paper title search) ───────────────────────────────────

FULLTEXT_INDEXES: list[str] = [
    "CREATE FULLTEXT INDEX paper_title_ft IF NOT EXISTS FOR (n:Paper) ON EACH [n.title]",
]

# ── Wipe statement (destructive — only called with explicit --wipe flag) ───────

WIPE_STATEMENT = "MATCH (n) DETACH DELETE n"
