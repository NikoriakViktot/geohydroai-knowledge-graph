"""CLI of the literature evidence run (the workbench step `literature` configures the paths first).

    prepare          validate atomic_claims.yaml, build the runtime corpus index, snapshot the bib
    retrieve         run the retrieval cascade per atomic claim (one Chroma/HNSW load per process)
    screen           Gemini role labelling with the verbatim-quote gate (one model lane per process)
    kakhovka-numbers extract every km²/m³s⁻¹ statement of the Kakhovka papers with its semantics
    references       CrossRef/OpenAlex verification of references.bib → 07 / 07b
    checks           deterministic manuscript checks A–H → findings JSON for 01
    export           02 / 03 / 05 deliverables + status derivation
"""
# Moved from tools/paper3_audit/cli.py (P6, 2026-10-02); run paths come from config.py (cfg).
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.workbench.literature import claims as claims_mod
from src.workbench.literature import manifest
from src.workbench.literature.config import LANES
from src.workbench.literature import config as cfg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("paper3_audit")


def _step(name: str, out_file: Path, force: bool) -> bool:
    if force or not out_file.exists():
        return True
    logger.info("Skipping %s (already exists: %s) — use --force to re-run", name, out_file.name)
    return False


def _load_claims(args):
    theses = claims_mod.load_theses(cfg.THESES_JSON)
    claims, novelty, meta = claims_mod.load_atomic_claims(theses=theses)
    problems = claims_mod.validate(theses, claims)
    if problems:
        for p in problems:
            logger.error("atomic_claims.yaml: %s", p)
        raise SystemExit(f"{len(problems)} problem(s) in atomic_claims.yaml")
    selected = claims_mod.by_pass(claims, getattr(args, "pass_id", None))
    if getattr(args, "thesis", None):
        wanted = set(args.thesis)
        selected = [c for c in selected if c.thesis_id in wanted or c.atomic_id in wanted]
    return theses, claims, selected, novelty, meta


# ── prepare ───────────────────────────────────────────────────────────────────

def cmd_prepare(args) -> int:
    from src.workbench.literature import bibtex, corpus
    cfg.OUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg.WORK_DIR.mkdir(parents=True, exist_ok=True)
    theses, claims, _, novelty, meta = _load_claims(args)
    logger.info("%d theses → %d atomic claims (%d not_needed); %d novelty questions",
                len(theses), len(claims), sum(c.not_needed for c in claims), len(novelty))
    sha = claims_mod.queries_sha256(claims)
    if _step("corpus index", cfg.WORK_DIR / corpus.INDEX_FILE, args.force):
        index, oa, edges = corpus.build_runtime_index()
    else:
        index = corpus.load_runtime_index(); oa, edges = corpus.load_openalex()
    try:
        bib_info = bibtex.snapshot_floodstate_files()
    except Exception as exc:
        logger.error("could not snapshot floodstate-eo files via wsl.exe: %s", exc)
        bib_info = {"error": str(exc)[:200]}
    from src.workbench.literature.retrieve import rules
    manifest.update(
        queries_version=meta.get("queries_version", ""), queries_sha256=sha,
        n_atomic_claims=len(claims), n_theses=len(theses),
        n_normalized_files=int(len(index)), n_with_openalex_id=int(len(oa)),
        n_reference_edges=int(len(edges)),
        n_duplicate_groups=int(index["duplicate_group"].replace("", None).nunique()),
        n_kakhovka_papers=int((index["kakhovka_mentions"] > 0).sum()),
        retrieval_rules=rules(), floodstate_snapshot=bib_info,
        claims_by_pass={p: sum(1 for c in claims if c.pass_id == p) for p in ("A", "B", "C")},
    )
    logger.info("prepare done: queries_sha256=%s…, corpus=%d", sha[:12], len(index))
    return 0


# ── retrieve ──────────────────────────────────────────────────────────────────

def cmd_retrieve(args) -> int:
    from src.workbench.literature import retrieve as retrieve_mod
    _, claims, selected, _, _ = _load_claims(args)
    sha = claims_mod.queries_sha256(claims)
    n_chroma = None
    if args.dry_run:
        for c in selected:
            print(c.atomic_id, c.priority, len(c.queries), "queries")
        return 0
    from src.config import EMBEDDING_MODEL
    from src.embedding.embedder import Embedder
    from src.vectorstore.chroma_store import VectorStore
    from src.workbench.literature.config import AUDIT_COLLECTION as COLLECTION_NAME
    import time
    t0 = time.time()
    store = VectorStore(persist_dir=cfg.AUDIT_CHROMA_DIR, collection_name=COLLECTION_NAME)
    try:
        n_chroma = store.count()
    except Exception as exc:
        logger.warning("store.count() failed: %s", exc)
    logger.info("Chroma %s opened in %.0f s (%s chunks)", COLLECTION_NAME, time.time() - t0, n_chroma)
    embedder = Embedder(model_name=EMBEDDING_MODEL)
    kg_available = retrieve_mod.probe_neo4j()
    manifest.update(routes={"kg": "ok" if kg_available else "route_unavailable"}, n_chroma=n_chroma)
    retrieve_mod.run(selected, store, embedder, queries_sha256=sha, n_chroma=n_chroma,
                     limit=args.limit, force=args.force, kg_available=kg_available)
    return 0


