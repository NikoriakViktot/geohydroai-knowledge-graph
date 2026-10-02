"""Metric values: deterministic extraction from text and TEI, and the corpus fact table.

Extraction rule (docs/api/endpoints/metrics.md): a value is reported only when a metric
name stands in the same sentence right before it ("NSE = −0.27", "an overall accuracy of
94.2 %", "RMSE of 1.2 m", "KGE values between 0.61 and 0.74"). The name is therefore in
the evidence by construction: "Percent" or topic words never become OA (no-fabrication
rule). Inequalities ("NSE > 0.5", usually a criterion) are kept with their qualifier.

Percentages: with an explicit "%" a value is divided by 100 for any metric bounded above
by 1; without it only for bounded ratio metrics (OA, F1, IoU, kappa …), as in
src/extraction/numbers.to_ratio. NSE 1.7 is never read as 0.017: it is rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.extraction.metric_ontology import (
    CANONICAL_LABELS, METRIC_RANGES, METRIC_TO_GROUP, RATIO_METRICS, SIGNED_RATIO_METRICS, _UNIT_CANONICAL,
)
from src.extraction.numbers import parse_number

ROOT = Path(__file__).resolve().parents[2]
FACTS_FILE = ROOT / "data" / "analytics" / "numeric_facts.parquet"
GROUNDING_FILE = ROOT / "data" / "graph_inputs" / "entity_grounding.parquet"

#: canonical id → names as written in papers (longest first when matching).
METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "metric.nse": ("Nash–Sutcliffe efficiency", "Nash-Sutcliffe efficiency", "Nash–Sutcliffe coefficient",
                   "Nash-Sutcliffe coefficient", "Nash–Sutcliffe", "Nash-Sutcliffe", "NSE"),
    "metric.kge": ("Kling–Gupta efficiency", "Kling-Gupta efficiency", "Kling–Gupta", "Kling-Gupta", "KGE"),
    "metric.r2": ("coefficient of determination", "R²", "R^2", "R2", "R 2"),
    "metric.rmse": ("root mean square error", "root-mean-square error", "root mean squared error", "RMSE"),
    "metric.mae": ("mean absolute error", "MAE"),
    "metric.mse": ("mean squared error", "mean square error", "MSE"),
    "metric.mape": ("mean absolute percentage error", "MAPE"),
    "metric.pbias": ("percent bias", "percentage bias", "PBIAS"),
    "metric.rsr": ("RSR",),
    "metric.overall_accuracy": ("overall accuracy", "OA"),
    "metric.f1_score": ("F1-score", "F1 score", "F-score", "F-measure", "F1"),
    "metric.iou": ("intersection over union", "Jaccard index", "IoU"),
    "metric.kappa": ("Cohen's kappa", "kappa coefficient", "kappa", "Kappa", "κ"),
    "metric.precision": ("precision",),
    "metric.recall": ("recall",),
    "metric.pod": ("probability of detection", "POD"),
    "metric.far": ("false alarm ratio", "false alarm rate", "FAR"),
    "metric.csi": ("critical success index", "CSI"),
    "metric.auc": ("area under the ROC curve", "area under the curve", "AUC"),
    "metric.r": ("correlation coefficient", "Pearson's r", "Pearson r"),
}
#: canonical id → key of metric_ontology.METRIC_RANGES / CANONICAL_LABELS
_RANGE_KEY = {"metric.r": "correlation"}

_NUM = r"[-−﹣－+]?\d+(?:[.,]\d+)?"
#: units recognised after a value; "/" may be spaced ("mm/ year"); the longest wins
_EXTRA_UNITS = {"mm/year": "mm/year", "mm/yr": "mm/year", "mm/a": "mm/year", "mm/month": "mm/month",
                "m/s": "m/s", "cm/s": "cm/s", "km²": "km²", "km2": "km²", "ha": "ha", "m3/s": "m³/s"}
_UNIT_MAP = {**{k: v for k, v in _UNIT_CANONICAL.items() if k not in ("-", "m^3/s")}, **_EXTRA_UNITS}
_UNIT_RE = "|".join(re.escape(u).replace("/", r"\s*/\s*") for u in sorted(_UNIT_MAP, key=len, reverse=True))
_TAIL = (r"['′’]?(?:\s*\([^()]{0,30}\))?\s*(?:values?|scores?|coefficients?|index|statistics?)?\s*"
         r"(?P<op>of|=|:|≈|~|>=|<=|>|<|≥|≤|was\s+found\s+to\s+be|were\s+found\s+to\s+be|is\s+found\s+to\s+be|"
         r"found\s+to\s+be|was|is|were|are|reached|achieved|obtained|equal(?:led)?\s+to|"
         r"ranged?\s+from|varied\s+from|between|from)?\s*"
         r"(?:about|approximately|around|nearly|over|above|below|up\s+to|only|just)?\s*"
         rf"(?P<v>{_NUM})\s*(?P<p>%|percent)?(?:\s*(?P<unit1>{_UNIT_RE})(?![A-Za-z0-9]))?"
         rf"(?:\s*(?:–|—|-|to|and)\s*(?P<v2>{_NUM})\s*(?P<p2>%|percent)?)?"
         rf"(?:\s*(?P<unit>{_UNIT_RE})(?![A-Za-z0-9]))?")
_QUALIFIER = {">": ">", "<": "<", ">=": "≥", "<=": "≤", "≥": "≥", "≤": "≤", "≈": "≈", "~": "≈"}


@dataclass(frozen=True)
class _Alias:
    canonical: str
    pattern: re.Pattern
    short: bool


def _alias_patterns() -> list[_Alias]:
    out = []
    for cid, names in METRIC_ALIASES.items():
        for name in sorted(names, key=len, reverse=True):
            body = re.escape(name).replace(r"\ ", r"\s+")
            compact = re.sub(r"\W", "", name)
            short = compact.isupper() and len(compact) <= 4
            out.append(_Alias(cid, re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9]){_TAIL}",
                                              0 if short else re.IGNORECASE), short))
    return out


_ALIASES = _alias_patterns()


def range_key(canonical: str) -> str:
    return _RANGE_KEY.get(canonical, canonical.removeprefix("metric.").removeprefix("method."))


def label(canonical: str) -> str:
    key = range_key(canonical)
    return CANONICAL_LABELS.get(key, key.replace("_", " "))


def verdict(canonical: str, value: float) -> str:
    rng = METRIC_RANGES.get(range_key(canonical))
    if rng is None:
        return "unknown_metric"
    return "ok" if rng.lo <= value <= rng.hi else "suspect"


def range_text(canonical: str) -> str:
    rng = METRIC_RANGES.get(range_key(canonical))
    if rng is None:
        return "no known range"
    lo = "" if rng.lo == float("-inf") else f"{rng.lo:g} ≤ "
    hi = "" if rng.hi == float("inf") else f" ≤ {rng.hi:g}"
    return f"{lo}{label(canonical)}{hi}"


def _scale(canonical: str, value: float, percent: bool) -> float:
    key = range_key(canonical)
    rng = METRIC_RANGES.get(key)
    if rng is None or rng.hi != 1.0:
        return value
    if percent and abs(value) <= 100.0:
        return value / 100.0
    bounded = key in RATIO_METRICS or key in SIGNED_RATIO_METRICS
    if bounded and 1.0 < abs(value) <= 100.0:
        return value / 100.0
    return value


def unit_of(raw: str | None) -> str | None:
    """A recognised unit, or None (the table column often holds other text)."""
    if not raw or not str(raw).strip():
        return None
    return _UNIT_MAP.get(str(raw).strip().lower().replace(" ", ""))


def extract_sentence(sentence: str, *, passage_id: str | None = None, page: int | None = None,
                     section: str | None = None, wanted: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    facts: list[dict] = []
    rejected: list[dict] = []
    taken: list[tuple[int, int]] = []
    for alias in _ALIASES:
        if wanted and alias.canonical not in wanted:
            continue
        for m in alias.pattern.finditer(sentence):
            if alias.short and m.group(0).split()[0].islower():
                continue
            if any(m.start() < e and s < m.end() for s, e in taken):   # a longer alias already took it
                continue
            taken.append((m.start(), m.end()))
            raw = m.group(0)
            try:
                v = parse_number(m.group("v").replace("–", "-"))
                v2 = parse_number(m.group("v2").replace("–", "-")) if m.group("v2") else None
            except ValueError:
                continue
            pct = bool(m.group("p") or m.group("p2"))
            value = _scale(alias.canonical, v, pct)
            value_hi = _scale(alias.canonical, v2, pct) if v2 is not None else None
            op = m.group("op") or ""
            qualifier = _QUALIFIER.get(op) or ("range" if v2 is not None else None)
            unit = "%" if pct else unit_of(m.group("unit") or m.group("unit1"))
            fact = {"metric": alias.canonical, "label": label(alias.canonical), "value": round(value, 6),
                    "value_hi": round(value_hi, 6) if value_hi is not None else None, "raw_value": raw.strip(),
                    "unit": unit, "qualifier": qualifier, "source": "text",
                    "evidence": {"text": sentence, "passage_id": passage_id, "page": page, "section": section}}
            bad = [x for x in (value, value_hi) if x is not None and verdict(alias.canonical, x) == "suspect"]
            if bad:
                rejected.append({"raw": raw.strip(), "metric": alias.canonical,
                                 "reason": f"outside valid range ({range_text(alias.canonical)})",
                                 "evidence": fact["evidence"]})
            else:
                fact["range_verdict"] = verdict(alias.canonical, value)
                facts.append(fact)
    return facts, rejected


def extract_text(text: str, wanted: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    from src.paper_3.evidence import split_sentences
    facts, rejected = [], []
    for sentence in split_sentences(text):
        f, r = extract_sentence(sentence, wanted=wanted)
        facts += f
        rejected += r
    return facts, rejected


def extract_document(doc, tei_path: Path | None, paper_id: str, wanted: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Text facts from every sentence of a parsed TEIDocument, plus its table facts."""
    from src.services import fulltext
    facts, rejected = [], []
    for p in fulltext.passages(doc):
        if p.kind == "table":
            continue                       # tables are read cell by cell below
        for s in p.sentences:
            f, r = extract_sentence(s.text, passage_id=p.passage_id, page=s.page or p.page,
                                    section=p.section or None, wanted=wanted)
            facts += f
            rejected += r
    if tei_path is not None:
        facts += table_facts(tei_path, paper_id, wanted)
    return facts, rejected


