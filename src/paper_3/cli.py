"""CLI orchestrator for the paper_3 gap analysis.

Usage:
  python -m src.paper_3.cli [--phase harvest|analyse|all] [--step NAME]
                            [--theses src/paper_3/theses.yaml]
                            [--out data/paper_3_audit]
                            [--workers 3] [--llm gemini|ollama]
                            [--force] [--dry-run]

Every step is idempotent and guarded on its own output, so the whole chain can be
interrupted and resumed. Steps that need a service they cannot reach abort with a
named error rather than quietly producing a thinner result — a gap analysis that
silently ran without Neo4j would report absences that are really outages.

Run `--step coverage --tag baseline` before `discover` to record what the corpus
could answer *before* the harvest; the difference is the harvest's value.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

HARVEST_STEPS = ("snapshot", "draft", "queries", "controls", "freeze-controls", "freeze",
                 "discover", "meta-candidates", "meta-classify",
                 "download", "download-oa", "grobid",
                 "pipeline", "normalize", "chroma", "enrich", "parquet", "graph")
ANALYSE_STEPS = ("index", "retrieve", "calibrate", "gate", "classify", "finalize",
                 "extract", "coverage", "screen", "matrix", "novelty", "literature",
                 "own-evidence", "gemini-package", "references", "assemble", "translate",
                 "docx", "publish")
ALL_STEPS = HARVEST_STEPS + ANALYSE_STEPS


def _step(name: str, out_file: Path, force: bool) -> bool:
    """True when the step should run. Mirrors src/paper_audit/cli.py:34."""
    if force or not out_file.exists():
        return True
    logger.info("Skipping %s (already exists: %s) — use --force to re-run",
                name, out_file.name)
    return False


def _manifest_path(out_dir: Path) -> Path:
    return out_dir / "run_manifest.json"


def load_manifest(out_dir: Path) -> dict:
    path = _manifest_path(out_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def update_manifest(out_dir: Path, **fields) -> dict:
    manifest = load_manifest(out_dir)
    manifest.update(fields)
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out_dir.mkdir(parents=True, exist_ok=True)
    _manifest_path(out_dir).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8")
    return manifest


def render_manifest_md(out_dir: Path) -> Path:
    """Human-readable run record, published alongside the matrix."""
    manifest = load_manifest(out_dir)
    lines = ["# Paper 3 — run manifest", ""]
    if not manifest:
        lines.append("_No run recorded yet._")
    for key, value in sorted(manifest.items()):
        if isinstance(value, dict):
            lines.append(f"- **{key}**:")
            for k, v in sorted(value.items()):
                lines.append(f"  - {k}: `{v}`")
        else:
            lines.append(f"- **{key}**: `{value}`")
    path = out_dir / "RUN_MANIFEST.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _control_recall(out_dir: Path) -> dict:
    """{thesis_id: ControlRecall} from POSITIVE_CONTROL.csv.

    A thesis with no recovered control gets RETRIEVAL_UNVALIDATED; a thesis that
    missed a verified *direct* control gets RETRIEVAL_INCOMPLETE. Neither may be
    read as a literature gap.
    """
    import pandas as pd

    from src.paper_3.gap_matrix import ControlRecall

    path = out_dir / "POSITIVE_CONTROL.csv"
    if not path.exists():
        logger.warning("POSITIVE_CONTROL.csv missing — every thesis will be "
                       "reported as RETRIEVAL_UNVALIDATED")
        return {}
    try:
        frame = pd.read_csv(path)
    except Exception as exc:
        logger.warning("POSITIVE_CONTROL.csv unreadable: %s", exc)
        return {}

    def flag(group, column):
        if column not in group:
            return pd.Series(False, index=group.index)
        return group[column].fillna(False).astype(bool)

    out: dict[str, ControlRecall] = {}
    for tid, group in frame.groupby("thesis_id"):
        in_corpus = group[flag(group, "in_corpus")]
        if in_corpus.empty:
            out[str(tid)] = ControlRecall()
            continue
        recovered = flag(in_corpus, "recovered")
        # Only controls a human has read count towards the direct-completeness
        # rule: an unverified control is a guess, and a guess must not block a
        # verdict any more than it may license one.
        verified = flag(in_corpus, "manually_checked")
        direct = flag(in_corpus, "is_direct") & verified
        holdout = in_corpus.get("role", pd.Series("", index=in_corpus.index)) \
            .fillna("").eq("holdout")
        out[str(tid)] = ControlRecall(
            n_controls=len(in_corpus),
            n_recovered=int(recovered.sum()),
            n_direct_verified=int(direct.sum()),
            n_direct_recovered=int((direct & recovered).sum()),
            n_holdout=int(holdout.sum()),
            n_holdout_recovered=int((holdout & recovered).sum()),
            n_expected_stage_recovered=int(
                flag(in_corpus, "recovered_at_expected_stage").sum()),
            n_semantic_recovered=int(
                flag(in_corpus, "recovered_semantically").sum()),
        )
    return out


def _evidence_levels(out_dir: Path) -> tuple[dict[str, str], dict[str, float]]:
    """Per thesis: how deep its evidence goes, and how much of it was unreadable.

    A thesis whose every adjudicated row came from an abstract is demoted in
    `gap_matrix.assign_verdict_full`; a thesis whose candidates were mostly
    abstract-less is a coverage problem, not a literature gap.
    """
    import pandas as pd

    from src.paper_3 import abstract_mode, gap_matrix

    levels: dict[str, str] = {}
    relations_path = out_dir / "relations.parquet"
    if relations_path.exists():
        relations = pd.read_parquet(relations_path)
        if "evidence_level" in relations:
            for thesis_id, group in relations.groupby("thesis_id"):
                seen = set(group["evidence_level"].fillna("full_text"))
                if seen == {"abstract"}:
                    levels[str(thesis_id)] = gap_matrix.ABSTRACT_ONLY
                elif "abstract" in seen:
                    levels[str(thesis_id)] = gap_matrix.MIXED_LEVEL
                else:
                    levels[str(thesis_id)] = gap_matrix.FULL_TEXT

    missing: dict[str, float] = {}
    candidates_path = out_dir / abstract_mode.CANDIDATES_FILE
    if candidates_path.exists():
        missing = abstract_mode.abstracts_missing_by_thesis(
            pd.read_parquet(candidates_path))
    return levels, missing


def _assert_gemini_key() -> None:
    """Fail before the first call, not several hundred calls in."""
    from src.paper_3._utils import resolve_gemini_key
    if not resolve_gemini_key():
        raise SystemExit(
            "No Gemini API key found. Set GEMINI_API_KEY (or GOOGLE_API_KEY) "
            "before running --step classify/extract/matrix/novelty, or pass "
            "--llm ollama to adjudicate locally.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evidence-grounded literature gap analysis for paper 3")
    parser.add_argument("--phase", choices=("harvest", "analyse", "all"), default="all")
    parser.add_argument("--step", action="append", choices=ALL_STEPS,
                        help="run only this step (repeatable)")
    parser.add_argument("--theses", default=None, help="path to theses.yaml")
    parser.add_argument("--out", default=None, help="artefact directory")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--llm", choices=("gemini", "ollama"), default="gemini")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap adjudication/extraction calls (for a trial run)")
    parser.add_argument("--top-n", type=int, default=15,
                        help="screening depth per thesis for the metadata pass "
                             "(a screening budget, not a completeness criterion)")
    parser.add_argument("--tag", default="", help="label recorded in the manifest")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="list the steps that would run, write nothing")
    args = parser.parse_args(argv)

    from src.paper_3._utils import OUT_DIR, THESES_PATH
    from src.paper_3.theses import load_theses, validate_theses

    out_dir = Path(args.out) if args.out else OUT_DIR
    theses_path = Path(args.theses) if args.theses else THESES_PATH

    theses = load_theses(theses_path)
    problems = validate_theses(theses)
    if problems:
        for p in problems:
            logger.error("theses.yaml: %s", p)
        return 2
    logger.info("Loaded %d theses from %s", len(theses), theses_path)

    if args.step:
        steps = [s for s in ALL_STEPS if s in set(args.step)]
    elif args.phase == "harvest":
        steps = list(HARVEST_STEPS)
    elif args.phase == "analyse":
        steps = list(ANALYSE_STEPS)
    else:
        steps = list(ALL_STEPS)

    if args.dry_run:
        logger.info("Would run: %s", " → ".join(steps))
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = load_manifest(out_dir).get("run_id") or \
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    update_manifest(out_dir, run_id=run_id, tag=args.tag, llm=args.llm,
                    theses_path=str(theses_path), steps_requested=steps)

    if any(s in steps for s in ("classify", "meta-classify", "extract",
                                "matrix", "novelty")) \
            and args.llm == "gemini":
        _assert_gemini_key()

    harvest_dir = out_dir / "harvest"

    try:
        for step in steps:
            if step == "snapshot":
                from src.paper_3 import snapshot
                manifest = snapshot.SNAPSHOT_DIR / snapshot.MANIFEST_NAME
                if _step(step, manifest, args.force):
                    snapshot.pull(run_id=run_id)
                    m = snapshot.load_manifest()
                    update_manifest(out_dir, snapshot={
                        "n_files": m["n_files"], "pulled_at": m["pulled_at"],
                        "repos": m["repos"], "missing_patterns": m["missing_patterns"]})

            elif step == "draft":
                from src.paper_3 import draft_anchors
                if _step(step, out_dir / "draft_anchors.json", args.force):
                    draft_anchors.run(out_dir=out_dir)

            elif step == "queries":
                from src.paper_3 import harvest_queries
                if _step(step, harvest_dir / "queries.json", args.force):
                    path = harvest_queries.run(out_dir=harvest_dir, theses=theses)
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    update_manifest(out_dir, harvest_queryset=payload["provenance"])

            elif step == "controls":
                from src.paper_3 import control_plan
                control_plan.run(out_dir=out_dir, theses=theses)
                update_manifest(out_dir,
                                controls=control_plan.summary(theses))

            elif step == "freeze-controls":
                from src.paper_3 import control_set
                try:
                    payload = control_set.freeze(out_dir=out_dir, theses=theses,
                                                 force=args.force)
                except RuntimeError as exc:
                    logger.error("%s", exc)
                    return 1
                update_manifest(out_dir,
                                control_set_sha256=payload["sha256"],
                                control_set_frozen_at=payload["frozen_at"])

            elif step == "freeze":
                from src.paper_3 import freeze
                freeze.run(out_dir=out_dir, tag=args.tag, force=args.force)
                freeze.render_md(out_dir=out_dir)

            elif step == "discover":
                from src.paper_3 import harvest_openalex
                if _step(step, harvest_dir / "harvest_candidates.parquet", args.force):
                    harvest_openalex.discover(theses=theses, out_dir=harvest_dir)

            elif step == "meta-candidates":
                from src.paper_3 import abstract_mode
                if _step(step, out_dir / abstract_mode.CANDIDATES_FILE, args.force):
                    abstract_mode.build_candidates(
                        theses=theses, out_dir=out_dir,
                        top_n_per_thesis=args.top_n)

            elif step == "meta-classify":
                from src.paper_3 import abstract_mode
                abstract_mode.run(theses=theses, out_dir=out_dir,
                                  llm=args.llm, limit=args.limit,
                                  top_n_per_thesis=args.top_n)

            elif step == "download":
                from src.paper_3 import harvest_ingest
                downloaded, failed = harvest_ingest.step_download(out_dir)
                update_manifest(out_dir, n_downloaded=len(downloaded),
                                n_download_failed=len(failed))

            elif step == "download-oa":
                from src.paper_3 import harvest_ingest
                result = harvest_ingest.step_download_oa(out_dir)
                update_manifest(out_dir, oa_fallback={
                    "attempted": int(len(result)),
                    "fetched": int((result["route"] != "unresolved").sum()),
                    "routes": result["route"].value_counts().to_dict()})

            elif step == "grobid":
                from src.paper_3 import harvest_ingest
                update_manifest(out_dir, n_grobid=harvest_ingest.step_grobid(out_dir))

            elif step == "pipeline":
                from src.paper_3 import harvest_ingest
                if not harvest_ingest.step_pipeline(out_dir, workers=args.workers):
                    logger.error("pipeline step failed — stopping")
                    return 1

            elif step == "normalize":
                from src.paper_3 import harvest_ingest
                harvest_ingest.step_normalize(out_dir)

            elif step == "chroma":
                from src.paper_3 import harvest_ingest
                stats = harvest_ingest.step_chroma(out_dir)
                update_manifest(out_dir, chroma=stats)
                if not stats["ok"]:
                    logger.error("ChromaDB did not grow — stopping before the "
                                 "analysis reads a possibly corrupt index")
                    return 1

            elif step == "enrich":
                from src.paper_3 import harvest_ingest
                harvest_ingest.step_enrich(out_dir)

            elif step == "parquet":
                from src.paper_3 import harvest_ingest
                harvest_ingest.step_parquet(out_dir)

            elif step == "graph":
                from src.paper_3 import harvest_ingest
                harvest_ingest.step_graph(out_dir)

            elif step == "index":
                from src.paper_3 import corpus_index
                if _step(step, out_dir / "corpus_index.parquet", args.force):
                    corpus_index.build_index(out_dir)

            elif step == "retrieve":
                from src.paper_3 import retrieve
                if _step(step, out_dir / "thesis_candidates.parquet", args.force):
                    _frame, diagnostics = retrieve.run(theses=theses, out_dir=out_dir)
                    update_manifest(out_dir, **diagnostics)

            elif step == "calibrate":
                from src.paper_3 import calibrate
                if _step(step, out_dir / calibrate.SHEET, args.force):
                    calibrate.build_sheet(out_dir)
                    logger.warning(
                        "Calibration sheet written. Label it by hand, then run "
                        "`python -m src.paper_3.calibrate report` before trusting "
                        "any verdict — the distance gate is not yet calibrated.")

            elif step == "gate":
                from src.paper_3 import acceptance
                ready = acceptance.run(out_dir=out_dir, theses=theses)
                update_manifest(out_dir, acceptance_gate_ready=ready)
                if not ready and not args.force:
                    logger.error(
                        "Acceptance gate not met. Adjudicating now would produce "
                        "rows that cannot be told apart from retrieval failures. "
                        "Fix the criteria above, or pass --force to proceed "
                        "knowingly (it is recorded in the manifest).")
                    return 1

            elif step == "classify":
                from src.paper_3 import classify_relation
                classify_relation.run(theses=theses, out_dir=out_dir,
                                      llm=args.llm, limit=args.limit)

            elif step == "finalize":
                from src.paper_3 import finalize_relations
                relations, rejected = finalize_relations.run(out_dir=out_dir)
                update_manifest(out_dir, n_relations=len(relations),
                                n_quotes_rejected=len(rejected))

            elif step == "extract":
                from src.paper_3 import extract_fields
                extract_fields.run(theses=theses, out_dir=out_dir,
                                   llm=args.llm, limit=args.limit)

            elif step == "coverage":
                from src.paper_3 import corpus_index
                manifest = load_manifest(out_dir)
                worldwide = {}
                worldwide_path = harvest_dir / "worldwide_counts.json"
                if worldwide_path.exists():
                    worldwide = json.loads(worldwide_path.read_text(encoding="utf-8"))
                candidates = None
                candidates_path = harvest_dir / "harvest_candidates.parquet"
                if candidates_path.exists():
                    import pandas as pd
                    candidates = pd.read_parquet(candidates_path)
                corpus_index.build_coverage(
                    theses=theses, out_dir=out_dir,
                    worldwide_counts=worldwide, candidates=candidates,
                    keyterm_counts=manifest.get("keyterm_counts"),
                    semantic_stats=manifest.get("semantic_stats"),
                    kg_expansion_available=manifest.get("kg_expansion_available", True),
                )

            elif step == "screen":
                from src.paper_3.v2 import slice_screening
                if _step(step, out_dir / slice_screening.DENOMINATORS_CSV, args.force):
                    slice_screening.run(out_dir=out_dir, harvest_dir=harvest_dir)

            elif step == "matrix":
                from src.paper_3 import gap_matrix, novelty
                manifest = load_manifest(out_dir)
                prose = novelty.summarise_theses(
                    out_dir=out_dir, theses=theses, llm=args.llm)
                control_recall = _control_recall(out_dir)
                evidence_levels, missing_abstracts = _evidence_levels(out_dir)
                gap_matrix.run(
                    out_dir=out_dir, theses=theses, prose=prose, run_id=run_id,
                    control_recall=control_recall,
                    evidence_levels=evidence_levels,
                    missing_abstracts=missing_abstracts,
                    kg_expansion_available=manifest.get("kg_expansion_available", True),
                    adjudication_tier=args.llm)

            elif step == "novelty":
                from src.paper_3 import novelty
                novelty.run(out_dir=out_dir, theses=theses, llm=args.llm)

            elif step == "literature":
                from src.paper_3.v2 import literature_matrix
                if _step(step, out_dir / literature_matrix.STUB_FILE, args.force):
                    literature_matrix.run(out_dir=out_dir, theses=theses)

            elif step == "own-evidence":
                from src.paper_3 import own_evidence
                if _step(step, out_dir / own_evidence.EVIDENCE_FILE, args.force):
                    own_evidence.run(out_dir=out_dir)

            elif step == "gemini-package":
                from src.paper_3 import gemini_package
                if _step(step, out_dir / gemini_package.PACKAGE_DIR / "T01.json", args.force):
                    gemini_package.run(out_dir=out_dir, theses=theses)

            elif step == "references":
                from src.paper_3.v2 import reference_registry
                if _step(step, out_dir / reference_registry.REGISTRY_CSV, args.force):
                    reference_registry.run(out_dir=out_dir)

            elif step == "assemble":
                from src.paper_3.v2 import assemble
                if _step(step, out_dir / assemble.MANUSCRIPT_EN, args.force):
                    assemble.assemble(out_dir=out_dir)

            elif step == "translate":
                from src.paper_3.v2 import translate
                if _step(step, out_dir / translate.MANUSCRIPT_UK, args.force):
                    translate.run(out_dir=out_dir)

            elif step == "docx":
                from src.paper_3.v2 import assemble, translate
                md = out_dir / assemble.MANUSCRIPT_EN
                if _step(step, md.with_suffix(".docx"), args.force):
                    assemble.to_docx(md)
                uk = out_dir / translate.MANUSCRIPT_UK
                if uk.exists() and _step(step, uk.with_suffix(".docx"), args.force):
                    assemble.to_docx(uk)

            elif step == "publish":
                from src.paper_3 import publish
                render_manifest_md(out_dir)
                publish.run(out_dir=out_dir, run_id=run_id)

    except Exception as exc:
        from src.paper_3.harvest_ingest import StepError
        if isinstance(exc, (StepError, FileNotFoundError)):
            logger.error("%s", exc)
            return 1
        raise

    render_manifest_md(out_dir)
    logger.info("Done. Artefacts in %s", out_dir)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