# ── later steps (modules imported lazily so a missing one fails only its own command) ──

def cmd_select(args) -> int:
    from src.workbench.literature import retrieve as retrieve_mod
    _, claims, _, _, _ = _load_claims(args)
    retrieve_mod.run_select(claims=claims)
    retrieve_mod.apply_supplementary()
    from src.workbench.literature.retrieve import SCREEN_QUOTA, COUNTER_RESERVE
    manifest.update(screen_quota=SCREEN_QUOTA, screen_counter_reserve=COUNTER_RESERVE)
    return 0


def cmd_screen(args) -> int:
    from src.workbench.literature import screen as screen_mod
    _, claims, selected, _, _ = _load_claims(args)
    shard = None
    if args.shard:
        k, n = args.shard.split("/"); shard = (int(k), int(n))
    return screen_mod.run(selected, lane=args.lane, limit=args.limit, both_lanes=args.both_lanes,
                          llm=args.llm, finalize_only=args.finalize_only, shard=shard)


def cmd_numbers(args) -> int:
    from src.workbench.literature import numbers as numbers_mod
    return numbers_mod.run(force=args.force)


def cmd_references(args) -> int:
    from src.workbench.literature import references as refs_mod
    return refs_mod.run(force=args.force, offline=args.offline)


def cmd_enrich(args) -> int:
    from src.workbench.literature import references as refs_mod
    return refs_mod.enrich_retained(offline=args.offline)


def cmd_checks(args) -> int:
    from src.workbench.literature import checks as checks_mod
    return checks_mod.run()


def cmd_revise(args) -> int:
    from src.workbench.literature import revise as revise_mod
    return revise_mod.run()


def cmd_report(args) -> int:
    from src.workbench.literature import report as report_mod
    theses, claims, _, novelty, meta = _load_claims(args)
    n = report_mod.fill_novelty()
    table = report_mod.thesis_table(theses, claims)
    (cfg.WORK_DIR / "thesis_verdicts_table.md").write_text(table, encoding="utf-8")
    rep = report_mod.completion_report(theses, claims)
    (cfg.OUT_DIR / "completion_report.md").write_text(rep + "\n", encoding="utf-8")
    print(rep); logger.info("report: %d novelty verdicts filled; thesis table → _work/thesis_verdicts_table.md", n)
    return 0


def cmd_export(args) -> int:
    from src.workbench.literature import export as export_mod
    theses, claims, _, novelty, meta = _load_claims(args)
    return export_mod.run(theses, claims, novelty)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paper3_literature_audit", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--force", action="store_true")
        sp.add_argument("--limit", type=int, default=None)
        sp.add_argument("--thesis", action="append", default=None, help="TH-… or TH-….A (repeatable)")
        sp.add_argument("--pass", dest="pass_id", choices=("A", "B", "C"), default=None)
        sp.add_argument("--dry-run", action="store_true")

    for name, fn in (("prepare", cmd_prepare), ("retrieve", cmd_retrieve), ("select", cmd_select), ("export", cmd_export),
                     ("checks", cmd_checks), ("kakhovka-numbers", cmd_numbers), ("revise", cmd_revise), ("report", cmd_report)):
        sp = sub.add_parser(name); common(sp); sp.set_defaults(fn=fn)
    sp = sub.add_parser("screen"); common(sp); sp.set_defaults(fn=cmd_screen)
    sp.add_argument("--lane", choices=LANES, default=LANES[0])
    sp.add_argument("--both-lanes", action="store_true", help="screen pairs already done by the other lane too")
    sp.add_argument("--llm", choices=("gemini", "ollama"), default="gemini")
    sp.add_argument("--finalize-only", action="store_true", help="no calls; just re-judge the raw file")
    sp.add_argument("--shard", default=None, help="k/n: this lane takes every pair whose ordinal %% n == k")
    sp = sub.add_parser("references"); common(sp); sp.set_defaults(fn=cmd_references)
    sp.add_argument("--offline", action="store_true", help="no CrossRef/OpenAlex calls; caches only")
    sp = sub.add_parser("enrich-retained"); common(sp); sp.set_defaults(fn=cmd_enrich)
    sp.add_argument("--offline", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
