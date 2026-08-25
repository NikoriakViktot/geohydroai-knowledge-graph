"""
process_paper.py  —  GeoHydroAI Stage 1 Ray task (memory-aware, idempotent)
===========================================================================

A single @ray.remote task that processes one TEI XML paper end-to-end.

This task is intentionally thin: it coordinates actor calls and manages the
memory lifecycle between stages.  All extraction logic lives in pipeline.py.

Idempotency
-----------
Step 0 (before any real work) checks whether {xml_path.stem}.paper.json
already exists in out_dir.  If it does and overwrite=False, the task returns
immediately with {"_status": "SKIPPED", "paper_id": "..."} without touching
spaCy, embeddings, or OllamaActor.

The primary skip happens in pipeline_runner.py before Ray task creation.
This guard is the task-level defense-in-depth for concurrent runners.

Memory lifecycle
----------------
Each stage releases large intermediates before the next stage begins:

  0. Idempotency guard — return SKIPPED sentinel if output exists
  1. TEIParser.parse_file() → body_text() → submit NER future immediately
  2. del doc / full_text → gc.collect()
     (TEIDocument freed before extraction; NER runs concurrently in SpacyActor)
  3. Collect NER result → run build_paper_json()
  4. del ner_entities → gc.collect()
  5. Run OllamaActor if needed → del candidate / sections extracts
  6. Return json_safe(paper)

Design principles
-----------------
- Never hold the full XML tree and the extracted paper simultaneously.
- Never hold large intermediate objects (ner_entities, candidate_json,
  section excerpts) beyond the single call that needs them.
- gc.collect() is called explicitly at each GC-critical checkpoint so that
  the CPython cyclic GC doesn't accumulate large objects between tasks.

Architecture:
  Ray Runtime
  └── process_paper (task)
      ├── Step 0: idempotency guard (filesystem check, no actor calls)
      ├── SpacyActor.extract.remote(full_text)   — overlaps with GC/setup
      ├── EmbeddingActor.encode.remote(...)       — via encode_fn wrapper
      └── OllamaActor.judge.remote(...)           — conditional

Upgrade path:
  Replace ray.get() wrappers with async Ray DAG chaining to eliminate
  blocking waits inside the worker.
"""

from __future__ import annotations

import gc
import hashlib
import logging
import os
import time
from pathlib import Path

import numpy as np
import ray

from src.normalization.normalization_utils import (
    normalize_paper_entities as _normalize_paper_entities,
    validate_normalized_schema as _validate_normalized_schema,
    save_normalized as _save_normalized_impl,
)

log = logging.getLogger(__name__)

_NORMALIZED_DIR = Path(__file__).resolve().parents[2] / "data" / "normalized"
_SODB_DIR       = Path(os.getenv("SODB_DIR",
                        str(Path(__file__).resolve().parents[2] / "data" / "sodb")))

# Stable hash for process_paper extraction logic version.
# Bump the literal string when extraction behaviour changes so SODBManifest
# marks cached numeric_facts.parquet entries as stale.
_PIPELINE_HASH = "sha256:" + hashlib.sha256(b"process_paper:v1.0").hexdigest()[:16]

from src.extraction.table_extractor import detect_period_type as _detect_period_type


def _save_normalized(paper: dict) -> None:
    """Write paper to data/normalized/{paper_id}.json."""
    try:
        _save_normalized_impl(paper, _NORMALIZED_DIR)
    except Exception as exc:
        log.warning("[normalize] failed to save normalized paper: %s", exc)


# ── P1-T4: regions.parquet helpers ───────────────────────────────────────────

def _load_regions(paper_id: str) -> list[dict] | None:
    """Load regions.parquet for a paper. Returns None if absent or unreadable."""
    path = _SODB_DIR / paper_id / "regions.parquet"
    if not path.exists():
        return None
    try:
        import pyarrow.parquet as pq
        return pq.read_table(path).to_pylist()
    except Exception as exc:
        log.warning("[process_paper] regions.parquet read failed for %s: %s", paper_id, exc)
        return None


