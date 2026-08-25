"""
semantic_validator.py — Stage 2.5: Object Semantic Validation.

Responsibility:
  Read Stage 1 parsed artifacts + Stage 2 scientific objects for one paper,
  run every object through type-specific semantic validators and the cross-
  object reasoning engine, enforce consistency constraints, build semantic
  graph edges, and persist:

      data/sodb/{paper_id}/
          semantic_annotations.parquet  — one row per validated object
          semantic_edges.parquet        — typed semantic graph edges
          semantic_manifest.json        — stage completion record

Design rules:
  - Reads ONLY from data/parsed/ and data/sodb/ (stages 1+2).
  - Writes ONLY to data/sodb/{paper_id}/.
  - Never mutates Stage 0–2 artifacts.
  - All writes are atomic (.tmp → rename).
  - Never raises — all exceptions are caught and surfaced in Stage25Result.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from src.analytics.parquet_schema import SEMANTIC_ANNOTATIONS_SCHEMA, SEMANTIC_EDGES_SCHEMA
from src.config.settings import PARSED_DIR, PIPELINE_VERSION
from src.semantic_objects.equation_semantics import EquationSemanticValidator
from src.semantic_objects.figure_semantics import FigureSemanticValidator
from src.semantic_objects.object_reasoner import ObjectReasoner
from src.semantic_objects.semantic_constraints import apply_constraints
from src.semantic_objects.semantic_evidence import (
    ObjectContext,
    ScientificRole,
    SemanticAnnotation,
    SemanticEdge,
    Stage25Result,
)
from src.semantic_objects.table_semantics import TableSemanticValidator

log = logging.getLogger(__name__)

_SODB_ROOT = Path(os.getenv("SODB_DIR",
    str(Path(__file__).resolve().parents[3] / "data" / "sodb")))

_SECTION_TEXT_LIMIT = 2000  # chars — truncate to keep context manageable


class SemanticValidatorStage:
    """
    Stage 2.5 orchestrator.

    Parameters
    ----------
    sodb_root     : Root of data/sodb/ tree.
    parsed_root   : Root of data/parsed/ tree.
    pipeline_hash : Pipeline version identifier.
    force         : Re-validate even when semantic artifacts already exist.
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

        self._fig_validator = FigureSemanticValidator()
        self._tbl_validator = TableSemanticValidator()
        self._eq_validator  = EquationSemanticValidator()
        self._reasoner      = ObjectReasoner()

    def run(self, paper_id: str) -> Stage25Result:
        """
        Validate all scientific objects for one paper.

        Returns
        -------
        Stage25Result — never raises.
        """
        paper_dir   = self._sodb_root / paper_id
        ann_path    = paper_dir / "semantic_annotations.parquet"
        edge_path   = paper_dir / "semantic_edges.parquet"
        mfst_path   = paper_dir / "semantic_manifest.json"
        parsed_dir  = self._parsed_root / paper_id
        events: list[str] = [f"start:{_now_iso()}"]

        if ann_path.exists() and edge_path.exists() and mfst_path.exists() and not self._force:
            log.debug("[stage2.5] already complete — %s", paper_id[:16])
            return Stage25Result(
                paper_id=paper_id, status="skipped",
                sodb_root=paper_dir, events=["already_complete"],
            )

        # ── Load Stage 1 artifacts ────────────────────────────────────────────
        try:
            artifacts = _load_artifacts(parsed_dir)
        except Exception as exc:
            log.error("[stage2.5] load failed for %s: %s", paper_id[:16], exc)
            return Stage25Result(
                paper_id=paper_id, status="error",
                sodb_root=paper_dir,
                events=events + [f"load_error:{exc}"],
            )

        # ── Load Stage 2 scientific_objects ───────────────────────────────────
        sci_objects = _load_sci_objects(paper_dir)

        # ── Build indexes ─────────────────────────────────────────────────────
        section_index  = _build_section_index(artifacts["sections"])
        caption_index  = _build_caption_index(artifacts["captions"])
        sci_obj_index  = {r["object_id"]: r for r in sci_objects}

        events.append("artifacts_loaded")

        # ── Validate each object type ─────────────────────────────────────────
        annotations: list[SemanticAnnotation] = []

        for row in artifacts.get("figures", []):
            ctx = _build_context(row, "figure_id", section_index, caption_index,
                                 sci_obj_index, paper_id)
            ann = self._fig_validator.validate(row, ctx)
            self._reasoner.apply_rules(ann, ctx)
            apply_constraints(ann)
            annotations.append(ann)

        for row in artifacts.get("tables", []):
            ctx = _build_context(row, "table_id", section_index, caption_index,
                                 sci_obj_index, paper_id)
            ann = self._tbl_validator.validate(row, ctx)
            self._reasoner.apply_rules(ann, ctx)
            apply_constraints(ann)
            annotations.append(ann)

        for row in artifacts.get("equations", []):
            ctx = _build_context(row, "equation_id", section_index, caption_index,
                                 sci_obj_index, paper_id)
            ann = self._eq_validator.validate(row, ctx)
            self._reasoner.apply_rules(ann, ctx)
            apply_constraints(ann)
            annotations.append(ann)

        events.append(f"validated:{len(annotations)}")

        # ── Build semantic graph edges ─────────────────────────────────────────
        edges = _build_semantic_edges(annotations, artifacts.get("sections", []), paper_id)
        events.append(f"edges:{len(edges)}")

        # ── Write outputs ─────────────────────────────────────────────────────
        try:
            paper_dir.mkdir(parents=True, exist_ok=True)
            _write_annotations(annotations, ann_path, self._pipeline_hash)
            _write_edges(edges, edge_path, self._pipeline_hash)
            _write_manifest(mfst_path, paper_id, self._pipeline_hash,
                            len(annotations), len(edges))
        except Exception as exc:
            log.error("[stage2.5] write failed for %s: %s", paper_id[:16], exc)
            return Stage25Result(
                paper_id=paper_id, status="error",
                sodb_root=paper_dir,
                events=events + [f"write_error:{exc}"],
            )

        events.append(f"done:{_now_iso()}")
        log.info("[stage2.5] complete — %s annotations=%d edges=%d",
                 paper_id[:16], len(annotations), len(edges))

        return Stage25Result(
            paper_id=paper_id, status="ok",
            sodb_root=paper_dir,
            annotation_count=len(annotations),
            edge_count=len(edges),
            events=events,
        )


