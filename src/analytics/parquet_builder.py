"""
parquet_builder.py  —  GeoHydroAI Stage 3A parquet analytics builder
=====================================================================

Streams data/enriched/*.json one file at a time and writes 11 parquet tables
to data/analytics/.  Does NOT load all files into RAM simultaneously.

Source of truth for entity data:
    methods / sensors / metrics  — ONLY from paper.normalized_entities
                                   ONLY rows where canonical_id is not None
                                   NEVER from raw NER labels or paper.entities lists

Architecture
------------
Stage 1: XML → paper.json      (do not touch)
Stage 2: normalization backfill (do not touch)
Stage 2B: OpenAlex enrichment   (do not touch)
Stage 3A: this module → parquet

Usage
-----
    python -m src.analytics.parquet_builder
    python -m src.analytics.parquet_builder --overwrite
    python -m src.analytics.parquet_builder --limit 100
    python -m src.analytics.parquet_builder \\
        --input-dir data/enriched \\
        --output-dir data/analytics

Flags
-----
    --input-dir PATH   Enriched JSON directory   (default: data/enriched/)
    --output-dir PATH  Parquet output directory  (default: data/analytics/)
    --overwrite        Overwrite existing parquet files
    --limit N          Process only first N enriched files
    --log-level LEVEL  Logging verbosity          (default: INFO)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pyarrow as pa
import pyarrow.parquet as pq

from src.analytics.parquet_schema import (
    TABLE_REGISTRY,
    PAPERS_SCHEMA,
    AUTHORS_SCHEMA,
    PAPER_AUTHOR_EDGES_SCHEMA,
    TOPICS_SCHEMA,
    PAPER_TOPIC_EDGES_SCHEMA,
    REFERENCES_SCHEMA,
    PAPER_REFERENCE_EDGES_SCHEMA,
    METHODS_SCHEMA,
    SENSORS_SCHEMA,
    METRICS_SCHEMA,
    INSTITUTIONS_SCHEMA,
)

log = logging.getLogger(__name__)

_PROJECT_ROOT  = Path(__file__).resolve().parents[2]
_DEFAULT_INPUT  = _PROJECT_ROOT / "data" / "enriched"
_DEFAULT_OUTPUT = _PROJECT_ROOT / "data" / "analytics"


# ─────────────────────────────────────────────────────────────────────────────
# Reference ID hashing
# ─────────────────────────────────────────────────────────────────────────────

def _ref_id(doi: Optional[str], title: Optional[str], raw_text: Optional[str]) -> Optional[str]:
    """
    Deterministic 16-char hex ID for a bibliography entry.

    Priority: doi → title → raw_text.  Returns None if all empty.
    """
    if doi:
        key = f"doi:{doi.strip().lower()}"
    elif title:
        key = f"title:{title.strip().lower()}"
    elif raw_text:
        key = f"raw:{raw_text[:200].strip().lower()}"
    else:
        return None
    return hashlib.sha1(key.encode()).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
# Per-file extraction
# ─────────────────────────────────────────────────────────────────────────────

def _safe_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _safe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _extract(
    enriched: dict,
    *,
    papers_rows:       list[dict],
    authors_seen:      dict[str, dict],
    author_edges:      list[dict],
    topics_seen:       dict[str, dict],
    topic_edges:       list[dict],
    refs_seen:         dict[str, dict],
    ref_edges:         list[dict],
    methods_seen:      dict[str, dict],
    sensors_seen:      dict[str, dict],
    metrics_seen:      dict[str, dict],
    institutions_seen: dict[str, dict],
    source_name:       str,
) -> None:
    """Extract all table rows from one enriched JSON document."""

    paper = enriched.get("paper") or {}
    openalex = enriched.get("openalex") or {}
    meta = paper.get("metadata") or {}

    paper_id = _safe_str(meta.get("paper_id"))
    if not paper_id:
        log.warning("[builder] skipping file with no paper_id: %s", source_name)
        return

    # ── Entities geo ─────────────────────────────────────────────────────────
    entities = paper.get("entities") or {}
    geo      = entities.get("geo") if isinstance(entities, dict) else {}
    geo      = geo if isinstance(geo, dict) else {}
    study_geo  = geo.get("study_geo") or {}
    study_type = geo.get("study_type") or {}

    primary_country = _safe_str(
        study_geo.get("primary_country") if isinstance(study_geo, dict) else None
    )
    study_type_label = _safe_str(
        study_type.get("label") if isinstance(study_type, dict) else None
    )

    # ── Sections ─────────────────────────────────────────────────────────────
    sections = paper.get("sections") or {}
    abstract_text   = sections.get("abstract", "") if isinstance(sections, dict) else ""
    abstract_length = len(abstract_text) if abstract_text else 0

    # ── OpenAlex top-level ───────────────────────────────────────────────────
    openalex_id    = _safe_str(openalex.get("openalex_id"))
    cited_by_count = _safe_int(openalex.get("cited_by_count"))
    oa_topics      = openalex.get("topics") or []
    oa_authors     = openalex.get("authors") or []
    oa_institutions = openalex.get("institutions") or []

    # ── Normalized entities counts ────────────────────────────────────────────
    normalized = paper.get("normalized_entities") or {}

    def _matched_count(field: str) -> int:
        return sum(
            1 for e in (normalized.get(field) or [])
            if e.get("canonical_id") is not None
        )

    methods_count = _matched_count("methods")
    sensors_count = sum(_matched_count(f) for f in ("satellites", "sensors"))
    metrics_count = _matched_count("metrics")

    # ── References ───────────────────────────────────────────────────────────
    raw_refs = paper.get("references") or []

    # ── Papers row ───────────────────────────────────────────────────────────
    doi = _safe_str(meta.get("doi"))
    papers_rows.append({
        "paper_id":         paper_id,
        "doi":              doi,
        "title":            _safe_str(meta.get("title")),
        "year":             _safe_str(meta.get("year")),
        "journal":          _safe_str(meta.get("journal")),
        "publisher":        _safe_str(meta.get("publisher")),
        "study_type":       study_type_label,
        "primary_country":  primary_country,
        "cited_by_count":   cited_by_count,
        "openalex_id":      openalex_id,
        "abstract_length":  abstract_length,
        "references_count": len(raw_refs),
        "authors_count":    len(oa_authors) or len(meta.get("authors") or []),
        "topics_count":     len(oa_topics),
        "methods_count":    methods_count,
        "sensors_count":    sensors_count,
        "metrics_count":    metrics_count,
        "has_openalex":     openalex_id is not None,
        "has_doi":          doi is not None,
        "has_abstract":     abstract_length > 0,
        "created_from":     source_name,
    })

    # ── Authors + paper_author_edges ─────────────────────────────────────────
    for author in oa_authors:
        if not isinstance(author, dict):
            continue
        author_id = _safe_str(author.get("id"))
        if not author_id:
            continue

        if author_id not in authors_seen:
            orcid = _safe_str(author.get("orcid"))
            if orcid and orcid.startswith("https://orcid.org/"):
                orcid = orcid[len("https://orcid.org/"):]
            authors_seen[author_id] = {
                "author_id":    author_id,
                "display_name": _safe_str(author.get("name")),
                "orcid":        orcid,
                "papers":       set(),
            }
        authors_seen[author_id]["papers"].add(paper_id)

        author_edges.append({
            "paper_id":         paper_id,
            "author_id":        author_id,
            "author_position":  _safe_str(author.get("author_position")),
            "is_corresponding": bool(author.get("is_corresponding", False)),
        })

        # ── Institutions from author ──────────────────────────────────────────
        for inst in (author.get("institutions") or []):
            if not isinstance(inst, dict):
                continue
            inst_id = _safe_str(inst.get("id"))
            if not inst_id:
                continue
            if inst_id not in institutions_seen:
                institutions_seen[inst_id] = {
                    "institution_id":   inst_id,
                    "display_name":     _safe_str(inst.get("name")),
                    "country_code":     _safe_str(inst.get("country_code")),
                    "institution_type": _safe_str(inst.get("type")),
                    "papers":           set(),
                    "authors":          set(),
                }
            institutions_seen[inst_id]["papers"].add(paper_id)
            institutions_seen[inst_id]["authors"].add(author_id)

    # ── Paper-level institutions (may add new or confirm existing) ────────────
    for inst in oa_institutions:
        if not isinstance(inst, dict):
            continue
        inst_id = _safe_str(inst.get("id"))
        if not inst_id:
            continue
        if inst_id not in institutions_seen:
            institutions_seen[inst_id] = {
                "institution_id":   inst_id,
                "display_name":     _safe_str(inst.get("name")),
                "country_code":     _safe_str(inst.get("country_code")),
                "institution_type": _safe_str(inst.get("type")),
                "papers":           set(),
                "authors":          set(),
            }
        institutions_seen[inst_id]["papers"].add(paper_id)

    # ── Topics + paper_topic_edges ───────────────────────────────────────────
    for topic in oa_topics:
        if not isinstance(topic, dict):
            continue
        topic_id = _safe_str(topic.get("id"))
        if not topic_id:
            continue
        score = float(topic.get("score") or 0.0)

        if topic_id not in topics_seen:
            topics_seen[topic_id] = {
                "topic_id":   topic_id,
                "topic_name": _safe_str(topic.get("name")),
                "papers":     set(),
                "scores":     [],
            }
        topics_seen[topic_id]["papers"].add(paper_id)
        topics_seen[topic_id]["scores"].append(score)

        topic_edges.append({
            "paper_id": paper_id,
            "topic_id": topic_id,
            "score":    score,
        })

    # ── References + paper_reference_edges ───────────────────────────────────
    for ref in raw_refs:
        if not isinstance(ref, dict):
            continue
        doi_r    = _safe_str(ref.get("doi"))
        title_r  = _safe_str(ref.get("title"))
        raw_text = _safe_str(ref.get("raw_text"))
        rid      = _ref_id(doi_r, title_r, raw_text)
        if rid is None:
            continue

        if rid not in refs_seen:
            authors_r = ref.get("authors") or []
            refs_seen[rid] = {
                "reference_id":  rid,
                "doi":           doi_r,
                "title":         title_r,
                "journal":       _safe_str(ref.get("journal")),
                "year":          _safe_int(ref.get("year")),
                "authors_count": len(authors_r),
                "raw_text":      raw_text,
                "openalex_id":   None,
            }

        ref_edges.append({"paper_id": paper_id, "reference_id": rid})

    # ── Normalized entity tables (methods / sensors / metrics) ───────────────
    # ONLY rows where canonical_id is not None — never raw NER labels
    _populate_entities(paper_id, normalized, methods_seen, sensors_seen, metrics_seen)


def _populate_entities(
    paper_id:      str,
    normalized:    dict,
    methods_seen:  dict[str, dict],
    sensors_seen:  dict[str, dict],
    metrics_seen:  dict[str, dict],
) -> None:
    """Accumulate method/sensor/metric canonical entities from normalized_entities."""

    # Methods: fields methods, models, algorithms
    for field in ("methods", "models", "algorithms"):
        for ent in (normalized.get(field) or []):
            if not isinstance(ent, dict):
                continue
            cid = _safe_str(ent.get("canonical_id"))
            if cid is None:
                continue
            if cid not in methods_seen:
                methods_seen[cid] = {
                    "canonical_id": cid,
                    "display_name": _safe_str(ent.get("display_name")),
                    "type":         _safe_str(ent.get("type")),
                    "type_group":   None,
                    "papers":       set(),
                    "count":        0,
                }
            methods_seen[cid]["papers"].add(paper_id)
            methods_seen[cid]["count"] += 1

    # Sensors: fields satellites, sensors
    for field in ("satellites", "sensors"):
        for ent in (normalized.get(field) or []):
            if not isinstance(ent, dict):
                continue
            cid = _safe_str(ent.get("canonical_id"))
            if cid is None:
                continue
            if cid not in sensors_seen:
                sensors_seen[cid] = {
                    "canonical_id":  cid,
                    "display_name":  _safe_str(ent.get("display_name")),
                    "sensor_family": None,
                    "papers":        set(),
                }
            sensors_seen[cid]["papers"].add(paper_id)

    # Metrics: field metrics
    for ent in (normalized.get("metrics") or []):
        if not isinstance(ent, dict):
            continue
        cid = _safe_str(ent.get("canonical_id"))
        if cid is None:
            continue
        if cid not in metrics_seen:
            metrics_seen[cid] = {
                "canonical_id": cid,
                "display_name": _safe_str(ent.get("display_name")),
                "metric_type":  None,
                "papers":       set(),
            }
        metrics_seen[cid]["papers"].add(paper_id)


# ─────────────────────────────────────────────────────────────────────────────
# Ontology enrichment (type_group, sensor_family, metric_type)
# ─────────────────────────────────────────────────────────────────────────────

def _enrich_from_ontology(
    methods_seen:  dict[str, dict],
    sensors_seen:  dict[str, dict],
    metrics_seen:  dict[str, dict],
) -> None:
    """Pull type_group / sensor_family / metric_type from the ontology registry."""
    try:
        from src.normalization.alias_resolver import load_ontology_registry
        registry = load_ontology_registry()
    except Exception as exc:
        log.warning("[builder] could not load ontology registry for enrichment: %s", exc)
        return

    for cid, row in methods_seen.items():
        ent = registry.get(cid, {})
        row["type_group"] = _safe_str(ent.get("type_group"))

    for cid, row in sensors_seen.items():
        ent = registry.get(cid, {})
        row["sensor_family"] = _safe_str(ent.get("sensor_family"))

    for cid, row in metrics_seen.items():
        ent = registry.get(cid, {})
        row["metric_type"] = _safe_str(ent.get("metric_type"))


# ─────────────────────────────────────────────────────────────────────────────
# Table writers
# ─────────────────────────────────────────────────────────────────────────────

def _write_table(
    name:       str,
    data:       dict[str, list],
    schema:     pa.Schema,
    output_dir: Path,
) -> int:
    """Cast data columns to schema types and write parquet. Returns row count."""
    table = pa.table(data, schema=schema)
    out   = output_dir / f"{name}.parquet"
    pq.write_table(table, out, compression="snappy")
    log.info("[builder] wrote %s (%d rows) → %s", name, len(table), out.name)
    return len(table)


def _build_tables(
    papers_rows:       list[dict],
    authors_seen:      dict[str, dict],
    author_edges:      list[dict],
    topics_seen:       dict[str, dict],
    topic_edges:       list[dict],
    refs_seen:         dict[str, dict],
    ref_edges:         list[dict],
    methods_seen:      dict[str, dict],
    sensors_seen:      dict[str, dict],
    metrics_seen:      dict[str, dict],
    institutions_seen: dict[str, dict],
    output_dir:        Path,
) -> dict[str, int]:
    """Convert all accumulation structures to pyarrow tables and write parquet."""

    row_counts: dict[str, int] = {}

    # ── papers ────────────────────────────────────────────────────────────────
    p_cols: dict[str, list] = {f.name: [] for f in PAPERS_SCHEMA}
    for row in papers_rows:
        for f in PAPERS_SCHEMA:
            p_cols[f.name].append(row.get(f.name))
    row_counts["papers"] = _write_table("papers", p_cols, PAPERS_SCHEMA, output_dir)

    # ── authors ───────────────────────────────────────────────────────────────
    a_cols: dict[str, list] = {f.name: [] for f in AUTHORS_SCHEMA}
    for row in authors_seen.values():
        a_cols["author_id"].append(row["author_id"])
        a_cols["display_name"].append(row["display_name"])
        a_cols["orcid"].append(row["orcid"])
        a_cols["paper_count"].append(len(row["papers"]))
    row_counts["authors"] = _write_table("authors", a_cols, AUTHORS_SCHEMA, output_dir)

    # ── paper_author_edges ────────────────────────────────────────────────────
    ae_cols: dict[str, list] = {f.name: [] for f in PAPER_AUTHOR_EDGES_SCHEMA}
    for row in author_edges:
        for f in PAPER_AUTHOR_EDGES_SCHEMA:
            ae_cols[f.name].append(row.get(f.name))
    row_counts["paper_author_edges"] = _write_table(
        "paper_author_edges", ae_cols, PAPER_AUTHOR_EDGES_SCHEMA, output_dir
    )

    # ── topics ────────────────────────────────────────────────────────────────
    t_cols: dict[str, list] = {f.name: [] for f in TOPICS_SCHEMA}
    for row in topics_seen.values():
        scores = row["scores"]
        t_cols["topic_id"].append(row["topic_id"])
        t_cols["topic_name"].append(row["topic_name"])
        t_cols["paper_count"].append(len(row["papers"]))
        t_cols["max_score"].append(max(scores) if scores else None)
        t_cols["avg_score"].append(sum(scores) / len(scores) if scores else None)
    row_counts["topics"] = _write_table("topics", t_cols, TOPICS_SCHEMA, output_dir)

    # ── paper_topic_edges ─────────────────────────────────────────────────────
    te_cols: dict[str, list] = {f.name: [] for f in PAPER_TOPIC_EDGES_SCHEMA}
    for row in topic_edges:
        for f in PAPER_TOPIC_EDGES_SCHEMA:
            te_cols[f.name].append(row.get(f.name))
    row_counts["paper_topic_edges"] = _write_table(
        "paper_topic_edges", te_cols, PAPER_TOPIC_EDGES_SCHEMA, output_dir
    )

    # ── references ────────────────────────────────────────────────────────────
    r_cols: dict[str, list] = {f.name: [] for f in REFERENCES_SCHEMA}
    for row in refs_seen.values():
        for f in REFERENCES_SCHEMA:
            r_cols[f.name].append(row.get(f.name))
    row_counts["references"] = _write_table("references", r_cols, REFERENCES_SCHEMA, output_dir)

    # ── paper_reference_edges ─────────────────────────────────────────────────
    re_cols: dict[str, list] = {f.name: [] for f in PAPER_REFERENCE_EDGES_SCHEMA}
    for row in ref_edges:
        for f in PAPER_REFERENCE_EDGES_SCHEMA:
            re_cols[f.name].append(row.get(f.name))
    row_counts["paper_reference_edges"] = _write_table(
        "paper_reference_edges", re_cols, PAPER_REFERENCE_EDGES_SCHEMA, output_dir
    )

    # ── methods ───────────────────────────────────────────────────────────────
    m_cols: dict[str, list] = {f.name: [] for f in METHODS_SCHEMA}
    for row in methods_seen.values():
        m_cols["canonical_id"].append(row["canonical_id"])
        m_cols["display_name"].append(row["display_name"])
        m_cols["type"].append(row["type"])
        m_cols["type_group"].append(row["type_group"])
        m_cols["paper_count"].append(len(row["papers"]))
        m_cols["source_count"].append(row["count"])
    row_counts["methods"] = _write_table("methods", m_cols, METHODS_SCHEMA, output_dir)

    # ── sensors ───────────────────────────────────────────────────────────────
    s_cols: dict[str, list] = {f.name: [] for f in SENSORS_SCHEMA}
    for row in sensors_seen.values():
        s_cols["canonical_id"].append(row["canonical_id"])
        s_cols["display_name"].append(row["display_name"])
        s_cols["sensor_family"].append(row["sensor_family"])
        s_cols["paper_count"].append(len(row["papers"]))
    row_counts["sensors"] = _write_table("sensors", s_cols, SENSORS_SCHEMA, output_dir)

    # ── metrics ───────────────────────────────────────────────────────────────
    me_cols: dict[str, list] = {f.name: [] for f in METRICS_SCHEMA}
    for row in metrics_seen.values():
        me_cols["canonical_id"].append(row["canonical_id"])
        me_cols["display_name"].append(row["display_name"])
        me_cols["metric_type"].append(row["metric_type"])
        me_cols["paper_count"].append(len(row["papers"]))
    row_counts["metrics"] = _write_table("metrics", me_cols, METRICS_SCHEMA, output_dir)

    # ── institutions ──────────────────────────────────────────────────────────
    i_cols: dict[str, list] = {f.name: [] for f in INSTITUTIONS_SCHEMA}
    for row in institutions_seen.values():
        i_cols["institution_id"].append(row["institution_id"])
        i_cols["display_name"].append(row["display_name"])
        i_cols["country_code"].append(row["country_code"])
        i_cols["institution_type"].append(row["institution_type"])
        i_cols["paper_count"].append(len(row["papers"]))
        i_cols["author_count"].append(len(row["authors"]))
    row_counts["institutions"] = _write_table(
        "institutions", i_cols, INSTITUTIONS_SCHEMA, output_dir
    )

    return row_counts


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def build_all_tables(
    input_dir:  Path,
    output_dir: Path,
    overwrite:  bool = False,
    limit:      Optional[int] = None,
) -> dict:
    """
    Stream all enriched JSONs and write 11 parquet tables.

    Args:
        input_dir:  data/enriched/ directory
        output_dir: target for *.parquet files (e.g. data/analytics/)
        overwrite:  if False and all parquet files already exist, skip
        limit:      stop after N source files

    Returns:
        Summary dict.
    """
    source_files = sorted(input_dir.glob("*.json"))
    if not source_files:
        log.warning("[builder] no *.json files found in %s", input_dir)
        return {"total_files": 0, "processed": 0}

    if limit is not None:
        source_files = source_files[:limit]

    # Skip check: if all parquet files exist and not overwriting
    output_dir.mkdir(parents=True, exist_ok=True)
    if not overwrite:
        existing = [output_dir / fname for _, fname in TABLE_REGISTRY.values()]
        if all(p.exists() for p in existing):
            log.info("[builder] all parquet files exist, skipping (use --overwrite to rebuild)")
            return {
                "status":       "skipped",
                "total_files":  len(source_files),
                "processed":    0,
            }

    # ── Accumulation structures ───────────────────────────────────────────────
    papers_rows:       list[dict]        = []
    authors_seen:      dict[str, dict]   = {}
    author_edges:      list[dict]        = []
    topics_seen:       dict[str, dict]   = {}
    topic_edges:       list[dict]        = []
    refs_seen:         dict[str, dict]   = {}
    ref_edges:         list[dict]        = []
    methods_seen:      dict[str, dict]   = {}
    sensors_seen:      dict[str, dict]   = {}
    metrics_seen:      dict[str, dict]   = {}
    institutions_seen: dict[str, dict]   = {}

    t_start   = time.monotonic()
    errors    = 0
    processed = 0

    for i, path in enumerate(source_files, 1):
        try:
            enriched = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.error("[builder] failed to load %s: %s", path.name, exc)
            errors += 1
            continue

        try:
            _extract(
                enriched,
                papers_rows=papers_rows,
                authors_seen=authors_seen,
                author_edges=author_edges,
                topics_seen=topics_seen,
                topic_edges=topic_edges,
                refs_seen=refs_seen,
                ref_edges=ref_edges,
                methods_seen=methods_seen,
                sensors_seen=sensors_seen,
                metrics_seen=metrics_seen,
                institutions_seen=institutions_seen,
                source_name=path.name,
            )
            processed += 1
        except Exception as exc:
            log.error("[builder] extract failed for %s: %s", path.name, exc)
            errors += 1

        if i % 500 == 0 or i == len(source_files):
            elapsed = time.monotonic() - t_start
            log.info(
                "[builder] progress %d/%d  authors=%d  topics=%d  refs=%d  "
                "methods=%d  sensors=%d  metrics=%d  (%.1f/s)",
                i, len(source_files),
                len(authors_seen), len(topics_seen), len(refs_seen),
                len(methods_seen), len(sensors_seen), len(metrics_seen),
                i / elapsed if elapsed > 0 else 0,
            )

    # ── Ontology enrichment ───────────────────────────────────────────────────
    log.info("[builder] enriching entity tables from ontology registry…")
    _enrich_from_ontology(methods_seen, sensors_seen, metrics_seen)

    # ── Write parquet files ───────────────────────────────────────────────────
    log.info("[builder] writing parquet tables → %s", output_dir)
    row_counts = _build_tables(
        papers_rows, authors_seen, author_edges,
        topics_seen, topic_edges,
        refs_seen, ref_edges,
        methods_seen, sensors_seen, metrics_seen,
        institutions_seen, output_dir,
    )

    total_elapsed = round(time.monotonic() - t_start, 2)

    return {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "input_dir":     str(input_dir),
        "output_dir":    str(output_dir),
        "total_files":   len(source_files),
        "processed":     processed,
        "load_errors":   errors,
        "elapsed_s":     total_elapsed,
        "rate_per_s":    round(processed / total_elapsed, 2) if total_elapsed > 0 else 0,
        "row_counts":    row_counts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "GeoHydroAI Stage 3A: build parquet analytics layer from enriched JSONs.\n"
            "Reads data/enriched/*.json → writes 11 parquet tables to data/analytics/."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input-dir", type=Path, default=_DEFAULT_INPUT, metavar="PATH",
        help=f"Enriched JSON directory (default: {_DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=_DEFAULT_OUTPUT, metavar="PATH",
        help=f"Parquet output directory (default: {_DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Rebuild parquet even if all files already exist",
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Process only the first N enriched files",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser


def _main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.input_dir.exists():
        log.error("Input directory does not exist: %s", args.input_dir)
        sys.exit(1)

    log.info("=" * 60)
    log.info("GeoHydroAI Stage 3A — Parquet Analytics Builder")
    log.info("  Input  : %s", args.input_dir)
    log.info("  Output : %s", args.output_dir)
    log.info("  Overwrite: %s  Limit: %s", args.overwrite, args.limit)
    log.info("=" * 60)

    summary = build_all_tables(
        input_dir  = args.input_dir,
        output_dir = args.output_dir,
        overwrite  = args.overwrite,
        limit      = args.limit,
    )

    # ── Write summary JSON ────────────────────────────────────────────────────
    qa_dir = _PROJECT_ROOT / "data" / "analytics"
    qa_dir.mkdir(parents=True, exist_ok=True)
    summary_path = qa_dir / "parquet_build_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ── Terminal report ───────────────────────────────────────────────────────
    print()
    print("=" * 52)
    print("Stage 3A — Parquet Analytics Build Complete")
    print(f"  Input dir  : {summary['input_dir']}")
    print(f"  Output dir : {summary['output_dir']}")
    if summary.get("status") == "skipped":
        print("  Status     : skipped (all parquet files exist)")
    else:
        print(f"  Files      : {summary['processed']}/{summary['total_files']}")
        print(f"  Errors     : {summary.get('load_errors', 0)}")
        print(f"  Elapsed    : {summary.get('elapsed_s', 0):.1f}s")
        print(f"  Rate       : {summary.get('rate_per_s', 0):.1f} files/s")
        if summary.get("row_counts"):
            print()
            print("  Table row counts:")
            for tname, count in summary["row_counts"].items():
                print(f"    {tname:28s}: {count:>8,}")
    print(f"\n  Report: {summary_path}")
    print("=" * 52)

    sys.exit(0 if summary.get("load_errors", 0) == 0 else 1)


if __name__ == "__main__":
    _main()
