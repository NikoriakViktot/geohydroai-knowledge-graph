"""Read-only access to the Neo4j projection (docs/api/endpoints/graph.md).

* every query runs in a managed READ transaction (the server refuses writes) with a
  10 s timeout and a row limit;
* Cypher text is static: user input is passed as parameters only, never interpolated.
  Variable-length paths (citation lineage) have one static text per hop count;
* ad-hoc Cypher (`/graph/cypher`, admin) is EXPLAINed first and refused unless the
  planner classifies it as read-only.
"""

from __future__ import annotations

import base64
import json
import math
import re
from dataclasses import dataclass

TIMEOUT_S = 10.0
MAX_ROWS = 1000


class QueryTimeout(Exception):
    pass


class NotReadOnly(Exception):
    pass


class BadParams(ValueError):
    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in errors))
        self.errors = errors


_DRIVER = None


def driver():
    global _DRIVER
    if _DRIVER is None:
        from neo4j import GraphDatabase

        from src.config import settings
        _DRIVER = GraphDatabase.driver(settings.NEO4J_URI,
                                       auth=(settings.NEO4J_USER, settings.require_env("NEO4J_PASSWORD")))
    return _DRIVER


def _plain(value):
    """Neo4j values → JSON-friendly values."""
    from neo4j.graph import Node, Path, Relationship
    if isinstance(value, Node):
        return {"labels": sorted(value.labels), **dict(value)}
    if isinstance(value, Relationship):
        return {"type": value.type, **dict(value)}
    if isinstance(value, Path):
        return {"nodes": [_plain(n) for n in value.nodes], "relationships": [_plain(r) for r in value.relationships]}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, float) and math.isnan(value):
        return None                      # parquet NaN stored as a property
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def mention_in_evidence(surface: str | None, evidence: list[str] | None) -> bool | None:
    """Does an evidence snippet contain the surface form as a word (no letter or digit either
    side, so "HAND-based" counts and "empirical" is not "iRIC"; case-sensitive for acronyms
    such as HAND, so that "on the other hand" does not count)? None when
    there is nothing to check. On 2026-10-02 only 30 % of USES_METHOD and 38 % of
    USES_SENSOR snippets did."""
    if not surface or not evidence:
        return None
    flags = 0 if surface.isupper() else re.IGNORECASE
    pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(surface)}(?![A-Za-z0-9])", flags)
    return any(pattern.search(e or "") for e in evidence)


def run_read(query: str, params: dict | None = None, limit: int = MAX_ROWS) -> tuple[list[str], list[list], bool]:
    """(columns, rows, truncated) of a read-only query."""
    from neo4j import unit_of_work
    from neo4j.exceptions import ClientError

    @unit_of_work(timeout=TIMEOUT_S)
    def work(tx):
        result = tx.run(query, params or {})
        columns = list(result.keys())
        rows, truncated = [], False
        for i, record in enumerate(result):
            if i >= limit:
                truncated = True
                break
            rows.append([_plain(v) for v in record.values()])
        return columns, rows, truncated

    try:
        with driver().session() as s:
            return s.execute_read(work)
    except ClientError as exc:
        if "TransactionTimedOut" in (exc.code or "") or "Timeout" in (exc.code or ""):
            raise QueryTimeout(f"query exceeded {TIMEOUT_S:.0f} s") from exc
        raise


def records(query: str, params: dict | None = None, limit: int = MAX_ROWS) -> list[dict]:
    columns, rows, _ = run_read(query, params, limit)
    return [dict(zip(columns, r)) for r in rows]


# ── cursors ────────────────────────────────────────────────────────────────────

def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        pad = "=" * (-len(cursor) % 4)
        return max(0, int(json.loads(base64.urlsafe_b64decode(cursor + pad))["o"]))
    except Exception as exc:
        raise BadParams([{"loc": ["query", "cursor"], "msg": "not a cursor from this API", "type": "value_error"}]) from exc


# ── one paper ──────────────────────────────────────────────────────────────────