# ── Context builder ───────────────────────────────────────────────────────────

def _build_context(
    row:           dict[str, Any],
    id_field:      str,
    section_index: list[dict[str, Any]],
    caption_index: dict[str, str],
    sci_obj_index: dict[str, dict[str, Any]],
    paper_id:      str,
) -> ObjectContext:
    page      = row.get("page")
    sec       = _nearest_section(page, section_index)
    obj_id    = row.get(id_field, "")
    caption   = caption_index.get(obj_id)

    return ObjectContext(
        parsed_row=         row,
        sci_object=         sci_obj_index.get(obj_id),
        section_title=      sec.get("title") if sec else None,
        section_text=       (sec.get("text") or "")[:_SECTION_TEXT_LIMIT] if sec else None,
        caption=            caption,
        label=              row.get("label"),
        all_section_titles= [s.get("title", "") for s in section_index],
        paper_id=           paper_id,
    )


def _nearest_section(
    page: int | None,
    sections: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not sections:
        return None
    if page is None:
        return sections[0]
    # Find the section whose page is ≤ object page (closest start)
    best = None
    for sec in sections:
        sec_page = sec.get("page")
        if sec_page is None:
            continue
        if sec_page <= page:
            if best is None or sec_page > best.get("page", -1):
                best = sec
    return best or sections[0]


def _build_section_index(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(sections, key=lambda s: s.get("page") or 0)


def _build_caption_index(captions: list[dict[str, Any]]) -> dict[str, str]:
    """Map parent_id (figure_id / table_id) → caption text."""
    return {
        row["parent_id"]: (row.get("text") or "")
        for row in captions
        if row.get("parent_id")
    }


# ── Semantic graph builder ────────────────────────────────────────────────────

def _build_semantic_edges(
    annotations: list[SemanticAnnotation],
    sections:    list[dict[str, Any]],
    paper_id:    str,
) -> list[SemanticEdge]:
    edges: list[SemanticEdge] = []
    section_objects = [a for a in annotations if a.object_type == "section"]
    eval_objects    = [a for a in annotations
                       if ScientificRole.EVALUATION in a.scientific_role
                       or ScientificRole.EVALUATION_VIZ in a.scientific_role]
    model_output    = [a for a in annotations
                       if ScientificRole.MODEL_OUTPUT in a.scientific_role]

    # EVALUATES: metrics/comparison tables → results sections
    result_sections = [a for a in section_objects
                       if _is_results_section(a.semantic_type)]
    for ev in eval_objects:
        for sec in result_sections:
            eid = f"{paper_id}_sem_eval_{ev.object_id[:8]}_{sec.object_id[:8]}"
            edges.append(SemanticEdge(
                edge_id=   eid,
                paper_id=  paper_id,
                source_id= ev.object_id,
                target_id= sec.object_id,
                relation=  "EVALUATES",
                confidence=min(ev.semantic_confidence, 0.95),
                evidence=  f"{ev.semantic_type}→{sec.semantic_type}",
            ))

    # VISUALIZES: model-output figures → results sections
    for fig in model_output:
        if fig.object_type != "figure":
            continue
        for sec in result_sections:
            eid = f"{paper_id}_sem_viz_{fig.object_id[:8]}_{sec.object_id[:8]}"
            edges.append(SemanticEdge(
                edge_id=   eid,
                paper_id=  paper_id,
                source_id= fig.object_id,
                target_id= sec.object_id,
                relation=  "VISUALIZES",
                confidence=min(fig.semantic_confidence, 0.90),
                evidence=  f"{fig.semantic_type}→results",
            ))

    # VALIDATES: validation-role tables → same result sections
    for tbl in [a for a in annotations
                if a.object_type == "table"
                and ScientificRole.MODEL_VALIDATION in a.scientific_role]:
        for sec in result_sections:
            eid = f"{paper_id}_sem_val_{tbl.object_id[:8]}_{sec.object_id[:8]}"
            edges.append(SemanticEdge(
                edge_id=   eid,
                paper_id=  paper_id,
                source_id= tbl.object_id,
                target_id= sec.object_id,
                relation=  "VALIDATES",
                confidence=min(tbl.semantic_confidence, 0.92),
                evidence=  "metrics_table→results",
            ))

    return edges


def _is_results_section(semantic_type: str) -> bool:
    from src.document.scientific_objects import SemanticType
    return semantic_type in {
        SemanticType.RESULTS_SECTION,
        SemanticType.CONCLUSION_SECTION,
    }


# ── Parquet writers ───────────────────────────────────────────────────────────

def _write_annotations(
    annotations:   list[SemanticAnnotation],
    path:          Path,
    pipeline_hash: str,
) -> None:
    now  = datetime.now(timezone.utc)
    rows = []
    for ann in annotations:
        row = ann.to_row()
        row["pipeline_hash"] = pipeline_hash
        row["created_at"]    = now
        rows.append(row)
    table = pa.Table.from_pylist(rows, schema=SEMANTIC_ANNOTATIONS_SCHEMA)
    tmp   = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="snappy")
    tmp.rename(path)


def _write_edges(
    edges:         list[SemanticEdge],
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
    table = pa.Table.from_pylist(rows, schema=SEMANTIC_EDGES_SCHEMA)
    tmp   = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="snappy")
    tmp.rename(path)


def _write_manifest(
    path:          Path,
    paper_id:      str,
    pipeline_hash: str,
    ann_count:     int,
    edge_count:    int,
) -> None:
    payload = {
        "paper_id":         paper_id,
        "pipeline_hash":    pipeline_hash,
        "completed_at":     _now_iso(),
        "annotation_count": ann_count,
        "edge_count":       edge_count,
        "stage":            "2.5",
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.rename(path)


# ── Artifact loaders ──────────────────────────────────────────────────────────

def _load_artifacts(parsed_dir: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for name in ("sections", "figures", "tables", "equations", "captions"):
        path = parsed_dir / f"{name}.parquet"
        if path.exists():
            result[name] = pq.read_table(path).to_pylist()
        else:
            result[name] = []
    return result


def _load_sci_objects(sodb_dir: Path) -> list[dict[str, Any]]:
    path = sodb_dir / "scientific_objects.parquet"
    if not path.exists():
        return []
    return pq.read_table(path).to_pylist()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