def table_facts(tei_path: Path, paper_id: str, wanted: set[str] | None = None) -> list[dict]:
    from src.extraction.table_extractor import extract_numeric_facts
    out = []
    for nf in extract_numeric_facts(tei_path, paper_id):
        if wanted and nf.canonical_id not in wanted:
            continue
        out.append({"metric": nf.canonical_id, "label": label(nf.canonical_id), "value": nf.value, "value_hi": None,
                    "raw_value": getattr(nf, "raw_cell", None) or str(nf.value), "unit": unit_of(nf.unit),
                    "qualifier": None, "source": "table", "range_verdict": verdict(nf.canonical_id, nf.value),
                    "fact_id": nf.fact_id,
                    "evidence": {"table_label": nf.table_label, "col_header": nf.col_header,
                                 "row_context": list(nf.row_context or []), "page": nf.page}})
    return out


# ── the corpus fact table (data/analytics/numeric_facts.parquet) ───────────────

def _verdict_sql() -> str:
    """CASE expression over canonical_id built from our own range table (no user input)."""
    parts = []
    for cid in sorted({*METRIC_ALIASES, *(f"metric.{k}" for k in METRIC_RANGES)}):
        rng = METRIC_RANGES.get(range_key(cid))
        if rng is None:
            continue
        lo = "TRUE" if rng.lo == float("-inf") else f"value >= {rng.lo!r}"
        hi = "TRUE" if rng.hi == float("inf") else f"value <= {rng.hi!r}"
        parts.append(f"WHEN canonical_id = '{cid}' THEN CASE WHEN {lo} AND {hi} THEN 'ok' ELSE 'suspect' END")
    return "CASE " + " ".join(parts) + " ELSE 'unknown_metric' END"


