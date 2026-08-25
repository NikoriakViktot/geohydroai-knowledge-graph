"""
graph_queries.py  —  Named Cypher query library for the GeoHydroAI knowledge graph.

Each function returns a (cypher_string, params_dict) tuple ready to pass to
driver.session().run(cypher, **params).

These are read-only analytical queries; none modify the graph.
"""
from __future__ import annotations

from typing import Any

# ── Type alias ─────────────────────────────────────────────────────────────────
CypherQuery = tuple[str, dict[str, Any]]


# ── 1. Top methods in SAR flood mapping ───────────────────────────────────────

def top_methods_in_sar_flooding(limit: int = 15) -> CypherQuery:
    """
    Papers that use a SAR/Radar sensor AND belong to the Flood Risk topic,
    ranked by the methods they employ.
    """
    cypher = """
    MATCH (p:Paper)-[:USES_SENSOR]->(s:Sensor {family: 'SAR / Radar'})
    MATCH (p)-[:HAS_TOPIC]->(t:Topic)
    WHERE t.topic_name CONTAINS 'Flood'
    MATCH (p)-[:USES_METHOD]->(m:Method)
    RETURN m.display_name  AS method,
           m.family        AS family,
           count(p)        AS paper_count
    ORDER BY paper_count DESC
    LIMIT $limit
    """
    return cypher, {"limit": limit}


# ── 2. Most influential flood papers (by citations) ───────────────────────────

def most_influential_flood_papers(limit: int = 20) -> CypherQuery:
    """
    Papers tagged with the Flood Risk topic, ranked by citation count.
    Returns title, year, country, journal, and which methods they use.
    """
    cypher = """
    MATCH (p:Paper)-[:HAS_TOPIC]->(t:Topic)
    WHERE t.topic_name CONTAINS 'Flood'
    OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method)
    OPTIONAL MATCH (p)-[:FROM_COUNTRY]->(c:Country)
    RETURN p.title         AS title,
           p.year          AS year,
           p.cited_by_count AS citations,
           p.doi           AS doi,
           c.name          AS country,
           collect(DISTINCT m.display_name) AS methods
    ORDER BY citations DESC
    LIMIT $limit
    """
    return cypher, {"limit": limit}


# ── 3. Papers using U-Net with Sentinel-1 ─────────────────────────────────────

def unet_sentinel1_papers(limit: int = 25) -> CypherQuery:
    """
    Papers that simultaneously use an ML/AI method AND a SAR/Radar sensor,
    filtered to the canonical IDs for ANN / ML / Random Forest methods and
    radar-family sensors.  (U-Net is not yet a distinct canonical_id; the
    query uses the family 'ML / AI' as a proxy.)
    """
    cypher = """
    MATCH (p:Paper)-[:USES_METHOD]->(m:Method {family: 'ML / AI'})
    MATCH (p)-[:USES_SENSOR]->(s:Sensor {family: 'SAR / Radar'})
    OPTIONAL MATCH (p)-[:FROM_COUNTRY]->(c:Country)
    RETURN p.title         AS title,
           p.year          AS year,
           p.cited_by_count AS citations,
           m.display_name  AS method,
           s.display_name  AS sensor,
           c.name          AS country
    ORDER BY p.cited_by_count DESC
    LIMIT $limit
    """
    return cypher, {"limit": limit}


# ── 4. Flood studies in Ukraine ────────────────────────────────────────────────

def flood_studies_in_country(country: str = "Ukraine", limit: int = 30) -> CypherQuery:
    """
    Papers from a specific country tagged with the Flood Risk topic,
    with their methods and sensors.
    """
    cypher = """
    MATCH (p:Paper)-[:FROM_COUNTRY]->(c:Country {name: $country})
    MATCH (p)-[:HAS_TOPIC]->(t:Topic)
    WHERE t.topic_name CONTAINS 'Flood'
    OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method)
    OPTIONAL MATCH (p)-[:USES_SENSOR]->(s:Sensor)
    RETURN p.title         AS title,
           p.year          AS year,
           p.cited_by_count AS citations,
           collect(DISTINCT m.display_name) AS methods,
           collect(DISTINCT s.display_name) AS sensors
    ORDER BY p.cited_by_count DESC
    LIMIT $limit
    """
    return cypher, {"country": country, "limit": limit}


# ── 5. Most common sensor–method combinations ─────────────────────────────────

def top_sensor_method_combinations(limit: int = 20) -> CypherQuery:
    """
    Sensor–method pairs ranked by the number of papers that use both.
    Uses the direct COMMONLY_USED_WITH edge (pre-computed co-occurrence).
    Falls back to computing it from USES_SENSOR / USES_METHOD if the edge
    doesn't exist yet.
    """
    cypher = """
    MATCH (m:Method)-[e:COMMONLY_USED_WITH]->(s:Sensor)
    RETURN m.display_name AS method,
           m.family       AS method_family,
           s.display_name AS sensor,
           s.family       AS sensor_family,
           e.count        AS co_occurrence_count
    ORDER BY co_occurrence_count DESC
    LIMIT $limit
    """
    return cypher, {"limit": limit}


