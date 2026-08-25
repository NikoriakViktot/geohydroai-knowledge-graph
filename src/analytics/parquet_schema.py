"""
parquet_schema.py  —  GeoHydroAI Stage 3A pyarrow schema definitions
=====================================================================

Single source of truth for all parquet table schemas.
All downstream consumers must import from here — never define schemas inline.

Corpus-level analytics tables (one .parquet per corpus build):
    papers               — bibliographic + analytics summary per paper
    authors              — unique OpenAlex author records
    paper_author_edges   — paper ↔ author membership
    topics               — unique OpenAlex topic records with aggregate scores
    paper_topic_edges    — paper ↔ topic relevance scores
    references           — deduplicated bibliography entries
    paper_reference_edges — paper ↔ cited reference links
    methods              — canonical method entities (from normalized_entities only)
    sensors              — canonical sensor/satellite entities
    metrics              — canonical performance metric entities
    institutions         — OpenAlex institution records with paper/author counts

SODB per-paper tables (one .parquet per paper under data/sodb/{paper_id}/):
    regions              — semantic regions from GROBID coords + Nougat inference
    formulas             — extracted formulas with optional LaTeX from Nougat
    numeric_facts        — atomic quantitative evidence (NSE/KGE/RMSE values + context)
"""

import pyarrow as pa

# ─────────────────────────────────────────────────────────────────────────────
# Table schemas
# ─────────────────────────────────────────────────────────────────────────────

PAPERS_SCHEMA = pa.schema([
    pa.field("paper_id",         pa.string(),  nullable=False),
    pa.field("doi",              pa.string()),
    pa.field("title",            pa.string()),
    pa.field("year",             pa.string()),
    pa.field("journal",          pa.string()),
    pa.field("publisher",        pa.string()),
    pa.field("study_type",       pa.string()),
    pa.field("primary_country",  pa.string()),
    pa.field("cited_by_count",   pa.int32()),
    pa.field("openalex_id",      pa.string()),
    pa.field("abstract_length",  pa.int32()),
    pa.field("references_count", pa.int32()),
    pa.field("authors_count",    pa.int32()),
    pa.field("topics_count",     pa.int32()),
    pa.field("methods_count",    pa.int32()),
    pa.field("sensors_count",    pa.int32()),
    pa.field("metrics_count",    pa.int32()),
    pa.field("has_openalex",     pa.bool_()),
    pa.field("has_doi",          pa.bool_()),
    pa.field("has_abstract",     pa.bool_()),
    pa.field("created_from",     pa.string()),
])

AUTHORS_SCHEMA = pa.schema([
    pa.field("author_id",    pa.string(), nullable=False),
    pa.field("display_name", pa.string()),
    pa.field("orcid",        pa.string()),
    pa.field("paper_count",  pa.int32()),
])

PAPER_AUTHOR_EDGES_SCHEMA = pa.schema([
    pa.field("paper_id",         pa.string(), nullable=False),
    pa.field("author_id",        pa.string(), nullable=False),
    pa.field("author_position",  pa.string()),
    pa.field("is_corresponding", pa.bool_()),
])

TOPICS_SCHEMA = pa.schema([
    pa.field("topic_id",    pa.string(), nullable=False),
    pa.field("topic_name",  pa.string()),
    pa.field("paper_count", pa.int32()),
    pa.field("max_score",   pa.float32()),
    pa.field("avg_score",   pa.float32()),
])

PAPER_TOPIC_EDGES_SCHEMA = pa.schema([
    pa.field("paper_id", pa.string(), nullable=False),
    pa.field("topic_id", pa.string(), nullable=False),
    pa.field("score",    pa.float32()),
])

REFERENCES_SCHEMA = pa.schema([
    pa.field("reference_id",  pa.string(), nullable=False),
    pa.field("doi",           pa.string()),
    pa.field("title",         pa.string()),
    pa.field("journal",       pa.string()),
    pa.field("year",          pa.int32()),
    pa.field("authors_count", pa.int32()),
    pa.field("raw_text",      pa.string()),
    pa.field("openalex_id",   pa.string()),
])

PAPER_REFERENCE_EDGES_SCHEMA = pa.schema([
    pa.field("paper_id",     pa.string(), nullable=False),
    pa.field("reference_id", pa.string(), nullable=False),
])

