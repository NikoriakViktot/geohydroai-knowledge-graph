"""Reads of the equation-centric graph for /v1/equations, /v1/quantities and /v1/laws
(docs/api/endpoints/equations.md). Every Cypher text is static and starts with a '// name'
line (tests dispatch on it); user values are bound as parameters."""

from __future__ import annotations

import json
from functools import lru_cache

from src.services import graph_read as g

EQ_PARTS = ("parameters", "laws", "equivalents", "code")
LAW_STATUS = ("accepted", "candidate", "any")
_EQ_FIELDS = ("eq_id", "paper_id", "xml_id", "equation_number", "page", "latex_raw", "latex", "text_grobid",
              "formula_text_hash", "formula_structural_hash", "canonical_expression", "structure_status", "purpose",
              "purpose_source", "section", "context_text", "image_path", "image_sha256")
_LINK_FIELDS = ("status", "score", "variant", "s_quantity", "s_math", "s_text", "s_concept", "math_method",
                "mapping", "capped")


def _json(v):
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _link(row: dict) -> dict:
    out = {k: row.get(k) for k in ("law_id", "name") if k in row}
    out.update({k: _json(row.get(k)) for k in _LINK_FIELDS})
    return out


# ── one equation ───────────────────────────────────────────────────────────────

def equation(eq_id: str, parts: tuple[str, ...] = EQ_PARTS) -> dict | None:
    rows = g.records("""// equation
MATCH (e:Equation {eq_id: $eq_id})
OPTIONAL MATCH (p:Paper)-[:HAS_EQUATION]->(e)
OPTIONAL MATCH (e)-[c:COMPUTES]->(q:Quantity)
OPTIONAL MATCH (q)-[:NORMALIZED_TO]->(qc:QuantityConcept)
RETURN e AS e, p {.paper_id, .doi, .title, .year} AS paper,
       [x IN collect(DISTINCT CASE WHEN q IS NULL THEN NULL
            ELSE {quantity: q.name, quantity_id: qc.canonical_id, derivative: c.derivative} END) WHERE x IS NOT NULL] AS computes
""", {"eq_id": eq_id}, 1)
    if not rows:
        return None
    e = rows[0]["e"] or {}
    out = {"equation": {k: e.get(k) for k in _EQ_FIELDS}, "paper": rows[0]["paper"],
           "computes": rows[0]["computes"] or [], "parameters": None, "laws": None, "equivalents": None, "code": None}
    if "parameters" in parts or "code" in parts:
        out["parameters"] = parameters(eq_id)
    if "laws" in parts:
        out["laws"] = [_link(r) for r in g.records("""// equation_laws
MATCH (e:Equation {eq_id: $eq_id})-[x:EQUATION_INSTANCE_OF]->(l:PhysicalLaw)
RETURN l.law_id AS law_id, l.name AS name, x.status AS status, x.score AS score, x.variant AS variant,
       x.s_quantity AS s_quantity, x.s_math AS s_math, x.s_text AS s_text, x.s_concept AS s_concept,
       x.math_method AS math_method, x.mapping AS mapping, x.capped AS capped
ORDER BY x.score DESC
""", {"eq_id": eq_id})]
    sh = e.get("formula_structural_hash")
    if "equivalents" in parts:
        exact = g.records("""// equation_exact
MATCH (e:Equation {eq_id: $eq_id})-[:HAS_STRUCTURE]->(s:FormulaStructure {structural_hash: $sh})
MATCH (o:Equation)-[:HAS_STRUCTURE]->(s)
WHERE o <> e AND o.formula_structural_hash = $sh
RETURN o.eq_id AS eq_id, o.paper_id AS paper_id ORDER BY o.paper_id, o.eq_id
""", {"eq_id": eq_id, "sh": sh}, 51) if sh else []
        alg = g.records("""// equation_algebraic
MATCH (s:FormulaStructure {structural_hash: $sh})-[a:ALGEBRAIC_EQUIVALENT]-(t:FormulaStructure)
RETURN t.structural_hash AS structural_hash, t.canonical_expression AS canonical_expression,
       a.method AS method, a.variable AS variable, a.mapping AS mapping, a.domain AS domain
""", {"sh": sh}) if sh else []
        out["equivalents"] = {"exact": exact[:50], "exact_count": len(exact) if len(exact) <= 50 else None,
                              "algebraic": [{**a, "mapping": _json(a.get("mapping"))} for a in alg]}
    if "code" in parts and sh:
        out["code"] = code(e, out["paper"], out["parameters"] or [])
    if "parameters" not in parts:
        out["parameters"] = None
    return out