def _nougat_supplement(regions: list[dict]) -> str | None:
    """
    Combine Nougat content from all regions into a single text block.

    Formula regions contribute their LaTeX (nougat_latex); all others
    contribute visual_text (nougat_text).  The supplement is attached to
    doc.markdown_text so build_paper_json can use it as a richer methods
    section source.
    """
    parts: list[str] = []
    for r in regions:
        text = r.get("nougat_latex") or r.get("nougat_text")
        if text and text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts) if parts else None


# ── P1-T5: NumericFact extraction + parquet writer ───────────────────────────

def _extract_and_persist_formulas(doc: "TEIDocument", paper_id: str) -> list[dict]:
    """Extract formulas from TEIDocument and write formulas.parquet via SODBWriter."""
    try:
        from src.extraction.formula_extractor import extract_formulas
        from src.document.sodb_writer import SODBWriter

        records = extract_formulas(doc)
        if not records:
            return []

        writer = SODBWriter(paper_id, _PIPELINE_HASH, _SODB_DIR)
        rows   = [r.to_row() for r in records]
        writer.write_formulas(rows)
        log.info("[process_paper] formulas.parquet: %d formulas → %s",
                 len(rows), paper_id)
        return rows
    except Exception as exc:
        log.warning("[process_paper] formula extraction failed for %s: %s", paper_id, exc)
        return []


def _extract_and_persist_tables(doc: "TEIDocument", paper_id: str) -> list[dict]:
    """Extract Table objects from TEIDocument and write tables.parquet via SODBWriter."""
    try:
        import re
        from src.document.sodb_writer import SODBWriter

        _NUM_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")

        rows: list[dict] = []
        for n, table in enumerate(doc.tables, start=1):
            page       = table.coords.primary_page if table.coords else None
            row_count  = len(table.rows)
            col_count  = len(table.rows[0]) if table.rows else 0
            header_row = list(table.rows[0]) if table.rows else None
            has_num    = any(
                _NUM_RE.match(cell.strip())
                for row in table.rows
                for cell in row
                if cell
            )
            rows.append({
                "table_id":         f"{paper_id}_tbl_{n:03d}",
                "paper_id":         paper_id,
                "region_id":        None,
                "xml_id":           table.xml_id or None,
                "page":             page,
                "label":            table.label or None,
                "caption":          table.caption or None,
                "header_row":       header_row,
                "row_count":        row_count,
                "col_count":        col_count,
                "has_numeric_data": has_num,
                "source_parser":    "GROBID",
            })

        if not rows:
            return []

        writer = SODBWriter(paper_id, _PIPELINE_HASH, _SODB_DIR)
        writer.write_tables(rows)
        log.info("[process_paper] tables.parquet: %d tables → %s", len(rows), paper_id)
        return rows
    except Exception as exc:
        log.warning("[process_paper] table persistence failed for %s: %s", paper_id, exc)
        return []


def _extract_and_persist_numeric_facts(xml_path: Path, paper_id: str) -> list[dict]:
    """
    Extract NumericFacts from the TEI XML and write numeric_facts.parquet.

    Returns the list of row dicts (empty list if no facts found or on error).
    Never raises — failures are logged and the pipeline continues.
    """
    try:
        from src.extraction.table_extractor import extract_numeric_facts
        from src.document.sodb_writer import SODBWriter

        facts = extract_numeric_facts(xml_path, paper_id)
        if not facts:
            return []

        rows = [
            {
                "fact_id":          f.fact_id,
                "paper_id":         f.paper_id,
                "source_region_id": None,
                "table_id":         f.table_id,
                "table_label":      f.table_label,
                "page":             f.page,
                "col_header":       f.col_header,
                "row_context":      " | ".join(f.row_context) if f.row_context else None,
                "metric":           f.metric,
                "canonical_id":     f.canonical_id,
                "node_label":       f.node_label,
                "value":            f.value,
                "unit":             f.unit,
                "confidence":       float(f.confidence),
                "basin_id":         None,
                "period_type":      _detect_period_type(f.row_context, f.col_header),
                "source":           f.source,
            }
            for f in facts
        ]

        writer = SODBWriter(paper_id, _PIPELINE_HASH, _SODB_DIR)
        writer.write_numeric_facts(rows)
        log.info("[process_paper] numeric_facts.parquet: %d facts → %s",
                 len(rows), paper_id)
        return rows

    except Exception as exc:
        log.warning("[process_paper] numeric_facts extraction failed for %s: %s",
                    paper_id, exc)
        return []