METHODS_SCHEMA = pa.schema([
    pa.field("canonical_id",  pa.string(), nullable=False),
    pa.field("display_name",  pa.string()),
    pa.field("type",          pa.string()),
    pa.field("type_group",    pa.string()),
    pa.field("paper_count",   pa.int32()),
    pa.field("source_count",  pa.int32()),
])

SENSORS_SCHEMA = pa.schema([
    pa.field("canonical_id",   pa.string(), nullable=False),
    pa.field("display_name",   pa.string()),
    pa.field("sensor_family",  pa.string()),
    pa.field("paper_count",    pa.int32()),
])

METRICS_SCHEMA = pa.schema([
    pa.field("canonical_id",  pa.string(), nullable=False),
    pa.field("display_name",  pa.string()),
    pa.field("metric_type",   pa.string()),
    pa.field("paper_count",   pa.int32()),
])

INSTITUTIONS_SCHEMA = pa.schema([
    pa.field("institution_id",   pa.string(), nullable=False),
    pa.field("display_name",     pa.string()),
    pa.field("country_code",     pa.string()),
    pa.field("institution_type", pa.string()),
    pa.field("paper_count",      pa.int32()),
    pa.field("author_count",     pa.int32()),
])

# ─────────────────────────────────────────────────────────────────────────────
# SODB per-paper schemas (written to data/sodb/{paper_id}/*.parquet)
# ─────────────────────────────────────────────────────────────────────────────

REGIONS_SCHEMA = pa.schema([
    pa.field("region_id",       pa.string(),            nullable=False),  # from ScientificRegion.region_id
    pa.field("paper_id",        pa.string(),            nullable=False),
    pa.field("page",            pa.int32(),             nullable=False),
    pa.field("bbox_x0",         pa.float32(),           nullable=False),
    pa.field("bbox_y0",         pa.float32(),           nullable=False),
    pa.field("bbox_x1",         pa.float32(),           nullable=False),
    pa.field("bbox_y1",         pa.float32(),           nullable=False),
    pa.field("region_type",     pa.string(),            nullable=False),  # FORMULA_REGION|TABLE_REGION|FIGURE_REGION|...
    pa.field("source_parser",   pa.string(),            nullable=False),  # GROBID|NOUGAT|HYBRID
    pa.field("nougat_text",     pa.string(),            nullable=True),   # visual_text from Nougat
    pa.field("nougat_latex",    pa.string(),            nullable=True),   # markdown_text from Nougat (formula regions)
    pa.field("confidence",      pa.float32(),           nullable=True),
    pa.field("crop_path",       pa.string(),            nullable=True),   # path relative to project root
    pa.field("merge_group_id",  pa.string(),            nullable=True),   # reserved for future merge tracking
    pa.field("pipeline_hash",   pa.string(),            nullable=False),
    pa.field("created_at",      pa.timestamp("ms"),     nullable=False),
])

FORMULAS_SCHEMA = pa.schema([
    pa.field("formula_id",      pa.string(),            nullable=False),
    pa.field("paper_id",        pa.string(),            nullable=False),
    pa.field("region_id",       pa.string(),            nullable=True),   # FK → regions
    pa.field("xml_id",          pa.string(),            nullable=True),   # GROBID xml:id
    pa.field("page",            pa.int32(),             nullable=True),
    pa.field("text",            pa.string(),            nullable=True),   # plain text fallback
    pa.field("latex",           pa.string(),            nullable=True),   # Nougat LaTeX
    pa.field("formula_class",   pa.string(),            nullable=True),   # NSE|RMSE|KGE|CONTINUITY|...
    pa.field("confidence",      pa.float32(),           nullable=True),
    pa.field("source_parser",   pa.string(),            nullable=False),
    pa.field("pipeline_hash",   pa.string(),            nullable=False),
    pa.field("created_at",      pa.timestamp("ms"),     nullable=False),
])