def parameters(eq_id: str) -> list[dict]:
    return g.records("""// equation_parameters
MATCH (e:Equation {eq_id: $eq_id})-[:HAS_PARAMETER]->(p:Parameter)
WHERE NOT coalesce(p.stale, false)
RETURN p.symbol AS symbol, p.symbol_tex AS symbol_tex, p.description AS description, p.unit AS unit,
       p.value AS value, p.source AS source, p.quantity AS quantity, p.quantity_id AS quantity_id,
       p.dimension_check AS dimension_check
ORDER BY p.symbol
""", {"eq_id": eq_id})


def code(e: dict, paper: dict | None, params: list[dict]) -> dict | None:
    rows = g.records("""// equation_code
MATCH (s:FormulaStructure {structural_hash: $sh})
RETURN s.code_status AS status, s.code_form AS form, s.code_target AS target, s.code_args AS args,
       s.code_python AS python, s.code_julia AS julia, s.code_check AS check, s.canonical_expression AS canonical_expression
""", {"sh": e.get("formula_structural_hash")}, 1)
    if not rows or not rows[0].get("python"):
        return None
    c = rows[0]
    from src.document.formula_code import annotate
    eq_row = {"equation_id": e.get("eq_id"), "paper_id": e.get("paper_id"), "equation_number": e.get("equation_number"),
              "xml_id": e.get("xml_id"), "page": e.get("page"), "parameters": json.dumps(params, ensure_ascii=False)}
    row = {**c, "args": _json(c.get("args")) or []}
    return {"target": c.get("target"), "form": c.get("form"), "check": c.get("check"), "args": row["args"],
            "python": c.get("python"), "julia": c.get("julia"),
            "python_annotated": annotate(row, eq_row, "python"), "julia_annotated": annotate(row, eq_row, "julia")}


# ── the chain ──────────────────────────────────────────────────────────────────

def chain(eq_id: str) -> dict | None:
    base = equation(eq_id, ("parameters", "laws"))
    if base is None:
        return None
    from src.ontology.quantities import ontology
    by_id = ontology()["by_id"]
    quantities: dict[str, dict] = {}
    for p in base["parameters"] or []:
        qid = p.get("quantity_id")
        if qid:
            q = quantities.setdefault(qid, {"quantity_id": qid, "label": (by_id.get(qid) or {}).get("label"),
                                            "dimension": (by_id.get(qid) or {}).get("dimension_text"), "symbols": []})
            q["symbols"].append(p.get("symbol"))
    concepts = g.records("""// chain_concepts
MATCH (e:Equation {eq_id: $eq_id})
OPTIONAL MATCH (e)-[:DEFINES_METRIC]->(m:Metric)
OPTIONAL MATCH (e)-[:EQUATION_GROUNDS_TO]->(md:Method)
OPTIONAL MATCH (e)-[x:EQUATION_INSTANCE_OF]->(:PhysicalLaw)-[:RELATES_TO]->(lc)
WHERE x.status = 'accepted'
WITH [i IN collect(DISTINCT {label: 'Metric', canonical_id: m.canonical_id, via: 'DEFINES_METRIC'})
          WHERE i.canonical_id IS NOT NULL]
   + [i IN collect(DISTINCT {label: 'Method', canonical_id: md.canonical_id, via: 'EQUATION_GROUNDS_TO'})
          WHERE i.canonical_id IS NOT NULL]
   + [i IN collect(DISTINCT {label: head(labels(lc)), canonical_id: lc.canonical_id, via: 'law'})
          WHERE i.canonical_id IS NOT NULL] AS concepts
RETURN concepts
""", {"eq_id": eq_id}, 1)
    concepts = (concepts[0]["concepts"] if concepts else []) or []
    metrics = sorted({c["canonical_id"] for c in concepts if c["label"] == "Metric"})
    facts = g.records("""// chain_facts
MATCH (p:Paper {paper_id: $paper_id})-[:HAS_NUMERIC_FACT]->(f:NumericFact)
WHERE f.canonical_id IN $metrics
RETURN f.fact_id AS fact_id, f.canonical_id AS metric, f.value AS value, f.unit AS unit,
       f.table_label AS table_label, f.page AS page, f.raw_cell AS raw_cell, f.row_context AS row_context,
       f.col_header AS col_header
ORDER BY f.page, f.table_label LIMIT 200
""", {"paper_id": base["equation"]["paper_id"], "metrics": metrics}, 200) if metrics else []
    return {"equation": base["equation"], "paper": base["paper"], "parameters": base["parameters"] or [],
            "quantities": list(quantities.values()), "laws": base["laws"] or [], "concepts": concepts,
            "reported_values": facts}


