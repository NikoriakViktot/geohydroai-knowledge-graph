"""Quality filter for the published paper catalog: what a card shows, what it hides and why.

Every shown value carries a status:
  human_verified — the person checked it in the MVP (verify.current, AGENT_RULES R-ACC-7);
  model          — extracted by the pipeline and passed every rule in rules.yaml;
  source         — bibliographic metadata as given by OpenAlex / the publisher.
Every hidden value is reported with a reason (Hidden), so the filter itself can be audited.
A human judgement always wins over a rule: "correct" shows a value the rules would hide,
"incorrect" hides it (or replaces it with the person's correction).

Pure functions over plain dicts: no stores are opened here (src/catalog/build.py loads them).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from src.extraction.metric_ontology import METRIC_RANGES
from src.services import metrics as metric_rules

RULES_FILE = Path(__file__).with_name("rules.yaml")

HUMAN, MODEL, SOURCE = "human_verified", "model", "source"

#: entity kind on the card → (normalized_entities key, extractor entities key, grounding relation)
ENTITY_KINDS = {
    "methods": ("methods", "methods", "USES_METHOD"),
    "sensors": ("satellites", "satellites", "USES_SENSOR"),
    "data": ("dems", "dems", None),
}

#: table units the shared unit map does not know (TEI keeps exponents as separate tokens)
_EXTRA_UNITS = {"m3s-1": "m³/s", "m³s-1": "m³/s", "mmd-1": "mm/day", "mmday-1": "mm/day", "mmh-1": "mm/h",
                "ms-1": "m/s", "cms-1": "cm/s"}


@lru_cache(maxsize=4)
def load_rules(path: Path = RULES_FILE) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@dataclass
class Hidden:
    paper_id: str
    field: str
    value: str
    reason: str


@dataclass
class PaperInput:
    """Everything known about one paper, as loaded by build.py."""
    identity: dict                                   # core.paper row
    bib: dict = field(default_factory=dict)          # authors, cited_by_count, oa_url, oa_status, is_retracted
    normalized: dict | None = None                   # data/normalized/<paper_id>.json
    grounding: dict = field(default_factory=dict)    # (rel, canonical_id) → grounded, tei_mentions, tei_evidence
    facts: list[dict] = field(default_factory=list)  # numeric_facts rows
    topics: list[tuple[str, float]] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)  # verify.current rows of this paper


# ── human judgements ──────────────────────────────────────────────────────────

def _checks_by_target(checks: list[dict]) -> dict[tuple[str, str], dict]:
    """(target_kind, target_id) → the latest judgement (verify.current already keeps one per field)."""
    out: dict[tuple[str, str], dict] = {}
    for c in sorted(checks, key=lambda c: str(c.get("created_at") or "")):
        out[(c["target_kind"], str(c["target_id"]))] = c
    return out


def _corrected(check: dict) -> dict:
    c = check.get("corrected")
    if isinstance(c, str):
        try:
            c = json.loads(c)
        except ValueError:
            c = None
    return c if isinstance(c, dict) else {}


# ── text helpers ──────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"[\s\-‐-―_]+", " ", str(text)).strip().lower()


def _long_forms(terms: list[str], min_chars: int) -> list[str]:
    return [t for t in terms if t and len(t.replace(" ", "")) >= min_chars]


def _evidence_texts(*sources) -> list[str]:
    out = []
    for s in sources:
        if not s:
            continue
        if isinstance(s, str):
            out.append(s)
        else:
            out.extend(str(x) for x in s if x)
    return out


# ── identity ──────────────────────────────────────────────────────────────────

def identity_block(inp: PaperInput, rules: dict, hidden: list[Hidden]) -> tuple[dict | None, str | None]:
    """The bibliographic part of the card, or (None, reason) when the card is not published."""
    r = rules["identity"]
    ident = dict(inp.identity)
    pid = ident["paper_id"]
    status = SOURCE
    check = _checks_by_target(inp.checks).get(("paper", pid))
    if check and check.get("field") in (None, "metadata"):
        if check["verdict"] == "correct":
            status = HUMAN
        elif check["verdict"] == "incorrect":
            fix = {k: v for k, v in _corrected(check).items() if k in ("title", "year", "doi", "venue")}
            if not fix:
                return None, "human_rejected_metadata"
            ident.update(fix)
            status = HUMAN

    if ident.get("duplicate_of") or ident.get("identity_status") in r["excluded_identity_statuses"]:
        return None, f"identity_{ident.get('identity_status') or 'duplicate'}"
    title = (ident.get("title") or "").strip()
    if len(title) < r["min_title_chars"]:
        # core.paper lacks a title for some DOIs: OpenAlex, then the TEI header, fill it
        meta = (inp.normalized or {}).get("metadata") or {}
        title = next((t.strip() for t in (inp.bib.get("title"), meta.get("title"))
                      if t and len(t.strip()) >= r["min_title_chars"]), "")
        if not title:
            return None, "no_title"
    if not ident.get("year") and inp.bib.get("year"):
        ident["year"] = inp.bib["year"]

    doi = (ident.get("doi") or "").strip() or None
    if doi and ident.get("identity_status") in r["doubtful_identity_statuses"] and status != HUMAN:
        hidden.append(Hidden(pid, "links.doi", doi, ident["identity_status"]))
        doi = None
    oa_id = (ident.get("openalex_id") or "").strip() or None
    if oa_id and not oa_id.startswith("http"):
        oa_id = f"https://openalex.org/{oa_id}"
    links = {
        "doi": f"https://doi.org/{doi}" if doi else None,
        "openalex": oa_id,
        "open_access": inp.bib.get("oa_url") or None,
        "open_access_status": inp.bib.get("oa_status") or None,
    }
    if r["require_link"] and not (links["doi"] or links["openalex"]):
        return None, "no_link"

    authors = [a for a in (inp.bib.get("authors") or []) if a]
    block = {
        "paper_id": pid,
        "title": title,
        "year": int(ident["year"]) if str(ident.get("year") or "").isdigit() else None,
        "venue": ident.get("venue") or None,
        "authors": authors[: r["max_authors"]],
        "authors_truncated": len(authors) > r["max_authors"],
        "cited_by_count": inp.bib.get("cited_by_count"),
        "links": links,
        "metadata_status": status,
        "retracted": bool(inp.bib.get("is_retracted")),
    }
    return block, None


# ── entities: methods, sensors, data ──────────────────────────────────────────

def entity_list(inp: PaperInput, kind: str, rules: dict, hidden: list[Hidden]) -> list[dict]:
    r = rules["entities"]
    norm_key, ent_key, rel = ENTITY_KINDS[kind]
    pid = inp.identity["paper_id"]
    norm = (inp.normalized or {}).get("normalized_entities") or {}
    raw = {str(e.get("name")): e for e in ((inp.normalized or {}).get("entities") or {}).get(ent_key) or []
           if isinstance(e, dict)}
    checks = _checks_by_target(inp.checks)
    shown: dict[str, dict] = {}
    for n in norm.get(norm_key) or []:
        cid = n.get("canonical_id")
        name = n.get("display_name") or n.get("raw_name")
        if not cid:
            hidden.append(Hidden(pid, kind, str(n.get("raw_name")), "not_in_ontology"))
            continue
        if cid in shown:
            continue
        ext = raw.get(str(n.get("raw_name"))) or {}
        g = inp.grounding.get((rel, cid)) if rel else None
        human = checks.get(("entity_edge", f"{pid}|{cid}"))
        if human and human["verdict"] == "incorrect":
            hidden.append(Hidden(pid, kind, cid, "human_rejected"))
            continue
        if human and human["verdict"] == "correct":
            shown[cid] = {"id": cid, "name": name, "mentions": (g or {}).get("tei_mentions"), "status": HUMAN}
            continue

        reason = None
        if cid in r["generic_ids"]:
            reason = "generic_term"
        elif ext.get("role") not in r["show_roles"]:
            reason = f"role_{ext.get('role') or 'unknown'}"
        elif float(ext.get("final_score") or 0) < r["min_final_score"]:
            reason = "low_score"
        elif float(n.get("confidence") or 0) < r["min_normalized_confidence"]:
            reason = "ambiguous_normalization"
        elif g is not None and not g.get("grounded"):
            reason = "not_in_text"
        elif cid in r["ambiguous_acronyms"]:
            # "maximum likelihood", not "machine learning (ML)"; "cross-validation", not "coefficient of variation"
            longs = _long_forms([str(name or ""), *((g or {}).get("terms") or [])], r["long_form_min_chars"])
            evidence = _norm(" ".join(_evidence_texts((g or {}).get("tei_evidence"), ext.get("evidence"))))
            if not any(_norm(lf) in evidence for lf in longs):
                reason = "ambiguous_acronym"
        if reason:
            hidden.append(Hidden(pid, kind, cid, reason))
            continue
        shown[cid] = {"id": cid, "name": name, "mentions": (g or {}).get("tei_mentions"), "status": MODEL}

    items = sorted(shown.values(), key=lambda e: (e["status"] != HUMAN, -(e["mentions"] or 0), e["id"]))
    for e in items[r["max_per_kind"]:]:
        hidden.append(Hidden(pid, kind, e["id"], "over_limit"))
    return items[: r["max_per_kind"]]


# ── countries ─────────────────────────────────────────────────────────────────

def countries(inp: PaperInput, rules: dict, hidden: list[Hidden]) -> tuple[list[dict], list[dict]]:
    """(study-area countries, author-affiliation countries)."""
    r = rules["countries"]
    pid = inp.identity["paper_id"]
    geo = (((inp.normalized or {}).get("entities") or {}).get("geo")) or {}
    study = geo.get("study_geo") or {}
    check = _checks_by_target(inp.checks).get(("location", pid))
    if check and check["verdict"] == "incorrect":
        fix = _corrected(check)
        names = fix.get("countries") or ([fix["primary_country"]] if fix.get("primary_country") else [])
        hidden.append(Hidden(pid, "study_countries", str(study.get("primary_country")), "human_rejected"))
        study_out = [{"name": n, "status": HUMAN} for n in names]
    else:
        status = HUMAN if check and check["verdict"] == "correct" else MODEL
        org = re.compile(r["organisation_after_name"], re.I)
        seen: dict[str, dict] = {}
        for c in study.get("countries") or []:
            name = str(c.get("name") or "").strip()
            if not name or name in seen:
                continue
            reason = None
            if c.get("source") not in r["sources"]:
                reason = f"source_{c.get('source')}"
            elif r["require_name_in_evidence"]:
                ev = str(c.get("evidence") or "")
                hits = [m for m in re.finditer(rf"(?<!\w){re.escape(name)}(?!\w)", ev)
                        if not org.match(ev, m.end())]
                if not hits:
                    reason = "name_not_in_evidence"
            if reason:
                if c.get("source") != "ner":  # ner noise is thousands of names; not worth a report line
                    hidden.append(Hidden(pid, "study_countries", name, reason))
                continue
            seen[name] = {"name": name, "status": status}
        study_out = list(seen.values())[: r["max"]]

    authors: dict[str, dict] = {}
    for a in geo.get("author_geo") or []:
        name = str(a.get("name") or "").strip()
        if name and float(a.get("confidence") or 0) >= r["author_country_min_confidence"]:
            authors.setdefault(name, {"name": name, "status": MODEL})
    return study_out, list(authors.values())[: r["max"]]


# ── task / study type / topics ────────────────────────────────────────────────

def label(value: dict | None, name: str, inp: PaperInput, rules: dict, hidden: list[Hidden]) -> dict | None:
    r = rules["labels"]
    if not isinstance(value, dict) or not value.get("label"):
        return None
    if name not in r["publish"]:
        hidden.append(Hidden(inp.identity["paper_id"], name, str(value["label"]), "label_not_validated"))
        return None
    v, conf = str(value["label"]), float(value.get("confidence") or 0)
    if v.lower() in r["hidden_values"]:
        return None
    if conf < r["min_confidence"]:
        hidden.append(Hidden(inp.identity["paper_id"], name, v, "low_confidence"))
        return None
    return {"value": v, "confidence": round(conf, 2), "status": MODEL}


def topics(inp: PaperInput, rules: dict) -> list[dict]:
    r = rules["topics"]
    good = sorted((t for t in inp.topics if t[1] is not None and t[1] >= r["min_score"]), key=lambda t: (-t[1], t[0]))
    return [{"name": n, "score": round(float(s), 3), "status": SOURCE} for n, s in good[: r["max"]]]


# ── numeric results from tables ───────────────────────────────────────────────

def _context(raw) -> str:
    if raw is None:
        return ""
    items = raw
    if isinstance(raw, str):
        try:
            items = json.loads(raw)
        except ValueError:
            items = [raw]
    if isinstance(items, str):
        items = [items]
    return " · ".join(str(x).strip() for x in items if str(x).strip())


def _unit(raw) -> str | None:
    if raw is None:
        return None
    u = metric_rules.unit_of(raw)
    return u or _EXTRA_UNITS.get(str(raw).strip().lower().replace(" ", ""))


def results(inp: PaperInput, rules: dict, hidden: list[Hidden]) -> list[dict]:
    r = rules["results"]
    pid = inp.identity["paper_id"]
    checks = _checks_by_target(inp.checks)
    out, seen = [], set()
    for f in sorted(inp.facts, key=lambda f: (f.get("page") or 0, str(f.get("table_id")), str(f.get("fact_id")))):
        cid = str(f.get("canonical_id") or "")
        fid = str(f.get("fact_id"))
        value = f.get("value")
        unit = _unit(f.get("unit"))
        ctx = _context(f.get("row_context"))
        human = checks.get(("metric_fact", fid))
        status = MODEL
        if human and human["verdict"] == "incorrect":
            fix = _corrected(human)
            if "value" not in fix:
                hidden.append(Hidden(pid, "results", fid, "human_rejected"))
                continue
            value, unit, status = fix["value"], fix.get("unit", unit), HUMAN
        elif human and human["verdict"] == "correct":
            status = HUMAN

        if status != HUMAN:
            reason = None
            rng = METRIC_RANGES.get(metric_rules.range_key(cid)) if cid.startswith("metric.") else None
            if value is None:
                reason = "no_value"
            elif rng is None:
                reason = "metric_without_range"
            else:
                value = float(value)
                dimensionless = rng.hi == 1.0
                # only an explicit "%" turns 94.2 into 0.942; kappa 1.4 without it is an error, not 1.4 %
                if dimensionless and unit == "%":
                    value, unit = (value / 100.0 if abs(value) <= 100.0 else value), None
                if float(f.get("confidence") or 0) < r["min_confidence"]:
                    reason = "low_confidence"
                elif r["require_range_ok"] and metric_rules.verdict(cid, value) != "ok":
                    reason = "out_of_range"
                elif r["require_unit_for_dimensional"] and not dimensionless and not unit:
                    reason = "unit_unknown"
                elif not ctx or len(ctx) > r["max_context_chars"] or not re.search(r"[A-Za-z]", ctx):
                    reason = "no_row_label"
            if reason:
                hidden.append(Hidden(pid, "results", fid, reason))
                continue

        key = (cid, ctx, round(float(value), 6), unit)
        if key in seen:
            continue
        seen.add(key)
        out.append({"fact_id": fid, "metric": cid, "metric_label": metric_rules.label(cid), "value": value,
                    "unit": unit, "context": ctx, "table": f.get("table_label"), "page": f.get("page"),
                    "status": status})
    out.sort(key=lambda x: (x["status"] != HUMAN,))
    for x in out[r["max"]:]:
        hidden.append(Hidden(pid, "results", x["fact_id"], "over_limit"))
    return out[: r["max"]]


# ── the card ──────────────────────────────────────────────────────────────────

def build_card(inp: PaperInput, rules: dict | None = None) -> tuple[dict | None, str | None, list[Hidden]]:
    """(card, None, hidden) or (None, exclusion reason, hidden)."""
    rules = rules or load_rules()
    hidden: list[Hidden] = []
    ident, why = identity_block(inp, rules, hidden)
    if ident is None:
        return None, why, hidden

    norm = inp.normalized
    if norm is None:
        analysis = None
    else:
        study_c, author_c = countries(inp, rules, hidden)
        analysis = {
            "task": label(norm.get("task") or (norm.get("entities") or {}).get("task"), "task", inp, rules, hidden),
            "study_type": label(norm.get("study_type") if isinstance(norm.get("study_type"), dict)
                                else ((norm.get("entities") or {}).get("geo") or {}).get("study_type"),
                                "study_type", inp, rules, hidden),
            "study_countries": study_c,
            "author_countries": author_c,
            "methods": entity_list(inp, "methods", rules, hidden),
            "sensors": entity_list(inp, "sensors", rules, hidden),
            "data": entity_list(inp, "data", rules, hidden),
            "results": results(inp, rules, hidden),
        }
    reasons: dict[str, int] = {}
    for h in hidden:
        reasons[f"{h.field}:{h.reason}"] = reasons.get(f"{h.field}:{h.reason}", 0) + 1
    card = {
        **ident,
        "topics": topics(inp, rules),
        "analysis": analysis,
        "quality": {
            "rules_version": rules["version"],
            "analysis_available": analysis is not None,
            "human_checks": len(inp.checks),
            "hidden": dict(sorted(reasons.items())),
        },
    }
    return card, None, hidden