_PAPER_FIELDS = """p.paper_id AS paper_id, p.doi AS doi, p.title AS title, p.year AS year, p.journal AS venue,
       p.identity_status AS identity_status, coalesce(p.is_reference_stub, false) AS is_reference_stub,
       p.cited_by_count AS cited_by_count, p.openalex_id AS openalex_id"""

Q_PAPER = f"""// paper
MATCH (p:Paper) WHERE ($paper_id IS NOT NULL AND p.paper_id = $paper_id) OR ($doi IS NOT NULL AND p.doi = $doi)
RETURN {_PAPER_FIELDS}, p.study_type AS study_type, p.primary_country AS primary_country
ORDER BY p.is_reference_stub IS NULL DESC LIMIT 1"""

_MATCH_ONE = """MATCH (p:Paper) WHERE ($paper_id IS NOT NULL AND p.paper_id = $paper_id) OR ($doi IS NOT NULL AND p.doi = $doi)
WITH p ORDER BY p.is_reference_stub IS NULL DESC LIMIT 1"""

Q_AUTHORS = f"""// authors
{_MATCH_ONE}
MATCH (a:Author)-[r:AUTHORED]->(p)
OPTIONAL MATCH (a)-[:AFFILIATED_WITH]->(i:Institution)
RETURN a.display_name AS name, a.orcid AS orcid, r.position AS position, r.is_corresponding AS corresponding,
       collect(DISTINCT i.display_name)[..5] AS institutions
ORDER BY CASE r.position WHEN 'first' THEN 0 WHEN 'middle' THEN 1 ELSE 2 END, name"""

_ENTITY = """{match}
MATCH (p)-[r:{rel}]->(e:{label})
RETURN e.canonical_id AS canonical_id, e.display_name AS display_name, e.family AS family,
       r.confidence AS confidence, r.role AS role, r.surface_form AS surface_form, r.evidence AS evidence,
       r.page AS page, r.section AS section
ORDER BY CASE r.role WHEN 'used' THEN 0 ELSE 1 END, r.confidence DESC, canonical_id"""
Q_METHODS = "// methods\n" + _ENTITY.format(match=_MATCH_ONE, rel="USES_METHOD", label="Method")
Q_SENSORS = "// sensors\n" + _ENTITY.format(match=_MATCH_ONE, rel="USES_SENSOR", label="Sensor")
Q_METRICS = "// metrics\n" + _ENTITY.format(match=_MATCH_ONE, rel="REPORTS_METRIC", label="Metric")

Q_TOPICS = f"""// topics
{_MATCH_ONE}
MATCH (p)-[r:HAS_TOPIC]->(t:Topic)
RETURN t.topic_id AS topic_id, t.topic_name AS name, r.score AS score ORDER BY score DESC, name"""

Q_COUNTRIES = f"""// countries
{_MATCH_ONE}
OPTIONAL MATCH (p)-[:FROM_COUNTRY]->(c:Country)
OPTIONAL MATCH (p)-[:INVESTIGATES]->(f:FloodEvent)
RETURN collect(DISTINCT c.name) AS countries,
       collect(DISTINCT CASE WHEN f IS NULL THEN null ELSE {{name: f.name, year: f.year, country: f.country}} END) AS events"""

Q_FACTS = f"""// facts
{_MATCH_ONE}
MATCH (p)-[:HAS_NUMERIC_FACT]->(f:NumericFact)
RETURN f.fact_id AS fact_id, f.metric AS metric, f.canonical_id AS canonical_id, f.value AS value,
       f.raw_cell AS raw_cell, f.table_label AS table_label, f.row_context AS row_context,
       f.col_header AS col_header, f.page AS page, f.confidence AS confidence
ORDER BY f.table_id, f.fact_id LIMIT 500"""

Q_COUNTS = f"""// counts
{_MATCH_ONE}
OPTIONAL MATCH (p)-[:CITES]->(o:Paper)
WITH p, count(o) AS cites_out, count(CASE WHEN o.is_reference_stub IS NULL THEN 1 END) AS cites_corpus
OPTIONAL MATCH (i:Paper)-[:CITES]->(p) WHERE i.is_reference_stub IS NULL
RETURN cites_out, cites_corpus, count(i) AS cited_in_corpus"""