def _make_encode_fn(embedding_actor):
    """
    Wrap EmbeddingActor.encode into a synchronous callable compatible with
    the encode_fn contract expected by pipeline.py.

    Returns list[list[float]] converted back to float32 numpy array so that
    cosine_similarity works unchanged.
    """
    def encode_fn(texts: list[str]) -> np.ndarray:
        # Фаза 3.2: retry на transient-збоях актора + таймаут проти hang
        from src.orchestration.retry import retry_call, ray_get
        raw = retry_call(
            lambda: ray_get(embedding_actor.encode.remote(texts),
                            label="EmbeddingActor.encode"),
            label="encode_fn",
        )
        return np.array(raw, dtype=np.float32)
    return encode_fn


# ─────────────────────────────────────────────────────────────────────────────
# Ray task
# ─────────────────────────────────────────────────────────────────────────────

@ray.remote
def process_paper(
    xml_path: str,
    embedding_actor,
    spacy_actor,
    ollama_actor,
    out_dir: str = "",
    overwrite: bool = False,
) -> dict:
    """
    Distributed Ray task: process a single TEI XML paper.

    Args:
        xml_path:        Absolute path to the .tei.xml file (str, not Path —
                         Ray serialises task arguments as strings).
        embedding_actor: Ray actor handle — EmbeddingActor.
        spacy_actor:     Ray actor handle — SpacyActor.
        ollama_actor:    Ray actor handle — OllamaActor.
        out_dir:         Output directory for .paper.json files (str).
                         Falls back to settings.OUT_DIR when empty.
        overwrite:       When False (default), skip if .paper.json exists.
                         When True, reprocess unconditionally.

    Returns:
        Fully extracted, validated, JSON-serialisable paper dict, or
        {"_status": "SKIPPED", "paper_id": "..."} if already done.
    """
    # local imports inside Ray worker process
    from src.config.settings import OUT_DIR as _DEFAULT_OUT_DIR
    from src.document import TEIParser
    from src.ingestion.pipeline import (
        build_paper_json,
        needs_judge,
        apply_judge_verdict,
        json_safe,
    )
    from src.ingestion.utils import ensure_dict
    from src.validation.judge_normalizer import normalize_judge_verdict

    xml_path = Path(xml_path)

    # ── 0. Idempotency guard ──────────────────────────────────────────────
    # This is the task-level defense-in-depth check.  The primary skip
    # happens in the orchestrator (pipeline_runner.py) before Ray task
    # creation.  This guard fires only on race conditions (two runners
    # hitting the same corpus) or when overwrite semantics differ between
    # the orchestrator and task arguments.
    #
    # Must execute BEFORE: XML parsing, NER, embedding, OllamaActor.
    _out_dir  = Path(out_dir) if out_dir else _DEFAULT_OUT_DIR
    paper_id  = xml_path.stem.replace(".tei", "")
    _out_path = _out_dir / f"{xml_path.stem}.paper.json"
    if not overwrite and _out_path.exists():
        log.info("[process_paper] SKIPPED_EXISTS — %s", xml_path.name)
        return {"_status": "SKIPPED", "paper_id": paper_id}

    log.info("[process_paper] start — %s", xml_path.name)

    # ── 1. Parse TEI; submit NER immediately so it overlaps with cleanup ──
    doc       = TEIParser().parse_file(xml_path, paper_id)
    full_text = doc.body_text()

    # coordinate validation runs inline — cheap, non-fatal
    try:
        from src.document.coord_validator import validate_document
        coord_report = validate_document(doc)
        if coord_report.error_count:
            log.warning(
                "[process_paper] coord issues in %s: %s",
                xml_path.name, coord_report.summary(),
            )
    except Exception as _exc:
        log.debug("[process_paper] coord_validator skipped: %s", _exc)

    log.info("[process_paper] NER submitted — %s", xml_path.name)
    ner_future = spacy_actor.extract.remote(full_text)

    # ── P1-T4: Enrich doc with Nougat region content if available ─────────
    # regions.parquet is written by NougatRegionPipeline (runs separately).
    # When present, combine formula LaTeX + visual text into doc.markdown_text
    # so build_paper_json has richer method-section content.
    # GROBID-parsed docs always have markdown_text=None; safe to overwrite.
    _regions = _load_regions(paper_id)
    if _regions:
        _nougat_md = _nougat_supplement(_regions)
        if _nougat_md:
            import dataclasses as _dc
            doc = _dc.replace(doc, markdown_text=_nougat_md)
        log.info("[process_paper] Nougat regions loaded: %d regions for %s",
                 len(_regions), xml_path.name)

    # ── 2. Free body text; keep doc alive for build_paper_json ────────────
    #    doc is now passed to build_paper_json() to skip the second XML parse.
    #    Freeing full_text here is enough — the string is ~10× smaller than
    #    the full TEIDocument and is no longer needed after NER submission.
    del full_text
    gc.collect()
    log.info("[process_paper] freed body text  gc.collect()")

    # ── 3. Build encode_fn; collect NER results ───────────────────────────
    encode_fn   = _make_encode_fn(embedding_actor)
    from src.orchestration.retry import ray_get
    ner_entities = ray_get(ner_future, label="SpacyActor.ner")
    log.info("[process_paper] NER done (%d entities) — %s",
             len(ner_entities), xml_path.name)

    # ── 4. Run extraction + embedding layers ──────────────────────────────
    #    Pass doc so build_paper_json skips the second XML parse.
    paper = build_paper_json(
        xml_path,
        doc=doc,
        encode_fn=encode_fn,
        ner_entities=ner_entities,
    )

    # ── P1-T5 / P2: SODB extraction (numeric facts, formulas, tables) ──────
    # All three run while doc is still alive; SODBWriter handles atomic writes.
    from src.document.sodb_manifest import SODBManifest
    _manifest = SODBManifest(paper_id, _PIPELINE_HASH, _SODB_DIR)

    _numeric_facts = _extract_and_persist_numeric_facts(xml_path, paper_id)
    if _numeric_facts:
        paper.setdefault("entities", {})["numeric_facts"] = _numeric_facts
        _manifest.mark_done("numeric_facts")

    _formula_rows = _extract_and_persist_formulas(doc, paper_id)
    if _formula_rows is not None:
        _manifest.mark_done("formula_extract")

    _table_rows = _extract_and_persist_tables(doc, paper_id)
    if _table_rows is not None:
        _manifest.mark_done("table_extract")

    # ── 4.5. Ground entities to sentence coordinates ─────────────────────
    try:
        from src.document.entity_grounder import ground_entities
        paper = ground_entities(paper, doc)
        log.info("[process_paper] entity grounding done — %s", xml_path.name)
    except Exception as exc:
        log.warning("[process_paper] entity grounding error (non-fatal): %s", exc)

    # ── 5. Layout-aware chunking → ChromaDB (while doc is still alive) ───
    try:
        from src.document import LayoutAwareChunker
        from src.vectorstore.chroma_store import VectorStore

        chunks = LayoutAwareChunker(strategy="sentence").chunk(doc)
        if chunks:
            chunk_texts = [c.text for c in chunks]
            chunk_vecs  = np.array(encode_fn(chunk_texts), dtype=np.float32)
            VectorStore().upsert_document_chunks(chunks, chunk_vecs)
            log.info("[process_paper] chunked %d chunks → ChromaDB — %s",
                     len(chunks), xml_path.name)
    except Exception as exc:
        log.warning("[process_paper] chunking/vectorstore error (non-fatal): %s", exc)

    # ── 6. Free TEIDocument + NER data — no longer needed ─────────────────
    del doc, ner_entities
    gc.collect()
    log.info("[process_paper] freed TEIDocument + ner_entities  gc.collect()")

    # ── 7. Ontology normalization (deterministic alias + semantic fallback) ─
    paper["schema_version"] = "1.0"
    paper = _normalize_paper_entities(paper)
    _validate_normalized_schema(paper)   # sets provenance.normalized_schema_valid; never raises
    _save_normalized(paper)
    unmatched_count = len(paper.get("provenance", {}).get("unmatched_entities", []))
    schema_valid    = paper.get("provenance", {}).get("normalized_schema_valid", None)
    log.info(
        "[process_paper] normalization done (unmatched=%d, schema_valid=%s) — %s",
        unmatched_count, schema_valid, xml_path.name,
    )

    # ── 8. Conditional LLM validation via OllamaActor ────────────────────
    if needs_judge(paper):
        metadata = ensure_dict(paper.get("metadata"))
        entities = ensure_dict(paper.get("entities"))
        sections = ensure_dict(paper.get("sections"))
        geo      = ensure_dict(entities.get("geo"))
        paper_id = metadata.get("paper_id", xml_path.name)

        # Canonical candidate schema: study_type and task as label strings,
        # matching judge_paper_with_ollama() CLI form so the prompt receives
        # the same shape regardless of execution path.
        candidate = {
            "paper_id":   paper_id,
            "title":      metadata.get("title"),
            "study_type": ensure_dict(geo.get("study_type")).get("label", "unknown"),
            "study_geo":  ensure_dict(geo.get("study_geo")),
            "task":       ensure_dict(entities.get("task")).get("label", "unknown"),
            "author_geo": geo.get("author_geo", []),
            "satellites": entities.get("satellites", []),
            "dems":       entities.get("dems", []),
            "methods":    entities.get("methods", []),
        }

        log.info(
            "[judge-dispatch] paper_id=%s study_type=%s task=%s",
            paper_id,
            candidate["study_type"],
            candidate["task"],
        )
        _judge_t0 = time.monotonic()
        try:
            raw_verdict = ray_get(
                ollama_actor.judge.remote(candidate, sections),
                timeout_s=float(os.getenv("JUDGE_TIMEOUT_S", "300")),
                label="OllamaActor.judge",
            )
        except Exception as exc:
            _judge_latency = round(time.monotonic() - _judge_t0, 3)
            log.warning(
                "[judge-failed] paper_id=%s latency=%.3fs reason=%s",
                paper_id, _judge_latency, str(exc)[:200],
            )
            raw_verdict = {"status": "failed", "reason": str(exc)[:200]}

        # free judge inputs immediately after the call
        del candidate, sections, metadata, entities, geo
        gc.collect()

        # normalise before apply — LLM output is never trusted directly
        verdict = normalize_judge_verdict(raw_verdict)
        paper   = apply_judge_verdict(paper, verdict)

        status = verdict.get("status")
        _judge_latency = round(time.monotonic() - _judge_t0, 3)

        # Телеметрія (Фаза 2.5): repair-count нормалізатора та judge-статус —
        # у provenance незалежно від результату, для агрегації по корпусу.
        paper["provenance"]["judge_verdict_repairs"] = int(
            verdict.get("normalizer_repairs", 0))
        paper["provenance"]["judge_status"] = status or "failed"

        if status not in {"skipped", "failed", None}:
            paper["llm_judge"]                = verdict
            paper["provenance"]["judge_used"] = True
            paper["provenance"]["judge_model"] = os.getenv(
                "OLLAMA_MODEL", "mistral-nemo:12b"
            )
            paper["provenance"]["judge_latency_s"] = _judge_latency
            corrected = sum(
                1 for k in ("study_type", "task", "study_country")
                if not (verdict.get(k) or {}).get("accepted", True)
            )
            log.info(
                "[judge-success] paper_id=%s latency=%.3fs corrected_labels=%d status=%s",
                paper_id, _judge_latency, corrected, status,
            )
        elif status == "skipped":
            log.info(
                "[judge-skipped] paper_id=%s latency=%.3fs reason=%s",
                paper_id, _judge_latency, verdict.get("reason", "unknown"),
            )
        else:
            log.warning(
                "[judge-failed] paper_id=%s latency=%.3fs reason=%s",
                paper_id, _judge_latency, verdict.get("reason", "unknown"),
            )

    log.info("[process_paper] done — %s", xml_path.name)
    return json_safe(paper)
