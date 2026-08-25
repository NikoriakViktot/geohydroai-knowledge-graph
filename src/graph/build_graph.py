"""
build_graph.py  —  Stage 5 CLI orchestrator for the GeoHydroAI knowledge graph.

Loads all nodes and edges from Stage 2/3 outputs (enriched JSON + parquet),
writes them to Neo4j in dependency order, then generates a summary JSON.

Usage
-----
    python -m src.graph.build_graph [OPTIONS]

Options
-------
    --uri       NEO4J_URI      bolt://localhost:7687
    --user      NEO4J_USER     neo4j
    --password  NEO4J_PASSWORD (required; set in .env — no default)
    --wipe                     Wipe graph before ingestion (destructive)
    --limit N                  Cap enriched-JSON scan at N files (dev/testing)
    --skip-stats               Skip graph_statistics generation at end
    --log-level LEVEL          DEBUG | INFO | WARNING  (default: INFO)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

# ── dotenv (optional) ─────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed; rely on environment variables directly

from src.graph.neo4j_writer import GraphWriter
from src.graph import graph_loader as loader

log = logging.getLogger("geohydro.graph.build")


# ── CLI ────────────────────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="build_graph",
        description="Build the Stage 5 GeoHydroAI knowledge graph in Neo4j.",
    )
    p.add_argument("--uri",       default=os.getenv("NEO4J_URI",      "bolt://localhost:7687"))
    p.add_argument("--user",      default=os.getenv("NEO4J_USER",     "neo4j"))
    p.add_argument("--password",  default=os.getenv("NEO4J_PASSWORD"))
    p.add_argument("--wipe",      action="store_true",
                   help="Delete ALL nodes and relationships before ingestion.")
    p.add_argument("--limit",     type=int, default=None, metavar="N",
                   help="Scan only the first N enriched JSON files (dev mode).")
    p.add_argument("--skip-stats", action="store_true",
                   help="Skip graph_statistics generation.")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args(argv)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _rows(dataclass_list) -> list[dict]:
    """Convert a list of dataclass instances to plain dicts for the writer."""
    return [asdict(obj) for obj in dataclass_list]


def _phase(label: str) -> None:
    log.info("")
    log.info("══ %s ══", label)


def _elapsed(t0: float) -> str:
    secs = time.perf_counter() - t0
    return f"{secs:.1f}s"


# ── Main pipeline ──────────────────────────────────────────────────────────────

def build(args: argparse.Namespace) -> None:
    t_total = time.perf_counter()

    with GraphWriter(uri=args.uri, user=args.user, password=args.password) as gw:

        if not gw.verify_connection():
            log.error("Cannot reach Neo4j at %s — aborting.", args.uri)
            sys.exit(1)

        # ── 0. Optional wipe ──────────────────────────────────────────────────
        if args.wipe:
            _phase("WIPE")
            log.warning("Wiping all graph data …")
            gw.wipe()

        # ── 1. Schema: constraints & indexes ─────────────────────────────────
        _phase("SCHEMA")
        gw.create_constraints()

        # ── 2. Load parquet-derived data ──────────────────────────────────────
        _phase("LOADING PARQUET")

        t = time.perf_counter()
        log.info("Loading Paper nodes …")
        paper_rows = loader.load_paper_nodes()
        log.info("  %d papers  (%s)", len(paper_rows), _elapsed(t))

        t = time.perf_counter()
        log.info("Loading Author nodes …")
        author_rows = loader.load_author_nodes()
        log.info("  %d authors  (%s)", len(author_rows), _elapsed(t))

        t = time.perf_counter()
        log.info("Loading Institution nodes …")
        institution_rows = loader.load_institution_nodes()
        log.info("  %d institutions  (%s)", len(institution_rows), _elapsed(t))

        t = time.perf_counter()
        log.info("Loading Topic nodes …")
        topic_rows = loader.load_topic_nodes()
        log.info("  %d topics  (%s)", len(topic_rows), _elapsed(t))

        t = time.perf_counter()
        log.info("Loading Method nodes …")
        method_rows = loader.load_method_nodes()
        log.info("  %d methods  (%s)", len(method_rows), _elapsed(t))

        t = time.perf_counter()
        log.info("Loading Sensor nodes …")
        sensor_rows = loader.load_sensor_nodes()
        log.info("  %d sensors  (%s)", len(sensor_rows), _elapsed(t))

        t = time.perf_counter()
        log.info("Loading Metric nodes …")
        metric_rows = loader.load_metric_nodes()
        log.info("  %d metrics  (%s)", len(metric_rows), _elapsed(t))

        log.info("Deriving Country nodes …")
        country_rows = loader.load_country_nodes(paper_rows, institution_rows)
        log.info("  %d countries", len(country_rows))

        # ── 3. Write nodes ────────────────────────────────────────────────────
        _phase("WRITING NODES")

        gw.write_papers(paper_rows)
        gw.write_authors(author_rows)
        gw.write_institutions(institution_rows)
        gw.write_topics(topic_rows)
        gw.write_methods(method_rows)
        gw.write_sensors(sensor_rows)
        gw.write_metrics(metric_rows)
        gw.write_countries(country_rows)

        flood_event_rows = loader.load_flood_event_nodes()  # already returns dicts
        gw.write_flood_events(flood_event_rows)
        log.info("  %d flood events", len(flood_event_rows))

        # ── 4. Write parquet-derived edges ────────────────────────────────────
        _phase("WRITING PARQUET EDGES")

        t = time.perf_counter()
        author_paper_rows = loader.load_author_paper_edges()
        gw.write_author_paper_edges(author_paper_rows)
        log.info("  Author→Paper  (%s)", _elapsed(t))

        t = time.perf_counter()
        paper_topic_rows = loader.load_paper_topic_edges()
        gw.write_paper_topic_edges(paper_topic_rows)
        log.info("  Paper→Topic  (%s)", _elapsed(t))

        t = time.perf_counter()
        paper_country_rows = loader.load_paper_country_edges(paper_rows)
        gw.write_paper_country_edges(paper_country_rows)
        log.info("  Paper→Country  (%s)", _elapsed(t))

        t = time.perf_counter()
        inst_country_rows = loader.load_institution_country_edges(institution_rows)
        gw.write_institution_country_edges(inst_country_rows)
        log.info("  Institution→Country  (%s)", _elapsed(t))

        # ── 5. Load + write enriched-JSON edges ───────────────────────────────
        _phase("LOADING ENRICHED JSON EDGES")

        t = time.perf_counter()
        log.info("Scanning enriched JSONs (limit=%s) …", args.limit or "all")
        pm_edges, ps_edges, pmet_edges, ai_edges = loader.load_entity_edges_from_enriched(
            limit=args.limit
        )
        log.info(
            "  paper→method=%d  paper→sensor=%d  paper→metric=%d  "
            "author→institution=%d  (%s)",
            len(pm_edges), len(ps_edges), len(pmet_edges), len(ai_edges),
            _elapsed(t),
        )

        _phase("WRITING ENRICHED EDGES")

        gw.write_paper_method_edges(pm_edges)
        gw.write_paper_sensor_edges(ps_edges)
        gw.write_paper_metric_edges(pmet_edges)
        gw.write_author_institution_edges(ai_edges)

        # ── 6. Within-corpus REFERENCES edges ────────────────────────────────
        _phase("WITHIN-CORPUS REFERENCES")

        t = time.perf_counter()
        ref_rows = loader.load_within_corpus_references(limit=args.limit)
        log.info("  %d resolvable REFERENCES edges  (%s)", len(ref_rows), _elapsed(t))
        gw.write_paper_reference_edges(ref_rows)

        # ── 7. Co-occurrence edges ────────────────────────────────────────────
        _phase("CO-OCCURRENCE EDGES")

        t = time.perf_counter()
        mm_edges, ss_edges, ms_edges = loader.compute_cooccurrence_edges(
            pm_edges, ps_edges, threshold=3
        )
        log.info(
            "  method↔method=%d  sensor↔sensor=%d  method→sensor=%d  (%s)",
            len(mm_edges), len(ss_edges), len(ms_edges), _elapsed(t),
        )

        gw.write_method_cooccurrence_edges(mm_edges)
        gw.write_sensor_cooccurrence_edges(ss_edges)
        gw.write_method_sensor_edges(ms_edges)

        # ── 8. Paper↔FloodEvent edges ─────────────────────────────────────────
        _phase("FLOOD EVENT EDGES")

        flood_paper_rows = loader.load_paper_flood_event_edges(paper_rows)
        gw.write_paper_flood_event_edges(flood_paper_rows)
        log.info("  %d Paper→FloodEvent edges", len(flood_paper_rows))

        # ── 9. Bibliography CITES edges (stubs for unprocessed references) ────
        _phase("CITES EDGES")

        t = time.perf_counter()
        cites_rows = loader.load_cites_edges(limit=args.limit)
        log.info("  %d CITES edge candidates  (%s)", len(cites_rows), _elapsed(t))
        gw.write_cites_edges(cites_rows)

        # ── 10. Final counts ──────────────────────────────────────────────────
        _phase("VERIFICATION")

        node_counts = gw.node_counts()
        rel_counts  = gw.rel_counts()
        log.info("Node counts:")
        for label, cnt in node_counts.items():
            log.info("  %-20s %8d", label, cnt)
        log.info("Relationship counts:")
        for rtype, cnt in rel_counts.items():
            log.info("  %-30s %8d", rtype, cnt)

    # ── 11. Statistics JSON ───────────────────────────────────────────────────
    if not args.skip_stats:
        _phase("STATISTICS")
        try:
            from src.graph.graph_statistics import generate_summary
            summary = generate_summary(
                uri=args.uri, user=args.user, password=args.password
            )
            log.info(
                "Summary: %d nodes, %d relationships → data/graph/graph_summary.json",
                summary["totals"]["nodes"],
                summary["totals"]["relationships"],
            )
        except Exception as exc:
            log.warning("Statistics generation failed (non-fatal): %s", exc)

    # ── Done ──────────────────────────────────────────────────────────────────
    _phase("COMPLETE")
    log.info("Total wall time: %s", _elapsed(t_total))


# ── Entry point ────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    build(args)


if __name__ == "__main__":
    main()