def resolve_metric(name: str) -> str | None:
    """'metric.nse', 'NSE', 'Nash-Sutcliffe' → 'metric.nse'."""
    n = (name or "").strip()
    if n in METRIC_ALIASES or n.startswith(("metric.", "method.")):
        return n
    for cid, names in METRIC_ALIASES.items():
        if any(n.lower() == a.lower() for a in names):
            return cid
    from src.normalization.alias_resolver import normalize_alias
    found = normalize_alias(n)
    return found if found and found.startswith("metric.") else None


def query_facts(*, metric: str | None, lo: float | None, hi: float | None, paper_ids: list[str] | None,
                method: str | None, sensor: str | None, range_verdict: str, limit: int, offset: int) -> dict:
    import duckdb
    where, params = [], []
    if metric:
        where.append("canonical_id = ?")
        params.append(metric)
    if lo is not None:
        where.append("value >= ?")
        params.append(lo)
    if hi is not None:
        where.append("value <= ?")
        params.append(hi)
    if paper_ids is not None:
        where.append("list_contains(?, paper_id)")
        params.append(paper_ids)
    for rel, cid in (("USES_METHOD", method), ("USES_SENSOR", sensor)):
        if cid:
            where.append("paper_id IN (SELECT paper_id FROM read_parquet(?) WHERE rel = ? AND canonical_id = ? "
                         "AND grounded)")
            params += [str(GROUNDING_FILE), rel, cid]
    if range_verdict != "any":
        where.append("verdict = ?")
        params.append(range_verdict)
    cond = " AND ".join(where) or "TRUE"
    base = (f"SELECT *, {_verdict_sql()} AS verdict FROM read_parquet(?)")
    con = duckdb.connect()
    try:
        summary = con.execute(
            f"SELECT count(*), count(DISTINCT paper_id), quantile_cont(value, 0.5), quantile_cont(value, 0.1), "
            f"quantile_cont(value, 0.9) FROM ({base}) WHERE {cond}", [str(FACTS_FILE), *params]).fetchone()
        rows = con.execute(
            f"SELECT fact_id, paper_id, canonical_id, value, unit, table_label, col_header, row_context, page, "
            f"confidence, verdict FROM ({base}) WHERE {cond} ORDER BY paper_id, table_id, fact_id LIMIT ? OFFSET ?",
            [str(FACTS_FILE), *params, limit, offset]).fetchall()
        coverage = con.execute("SELECT count(DISTINCT paper_id) FROM read_parquet(?)", [str(FACTS_FILE)]).fetchone()[0]
    finally:
        con.close()
    items = []
    for fact_id, pid, cid, value, unit, tlabel, col, row_ctx, page, conf, verd in rows:
        ctx = row_ctx
        if isinstance(ctx, str):
            try:
                import json
                ctx = json.loads(ctx)
            except ValueError:
                ctx = [ctx]
        items.append({"fact_id": fact_id, "paper_id": pid, "metric": cid, "label": label(cid), "value": value,
                      "value_hi": None, "raw_value": None, "unit": unit_of(unit),
                      "unit_raw": unit if unit and not unit_of(unit) else None, "qualifier": None, "source": "table",
                      "range_verdict": verd,
                      "evidence": {"table_label": tlabel, "col_header": col, "row_context": list(ctx or []),
                                   "page": int(page) if page is not None and page == page else None}})
    n, papers, med, p10, p90 = summary
    return {"items": items, "total": n,
            "summary": {"n": n, "papers": papers, "median": med, "p10": p10, "p90": p90},
            "coverage": {"papers_with_table_facts": coverage, "source": "GROBID TEI tables only"}}