TABLES_SCHEMA = pa.schema([
    pa.field("table_id",          pa.string(),               nullable=False),  # "{paper_id}_tbl_{n:03d}"
    pa.field("paper_id",          pa.string(),               nullable=False),
    pa.field("region_id",         pa.string(),               nullable=True),   # FK → regions (matched later)
    pa.field("xml_id",            pa.string(),               nullable=True),   # GROBID xml:id
    pa.field("page",              pa.int32(),                nullable=True),
    pa.field("label",             pa.string(),               nullable=True),
    pa.field("caption",           pa.string(),               nullable=True),
    pa.field("header_row",        pa.list_(pa.string()),     nullable=True),   # first header row cells
    pa.field("row_count",         pa.int32(),                nullable=True),
    pa.field("col_count",         pa.int32(),                nullable=True),
    pa.field("has_numeric_data",  pa.bool_(),                nullable=True),
    pa.field("source_parser",     pa.string(),               nullable=False),
    pa.field("pipeline_hash",     pa.string(),               nullable=False),
    pa.field("created_at",        pa.timestamp("ms"),        nullable=False),
])

NUMERIC_FACTS_SCHEMA = pa.schema([
    pa.field("fact_id",          pa.string(),           nullable=False),
    pa.field("paper_id",         pa.string(),           nullable=False),
    pa.field("source_region_id", pa.string(),           nullable=True),   # FK → regions
    pa.field("table_id",         pa.string(),           nullable=True),
    pa.field("table_label",      pa.string(),           nullable=True),
    pa.field("page",             pa.int32(),            nullable=True),
    pa.field("col_header",       pa.string(),           nullable=True),
    pa.field("row_context",      pa.string(),           nullable=True),
    pa.field("metric",           pa.string(),           nullable=False),  # NSE|KGE|RMSE|MAE|PBIAS|...
    pa.field("canonical_id",     pa.string(),           nullable=False),  # ontology ID
    pa.field("node_label",       pa.string(),           nullable=False),  # Neo4j label
    pa.field("value",            pa.float64(),          nullable=True),
    pa.field("unit",             pa.string(),           nullable=True),
    pa.field("confidence",       pa.float32(),          nullable=False),
    pa.field("basin_id",         pa.string(),           nullable=True),
    pa.field("period_type",      pa.string(),           nullable=True),   # CALIBRATION|VALIDATION|TEST
    pa.field("source",           pa.string(),           nullable=False),  # table_extractor|nougat_formula|...
    pa.field("pipeline_hash",    pa.string(),           nullable=False),
    pa.field("created_at",       pa.timestamp("ms"),    nullable=False),
])

CHUNKS_SCHEMA = pa.schema([
    pa.field("chunk_id",       pa.string(),            nullable=False),
    pa.field("paper_id",       pa.string(),            nullable=False),
    pa.field("region_id",      pa.string(),            nullable=True),    # FK → regions
    pa.field("chunk_index",    pa.int32(),             nullable=False),
    pa.field("chunk_type",     pa.string(),            nullable=False),   # sentence|paragraph|section
    pa.field("text",           pa.string(),            nullable=False),
    pa.field("section_title",  pa.string(),            nullable=True),
    pa.field("page",           pa.int32(),             nullable=True),
    pa.field("embedding",      pa.list_(pa.float32()), nullable=True),    # float32[N] — stored at write time
    pa.field("source_parser",  pa.string(),            nullable=False),
    pa.field("pipeline_hash",  pa.string(),            nullable=False),
    pa.field("created_at",     pa.timestamp("ms"),     nullable=False),
])

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — parsed layer (one .parquet per paper under data/parsed/{paper_id}/)
# ─────────────────────────────────────────────────────────────────────────────

SECTIONS_SCHEMA = pa.schema([
    pa.field("section_id",    pa.string(),  nullable=False),  # "{paper_id}_sec_{n:03d}"
    pa.field("paper_id",      pa.string(),  nullable=False),
    pa.field("title",         pa.string(),  nullable=True),
    pa.field("level",         pa.int32(),   nullable=False),  # 1=top, 2=sub, 3=subsub
    pa.field("n",             pa.string(),  nullable=True),   # section number "1.2"
    pa.field("text",          pa.string(),  nullable=True),
    pa.field("word_count",    pa.int32(),   nullable=True),
    pa.field("page",          pa.int32(),   nullable=True),
    pa.field("bbox_x0",       pa.float32(), nullable=True),
    pa.field("bbox_y0",       pa.float32(), nullable=True),
    pa.field("bbox_x1",       pa.float32(), nullable=True),
    pa.field("bbox_y1",       pa.float32(), nullable=True),
    pa.field("source_parser", pa.string(),  nullable=False),
    pa.field("pipeline_hash", pa.string(),  nullable=False),
    pa.field("created_at",    pa.timestamp("ms"), nullable=False),
])