PAPER_PARTS = {"authors": Q_AUTHORS, "methods": Q_METHODS, "sensors": Q_SENSORS, "metrics": Q_METRICS,
               "topics": Q_TOPICS, "countries": Q_COUNTRIES, "facts": Q_FACTS}
DEFAULT_PARTS = ("authors", "methods", "sensors", "metrics", "topics", "countries")


def selector(doi_or_paper_id: str) -> dict:
    from src.services.identity import normalize_doi
    doi = normalize_doi(doi_or_paper_id)
    return {"doi": doi, "paper_id": None if doi else doi_or_paper_id}


def paper(doi_or_paper_id: str, parts: tuple[str, ...] = DEFAULT_PARTS) -> dict | None:
    sel = selector(doi_or_paper_id)
    head = records(Q_PAPER, sel, 1)
    if not head:
        return None
    out: dict = {"paper": head[0]}
    for part in parts:
        rows = records(PAPER_PARTS[part], sel, MAX_ROWS)
        if part == "countries":
            row = rows[0] if rows else {"countries": [], "events": []}
            out["countries"] = [c for c in row["countries"] if c]
            out["flood_events"] = [e for e in row["events"] if e]
        else:
            if part in ("methods", "sensors", "metrics"):
                rows = [dict(r, mention_in_evidence=mention_in_evidence(r.get("surface_form"), r.get("evidence")))
                        for r in rows]
            out[part] = rows
    counts = records(Q_COUNTS, sel, 1)
    out["counts"] = counts[0] if counts else {"cites_out": 0, "cites_corpus": 0, "cited_in_corpus": 0}
    return out


# ── citations ──────────────────────────────────────────────────────────────────

_CITED_ORDER = "ORDER BY coalesce(q.year, 0) DESC, coalesce(q.title, ''), coalesce(q.paper_id, q.doi, '')"
Q_CITES_OUT = f"""// cites_out
{_MATCH_ONE}
MATCH (p)-[:CITES]->(q:Paper) WHERE NOT $in_corpus_only OR q.is_reference_stub IS NULL
WITH q {_CITED_ORDER} SKIP $skip LIMIT $limit
RETURN {_PAPER_FIELDS.replace('p.', 'q.')}, 'out' AS direction"""
Q_CITES_IN = f"""// cites_in
{_MATCH_ONE}
MATCH (q:Paper)-[:CITES]->(p) WHERE q.is_reference_stub IS NULL
WITH q {_CITED_ORDER} SKIP $skip LIMIT $limit
RETURN {_PAPER_FIELDS.replace('p.', 'q.')}, 'in' AS direction"""
Q_CITES_COUNTS = f"""// cites_counts
{_MATCH_ONE}
OPTIONAL MATCH (p)-[:CITES]->(o:Paper)
WITH p, count(o) AS out, count(CASE WHEN o.is_reference_stub IS NULL THEN 1 END) AS out_corpus
OPTIONAL MATCH (i:Paper)-[:CITES]->(p) WHERE i.is_reference_stub IS NULL
RETURN out, out_corpus, count(i) AS in_corpus"""


def citations(doi_or_paper_id: str, direction: str, in_corpus_only: bool, limit: int, offset: int) -> dict | None:
    sel = selector(doi_or_paper_id)
    if not records(Q_PAPER, sel, 1):
        return None
    counts = records(Q_CITES_COUNTS, sel, 1)[0]
    total = {"out": counts["out_corpus"] if in_corpus_only else counts["out"], "in": counts["in_corpus"]}
    items: list[dict] = []
    if direction == "both":
        # outgoing first, then incoming, as one sequence
        n_out = total["out"]
        if offset < n_out:
            items += records(Q_CITES_OUT, {**sel, "in_corpus_only": in_corpus_only, "skip": offset, "limit": limit})
        rest = limit - len(items)
        if rest > 0:
            items += records(Q_CITES_IN, {**sel, "skip": max(0, offset - n_out), "limit": rest})
        n_total = n_out + total["in"]
    else:
        q = Q_CITES_OUT if direction == "out" else Q_CITES_IN
        items = records(q, {**sel, "in_corpus_only": in_corpus_only, "skip": offset, "limit": limit})
        n_total = total[direction]
    nxt = offset + len(items)
    return {"items": items, "next_cursor": encode_cursor(nxt) if nxt < n_total else None,
            "counts": {"out": counts["out"], "out_in_corpus": counts["out_corpus"], "in": counts["in_corpus"]}}


