"""
neo4j_writer.py  —  Batch writer for the Stage 5 knowledge graph.

All writes use UNWIND batches (default 500 rows) so the driver never sends
more than one Bolt message per call.  Every Cypher statement uses MERGE, making
the entire pipeline idempotent — safe to re-run after failures.
"""
from __future__ import annotations

import logging
from typing import Any

from neo4j import GraphDatabase

from src.config import settings
from src.graph.graph_constraints import CONSTRAINTS, INDEXES, FULLTEXT_INDEXES, WIPE_STATEMENT

log = logging.getLogger("geohydro.graph.writer")

_BATCH = 500   # rows per UNWIND call


class GraphWriter:
    """
    Thread-safe Neo4j batch writer.

    Usage::

        with GraphWriter() as gw:
            gw.create_constraints()
            gw.write_papers(paper_rows)
            gw.write_authors(author_rows)
            ...
    """

    def __init__(
        self,
        uri:      str | None = None,
        user:     str | None = None,
        password: str | None = None,
    ) -> None:
        uri      = uri      or settings.NEO4J_URI
        user     = user     or settings.NEO4J_USER
        password = password or settings.require_env("NEO4J_PASSWORD")
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        log.info("Connected to Neo4j at %s", uri)

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "GraphWriter":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── Schema setup ──────────────────────────────────────────────────────────

    def wipe(self) -> None:
        """Delete ALL nodes and relationships. Destructive — requires explicit call."""
        with self._driver.session() as s:
            s.run(WIPE_STATEMENT)
        log.warning("Graph wiped.")

    def create_constraints(self) -> None:
        with self._driver.session() as s:
            for stmt in CONSTRAINTS:
                s.run(stmt)
            for stmt in INDEXES:
                s.run(stmt)
            for stmt in FULLTEXT_INDEXES:
                try:
                    s.run(stmt)
                except Exception as exc:   # full-text not available on all editions
                    log.debug("Full-text index skipped: %s", exc)
        log.info("Constraints and indexes applied.")

    # ── Node writers ──────────────────────────────────────────────────────────

    def write_papers(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MERGE (p:Paper {paper_id: r.paper_id})
        SET p.title           = r.title,
            p.doi             = r.doi,
            p.year            = r.year,
            p.cited_by_count  = r.cited_by_count,
            p.openalex_id     = r.openalex_id,
            p.journal         = r.journal,
            p.study_type      = r.study_type,
            p.primary_country = r.primary_country,
            p.identity_status = r.identity_status
        """
        self._batch_write("Papers", cypher, rows)

    def write_authors(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MERGE (a:Author {author_id: r.author_id})
        SET a.display_name = r.display_name,
            a.orcid        = r.orcid,
            a.paper_count  = r.paper_count
        """
        self._batch_write("Authors", cypher, rows)

    def write_institutions(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MERGE (i:Institution {institution_id: r.institution_id})
        SET i.display_name     = r.display_name,
            i.country_code     = r.country_code,
            i.institution_type = r.institution_type,
            i.paper_count      = r.paper_count
        """
        self._batch_write("Institutions", cypher, rows)

    def write_topics(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MERGE (t:Topic {topic_id: r.topic_id})
        SET t.topic_name  = r.topic_name,
            t.paper_count = r.paper_count,
            t.avg_score   = r.avg_score
        """
        self._batch_write("Topics", cypher, rows)

    def write_methods(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MERGE (m:Method {canonical_id: r.canonical_id})
        SET m.display_name = r.display_name,
            m.family       = r.family,
            m.type_group   = r.type_group,
            m.paper_count  = r.paper_count
        """
        self._batch_write("Methods", cypher, rows)

    def write_sensors(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MERGE (s:Sensor {canonical_id: r.canonical_id})
        SET s.display_name = r.display_name,
            s.family       = r.family,
            s.type_group   = r.type_group,
            s.paper_count  = r.paper_count
        """
        self._batch_write("Sensors", cypher, rows)

    def write_metrics(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MERGE (m:Metric {canonical_id: r.canonical_id})
        SET m.display_name = r.display_name,
            m.metric_type  = r.metric_type,
            m.paper_count  = r.paper_count
        """
        self._batch_write("Metrics", cypher, rows)

    def write_countries(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MERGE (c:Country {name: r.name})
        SET c.iso_code = r.iso_code,
            c.lat      = r.lat,
            c.lon      = r.lon
        """
        self._batch_write("Countries", cypher, rows)

    def write_flood_events(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MERGE (e:FloodEvent {name: r.name})
        SET e.country = r.country,
            e.year    = r.year,
            e.lat     = r.lat,
            e.lon     = r.lon
        """
        self._batch_write("FloodEvents", cypher, rows)

    # ── Relationship writers ───────────────────────────────────────────────────

    def write_paper_method_edges(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MATCH (p:Paper  {paper_id:     r.paper_id})
        MATCH (m:Method {canonical_id: r.canonical_id})
        MERGE (p)-[e:USES_METHOD]->(m)
        SET e.confidence        = r.confidence,
            e.extraction_score  = r.extraction_score,
            e.disambig_conf     = r.disambig_conf,
            e.evidence          = r.evidence,
            e.surface_form      = r.surface_form,
            e.role              = r.role,
            e.mentions          = r.mentions,
            e.page              = r.page,
            e.section           = r.section,
            e.coord_match       = r.coord_match,
            e.grounded          = r.grounded,
            e.tei_mentions      = r.tei_mentions,
            e.tei_evidence      = r.tei_evidence,
            e.tei_page          = r.tei_page,
            e.tei_section       = r.tei_section,
            e.resolver_version  = r.resolver_version,
            e.ontology_version  = r.ontology_version
        """
        self._batch_write("Paper→Method edges", cypher, rows)

    def write_paper_sensor_edges(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MATCH (p:Paper  {paper_id:     r.paper_id})
        MATCH (s:Sensor {canonical_id: r.canonical_id})
        MERGE (p)-[e:USES_SENSOR]->(s)
        SET e.confidence        = r.confidence,
            e.extraction_score  = r.extraction_score,
            e.disambig_conf     = r.disambig_conf,
            e.evidence          = r.evidence,
            e.surface_form      = r.surface_form,
            e.role              = r.role,
            e.mentions          = r.mentions,
            e.page              = r.page,
            e.section           = r.section,
            e.coord_match       = r.coord_match,
            e.grounded          = r.grounded,
            e.tei_mentions      = r.tei_mentions,
            e.tei_evidence      = r.tei_evidence,
            e.tei_page          = r.tei_page,
            e.tei_section       = r.tei_section,
            e.resolver_version  = r.resolver_version,
            e.ontology_version  = r.ontology_version
        """
        self._batch_write("Paper→Sensor edges", cypher, rows)

    def write_paper_metric_edges(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MATCH (p:Paper  {paper_id:     r.paper_id})
        MATCH (m:Metric {canonical_id: r.canonical_id})
        MERGE (p)-[e:REPORTS_METRIC]->(m)
        SET e.confidence        = r.confidence,
            e.extraction_score  = r.extraction_score,
            e.disambig_conf     = r.disambig_conf,
            e.evidence          = r.evidence,
            e.surface_form      = r.surface_form,
            e.role              = r.role,
            e.mentions          = r.mentions,
            e.page              = r.page,
            e.section           = r.section,
            e.coord_match       = r.coord_match,
            e.grounded          = r.grounded,
            e.tei_mentions      = r.tei_mentions,
            e.tei_evidence      = r.tei_evidence,
            e.tei_page          = r.tei_page,
            e.tei_section       = r.tei_section,
            e.resolver_version  = r.resolver_version,
            e.ontology_version  = r.ontology_version
        """
        self._batch_write("Paper→Metric edges", cypher, rows)

    def write_paper_topic_edges(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MATCH (p:Paper {paper_id: r.paper_id})
        MATCH (t:Topic {topic_id: r.topic_id})
        MERGE (p)-[e:HAS_TOPIC]->(t)
        SET e.score = r.score
        """
        self._batch_write("Paper→Topic edges", cypher, rows)

    def write_paper_country_edges(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MATCH (p:Paper   {paper_id: r.paper_id})
        MATCH (c:Country {name:     r.country_name})
        MERGE (p)-[:FROM_COUNTRY]->(c)
        """
        self._batch_write("Paper→Country edges", cypher, rows)

    def write_author_paper_edges(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MATCH (a:Author {author_id: r.author_id})
        MATCH (p:Paper  {paper_id:  r.paper_id})
        MERGE (a)-[e:AUTHORED]->(p)
        SET e.position         = r.position,
            e.is_corresponding = r.is_corresponding
        """
        self._batch_write("Author→Paper edges", cypher, rows)

    def write_author_institution_edges(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MATCH (a:Author      {author_id:      r.author_id})
        MATCH (i:Institution {institution_id: r.institution_id})
        MERGE (a)-[:AFFILIATED_WITH]->(i)
        """
        self._batch_write("Author→Institution edges", cypher, rows)

    def write_institution_country_edges(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MATCH (i:Institution {institution_id: r.institution_id})
        MATCH (c:Country     {name:           r.country_name})
        MERGE (i)-[:LOCATED_IN]->(c)
        """
        self._batch_write("Institution→Country edges", cypher, rows)

    def write_paper_reference_edges(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MATCH (src:Paper {paper_id: r.source_paper_id})
        MATCH (tgt:Paper {paper_id: r.target_paper_id})
        MERGE (src)-[:REFERENCES]->(tgt)
        """
        self._batch_write("Paper→Paper (REFERENCES) edges", cypher, rows)

    def write_cites_edges(self, rows: list[dict]) -> None:
        """
        Build CITES edges from a paper to its bibliography references.

        Each row must contain:
          source_paper_id: str  — the citing paper's paper_id
          target_doi:      str | None
          target_title:    str | None
          target_year:     int | None

        Strategy:
          1. DOI-keyed: MERGE a Paper node on doi; creates a stub if not yet ingested.
          2. Title-keyed (no doi): MERGE on normalised title as last resort.

        Stub nodes carry is_reference_stub=true so they can be distinguished from
        fully-ingested papers in graph queries.
        """
        doi_rows   = [r for r in rows if r.get("target_doi")]
        nodoi_rows = [r for r in rows if not r.get("target_doi") and r.get("target_title")]

        if doi_rows:
            cypher = """
            UNWIND $rows AS r
            MATCH  (src:Paper {paper_id: r.source_paper_id})
            MERGE  (tgt:Paper {doi: r.target_doi})
            ON CREATE SET tgt.title            = r.target_title,
                          tgt.year             = r.target_year,
                          tgt.is_reference_stub = true
            MERGE (src)-[:CITES]->(tgt)
            """
            self._batch_write("Paper CITES (doi)", cypher, doi_rows)

        if nodoi_rows:
            cypher = """
            UNWIND $rows AS r
            MATCH  (src:Paper {paper_id: r.source_paper_id})
            MERGE  (tgt:Paper {title: r.target_title})
            ON CREATE SET tgt.year             = r.target_year,
                          tgt.is_reference_stub = true
            MERGE (src)-[:CITES]->(tgt)
            """
            self._batch_write("Paper CITES (title-only)", cypher, nodoi_rows)

    def build_cites_rows(self, paper_id: str, references: list[dict]) -> list[dict]:
        """
        Helper: convert the pipeline's reference list format to write_cites_edges rows.

        references is the list of dicts from build_paper_json()["references"]:
          {title, authors, year, doi, journal, raw_text}
        """
        rows = []
        for ref in references:
            doi   = ref.get("doi")
            title = ref.get("title")
            if not doi and not title:
                continue
            rows.append({
                "source_paper_id": paper_id,
                "target_doi":      doi,
                "target_title":    title,
                "target_year":     ref.get("year"),
            })
        return rows

    def write_method_cooccurrence_edges(self, rows: list[dict]) -> None:
        """(:Method)-[:CO_OCCURS_WITH {count}]->(:Method) — undirected stored as directed."""
        cypher = """
        UNWIND $rows AS r
        MATCH (a:Method {canonical_id: r.id_a})
        MATCH (b:Method {canonical_id: r.id_b})
        MERGE (a)-[e:CO_OCCURS_WITH]->(b)
        SET e.count = r.count
        """
        self._batch_write("Method CO_OCCURS_WITH edges", cypher, rows)

    def write_sensor_cooccurrence_edges(self, rows: list[dict]) -> None:
        """(:Sensor)-[:CO_OCCURS_WITH {count}]->(:Sensor)"""
        cypher = """
        UNWIND $rows AS r
        MATCH (a:Sensor {canonical_id: r.id_a})
        MATCH (b:Sensor {canonical_id: r.id_b})
        MERGE (a)-[e:CO_OCCURS_WITH]->(b)
        SET e.count = r.count
        """
        self._batch_write("Sensor CO_OCCURS_WITH edges", cypher, rows)

    def write_method_sensor_edges(self, rows: list[dict]) -> None:
        """(:Method)-[:COMMONLY_USED_WITH {count}]->(:Sensor)"""
        cypher = """
        UNWIND $rows AS r
        MATCH (m:Method {canonical_id: r.id_a})
        MATCH (s:Sensor {canonical_id: r.id_b})
        MERGE (m)-[e:COMMONLY_USED_WITH]->(s)
        SET e.count = r.count
        """
        self._batch_write("Method→Sensor COMMONLY_USED_WITH edges", cypher, rows)

    def write_paper_flood_event_edges(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MATCH (p:Paper      {paper_id: r.paper_id})
        MATCH (e:FloodEvent {name:     r.event_name})
        MERGE (p)-[:INVESTIGATES]->(e)
        """
        self._batch_write("Paper→FloodEvent edges", cypher, rows)

    # ── Visual layer writers (Stage 5b) ───────────────────────────────────────

    def write_figures(self, rows: list[dict]) -> None:
        """MERGE ScientificFigure nodes."""
        cypher = """
        UNWIND $rows AS r
        MERGE (f:ScientificFigure {fig_id: r.fig_id})
        SET f.paper_id        = r.paper_id,
            f.page            = r.page,
            f.region_id       = r.region_id,
            f.figure_type     = r.figure_type,
            f.caption         = r.caption,
            f.nougat_markdown = r.nougat_markdown,
            f.nougat_quality  = r.nougat_quality,
            f.crop_strategy   = r.crop_strategy,
            f.bbox_json       = r.bbox_json,
            f.merge_strategy  = r.merge_strategy,
            f.semantic_priority = r.semantic_priority
        """
        self._batch_write("ScientificFigures", cypher, rows)

    def write_tables(self, rows: list[dict]) -> None:
        """MERGE ScientificTable nodes."""
        cypher = """
        UNWIND $rows AS r
        MERGE (t:ScientificTable {table_id: r.table_id})
        SET t.paper_id        = r.paper_id,
            t.page            = r.page,
            t.region_id       = r.region_id,
            t.caption         = r.caption,
            t.table_type      = r.table_type,
            t.nougat_markdown = r.nougat_markdown,
            t.nougat_quality  = r.nougat_quality,
            t.crop_strategy   = r.crop_strategy,
            t.bbox_json       = r.bbox_json,
            t.row_count       = r.row_count,
            t.column_count    = r.column_count
        """
        self._batch_write("ScientificTables", cypher, rows)

    def write_equations(self, rows: list[dict]) -> None:
        """MERGE Equation nodes."""
        cypher = """
        UNWIND $rows AS r
        MERGE (e:Equation {eq_id: r.eq_id})
        SET e.paper_id          = r.paper_id,
            e.page              = r.page,
            e.region_id         = r.region_id,
            e.latex_raw         = r.latex_raw,
            e.lhs_symbol        = r.lhs_symbol,
            e.equation_type     = r.equation_type,
            e.equation_number   = r.equation_number,
            e.nougat_quality    = r.nougat_quality,
            e.context_text      = r.context_text,
            e.canonical_eq_id   = r.canonical_eq_id
        """
        self._batch_write("Equations", cypher, rows)

    def write_paper_figure_edges(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MATCH (p:Paper           {paper_id: r.paper_id})
        MATCH (f:ScientificFigure{fig_id:   r.fig_id})
        MERGE (p)-[:HAS_FIGURE]->(f)
        """
        self._batch_write("Paper→ScientificFigure edges", cypher, rows)

    def write_paper_table_edges(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MATCH (p:Paper          {paper_id: r.paper_id})
        MATCH (t:ScientificTable{table_id: r.table_id})
        MERGE (p)-[:HAS_TABLE]->(t)
        """
        self._batch_write("Paper→ScientificTable edges", cypher, rows)

    def write_paper_equation_edges(self, rows: list[dict]) -> None:
        cypher = """
        UNWIND $rows AS r
        MATCH (p:Paper    {paper_id: r.paper_id})
        MATCH (e:Equation {eq_id:    r.eq_id})
        MERGE (p)-[:HAS_EQUATION]->(e)
        """
        self._batch_write("Paper→Equation edges", cypher, rows)

    def write_figure_mentions_edges(self, rows: list[dict]) -> None:
        """ScientificFigure -[:FIGURE_MENTIONS]-> Method|Sensor|Metric."""
        for label in ("Method", "Sensor", "Metric"):
            subset = [r for r in rows if r.get("node_label") == label]
            if not subset:
                continue
            cypher = f"""
            UNWIND $rows AS r
            MATCH (f:ScientificFigure {{fig_id:       r.fig_id}})
            MATCH (e:{label}          {{canonical_id: r.canonical_id}})
            MERGE (f)-[rel:FIGURE_MENTIONS]->(e)
            SET rel.evidence = r.evidence
            """
            self._batch_write(f"Figure→{label} FIGURE_MENTIONS", cypher, subset)

    def write_table_reports_edges(self, rows: list[dict]) -> None:
        """ScientificTable -[:TABLE_REPORTS]-> Metric."""
        cypher = """
        UNWIND $rows AS r
        MATCH (t:ScientificTable {table_id:    r.table_id})
        MATCH (m:Metric          {canonical_id: r.canonical_id})
        MERGE (t)-[rel:TABLE_REPORTS]->(m)
        SET rel.confidence = r.confidence
        """
        self._batch_write("Table→Metric TABLE_REPORTS", cypher, rows)

    def write_equation_grounds_to_edges(self, rows: list[dict]) -> None:
        """Equation -[:EQUATION_GROUNDS_TO]-> Method."""
        cypher = """
        UNWIND $rows AS r
        MATCH (e:Equation {eq_id:        r.eq_id})
        MATCH (m:Method   {canonical_id: r.canonical_id})
        MERGE (e)-[rel:EQUATION_GROUNDS_TO]->(m)
        SET rel.confidence = r.confidence
        """
        self._batch_write("Equation→Method EQUATION_GROUNDS_TO", cypher, rows)

    # ── NumericFact writers (Stage 5c) ────────────────────────────────────────

    def write_numeric_facts(self, rows: list[dict]) -> None:
        """MERGE NumericFact nodes with full scientific provenance."""
        cypher = """
        UNWIND $rows AS r
        MERGE (f:NumericFact {fact_id: r.fact_id})
        SET f.paper_id        = r.paper_id,
            f.table_id        = r.table_id,
            f.table_label     = r.table_label,
            f.page            = r.page,
            f.column_context  = r.column_context,
            f.col_header      = r.col_header,
            f.row_context     = r.row_context,
            f.raw_cell        = r.raw_cell,
            f.metric          = r.metric,
            f.canonical_id    = r.canonical_id,
            f.node_label      = r.node_label,
            f.value           = r.value,
            f.unit            = r.unit,
            f.confidence      = r.confidence,
            f.source          = r.source,
            f.period_type     = r.period_type
        """
        self._batch_write("NumericFact nodes", cypher, rows)

    def write_paper_numeric_fact_edges(self, rows: list[dict]) -> None:
        """Paper -[:HAS_NUMERIC_FACT]-> NumericFact."""
        cypher = """
        UNWIND $rows AS r
        MATCH (p:Paper       {paper_id: r.paper_id})
        MATCH (f:NumericFact {fact_id:  r.fact_id})
        MERGE (p)-[:HAS_NUMERIC_FACT]->(f)
        """
        self._batch_write("Paper→NumericFact HAS_NUMERIC_FACT", cypher, rows)

    def write_numeric_fact_measures_edges(self, rows: list[dict]) -> None:
        """NumericFact -[:MEASURES]-> Metric | Method."""
        for label in ("Metric", "Method"):
            subset = [r for r in rows if r["node_label"] == label]
            if not subset:
                continue
            cypher = f"""
            UNWIND $rows AS r
            MATCH (f:NumericFact {{fact_id:      r.fact_id}})
            MATCH (t:{label}     {{canonical_id: r.canonical_id}})
            MERGE (f)-[rel:MEASURES]->(t)
            SET rel.confidence = r.confidence
            """
            self._batch_write(f"NumericFact→{label} MEASURES", cypher, subset)

    # ── Trust-aware graph queries ─────────────────────────────────────────────

    def paper_method_path_scores(
        self,
        min_confidence: float = 0.70,
        limit: int = 100,
    ) -> list[dict]:
        """
        Return (paper_id, canonical_id, path_confidence) rows for Paper→Method
        paths where confidence >= min_confidence.

        path_confidence = edge.confidence  (single-hop; extend to multi-hop below)
        """
        cypher = """
        MATCH (p:Paper)-[e:USES_METHOD]->(m:Method)
        WHERE e.confidence >= $min_conf
        RETURN p.paper_id       AS paper_id,
               m.canonical_id  AS canonical_id,
               m.display_name  AS method_name,
               e.confidence    AS path_confidence,
               e.surface_form  AS surface_form,
               e.evidence      AS evidence,
               e.resolver_version AS resolver_version
        ORDER BY path_confidence DESC
        LIMIT $limit
        """
        with self._driver.session() as s:
            result = s.run(cypher, min_conf=min_confidence, limit=limit)
            return [dict(r) for r in result]

    def reasoning_chain_score(
        self,
        start_paper_id: str,
        end_canonical_id: str,
    ) -> list[dict]:
        """
        Find all paths Paper→Method→...→Entity and return each path with its
        chain confidence = product of all edge confidences along the path.

        Cypher uses apoc.algo.allSimplePaths if APOC is available, otherwise
        falls back to a 2-hop pattern.
        """
        cypher = """
        MATCH path =
            (p:Paper {paper_id: $paper_id})
            -[e1:USES_METHOD]->(m:Method)
            -[e2:COMMONLY_USED_WITH*0..1]->(x)
        WHERE x.canonical_id = $target_id
           OR m.canonical_id = $target_id
        WITH path,
             reduce(s = 1.0, e IN relationships(path) |
                 s * coalesce(e.confidence, 1.0)
             ) AS chain_confidence,
             [e IN relationships(path) | e.evidence] AS all_evidence
        RETURN chain_confidence,
               all_evidence,
               length(path) AS hops
        ORDER BY chain_confidence DESC
        LIMIT 20
        """
        with self._driver.session() as s:
            result = s.run(cypher, paper_id=start_paper_id, target_id=end_canonical_id)
            return [dict(r) for r in result]

    def trust_tier_summary(self) -> dict[str, int]:
        """
        Count edges by trust tier:
          high   confidence >= 0.80
          medium confidence >= 0.60
          low    confidence <  0.60
        """
        cypher = """
        MATCH ()-[e:USES_METHOD|USES_SENSOR|REPORTS_METRIC]->()
        WITH e.confidence AS c
        RETURN
            sum(CASE WHEN c >= 0.80 THEN 1 ELSE 0 END) AS high,
            sum(CASE WHEN c >= 0.60 AND c < 0.80 THEN 1 ELSE 0 END) AS medium,
            sum(CASE WHEN c < 0.60 THEN 1 ELSE 0 END) AS low
        """
        with self._driver.session() as s:
            r = s.run(cypher).single()
            return {"high": r["high"], "medium": r["medium"], "low": r["low"]} if r else {}

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _batch_write(self, label: str, cypher: str, rows: list[dict]) -> None:
        if not rows:
            log.debug("  %s — 0 rows, skipping.", label)
            return
        total = len(rows)
        written = 0
        with self._driver.session() as session:
            for i in range(0, total, _BATCH):
                chunk = rows[i : i + _BATCH]
                session.execute_write(lambda tx, c=chunk: tx.run(cypher, rows=c))
                written += len(chunk)
        log.info("  %-40s  %6d rows", label, written)

    def verify_connection(self) -> bool:
        try:
            with self._driver.session() as s:
                result = s.run("RETURN 1 AS ok").single()
                return result and result["ok"] == 1
        except Exception as exc:
            log.error("Neo4j connection failed: %s", exc)
            return False

    def node_counts(self) -> dict[str, int]:
        labels = [
            "Paper", "Author", "Institution", "Topic",
            "Method", "Sensor", "Metric", "Country", "FloodEvent",
            # Visual layer
            "ScientificFigure", "ScientificTable", "Equation",
            # Numeric facts
            "NumericFact",
        ]
        counts = {}
        with self._driver.session() as s:
            for label in labels:
                r = s.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()
                counts[label] = r["c"] if r else 0
        return counts

    def rel_counts(self) -> dict[str, int]:
        rel_types = [
            "USES_METHOD", "USES_SENSOR", "REPORTS_METRIC", "HAS_TOPIC",
            "FROM_COUNTRY", "REFERENCES", "CITES", "AUTHORED", "AFFILIATED_WITH",
            "LOCATED_IN", "CO_OCCURS_WITH", "COMMONLY_USED_WITH", "INVESTIGATES",
            # Visual layer
            "HAS_FIGURE", "HAS_TABLE", "HAS_EQUATION",
            "FIGURE_MENTIONS", "TABLE_REPORTS", "EQUATION_GROUNDS_TO",
            # Numeric facts
            "HAS_NUMERIC_FACT", "MEASURES",
        ]
        counts = {}
        with self._driver.session() as s:
            for rt in rel_types:
                r = s.run(f"MATCH ()-[r:{rt}]->() RETURN count(r) AS c").single()
                counts[rt] = r["c"] if r else 0
        return counts