PARSED_FIGURES_SCHEMA = pa.schema([
    pa.field("figure_id",     pa.string(),  nullable=False),  # "{paper_id}_fig_{n:03d}"
    pa.field("paper_id",      pa.string(),  nullable=False),
    pa.field("xml_id",        pa.string(),  nullable=True),
    pa.field("label",         pa.string(),  nullable=True),   # "Figure 1"
    pa.field("caption",       pa.string(),  nullable=True),
    pa.field("page",          pa.int32(),   nullable=True),
    pa.field("bbox_x0",       pa.float32(), nullable=True),
    pa.field("bbox_y0",       pa.float32(), nullable=True),
    pa.field("bbox_x1",       pa.float32(), nullable=True),
    pa.field("bbox_y1",       pa.float32(), nullable=True),
    pa.field("graphic_page",  pa.int32(),   nullable=True),
    pa.field("graphic_x0",    pa.float32(), nullable=True),
    pa.field("graphic_y0",    pa.float32(), nullable=True),
    pa.field("graphic_x1",    pa.float32(), nullable=True),
    pa.field("graphic_y1",    pa.float32(), nullable=True),
    pa.field("source_parser", pa.string(),  nullable=False),
    pa.field("pipeline_hash", pa.string(),  nullable=False),
    pa.field("created_at",    pa.timestamp("ms"), nullable=False),
])

EQUATIONS_SCHEMA = pa.schema([
    pa.field("equation_id",   pa.string(),  nullable=False),  # "{paper_id}_eq_{n:03d}"
    pa.field("paper_id",      pa.string(),  nullable=False),
    pa.field("xml_id",        pa.string(),  nullable=True),
    pa.field("text",          pa.string(),  nullable=True),   # plain text or LaTeX
    pa.field("page",          pa.int32(),   nullable=True),
    pa.field("bbox_x0",       pa.float32(), nullable=True),
    pa.field("bbox_y0",       pa.float32(), nullable=True),
    pa.field("bbox_x1",       pa.float32(), nullable=True),
    pa.field("bbox_y1",       pa.float32(), nullable=True),
    pa.field("source_parser", pa.string(),  nullable=False),
    pa.field("pipeline_hash", pa.string(),  nullable=False),
    pa.field("created_at",    pa.timestamp("ms"), nullable=False),
])

PARSED_REFS_SCHEMA = pa.schema([
    pa.field("ref_id",        pa.string(),  nullable=False),  # "{paper_id}_ref_{xml_id}"
    pa.field("paper_id",      pa.string(),  nullable=False),
    pa.field("xml_id",        pa.string(),  nullable=True),   # GROBID "b0", "b1"
    pa.field("title",         pa.string(),  nullable=True),
    pa.field("authors",       pa.string(),  nullable=True),   # semicolon-joined
    pa.field("journal",       pa.string(),  nullable=True),
    pa.field("year",          pa.int32(),   nullable=True),
    pa.field("doi",           pa.string(),  nullable=True),
    pa.field("raw",           pa.string(),  nullable=True),
    pa.field("source_parser", pa.string(),  nullable=False),
    pa.field("pipeline_hash", pa.string(),  nullable=False),
    pa.field("created_at",    pa.timestamp("ms"), nullable=False),
])

CAPTIONS_SCHEMA = pa.schema([
    pa.field("caption_id",    pa.string(),  nullable=False),  # "{paper_id}_cap_{n:03d}"
    pa.field("paper_id",      pa.string(),  nullable=False),
    pa.field("parent_id",     pa.string(),  nullable=True),   # FK → figure_id or table_id
    pa.field("parent_type",   pa.string(),  nullable=True),   # "figure" | "table"
    pa.field("label",         pa.string(),  nullable=True),   # "Figure 1", "Table 2"
    pa.field("text",          pa.string(),  nullable=True),
    pa.field("page",          pa.int32(),   nullable=True),
    pa.field("source_parser", pa.string(),  nullable=False),
    pa.field("pipeline_hash", pa.string(),  nullable=False),
    pa.field("created_at",    pa.timestamp("ms"), nullable=False),
])

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — scientific object layer (data/sodb/{paper_id}/)
# ─────────────────────────────────────────────────────────────────────────────

