"""Entities of one paper (GET /papers/{paper_id}/entities), from the files the graph is built from.

Methods, sensors and metric mentions come from the normalized JSON with the same edge
logic as the graph loader (surface form, extractor role, evidence snippets, PDF page) and
the grounding file (src/graph/entity_grounding.py): `grounded` tells whether the term
occurs as a word in the paper's TEI text, with those sentences in `tei_evidence`.
Task, study type and study area are the legacy extractor's labels. Country names are
filtered for obvious non-names (URLs, "al.") and the number dropped is reported.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIELDS = (("methods", "USES_METHOD", "methods"), ("satellites", "USES_SENSOR", "sensors"),
          ("metrics", "REPORTS_METRIC", "metrics"))


def normalized_path(paper_id: str) -> Path | None:
    from sqlalchemy import select

    from src.db.engine import session_scope
    from src.db.models import PaperFile
    with session_scope() as s:
        rel = s.scalar(select(PaperFile.path).where(PaperFile.paper_id == paper_id, PaperFile.kind == "normalized",
                                                    PaperFile.status == "ok").limit(1))
    path = ROOT / rel if rel else None
    return path if path and path.is_file() else None


@lru_cache(maxsize=1)
def _grounding(mtime: float) -> dict:
    from src.graph.graph_loader import load_entity_grounding
    return load_entity_grounding()


def grounding() -> dict:
    from src.graph.graph_loader import GROUNDING_FILE
    return _grounding(GROUNDING_FILE.stat().st_mtime) if GROUNDING_FILE.exists() else {}


def _country_ok(name: str) -> bool:
    n = (name or "").strip()
    return len(n) >= 3 and not re.search(r"[/@:\d]|\.\w{2,}$|^\W", n) and not re.fullmatch(r"\w{1,3}\.", n)


def _label(obj) -> dict | None:
    if not isinstance(obj, dict) or not obj.get("label"):
        return None
    return {"label": obj["label"], "confidence": obj.get("confidence"), "source": obj.get("source")}


def paper_entities(paper_id: str) -> dict | None:
    from src.graph.graph_loader import _edge_row, _raw_index
    path = normalized_path(paper_id)
    if path is None:
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    raw = _raw_index(doc)
    ground = grounding()
    names = {}
    out: dict = {"methods": [], "sensors": [], "metrics": []}
    for field, rel, key in FIELDS:
        seen = set()
        for ent in (doc.get("normalized_entities") or {}).get(field, []) or []:
            cid = ent.get("canonical_id")
            if not cid or cid in seen or (field == "satellites" and not cid.startswith("sensor.")):
                continue
            seen.add(cid)
            row = _edge_row(paper_id, ent, raw.get((ent.get("source_field") or field, str(ent.get("raw_name") or "")), []))
            g = ground.get((rel, paper_id, cid), {})
            names[cid] = ent.get("display_name")
            out[key].append({"canonical_id": cid, "display_name": ent.get("display_name"),
                             "surface_form": row["surface_form"] or None, "role": row["role"],
                             "confidence": row["confidence"], "grounded": g.get("grounded"),
                             "tei_mentions": g.get("tei_mentions"), "tei_evidence": list(g.get("tei_evidence") or []),
                             "evidence": row["evidence"], "page": row["page"]})
        out[key].sort(key=lambda m: ({True: 0, None: 1, False: 2}[m["grounded"]], m["role"] != "used", m["canonical_id"]))
    ents = doc.get("entities") or {}
    geo = ents.get("geo") or {}
    study = geo.get("study_geo") or {}
    countries = [c for c in study.get("countries") or [] if isinstance(c, dict)]
    kept = [c for c in countries if _country_ok(c.get("name"))]
    out["study_area"] = {"primary_country": study.get("primary_country"),
                         "countries": [{"name": c.get("name"), "source": c.get("source"),
                                        "confidence": c.get("confidence")} for c in kept],
                         "rivers": [r.get("name") if isinstance(r, dict) else r for r in study.get("rivers") or []][:20],
                         "dropped_country_names": len(countries) - len(kept)}
    out["task"] = _label(ents.get("task"))
    out["study_type"] = _label(geo.get("study_type"))
    out["source_file"] = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    return out