# ── search ─────────────────────────────────────────────────────────────────────

_SEARCH = {
    # every variant is static text; the filters that are not given are bound as null and switched off
    "head": """// equation_search
MATCH (e:Equation)
WHERE ($paper IS NULL OR e.paper_id = $paper)
  AND ($sh IS NULL OR e.formula_structural_hash = $sh)
  AND ($q IS NULL OR toLower(coalesce(e.purpose, '') + ' ' + coalesce(e.context_text, '') + ' '
                             + coalesce(e.section, '')) CONTAINS $q)
  AND ($quantity IS NULL OR EXISTS {
        MATCH (e)-[:HAS_PARAMETER]->(p:Parameter) WHERE NOT coalesce(p.stale, false) AND p.quantity_id = $quantity })
  AND ($law IS NULL OR EXISTS {
        MATCH (e)-[x:EQUATION_INSTANCE_OF]->(:PhysicalLaw {law_id: $law})
        WHERE $law_status = 'any' OR x.status = $law_status })
  AND (NOT $has_code OR EXISTS {
        MATCH (e)-[:HAS_STRUCTURE]->(s:FormulaStructure) WHERE s.code_python IS NOT NULL
          AND s.structural_hash = e.formula_structural_hash })
WITH e ORDER BY e.paper_id, e.eq_id
WITH collect(e) AS found
WITH size(found) AS total, found[$skip..$skip + $limit] AS page
UNWIND (CASE WHEN size(page) = 0 THEN [null] ELSE page END) AS e
OPTIONAL MATCH (p:Paper)-[:HAS_EQUATION]->(e)
OPTIONAL MATCH (e)-[x:EQUATION_INSTANCE_OF]->(l:PhysicalLaw) WHERE x.status IN ['accepted', 'candidate']
OPTIONAL MATCH (e)-[:HAS_PARAMETER]->(mp:Parameter)
  WHERE $quantity IS NOT NULL AND NOT coalesce(mp.stale, false) AND mp.quantity_id = $quantity
WITH total, e, p,
     [i IN collect(DISTINCT {law_id: l.law_id, status: x.status, score: x.score}) WHERE i.law_id IS NOT NULL] AS laws,
     [i IN collect(DISTINCT {symbol: mp.symbol, description: mp.description, unit: mp.unit,
                             quantity_id: mp.quantity_id, dimension_check: mp.dimension_check})
        WHERE i.symbol IS NOT NULL] AS matched
RETURN total, e.eq_id AS eq_id, e.paper_id AS paper_id, p.title AS title, p.year AS year,
       e.equation_number AS equation_number, e.page AS page, e.latex_raw AS latex_raw,
       e.canonical_expression AS canonical_expression, e.purpose AS purpose,
       e.structure_status AS structure_status, laws, matched AS matched_parameters
""",
}


class Unresolved(ValueError):
    def __init__(self, field: str, msg: str):
        super().__init__(msg)
        self.errors = [{"loc": ["query", field], "msg": msg, "type": "value_error"}]


def resolve_quantity(value: str | None) -> tuple[str | None, str | None]:
    """(quantity_id, method) for an id or a name; raises Unresolved for a name that maps to nothing."""
    if not value:
        return None, None
    from src.ontology.quantities import ontology
    if value in ontology()["by_id"]:
        return value, "id"
    from src.ontology.quantity_map import resolve
    m = resolve(value.strip().lower())
    if not m.quantity_id:
        raise Unresolved("quantity", f"no quantity concept for {value!r} (method {m.method}); see GET /v1/quantities")
    return m.quantity_id, m.method