SCIENTIFIC_OBJECTS_SCHEMA = pa.schema([
    pa.field("object_id",        pa.string(),  nullable=False),  # "{paper_id}_{type}_{n:03d}"
    pa.field("paper_id",         pa.string(),  nullable=False),
    pa.field("object_type",      pa.string(),  nullable=False),  # table|figure|equation|section|reference
    pa.field("semantic_type",    pa.string(),  nullable=True),   # metrics_table|flood_extent_map|…
    pa.field("source_id",        pa.string(),  nullable=True),   # FK → sections/figures/etc.
    pa.field("page",             pa.int32(),   nullable=True),
    pa.field("raw_text",         pa.string(),  nullable=True),
    pa.field("classifier_score", pa.float32(), nullable=True),   # confidence in semantic_type
    pa.field("classifier_source",pa.string(),  nullable=True),   # rules|embedding|llm
    # Typed boolean flags (only populated when relevant)
    pa.field("contains_models",      pa.bool_(), nullable=True),
    pa.field("contains_metrics",     pa.bool_(), nullable=True),
    pa.field("contains_timeseries",  pa.bool_(), nullable=True),
    pa.field("contains_coordinates", pa.bool_(), nullable=True),
    pa.field("contains_satellite",   pa.bool_(), nullable=True),
    pa.field("contains_hydrograph",  pa.bool_(), nullable=True),
    # Extracted candidates (stored as JSON string for parquet compatibility)
    pa.field("candidate_models",   pa.string(), nullable=True),  # JSON list
    pa.field("candidate_metrics",  pa.string(), nullable=True),
    pa.field("candidate_vars",     pa.string(), nullable=True),
    pa.field("variables",          pa.string(), nullable=True),  # for equations
    pa.field("related_models",     pa.string(), nullable=True),
    pa.field("domain",             pa.string(), nullable=True),
    pa.field("equation_name",      pa.string(), nullable=True),
    pa.field("pipeline_hash",      pa.string(), nullable=False),
    pa.field("created_at",         pa.timestamp("ms"), nullable=False),
])

OBJECT_GRAPH_SCHEMA = pa.schema([
    pa.field("edge_id",       pa.string(),  nullable=False),
    pa.field("paper_id",      pa.string(),  nullable=False),
    pa.field("source_id",     pa.string(),  nullable=False),  # object_id
    pa.field("target_id",     pa.string(),  nullable=False),  # object_id
    pa.field("relation",      pa.string(),  nullable=False),  # CAPTION_OF|IN_SECTION|CITES|FOLLOWS
    pa.field("confidence",    pa.float32(), nullable=True),
    pa.field("pipeline_hash", pa.string(),  nullable=False),
    pa.field("created_at",    pa.timestamp("ms"), nullable=False),
])

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2.5 — semantic validation layer (data/sodb/{paper_id}/)
# ─────────────────────────────────────────────────────────────────────────────

