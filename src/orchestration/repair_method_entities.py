"""
repair_method_entities.py — drop method entities that the fixed KB patterns no longer find.

Until 2026-10-03 the knowledge-base patterns matched model names as substrings
("iric" inside "empirical") and acronyms without case ("hand" in "on the other hand",
"et" in "et al."). Every paper.json written by the legacy pipeline carries those false
methods, and the graph has 835 iRIC / 666 HAND / 349 TRANSFORM papers that never
mention them.

For every paper.json the method text is rebuilt exactly as the pipeline builds it
(PipelineContext.method_text from paper["sections"]) and re-scanned with the current
KB. A method entity is kept only when the current extraction still finds its name.
Nothing is added — new matches have not been through the LLM judge.

Side effects per repaired paper:
  * paper.json rewritten atomically (entities.methods, normalized_entities.methods)
  * data/normalized/<id>.json and data/enriched/<id>.json deleted, so that
    normalization_runner / enrichment_runner (which skip existing outputs) rebuild them
  * one JSONL line in data/repair/method_entities_<date>.jsonl with the removed entity
    objects (full backup) — scripts/graph_cleanup_20261003.py deletes the matching
    USES_METHOD edges from Neo4j.

Usage:
    python -m src.orchestration.repair_method_entities [--dry-run] [--workers 6] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

log = logging.getLogger("geohydro.repair.methods")

ROOT          = Path(__file__).resolve().parents[2]
PAPER_JSON    = ROOT / "data" / "literature" / "paper_json"
NORMALIZED    = ROOT / "data" / "normalized"
ENRICHED      = ROOT / "data" / "enriched"
REPAIR_DIR    = ROOT / "data" / "repair"

_EXTRACTOR = None


def _extractor():
    global _EXTRACTOR
    if _EXTRACTOR is None:
        logging.disable(logging.WARNING)
        from src.ingestion.knowledge.entity_extractor import EntityExtractor
        from src.ingestion.stages.entity_pipeline import _get_kb
        _EXTRACTOR = EntityExtractor(_get_kb())
    return _EXTRACTOR


def _stem(path: Path) -> str:
    name = path.name
    for suffix in (".tei.paper.json", ".paper.json", ".json"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def repair_one(args: tuple[str, bool]) -> dict:
    """Re-scan one paper.json; returns a report dict (never raises)."""
    path_s, dry_run = args
    path = Path(path_s)
    stem = _stem(path)
    try:
        with open(path) as fh:
            paper = json.load(fh)
    except Exception as exc:                       # 12 truncated Elsevier files
        return {"stem": stem, "status": "load_error", "error": f"{type(exc).__name__}: {exc}"}

    entities = paper.get("entities") or {}
    methods  = entities.get("methods") or []
    if not methods:
        return {"stem": stem, "status": "no_methods"}

    from src.ingestion.stages.entity_pipeline import PipelineContext
    ctx   = PipelineContext(paper.get("sections") or {})
    found = {m["name"].lower() for m in _extractor().extract_methods(ctx.method_text)}

    removed = [m for m in methods if str(m.get("name", "")).lower() not in found]
    if not removed:
        return {"stem": stem, "status": "clean", "kept": len(methods)}

    removed_names = {str(m.get("name", "")).lower() for m in removed}
    entities["methods"] = [m for m in methods if str(m.get("name", "")).lower() in found]

    # the pipeline also stores a normalised copy; keep it consistent
    canon: dict[str, str | None] = {}
    ne = paper.get("normalized_entities")
    if isinstance(ne, dict) and isinstance(ne.get("methods"), list):
        kept_norm = []
        for ent in ne["methods"]:
            raw = str(ent.get("raw_name", "")).lower()
            if raw in removed_names:
                canon[raw] = ent.get("canonical_id")
            else:
                kept_norm.append(ent)
        ne["methods"] = kept_norm

    report = {
        "stem":      stem,
        "paper_id":  (paper.get("metadata") or {}).get("paper_id"),
        "status":    "repaired",
        "kept":      len(entities["methods"]),
        "removed":   [{"name": m.get("name"), "canonical_id": canon.get(str(m.get("name", "")).lower())}
                      for m in removed],
        "removed_entities": removed,
    }
    if dry_run:
        return report

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(paper, fh, ensure_ascii=False)
    os.replace(tmp, path)
    for out in (NORMALIZED / f"{stem}.json", ENRICHED / f"{stem}.json"):
        try:
            out.unlink()
            report.setdefault("deleted", []).append(str(out.relative_to(ROOT)))
        except FileNotFoundError:
            pass
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--paper-json-dir", type=Path, default=PAPER_JSON)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    files = sorted(args.paper_json_dir.glob("*.paper.json"))
    if args.limit:
        files = files[: args.limit]
    log.info("%d paper.json files, %d workers, dry_run=%s", len(files), args.workers, args.dry_run)

    REPAIR_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPAIR_DIR / f"method_entities_{date.today():%Y%m%d}{'_dryrun' if args.dry_run else ''}.jsonl"
    counts: dict[str, int] = {}
    removed_by_name: dict[str, int] = {}
    t0 = time.time()
    with open(out_path, "a") as out, ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i, rep in enumerate(pool.map(repair_one, [(str(f), args.dry_run) for f in files], chunksize=8), 1):
            counts[rep["status"]] = counts.get(rep["status"], 0) + 1
            if rep["status"] == "repaired":
                for r in rep["removed"]:
                    removed_by_name[r["name"]] = removed_by_name.get(r["name"], 0) + 1
                out.write(json.dumps(rep, ensure_ascii=False) + "\n")
            elif rep["status"] == "load_error":
                out.write(json.dumps(rep, ensure_ascii=False) + "\n")
            if i % 500 == 0:
                log.info("  %d/%d  %s  (%.0f s)", i, len(files), counts, time.time() - t0)
    log.info("done in %.0f s: %s", time.time() - t0, counts)
    top = sorted(removed_by_name.items(), key=lambda kv: -kv[1])[:25]
    log.info("removed entities by name (top 25): %s", top)
    log.info("report: %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