# ── entity → papers ────────────────────────────────────────────────────────────

_ENTITY_PAPERS = """// entity_papers:{label}
MATCH (p:Paper)-[r:{rel}]->(e:{label} {{{key}: $id}})
WHERE p.is_reference_stub IS NULL
  AND ($year_from IS NULL OR p.year >= $year_from) AND ($year_to IS NULL OR p.year <= $year_to)
  AND (r.confidence IS NULL OR r.confidence >= $min_confidence)
  AND ($role IS NULL OR r.role = $role)
WITH p, r ORDER BY coalesce(p.year, 0) DESC, p.paper_id
WITH collect({{p: p, r: r}}) AS hits
RETURN size(hits) AS total,
       [h IN hits[$skip..$skip + $limit] | {{
          paper_id: h.p.paper_id, doi: h.p.doi, title: h.p.title, year: h.p.year, venue: h.p.journal,
          identity_status: h.p.identity_status, is_reference_stub: false, cited_by_count: h.p.cited_by_count,
          openalex_id: h.p.openalex_id,
          confidence: h.r.confidence, role: h.r.role, surface_form: h.r.surface_form, evidence: h.r.evidence,
          page: h.r.page, score: h.r.score}}] AS items"""

ENTITY_LABELS = {
    "Method": ("USES_METHOD", "canonical_id"),
    "Sensor": ("USES_SENSOR", "canonical_id"),
    "Metric": ("REPORTS_METRIC", "canonical_id"),
    "Topic": ("HAS_TOPIC", "topic_id"),
    "Country": ("FROM_COUNTRY", "name"),
    "FloodEvent": ("INVESTIGATES", "name"),
}
Q_ENTITY_PAPERS = {label: _ENTITY_PAPERS.format(label=label, rel=rel, key=key)
                   for label, (rel, key) in ENTITY_LABELS.items()}
Q_CORPUS_SIZE = "// corpus_size\nMATCH (p:Paper) WHERE p.is_reference_stub IS NULL RETURN count(p) AS n"


def entity_papers(label: str, entity_id: str, *, year_from: int | None, year_to: int | None,
                  min_confidence: float, role: str | None, limit: int, offset: int) -> dict:
    rows = records(Q_ENTITY_PAPERS[label], {"id": entity_id, "year_from": year_from, "year_to": year_to,
                                            "min_confidence": min_confidence, "role": role,
                                            "skip": offset, "limit": limit}, 1)
    total = rows[0]["total"] if rows else 0
    items = [_plain(dict(i, mention_in_evidence=mention_in_evidence(i.get("surface_form"), i.get("evidence"))))
             for i in (rows[0]["items"] if rows else [])]
    corpus = records(Q_CORPUS_SIZE, {}, 1)[0]["n"]
    nxt = offset + len(items)
    return {"items": items, "count": total, "next_cursor": encode_cursor(nxt) if nxt < total else None,
            "coverage": {"corpus_papers_in_graph": corpus}}


# ── named queries ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Param:
    type: type
    default: object = None
    required: bool = False
    min: float | None = None
    max: float | None = None
    choices: tuple = ()
    description: str = ""


@dataclass(frozen=True)
class NamedQuery:
    name: str
    description: str
    params: dict[str, Param]
    returns: tuple[str, ...]
    cypher: str | dict          # dict: chosen by the value of `variant_by`
    variant_by: str | None = None
    one_of: tuple[str, ...] = ()   # at least one of these parameters is required