def top_sensor_method_combinations_computed(limit: int = 20) -> CypherQuery:
    """Fallback: compute sensor–method co-occurrence from edge traversal."""
    cypher = """
    MATCH (p:Paper)-[:USES_METHOD]->(m:Method)
    MATCH (p)-[:USES_SENSOR]->(s:Sensor)
    RETURN m.display_name AS method,
           s.display_name AS sensor,
           count(p)       AS papers
    ORDER BY papers DESC
    LIMIT $limit
    """
    return cypher, {"limit": limit}


# ── 6. Citation lineage of HAND-based flood mapping ──────────────────────────

def citation_lineage(canonical_id: str = "method.hand", hops: int = 2, limit: int = 50) -> CypherQuery:
    """
    Papers using a specific method, their downstream citations, up to `hops` hops.
    Returns the ego-network around HAND-based papers.
    """
    cypher = """
    MATCH (seed:Paper)-[:USES_METHOD]->(m:Method {canonical_id: $cid})
    CALL {
        WITH seed
        MATCH path = (seed)-[:REFERENCES*1..%d]->(cited:Paper)
        RETURN cited
        UNION
        MATCH path = (citing:Paper)-[:REFERENCES*1..%d]->(seed)
        RETURN citing AS cited
    }
    RETURN DISTINCT
        seed.title         AS seed_title,
        seed.year          AS seed_year,
        cited.title        AS cited_title,
        cited.year         AS cited_year,
        cited.cited_by_count AS cited_citations
    ORDER BY cited_citations DESC
    LIMIT $limit
    """ % (hops, hops)
    return cypher, {"cid": canonical_id, "limit": limit}


# ── 7. Topic evolution over time ──────────────────────────────────────────────

def topic_evolution(topic_contains: str = "Flood", start_year: int = 2010) -> CypherQuery:
    """
    Count papers per year for a topic substring match.
    Reveals the growth trajectory of flood research, SAR, AI forecasting, etc.
    """
    cypher = """
    MATCH (p:Paper)-[:HAS_TOPIC]->(t:Topic)
    WHERE t.topic_name CONTAINS $topic
      AND p.year IS NOT NULL
      AND p.year >= $start_year
    RETURN p.year      AS year,
           t.topic_name AS topic,
           count(p)    AS papers
    ORDER BY year, papers DESC
    """
    return cypher, {"topic": topic_contains, "start_year": start_year}


# ── 8. Sensor dominance by country ───────────────────────────────────────────

def sensor_dominance_by_country(limit: int = 30) -> CypherQuery:
    """
    Which sensors are most used per country, ordered by usage count.
    Useful for identifying regional satellite preferences.
    """
    cypher = """
    MATCH (p:Paper)-[:FROM_COUNTRY]->(c:Country)
    MATCH (p)-[:USES_SENSOR]->(s:Sensor)
    RETURN c.name          AS country,
           s.display_name  AS sensor,
           s.family        AS sensor_family,
           count(p)        AS papers
    ORDER BY papers DESC
    LIMIT $limit
    """
    return cypher, {"limit": limit}


# ── 9. Method co-occurrence network (for Cytoscape / graph viz) ───────────────

def method_cooccurrence_network(min_count: int = 3) -> CypherQuery:
    """
    Return all Method nodes and their CO_OCCURS_WITH edges above the threshold.
    Used to drive the citation/method co-occurrence network visualisation.
    """
    cypher = """
    MATCH (a:Method)-[e:CO_OCCURS_WITH]->(b:Method)
    WHERE e.count >= $min_count
    RETURN a.canonical_id  AS source_id,
           a.display_name  AS source_name,
           a.family        AS source_family,
           b.canonical_id  AS target_id,
           b.display_name  AS target_name,
           b.family        AS target_family,
           e.count         AS weight
    ORDER BY weight DESC
    """
    return cypher, {"min_count": min_count}


# ── 10. Top cited authors in flood remote sensing ─────────────────────────────

def top_flood_authors(limit: int = 20) -> CypherQuery:
    """
    Authors ranked by how many flood-topic papers they wrote and their
    average citation count across those papers.
    """
    cypher = """
    MATCH (a:Author)-[:AUTHORED]->(p:Paper)-[:HAS_TOPIC]->(t:Topic)
    WHERE t.topic_name CONTAINS 'Flood'
    OPTIONAL MATCH (a)-[:AFFILIATED_WITH]->(i:Institution)
    RETURN a.display_name               AS author,
           i.display_name               AS institution,
           i.country_code               AS country_code,
           count(DISTINCT p)            AS flood_papers,
           avg(p.cited_by_count)        AS avg_citations
    ORDER BY flood_papers DESC, avg_citations DESC
    LIMIT $limit
    """
    return cypher, {"limit": limit}


# ── Convenience runner ─────────────────────────────────────────────────────────

def run_query(driver, query_fn, **kwargs) -> list[dict]:
    """Execute a named query function and return results as a list of dicts."""
    cypher, params = query_fn(**kwargs)
    with driver.session() as session:
        result = session.run(cypher, **params)
        return [dict(record) for record in result]