# ── ontology ───────────────────────────────────────────────────────────────────

def ontology_metrics() -> list[dict]:
    import ast

    from src.normalization.alias_resolver import load_ontology_registry
    registry = load_ontology_registry()
    ids = sorted({*METRIC_ALIASES, *(k for k, v in registry.items() if v.get("type") == "metric"),
                  *(f"metric.{k}" for k in METRIC_RANGES)})
    out = []
    for cid in ids:
        ent = registry.get(cid) or {}
        raw_aliases = ent.get("aliases") or "[]"
        try:
            reg_aliases = ast.literal_eval(raw_aliases) if isinstance(raw_aliases, str) else list(raw_aliases)
        except (ValueError, SyntaxError):
            reg_aliases = []
        aliases = sorted({*METRIC_ALIASES.get(cid, ()), *(str(a) for a in reg_aliases)}, key=str.lower)
        rng = METRIC_RANGES.get(range_key(cid))
        out.append({"canonical_id": cid, "name": ent.get("display_name") or label(cid), "aliases": aliases,
                    "range": None if rng is None else {"lo": None if rng.lo == float("-inf") else rng.lo,
                                                      "hi": None if rng.hi == float("inf") else rng.hi},
                    "percent_scale": bool(rng and rng.percent_form),
                    "group": METRIC_TO_GROUP.get(range_key(cid)), "in_registry": cid in registry,
                    "extracted_from_text": cid in METRIC_ALIASES})
    return out
