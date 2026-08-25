"""
engineer.py — Stage 2: Scientific Object Engineering.

Responsibility: read Stage 1 parsed artifacts, classify each structural
element as a typed scientific object, build the object graph, and persist
to data/sodb/{paper_id}/.

Output contract (appended to data/sodb/{paper_id}/):
    scientific_objects.parquet — typed scientific objects with semantic labels
    object_graph.parquet       — directed edges between objects
    object_manifest.json       — stage 2 completion record

Design rules:
  - NO NLP, NO embeddings, NO LLMs — heuristic rules only.
  - Reads only from data/parsed/{paper_id}/ (Stage 1 artifacts).
  - Writes only to data/sodb/{paper_id}/ (SODB layer).
  - Never mutates Stage 1 or Stage 0 artifacts.
  - All writes are atomic (ParsedStore / SODBWriter).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from src.analytics.parquet_schema import OBJECT_GRAPH_SCHEMA, SCIENTIFIC_OBJECTS_SCHEMA
from src.config.settings import PARSED_DIR, PIPELINE_VERSION
from src.document.object_classifier import ScientificObjectClassifier
from src.document.scientific_objects import ObjectEdge, ScientificObject, SemanticType
from src.ingestion.stage1.parser_runner import Stage1Result

log = logging.getLogger(__name__)

_SODB_ROOT = Path(__file__).resolve().parents[4] / "data" / "sodb"

import os
_SODB_ROOT = Path(os.getenv("SODB_DIR", str(_SODB_ROOT)))


@dataclass(frozen=True)
class Stage2Result:
    """Output contract of Stage2Engineer.run()."""
    paper_id:      str
    status:        str           # "ok" | "skipped" | "error"
    sodb_root:     Path
    object_count:  int = 0
    edge_count:    int = 0
    events:        list[str] = field(default_factory=list)

    @property
    def should_continue(self) -> bool:
        return self.status == "ok"


class Stage2Engineer:
    """
    Builds scientific objects from Stage 1 parsed artifacts.

    Parameters
    ----------
    sodb_root     : Root of data/sodb/ tree.
    parsed_root   : Root of data/parsed/ tree.
    pipeline_hash : Pipeline version identifier for provenance.
    force         : Re-engineer even if Stage 2 artifacts already exist.
    """

    def __init__(
        self,
        sodb_root:     Path | None = None,
        parsed_root:   Path | None = None,
        pipeline_hash: str | None  = None,
        force:         bool        = False,
    ) -> None:
        self._sodb_root     = sodb_root     or _SODB_ROOT
        self._parsed_root   = parsed_root   or PARSED_DIR
        self._pipeline_hash = pipeline_hash or PIPELINE_VERSION
        self._force         = force
        self._classifier    = ScientificObjectClassifier()

    def run(self, stage1: Stage1Result) -> Stage2Result:
        """
        Build scientific objects from a completed Stage 1 result.

        Parameters
        ----------
        stage1 : A successful Stage1Result (status == "ok" or "skipped").

        Returns
        -------
        Stage2Result — never raises.
        """
        if stage1.status == "error":
            return Stage2Result(
                paper_id=stage1.paper_id, status="error",
                sodb_root=self._sodb_root,
                events=[f"stage1_error:{stage1.status}"],
            )

        paper_id   = stage1.paper_id
        paper_dir  = self._sodb_root / paper_id
        paper_dir.mkdir(parents=True, exist_ok=True)

        obj_path  = paper_dir / "scientific_objects.parquet"
        edge_path = paper_dir / "object_graph.parquet"
        mfst_path = paper_dir / "object_manifest.json"

        if obj_path.exists() and edge_path.exists() and mfst_path.exists() and not self._force:
            log.debug("[stage2] already complete — %s", paper_id[:16])
            return Stage2Result(
                paper_id=paper_id, status="skipped",
                sodb_root=paper_dir,
                events=["already_complete"],
            )

        events: list[str] = [f"start:{_now_iso()}"]
        parsed_dir = self._parsed_root / paper_id

        # ── Load Stage 1 artifacts ────────────────────────────────────────────
        try:
            artifacts = _load_artifacts(parsed_dir)
        except Exception as exc:
            log.error("[stage2] load failed for %s: %s", paper_id[:16], exc)
            return Stage2Result(
                paper_id=paper_id, status="error",
                sodb_root=paper_dir,
                events=events + [f"load_error:{exc}"],
            )

        events.append("artifacts_loaded")

        # ── Build scientific objects ───────────────────────────────────────────
        try:
            objects, edges = self._build_objects(paper_id, artifacts)
        except Exception as exc:
            log.error("[stage2] build failed for %s: %s", paper_id[:16], exc)
            return Stage2Result(
                paper_id=paper_id, status="error",
                sodb_root=paper_dir,
                events=events + [f"build_error:{exc}"],
            )

        events.append(f"built:objects={len(objects)},edges={len(edges)}")

        # ── Write parquet artifacts ───────────────────────────────────────────
        try:
            _write_objects(objects, obj_path, self._pipeline_hash)
            _write_edges(edges, edge_path, self._pipeline_hash)
            _write_manifest(mfst_path, paper_id, self._pipeline_hash,
                            len(objects), len(edges))
        except Exception as exc:
            log.error("[stage2] write failed for %s: %s", paper_id[:16], exc)
            return Stage2Result(
                paper_id=paper_id, status="error",
                sodb_root=paper_dir,
                events=events + [f"write_error:{exc}"],
            )

        events.append(f"done:{_now_iso()}")
        log.info("[stage2] complete — %s objects=%d edges=%d",
                 paper_id[:16], len(objects), len(edges))

        return Stage2Result(
            paper_id=paper_id, status="ok",
            sodb_root=paper_dir,
            object_count=len(objects),
            edge_count=len(edges),
            events=events,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_objects(
        self,
        paper_id:  str,
        artifacts: dict[str, list[dict[str, Any]]],
    ) -> tuple[list[ScientificObject], list[ObjectEdge]]:
        objects: list[ScientificObject] = []
        edges:   list[ObjectEdge]       = []

        _add_sections(  paper_id, artifacts.get("sections",   []), self._classifier, objects, edges)
        _add_figures(   paper_id, artifacts.get("figures",    []), self._classifier, objects)
        _add_tables(    paper_id, artifacts.get("tables",     []), self._classifier, objects)
        _add_equations( paper_id, artifacts.get("equations",  []), self._classifier, objects)
        _add_references(paper_id, artifacts.get("references", []), self._classifier, objects)

        # Build IN_SECTION edges (figures and tables reference their caption's
        # section by sequential position — cheap heuristic, good enough for Stage 2)
        _add_follows_edges(paper_id, objects, edges)

        return objects, edges


# ── Object builders ───────────────────────────────────────────────────────────

def _add_sections(
    paper_id:   str,
    rows:       list[dict[str, Any]],
    clf:        ScientificObjectClassifier,
    objects:    list[ScientificObject],
    edges:      list[ObjectEdge],
) -> None:
    for row in rows:
        sem, score, src, flags = clf.classify_section(row)
        obj = ScientificObject(
            object_id=         row["section_id"],
            paper_id=          paper_id,
            object_type=       "section",
            semantic_type=     sem,
            source_id=         row["section_id"],
            page=              row.get("page"),
            raw_text=          (row.get("text") or "")[:2000],
            classifier_score=  score,
            classifier_source= src,
            **_pick_flags(flags),
        )
        objects.append(obj)


def _add_figures(
    paper_id: str,
    rows:     list[dict[str, Any]],
    clf:      ScientificObjectClassifier,
    objects:  list[ScientificObject],
) -> None:
    for row in rows:
        sem, score, src, flags = clf.classify_figure(row)
        obj = ScientificObject(
            object_id=         row["figure_id"],
            paper_id=          paper_id,
            object_type=       "figure",
            semantic_type=     sem,
            source_id=         row["figure_id"],
            page=              row.get("page"),
            raw_text=          row.get("caption"),
            classifier_score=  score,
            classifier_source= src,
            **_pick_flags(flags),
        )
        objects.append(obj)


def _add_tables(
    paper_id: str,
    rows:     list[dict[str, Any]],
    clf:      ScientificObjectClassifier,
    objects:  list[ScientificObject],
) -> None:
    for row in rows:
        sem, score, src, flags = clf.classify_table(row)
        header = row.get("header_row") or []
        obj = ScientificObject(
            object_id=         row["table_id"],
            paper_id=          paper_id,
            object_type=       "table",
            semantic_type=     sem,
            source_id=         row["table_id"],
            page=              row.get("page"),
            raw_text=          row.get("caption"),
            classifier_score=  score,
            classifier_source= src,
            candidate_vars=    json.dumps(header) if header else None,
            **_pick_flags(flags),
        )
        objects.append(obj)


def _add_equations(
    paper_id: str,
    rows:     list[dict[str, Any]],
    clf:      ScientificObjectClassifier,
    objects:  list[ScientificObject],
) -> None:
    for row in rows:
        sem, score, src, flags = clf.classify_equation(row)
        obj = ScientificObject(
            object_id=         row["equation_id"],
            paper_id=          paper_id,
            object_type=       "equation",
            semantic_type=     sem,
            source_id=         row["equation_id"],
            page=              row.get("page"),
            raw_text=          row.get("text"),
            classifier_score=  score,
            classifier_source= src,
            equation_name=     flags.pop("equation_name", None),
            **_pick_flags(flags),
        )
        objects.append(obj)


def _add_references(
    paper_id: str,
    rows:     list[dict[str, Any]],
    clf:      ScientificObjectClassifier,
    objects:  list[ScientificObject],
) -> None:
    for row in rows:
        sem, score, src, flags = clf.classify_reference(row)
        obj = ScientificObject(
            object_id=         row["ref_id"],
            paper_id=          paper_id,
            object_type=       "reference",
            semantic_type=     sem,
            source_id=         row["ref_id"],
            page=              None,
            raw_text=          row.get("raw") or row.get("title"),
            classifier_score=  score,
            classifier_source= src,
        )
        objects.append(obj)


def _add_follows_edges(
    paper_id: str,
    objects:  list[ScientificObject],
    edges:    list[ObjectEdge],
) -> None:
    sections = [o for o in objects if o.object_type == "section"]
    for i in range(len(sections) - 1):
        eid = f"{paper_id}_edge_follows_{i:04d}"
        edges.append(ObjectEdge(
            edge_id=   eid,
            paper_id=  paper_id,
            source_id= sections[i].object_id,
            target_id= sections[i + 1].object_id,
            relation=  "FOLLOWS",
            confidence=1.0,
        ))


# ── Flag helpers ──────────────────────────────────────────────────────────────

_FLAG_FIELDS = {
    "contains_models", "contains_metrics", "contains_timeseries",
    "contains_coordinates", "contains_satellite", "contains_hydrograph",
    "candidate_models", "candidate_metrics",
}


def _pick_flags(flags: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in flags.items() if k in _FLAG_FIELDS}


# ── Parquet writers ───────────────────────────────────────────────────────────

def _write_objects(
    objects:       list[ScientificObject],
    path:          Path,
    pipeline_hash: str,
) -> None:
    now  = datetime.now(timezone.utc)
    rows = []
    for obj in objects:
        row = obj.to_row()
        row["pipeline_hash"] = pipeline_hash
        row["created_at"]    = now
        rows.append(row)
    table = pa.Table.from_pylist(rows, schema=SCIENTIFIC_OBJECTS_SCHEMA)
    tmp   = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="snappy")
    tmp.rename(path)


def _write_edges(
    edges:         list[ObjectEdge],
    path:          Path,
    pipeline_hash: str,
) -> None:
    now  = datetime.now(timezone.utc)
    rows = []
    for edge in edges:
        row = edge.to_row()
        row["pipeline_hash"] = pipeline_hash
        row["created_at"]    = now
        rows.append(row)
    table = pa.Table.from_pylist(rows, schema=OBJECT_GRAPH_SCHEMA)
    tmp   = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="snappy")
    tmp.rename(path)


def _write_manifest(
    path:          Path,
    paper_id:      str,
    pipeline_hash: str,
    object_count:  int,
    edge_count:    int,
) -> None:
    payload = {
        "paper_id":      paper_id,
        "pipeline_hash": pipeline_hash,
        "completed_at":  _now_iso(),
        "object_count":  object_count,
        "edge_count":    edge_count,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.rename(path)


# ── Artifact reader ───────────────────────────────────────────────────────────

def _load_artifacts(parsed_dir: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for name in ("sections", "figures", "tables", "equations", "references"):
        path = parsed_dir / f"{name}.parquet"
        if path.exists():
            result[name] = pq.read_table(path).to_pylist()
        else:
            result[name] = []
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