_YEARS = {"year_from": Param(int, description="first publication year, inclusive"),
          "year_to": Param(int, description="last publication year, inclusive")}
_YEAR_FILTER = "($year_from IS NULL OR p.year >= $year_from) AND ($year_to IS NULL OR p.year <= $year_to)"
_ROLE = {"role": Param(str, choices=("used", "mentioned"), description="only edges with this role")}


def _lineage(hops: int, direction: str) -> str:
    rel = f"[:CITES*1..{hops}]"
    pattern = f"(seed)-{rel}->(other:Paper)" if direction == "out" else f"(other:Paper)-{rel}->(seed)"
    return f"""// citation_lineage
MATCH (seed:Paper)-[r:USES_METHOD]->(:Method {{canonical_id: $canonical_id}})
WHERE seed.is_reference_stub IS NULL AND ($role IS NULL OR r.role = $role)
MATCH path = {pattern}
WHERE other <> seed
RETURN seed.paper_id AS seed, other.paper_id AS paper_id, other.doi AS doi, other.title AS title,
       other.year AS year, coalesce(other.is_reference_stub, false) AS is_reference_stub, min(length(path)) AS hops
ORDER BY hops, coalesce(year, 0) DESC"""


CATALOGUE: dict[str, NamedQuery] = {q.name: q for q in (
    NamedQuery("top_methods", "Methods by number of corpus papers in which the extractor found them.",
               {**_YEARS, **_ROLE}, ("canonical_id", "display_name", "family", "papers"),
               f"""// top_methods
MATCH (p:Paper)-[r:USES_METHOD]->(m:Method)
WHERE p.is_reference_stub IS NULL AND {_YEAR_FILTER} AND ($role IS NULL OR r.role = $role)
RETURN m.canonical_id AS canonical_id, m.display_name AS display_name, m.family AS family, count(DISTINCT p) AS papers
ORDER BY papers DESC, canonical_id"""),
    NamedQuery("top_sensors", "Sensors by number of corpus papers in which the extractor found them.",
               {**_YEARS, **_ROLE}, ("canonical_id", "display_name", "family", "papers"),
               f"""// top_sensors
MATCH (p:Paper)-[r:USES_SENSOR]->(s:Sensor)
WHERE p.is_reference_stub IS NULL AND {_YEAR_FILTER} AND ($role IS NULL OR r.role = $role)
RETURN s.canonical_id AS canonical_id, s.display_name AS display_name, s.family AS family, count(DISTINCT p) AS papers
ORDER BY papers DESC, canonical_id"""),
    NamedQuery("method_sensor_pairs", "Method–sensor pairs found together in at least `min_papers` corpus papers.",
               {"min_papers": Param(int, 5, min=1, max=1000), **_YEARS, **_ROLE},
               ("method", "sensor", "papers"),
               f"""// method_sensor_pairs
MATCH (p:Paper)-[rm:USES_METHOD]->(m:Method), (p)-[rs:USES_SENSOR]->(s:Sensor)
WHERE p.is_reference_stub IS NULL AND {_YEAR_FILTER}
  AND ($role IS NULL OR (rm.role = $role AND rs.role = $role))
WITH m, s, count(DISTINCT p) AS papers WHERE papers >= $min_papers
RETURN m.canonical_id AS method, s.canonical_id AS sensor, papers ORDER BY papers DESC, method, sensor"""),
    NamedQuery("papers_by_country", "Corpus papers whose study area is in a country (OpenAlex/extracted country name).",
               {"country": Param(str, required=True, description="e.g. Ukraine"), **_YEARS},
               ("paper_id", "doi", "title", "year"),
               f"""// papers_by_country
MATCH (p:Paper)-[:FROM_COUNTRY]->(:Country {{name: $country}})
WHERE p.is_reference_stub IS NULL AND {_YEAR_FILTER}
RETURN p.paper_id AS paper_id, p.doi AS doi, p.title AS title, p.year AS year ORDER BY coalesce(year, 0) DESC, paper_id"""),
    NamedQuery("citation_lineage", "Works cited by (out) or citing (in) the corpus papers that use a method, 1–3 hops.",
               {"canonical_id": Param(str, required=True, description="e.g. method.hand"),
                "hops": Param(int, 1, min=1, max=3), "direction": Param(str, "out", choices=("out", "in")), **_ROLE},
               ("seed", "paper_id", "doi", "title", "year", "is_reference_stub", "hops"),
               {(h, d): _lineage(h, d) for h in (1, 2, 3) for d in ("out", "in")}, variant_by="hops,direction"),
    NamedQuery("coauthor_network", "Co-authors of an author (by ORCID or exact display name) with shared paper counts.",
               {"orcid": Param(str, description="0000-0002-…"), "name": Param(str, description="exact display name")},
               ("coauthor", "orcid", "shared_papers"),
               one_of=("orcid", "name"),
               cypher="""// coauthor_network
MATCH (a:Author) WHERE ($orcid IS NOT NULL AND a.orcid ENDS WITH $orcid) OR ($name IS NOT NULL AND a.display_name = $name)
MATCH (a)-[:AUTHORED]->(p:Paper)<-[:AUTHORED]-(c:Author) WHERE c <> a
RETURN c.display_name AS coauthor, c.orcid AS orcid, count(DISTINCT p) AS shared_papers
ORDER BY shared_papers DESC, coauthor"""),
    NamedQuery("metric_ranges_by_method", "Distribution of a metric's table values among papers that use each method.",
               {"metric": Param(str, required=True, description="NumericFact canonical id, e.g. metric.nse"),
                "min_facts": Param(int, 3, min=1, max=10000), **_ROLE},
               ("method", "papers", "facts", "min", "median", "max"),
               """// metric_ranges_by_method
MATCH (p:Paper)-[r:USES_METHOD]->(m:Method), (p)-[:HAS_NUMERIC_FACT]->(f:NumericFact {canonical_id: $metric})
WHERE p.is_reference_stub IS NULL AND ($role IS NULL OR r.role = $role) AND f.value IS NOT NULL
WITH m, count(DISTINCT p) AS papers, collect(f.value) AS values WHERE size(values) >= $min_facts
UNWIND values AS v
WITH m, papers, size(values) AS facts, min(v) AS lo, percentileCont(v, 0.5) AS median, max(v) AS hi
RETURN m.canonical_id AS method, papers, facts, lo AS min, median, hi AS max ORDER BY papers DESC, method"""),
    NamedQuery("flood_event_papers", "Corpus papers that investigate a named flood event.",
               {"event": Param(str, required=True, description="FloodEvent name")},
               ("paper_id", "doi", "title", "year"),
               """// flood_event_papers
MATCH (p:Paper)-[:INVESTIGATES]->(:FloodEvent {name: $event})
RETURN p.paper_id AS paper_id, p.doi AS doi, p.title AS title, p.year AS year ORDER BY coalesce(year, 0) DESC, paper_id"""),
    NamedQuery("institution_output", "Institutions by number of corpus papers of their authors.",
               {"country_code": Param(str, description="ISO-2, e.g. UA")},
               ("institution", "country_code", "papers"),
               """// institution_output
MATCH (i:Institution)<-[:AFFILIATED_WITH]-(:Author)-[:AUTHORED]->(p:Paper)
WHERE p.is_reference_stub IS NULL AND ($country_code IS NULL OR i.country_code = $country_code)
RETURN i.display_name AS institution, i.country_code AS country_code, count(DISTINCT p) AS papers
ORDER BY papers DESC, institution"""),
    NamedQuery("numeric_facts_by_metric", "Numeric facts extracted from TEI tables for one metric.",
               {"metric": Param(str, required=True, description="canonical id, e.g. metric.nse"),
                "min_value": Param(float), "max_value": Param(float)},
               ("paper_id", "value", "raw_cell", "table_label", "row_context", "col_header", "page"),
               """// numeric_facts_by_metric
MATCH (p:Paper)-[:HAS_NUMERIC_FACT]->(f:NumericFact {canonical_id: $metric})
WHERE ($min_value IS NULL OR f.value >= $min_value) AND ($max_value IS NULL OR f.value <= $max_value)
RETURN p.paper_id AS paper_id, f.value AS value, f.raw_cell AS raw_cell, f.table_label AS table_label,
       f.row_context AS row_context, f.col_header AS col_header, f.page AS page
ORDER BY paper_id, table_label"""),
    NamedQuery("method_cooccurrence", "Method pairs that co-occur in at least `min_count` papers (precomputed edges).",
               {"min_count": Param(int, 3, min=1, max=10000)},
               ("method_a", "method_b", "count"),
               """// method_cooccurrence
MATCH (a:Method)-[e:CO_OCCURS_WITH]->(b:Method) WHERE e.count >= $min_count
RETURN a.canonical_id AS method_a, b.canonical_id AS method_b, e.count AS count ORDER BY count DESC, method_a"""),
)}


