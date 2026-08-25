"""
graph_statistics.py  —  Generate a summary JSON snapshot of the Stage 5 graph.

Connects to Neo4j, queries node/edge counts plus top-N ranked lists, and
writes the result to data/graph/graph_summary.json.  Safe to call repeatedly;
the JSON file is overwritten each run.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.graph.neo4j_writer import GraphWriter
from src.graph.graph_queries import (
    top_methods_in_sar_flooding,
    most_influential_flood_papers,
    top_sensor_method_combinations_computed,
    topic_evolution,
    sensor_dominance_by_country,
    top_flood_authors,
)

log = logging.getLogger("geohydro.graph.statistics")

_OUTPUT = Path("data/graph/graph_summary.json")


def _run(session, cypher: str, params: dict | None = None) -> list[dict]:
    result = session.run(cypher, **(params or {}))
    return [dict(r) for r in result]


def generate_summary(
    uri:      str | None = None,
    user:     str | None = None,
    password: str | None = None,
    out_path: Path = _OUTPUT,
) -> dict:
    """
    Query the live graph and return a summary dict.  Also persists to ``out_path``.
    """
    with GraphWriter(uri=uri, user=user, password=password) as gw:
        if not gw.verify_connection():
            raise RuntimeError("Cannot connect to Neo4j — check URI/credentials.")

        log.info("Collecting node and relationship counts …")
        node_counts = gw.node_counts()
        rel_counts  = gw.rel_counts()

        with gw._driver.session() as s:
            log.info("Querying top methods in SAR flooding …")
            top_sar_methods = _run(s, *top_methods_in_sar_flooding(limit=10))

            log.info("Querying most-cited flood papers …")
            top_papers = _run(s, *most_influential_flood_papers(limit=10))

            log.info("Querying top sensor-method pairs …")
            top_pairs = _run(s, *top_sensor_method_combinations_computed(limit=10))

            log.info("Querying top flood authors …")
            top_authors = _run(s, *top_flood_authors(limit=10))

            log.info("Querying sensor dominance by country …")
            top_country_sensors = _run(s, *sensor_dominance_by_country(limit=15))

            log.info("Querying topic evolution (Flood, 2010–) …")
            flood_evolution = _run(s, *topic_evolution("Flood", 2010))

            log.info("Querying topic evolution (SAR, 2010–) …")
            sar_evolution = _run(s, *topic_evolution("SAR", 2010))

            # Top countries by paper count
            top_countries = _run(s, """
                MATCH (p:Paper)-[:FROM_COUNTRY]->(c:Country)
                RETURN c.name AS country, count(p) AS papers
                ORDER BY papers DESC LIMIT 15
            """)

            # Top method families
            method_families = _run(s, """
                MATCH (m:Method)
                WHERE m.family IS NOT NULL
                RETURN m.family AS family, sum(m.paper_count) AS total_papers
                ORDER BY total_papers DESC
            """)

            # Top sensor families
            sensor_families = _run(s, """
                MATCH (s:Sensor)
                WHERE s.family IS NOT NULL
                RETURN s.family AS family, sum(s.paper_count) AS total_papers
                ORDER BY total_papers DESC
            """)

            # Flood papers per year
            flood_per_year = _run(s, """
                MATCH (p:Paper)-[:HAS_TOPIC]->(t:Topic)
                WHERE t.topic_name CONTAINS 'Flood'
                  AND p.year IS NOT NULL AND p.year >= 2000
                RETURN p.year AS year, count(p) AS papers
                ORDER BY year
            """)

    # ── Serialisation helpers ─────────────────────────────────────────────────
    def _clean(rows: list[dict]) -> list[dict]:
        """Convert any non-JSON-serialisable values (e.g. neo4j lists) to plain types."""
        out = []
        for row in rows:
            clean = {}
            for k, v in row.items():
                if hasattr(v, "__iter__") and not isinstance(v, (str, list, dict)):
                    clean[k] = list(v)
                elif isinstance(v, float) and v != v:   # NaN
                    clean[k] = None
                else:
                    clean[k] = v
            out.append(clean)
        return out

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "node_counts":  node_counts,
        "rel_counts":   rel_counts,
        "totals": {
            "nodes": sum(node_counts.values()),
            "relationships": sum(rel_counts.values()),
        },
        "top_sar_methods":      _clean(top_sar_methods),
        "top_flood_papers":     _clean(top_papers),
        "top_sensor_method_pairs": _clean(top_pairs),
        "top_flood_authors":    _clean(top_authors),
        "top_countries":        _clean(top_countries),
        "top_country_sensors":  _clean(top_country_sensors),
        "method_families":      _clean(method_families),
        "sensor_families":      _clean(sensor_families),
        "flood_papers_per_year": _clean(flood_per_year),
        "flood_topic_evolution": _clean(flood_evolution),
        "sar_topic_evolution":   _clean(sar_evolution),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    log.info("Graph summary written → %s", out_path)

    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    s = generate_summary()
    print(f"Nodes : {s['totals']['nodes']:,}")
    print(f"Edges : {s['totals']['relationships']:,}")
    for label, cnt in s["node_counts"].items():
        print(f"  {label:<20} {cnt:>8,}")