def search(*, quantity=None, law=None, law_status="accepted", paper=None, structural_hash=None, q=None,
           has_code=False, limit=50, offset=0) -> dict:
    if not any((quantity, law, paper, structural_hash, q, has_code)):
        raise Unresolved("query", "give at least one filter: quantity, law, paper, structural_hash, q or has_code")
    qid, qmethod = resolve_quantity(quantity)
    if law and law not in law_ids():
        raise Unresolved("law", f"unknown law {law!r}; see GET /v1/laws")
    rows = g.records(_SEARCH["head"], {"paper": paper, "sh": structural_hash, "q": q.lower() if q else None,
                                       "quantity": qid, "law": law, "law_status": law_status,
                                       "has_code": bool(has_code), "skip": offset, "limit": limit}, limit + 1)
    total = rows[0]["total"] if rows else 0
    items = [{k: v for k, v in r.items() if k != "total"} for r in rows if r.get("eq_id")]
    nxt = g.encode_cursor(offset + limit) if offset + limit < total else None
    return {"items": items, "count": total, "next_cursor": nxt,
            "resolved": {"quantity_id": qid, "quantity_method": qmethod}}


# ── quantities ─────────────────────────────────────────────────────────────────

def _concept(q: dict) -> dict:
    return {"quantity_id": q["id"], "label": q["label"], "kind": q.get("kind"), "dimension": q.get("dimension_text"),
            "alt_dimensions": [a["dimension_text"] for a in q.get("alt_dimensions", [])],
            "typical_unit": q.get("typical_unit"), "aliases": q.get("aliases", [])}


def quantities(q: str | None = None, kind: str | None = None, limit: int = 200) -> dict:
    from src.ontology.quantities import ontology
    o = ontology()
    counts = {r["quantity_id"]: r for r in g.records("""// quantity_counts
MATCH (e:Equation)-[:HAS_PARAMETER]->(p:Parameter)
WHERE NOT coalesce(p.stale, false) AND p.quantity_id IS NOT NULL
RETURN p.quantity_id AS quantity_id, count(p) AS parameters, count(DISTINCT e) AS equations
""", {}, 5000)}
    items = []
    needle = (q or "").lower()
    for c in o["by_id"].values():
        if kind and c.get("kind") != kind:
            continue
        if needle and needle not in " ".join([c["id"], c["label"], *c.get("aliases", [])]).lower():
            continue
        n = counts.get(c["id"], {})
        items.append({**_concept(c), "parameters": n.get("parameters", 0), "equations": n.get("equations", 0)})
    items.sort(key=lambda x: (-x["parameters"], x["quantity_id"]))
    return {"items": items[:limit], "count": len(items), "ontology_version": o["version"]}


def quantity(quantity_id: str) -> dict | None:
    from src.ontology.quantities import ontology
    c = ontology()["by_id"].get(quantity_id)
    if c is None:
        return None
    names = g.records("""// quantity_names
MATCH (q:Quantity)-[n:NORMALIZED_TO]->(:QuantityConcept {canonical_id: $id})
OPTIONAL MATCH (p:Parameter)-[:QUANTIFIES]->(q) WHERE NOT coalesce(p.stale, false)
RETURN q.name AS name, n.method AS method, n.score AS score, count(p) AS parameters
ORDER BY parameters DESC, name LIMIT 100
""", {"id": quantity_id}, 100)
    units = g.records("""// quantity_units
MATCH (p:Parameter {quantity_id: $id}) WHERE NOT coalesce(p.stale, false) AND p.unit IS NOT NULL
RETURN p.unit AS unit, p.dimension_check AS dimension_check, count(*) AS parameters
ORDER BY parameters DESC LIMIT 50
""", {"id": quantity_id}, 50)
    laws = g.records("""// quantity_laws
MATCH (l:PhysicalLaw)-[i:INVOLVES]->(:QuantityConcept {canonical_id: $id})
RETURN l.law_id AS law_id, l.name AS name, i.symbol AS symbol ORDER BY l.law_id
""", {"id": quantity_id})
    counts = g.records("""// quantity_totals
MATCH (e:Equation)-[:HAS_PARAMETER]->(p:Parameter {quantity_id: $id}) WHERE NOT coalesce(p.stale, false)
RETURN count(p) AS parameters, count(DISTINCT e) AS equations, count(DISTINCT e.paper_id) AS papers
""", {"id": quantity_id}, 1)
    return {"quantity": _concept(c), "surface_names": names, "units": units, "laws": laws,
            "counts": counts[0] if counts else {"parameters": 0, "equations": 0, "papers": 0}}