def describe(q: NamedQuery) -> dict:
    return {"name": q.name, "description": q.description, "returns": list(q.returns),
            "one_of": list(q.one_of),
            "params": {k: {"type": p.type.__name__, "required": p.required, "default": p.default,
                           **({"min": p.min} if p.min is not None else {}),
                           **({"max": p.max} if p.max is not None else {}),
                           **({"choices": list(p.choices)} if p.choices else {}),
                           **({"description": p.description} if p.description else {})}
                       for k, p in q.params.items()}}


def validate_params(q: NamedQuery, given: dict) -> dict:
    errors, out = [], {}
    for k in sorted(set(given) - set(q.params)):
        errors.append({"loc": ["body", "params", k], "msg": f"unknown parameter; allowed: {sorted(q.params)}",
                       "type": "extra_forbidden"})
    for name, p in q.params.items():
        value = given.get(name, p.default)
        if value is None:
            if p.required:
                errors.append({"loc": ["body", "params", name], "msg": "required", "type": "missing"})
            out[name] = None
            continue
        ok = isinstance(value, p.type) and not (p.type is int and isinstance(value, bool))
        if p.type is float and isinstance(value, int) and not isinstance(value, bool):
            value, ok = float(value), True
        if not ok:
            errors.append({"loc": ["body", "params", name], "msg": f"expected {p.type.__name__}", "type": "type_error"})
            continue
        if p.min is not None and value < p.min or p.max is not None and value > p.max:
            errors.append({"loc": ["body", "params", name], "msg": f"must be within [{p.min}, {p.max}]",
                           "type": "range_error"})
        if p.choices and value not in p.choices:
            errors.append({"loc": ["body", "params", name], "msg": f"one of {list(p.choices)}", "type": "literal_error"})
        out[name] = value
    if q.one_of and all(out.get(k) is None for k in q.one_of):
        errors.append({"loc": ["body", "params"], "msg": f"give at least one of {list(q.one_of)}", "type": "missing"})
    if errors:
        raise BadParams(errors)
    return out


def run_named(name: str, given: dict, limit: int) -> tuple[list[str], list[list], bool]:
    q = CATALOGUE[name]
    params = validate_params(q, given)
    if q.variant_by:
        key = tuple(params[k] for k in q.variant_by.split(","))
        cypher = q.cypher[key]
    else:
        cypher = q.cypher
    return run_read(cypher, params, limit)


# ── ad-hoc Cypher (admin) ──────────────────────────────────────────────────────

def explain_type(query: str, params: dict) -> str:
    from neo4j import unit_of_work

    @unit_of_work(timeout=TIMEOUT_S)
    def work(tx):
        return tx.run("EXPLAIN " + query, params).consume().query_type
    with driver().session() as s:
        return s.execute_read(work)


def run_cypher(query: str, params: dict, limit: int) -> tuple[list[str], list[list], bool]:
    kind = explain_type(query, params)
    if kind != "r":
        raise NotReadOnly(f"the planner classifies this query as '{kind}'; only read-only ('r') queries run here")
    return run_read(query, params, limit)