SEMANTIC_ANNOTATIONS_SCHEMA = pa.schema([
    pa.field("annotation_id",            pa.string(),  nullable=False),  # "{paper_id}_ann_{object_id}"
    pa.field("object_id",                pa.string(),  nullable=False),
    pa.field("paper_id",                 pa.string(),  nullable=False),
    pa.field("object_type",              pa.string(),  nullable=False),  # figure|table|equation|section
    pa.field("semantic_type",            pa.string(),  nullable=True),
    pa.field("scientific_role",          pa.string(),  nullable=True),   # JSON list of role strings
    pa.field("contains_discharge",       pa.bool_(),   nullable=True),
    pa.field("contains_water_level",     pa.bool_(),   nullable=True),
    pa.field("contains_uncertainty_band",pa.bool_(),   nullable=True),
    pa.field("contains_flood_extent",    pa.bool_(),   nullable=True),
    pa.field("contains_timeseries",      pa.bool_(),   nullable=True),
    pa.field("contains_spatial_data",    pa.bool_(),   nullable=True),
    pa.field("contains_remote_sensing",  pa.bool_(),   nullable=True),
    pa.field("likely_models",            pa.string(),  nullable=True),   # JSON list
    pa.field("candidate_tasks",          pa.string(),  nullable=True),   # JSON list
    pa.field("candidate_metrics",        pa.string(),  nullable=True),   # JSON list
    pa.field("domain",                   pa.string(),  nullable=True),
    pa.field("evidence_strength",        pa.float32(), nullable=True),
    pa.field("semantic_confidence",      pa.float32(), nullable=True),
    pa.field("evidence_trace",           pa.string(),  nullable=True),   # JSON list of EvidenceTrace dicts
    pa.field("constraint_violations",    pa.string(),  nullable=True),   # JSON list of violation strings
    pa.field("pipeline_hash",            pa.string(),  nullable=False),
    pa.field("created_at",               pa.timestamp("ms"), nullable=False),
])

SEMANTIC_EDGES_SCHEMA = pa.schema([
    pa.field("edge_id",       pa.string(),  nullable=False),
    pa.field("paper_id",      pa.string(),  nullable=False),
    pa.field("source_id",     pa.string(),  nullable=False),  # annotation object_id
    pa.field("target_id",     pa.string(),  nullable=False),  # annotation object_id
    pa.field("relation",      pa.string(),  nullable=False),  # EVALUATES|VISUALIZES|VALIDATES|SUPPORTS|DERIVED_FROM
    pa.field("confidence",    pa.float32(), nullable=True),
    pa.field("evidence",      pa.string(),  nullable=True),
    pa.field("pipeline_hash", pa.string(),  nullable=False),
    pa.field("created_at",    pa.timestamp("ms"), nullable=False),
])

# ─────────────────────────────────────────────────────────────────────────────
# Registry: table_name → (schema, parquet filename)
# ─────────────────────────────────────────────────────────────────────────────

TABLE_REGISTRY: dict[str, tuple[pa.Schema, str]] = {
    # ── Scientometric analytics (built by parquet_builder.py) ─────────────────
    "papers":               (PAPERS_SCHEMA,               "papers.parquet"),
    "authors":              (AUTHORS_SCHEMA,               "authors.parquet"),
    "paper_author_edges":   (PAPER_AUTHOR_EDGES_SCHEMA,   "paper_author_edges.parquet"),
    "topics":               (TOPICS_SCHEMA,                "topics.parquet"),
    "paper_topic_edges":    (PAPER_TOPIC_EDGES_SCHEMA,     "paper_topic_edges.parquet"),
    "references":           (REFERENCES_SCHEMA,            "references.parquet"),
    "paper_reference_edges":(PAPER_REFERENCE_EDGES_SCHEMA, "paper_reference_edges.parquet"),
    "methods":              (METHODS_SCHEMA,               "methods.parquet"),
    "sensors":              (SENSORS_SCHEMA,               "sensors.parquet"),
    "metrics":              (METRICS_SCHEMA,               "metrics.parquet"),
    "institutions":         (INSTITUTIONS_SCHEMA,          "institutions.parquet"),
    # ── SODB aggregates (built by sodb_aggregator.py) ─────────────────────────
    "regions":              (REGIONS_SCHEMA,               "regions.parquet"),
    "formulas":             (FORMULAS_SCHEMA,              "formulas.parquet"),
    "sodb_tables":          (TABLES_SCHEMA,                "sodb_tables.parquet"),
    "numeric_facts":        (NUMERIC_FACTS_SCHEMA,         "numeric_facts.parquet"),
    "scientific_objects":   (SCIENTIFIC_OBJECTS_SCHEMA,    "scientific_objects.parquet"),
    "object_graph":         (OBJECT_GRAPH_SCHEMA,          "object_graph.parquet"),
    "semantic_annotations": (SEMANTIC_ANNOTATIONS_SCHEMA,  "semantic_annotations.parquet"),
    "semantic_edges":       (SEMANTIC_EDGES_SCHEMA,        "semantic_edges.parquet"),
}