def normalize(items: list[dict]) -> dict:
    from src.ontology.quantities import check_dimension, dimension_text, ontology, quantity_dimension, unit_dimension
    from src.ontology.quantity_map import resolve
    o = ontology()
    out = []
    for it in items:
        name, unit = it.get("name") or "", it.get("unit")
        m = resolve(name.strip().lower())
        c = o["by_id"].get(m.quantity_id) if m.quantity_id else None
        out.append({"name": name, "unit": unit, "quantity_id": m.quantity_id, "label": c and c["label"],
                    "method": m.method, "score": m.score, "qualifiers": m.qualifiers,
                    "dimension": c and c.get("dimension_text"),
                    "unit_dimension": dimension_text(unit_dimension(unit)) if unit else None,
                    "dimension_check": check_dimension(m.quantity_id, unit) if unit else None})
    return {"items": out, "ontology_version": o["version"]}


# ── laws ───────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def law_ids() -> frozenset:
    from src.ontology.laws import registry
    return frozenset(l["id"] for l in registry()["laws"])


def laws() -> dict:
    from src.ontology.laws import ACCEPT, CANDIDATE, WEIGHTS, registry
    reg = registry()
    counts = {r["law_id"]: r for r in g.records("""// law_counts
MATCH (e:Equation)-[x:EQUATION_INSTANCE_OF]->(l:PhysicalLaw)
RETURN l.law_id AS law_id,
       count(CASE WHEN x.status = 'accepted' THEN e END) AS accepted_equations,
       count(DISTINCT CASE WHEN x.status = 'accepted' THEN e.paper_id END) AS accepted_papers,
       count(CASE WHEN x.status = 'candidate' THEN e END) AS candidate_equations
""", {}, 500)}
    items = []
    for law in reg["laws"]:
        n = counts.get(law["id"], {})
        items.append({"law_id": law["id"], "name": law["name"], "kind": law.get("kind"), "reference": law.get("reference"),
                      "variants": [f.get("variant") for f in law["forms"] if f.get("variant")],
                      "accepted_equations": n.get("accepted_equations", 0), "accepted_papers": n.get("accepted_papers", 0),
                      "candidate_equations": n.get("candidate_equations", 0)})
    return {"items": items, "laws_version": reg["version"], "weights": WEIGHTS,
            "thresholds": {"accepted": ACCEPT, "candidate": CANDIDATE}}


def law(law_id: str, status: str = "accepted", limit: int = 50, offset: int = 0) -> dict | None:
    from src.ontology.laws import forms, registry
    entry = next((l for l in registry()["laws"] if l["id"] == law_id), None)
    if entry is None:
        return None
    fms = [{"latex": f.latex, "variant": f.variant, "code_python": f.code.get("python"),
            "code_julia": f.code.get("julia"), "code_check": f.code.get("check")}
           for f in forms() if f.law_id == law_id]
    rows = g.records("""// law_instances
MATCH (e:Equation)-[x:EQUATION_INSTANCE_OF]->(:PhysicalLaw {law_id: $law_id})
WHERE $status = 'any' OR x.status = $status
WITH e, x ORDER BY x.score DESC, e.eq_id
WITH collect({e: e, x: x}) AS found
WITH size(found) AS total, found[$skip..$skip + $limit] AS page
UNWIND (CASE WHEN size(page) = 0 THEN [null] ELSE page END) AS it
WITH total, it.e AS e, it.x AS x
OPTIONAL MATCH (p:Paper)-[:HAS_EQUATION]->(e)
RETURN total, e.eq_id AS eq_id, e.paper_id AS paper_id, p.title AS title, p.year AS year, e.page AS page,
       e.latex_raw AS latex_raw, x.status AS status, x.score AS score, x.variant AS variant,
       x.s_quantity AS s_quantity, x.s_math AS s_math, x.s_text AS s_text, x.s_concept AS s_concept,
       x.math_method AS math_method, x.mapping AS mapping, x.capped AS capped
""", {"law_id": law_id, "status": status, "skip": offset, "limit": limit}, limit + 1)
    total = rows[0]["total"] if rows else 0
    inst = []
    for r in rows:
        if not r.get("eq_id"):
            continue
        inst.append({k: r.get(k) for k in ("eq_id", "paper_id", "title", "year", "page", "latex_raw")} | _link(r))
    return {"law": {"law_id": entry["id"], "name": entry["name"], "kind": entry.get("kind"),
                    "reference": entry.get("reference"), "text_cues": entry.get("text") or []},
            "forms": fms,
            "quantities": [{"symbol": s, "quantity_id": q} for s, q in (entry.get("variables") or {}).items() if q],
            "concepts": entry.get("concepts") or [], "instances": inst, "count": total,
            "next_cursor": g.encode_cursor(offset + limit) if offset + limit < total else None}
