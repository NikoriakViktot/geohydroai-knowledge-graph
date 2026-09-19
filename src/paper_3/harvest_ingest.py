"""Phase 0c–0j — bring the selected papers into the corpus.

Each step does exactly one kind of work and guards itself on the existence of its
own output files, so the chain is restartable at any point: interrupt it, re-run
it, and it resumes from the first missing file rather than starting over.

Nothing here mixes concerns. `step_download` speaks HTTP, `step_grobid` speaks XML,
`step_graph` writes Neo4j, and no step does two of those — which is what lets any
one of them fail without poisoning the others.

Papers enter the *main* corpus, tagged `cohort="paper_3"` in `cohort_paper_3.csv`
so that Article 1's frozen counts can still be reproduced by excluding them.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from src.paper_3._utils import (
    COHORT,
    HARVEST_DIR,
    NORMALIZED_DIR,
    OUT_DIR,
    PAPER_JSON_DIR,
    PDF_MISSING_DIR,
    PROJECT_ROOT,
    XML_DIR,
    reset_con,
)

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 60
_DOWNLOAD_DELAY = 1.5

#: Ray/Rust stability environment. CLAUDE.md requires these for any Ray-based run;
#: without them the forked workers deadlock on this WSL2 box.
RAY_ENV = {
    "TOKENIZERS_PARALLELISM": "false",
    "RAYON_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "2",
}

#: `--workers 3` is the stable ceiling for this machine (~3 GB RAM).
MAX_WORKERS = 3


class StepError(RuntimeError):
    """A required service is down. Raised rather than degrading silently."""


def _todo_frame(out_dir: Path) -> pd.DataFrame:
    path = Path(out_dir) / "harvest" / "harvest_candidates.parquet"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run --step discover first")
    frame = pd.read_parquet(path)
    return frame[frame["selected"].fillna(False)]


def _run_module(module: str, args: list[str], env_extra: dict | None = None,
                cwd: Path | None = None) -> bool:
    """Run `python -m module args…` in a subprocess. Returns success."""
    env = {**os.environ, **RAY_ENV, **(env_extra or {})}
    cmd = [sys.executable, "-m", module, *args]
    logger.info("→ %s", " ".join(cmd))
    result = subprocess.run(cmd, env=env, cwd=str(cwd or PROJECT_ROOT))
    if result.returncode != 0:
        logger.error("%s exited with code %d", module, result.returncode)
    return result.returncode == 0


# ── step: download ────────────────────────────────────────────────────────────

def _pdf_session() -> requests.Session:
    """Browser user agent: MDPI and IOP return 403 or HTML to anything else,
    even for genuinely open-access PDFs (same finding as recover_missing.py)."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                       "Gecko/20100101 Firefox/128.0"),
        "Accept": "application/pdf,*/*",
    })
    return session


def step_download(out_dir: Path | None = None) -> tuple[list[str], list[str]]:
    """Fetch the open-access PDFs. Returns (downloaded, failed).

    Only URLs OpenAlex already reported as open access are fetched; paywalled
    works are listed for manual handling and never scraped.
    """
    target = Path(out_dir) if out_dir else OUT_DIR
    todo = _todo_frame(target)
    PDF_MISSING_DIR.mkdir(parents=True, exist_ok=True)
    session = _pdf_session()

    downloaded, failed, cohort_rows = [], [], []
    for row in todo.itertuples():
        pdf_path = PDF_MISSING_DIR / f"{row.slug}.pdf"
        cohort_rows.append({
            "paper_id": row.slug, "doi": row.doi, "slug": row.slug,
            "title": row.title, "year": row.year,
            "matched_thesis_ids": ";".join(row.matched_thesis_ids),
            "harvested_at": date.today().isoformat(), "cohort": COHORT,
        })
        if pdf_path.exists():
            logger.info("already have %s", pdf_path.name)
            continue
        if not row.pdf_url:
            failed.append(row.doi)
            continue

        try:
            r = session.get(row.pdf_url, timeout=_HTTP_TIMEOUT, allow_redirects=True)
            content_type = r.headers.get("Content-Type", "")
            # Magic bytes, not just the content type: publishers routinely serve
            # an HTML interstitial with a 200 and a PDF content type.
            if r.status_code == 200 and (r.content[:5] == b"%PDF-" or "pdf" in content_type):
                if r.content[:5] != b"%PDF-":
                    logger.warning("  ✗ %s claims PDF but is not — skipped", row.doi)
                    failed.append(row.doi)
                else:
                    pdf_path.write_bytes(r.content)
                    downloaded.append(row.doi)
                    logger.info("  ✓ %s (%.1f KB)", pdf_path.name, len(r.content) / 1024)
            else:
                logger.warning("  ✗ %s → HTTP %d %s", row.doi, r.status_code, content_type)
                failed.append(row.doi)
        except requests.RequestException as exc:
            logger.warning("  ✗ %s → %s", row.doi, exc)
            failed.append(row.doi)
        time.sleep(_DOWNLOAD_DELAY)

    if cohort_rows:
        cohort_path = target / "cohort_paper_3.csv"
        target.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(cohort_rows).drop_duplicates("doi").to_csv(cohort_path, index=False)
        logger.info("Cohort manifest: %d papers → %s", len(cohort_rows), cohort_path)

    logger.info("Download: %d fetched, %d unavailable", len(downloaded), len(failed))
    return downloaded, failed


def step_download_oa(out_dir: Path | None = None) -> pd.DataFrame:
    """Second pass for open-access works the first pass could not fetch.

    Same rule as `step_download`: nothing paywalled is ever fetched. This pass
    only asks more places where an OA PDF is legitimately published.
    """
    from src.paper_3 import oa_resolver

    target = Path(out_dir) if out_dir else OUT_DIR
    todo = _todo_frame(target)
    return oa_resolver.run(todo, out_dir=target / "harvest")


# ── step: grobid ──────────────────────────────────────────────────────────────

def step_grobid(out_dir: Path | None = None) -> int:
    """PDF → TEI XML. Raises StepError when GROBID is not reachable."""
    target = Path(out_dir) if out_dir else OUT_DIR
    todo = _todo_frame(target)

    pending = [PDF_MISSING_DIR / f"{row.slug}.pdf" for row in todo.itertuples()
               if (PDF_MISSING_DIR / f"{row.slug}.pdf").exists()
               and not (XML_DIR / f"{row.slug}.tei.xml").exists()]
    if not pending:
        logger.info("GROBID: nothing to parse")
        return 0

    # Harvested papers already carry OpenAlex metadata, so CrossRef header
    # consolidation adds nothing here — and when the service is unreachable it
    # blocks past every client timeout (see grobid_client.CONSOLIDATE_HEADER).
    os.environ.setdefault("GROBID_CONSOLIDATE_HEADER", "0")

    from src.ingestion.grobid_client import GROBIDClient

    ok = 0
    with GROBIDClient(max_retries=3) as client:
        if not client.is_alive():
            raise StepError(
                "GROBID is not answering on :8070 — run `docker compose up -d`. "
                "Stopping rather than continuing with unparsed PDFs.")
        XML_DIR.mkdir(parents=True, exist_ok=True)
        for i, pdf in enumerate(pending, 1):
            logger.info("[%d/%d] GROBID → %s", i, len(pending), pdf.name)
            result = client.process_pdf(pdf)
            if result.success and result.xml_text:
                (XML_DIR / f"{pdf.stem}.tei.xml").write_text(
                    result.xml_text, encoding="utf-8")
                ok += 1
            else:
                logger.warning("  ✗ %s: %s", pdf.name, result.failure_type)
    logger.info("GROBID: %d/%d parsed", ok, len(pending))
    return ok


# ── step: pipeline ────────────────────────────────────────────────────────────

def _ollama_alive() -> bool:
    try:
        url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        return requests.get(f"{url}/api/tags", timeout=5).status_code == 200
    except requests.RequestException:
        return False


def _stage_symlinks(paths: list[Path], staging: Path) -> int:
    """Symlink the files for this run into a staging directory.

    The pipeline runner takes a directory, and the corpus holds ~7 000 XMLs. A
    staging directory is what keeps a 250-paper run from re-processing all of them.
    """
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    n = 0
    for path in paths:
        link = staging / path.name
        try:
            link.symlink_to(path.resolve())
            n += 1
        except OSError:
            shutil.copy2(path, link)
            n += 1
    return n


def step_pipeline(out_dir: Path | None = None, workers: int = MAX_WORKERS) -> bool:
    """TEI XML → paper.json via the Ray pipeline, over a staging directory only."""
    target = Path(out_dir) if out_dir else OUT_DIR
    todo = _todo_frame(target)
    workers = min(workers, MAX_WORKERS)

    pending = [XML_DIR / f"{row.slug}.tei.xml" for row in todo.itertuples()
               if (XML_DIR / f"{row.slug}.tei.xml").exists()
               and not (PAPER_JSON_DIR / f"{row.slug}.tei.paper.json").exists()]
    if not pending:
        logger.info("Pipeline: nothing to process")
        return True
    if not _ollama_alive():
        raise StepError(
            "Ollama is not answering — run `ollama serve`. Stopping rather than "
            "producing paper.json files with no LLM judge.")

    staging = HARVEST_DIR / "staging_xml"
    n = _stage_symlinks(pending, staging)
    logger.info("Pipeline: %d XML staged in %s", n, staging)
    return _run_module("src.orchestration.pipeline_runner",
                       ["--xml-dir", str(staging), "--workers", str(workers)])


# ── step: normalize ───────────────────────────────────────────────────────────

def step_normalize(out_dir: Path | None = None) -> bool:
    target = Path(out_dir) if out_dir else OUT_DIR
    todo = _todo_frame(target)

    pending = [PAPER_JSON_DIR / f"{row.slug}.tei.paper.json" for row in todo.itertuples()
               if (PAPER_JSON_DIR / f"{row.slug}.tei.paper.json").exists()
               and not (NORMALIZED_DIR / f"{row.slug}.json").exists()]
    if not pending:
        logger.info("Normalize: nothing to do")
        return True

    staging = HARVEST_DIR / "staging_json"
    _stage_symlinks(pending, staging)
    return _run_module("src.orchestration.normalization_runner",
                       ["--input-dir", str(staging), "--output-dir", str(NORMALIZED_DIR)])


# ── step: chroma ──────────────────────────────────────────────────────────────

def chroma_count() -> int:
    from src.config import COLLECTION_NAME
    from src.vectorstore.chroma_store import VectorStore
    return VectorStore(collection_name=COLLECTION_NAME).count()


def step_chroma(out_dir: Path | None = None, batch_size: int = 32) -> dict:
    """Index the new papers, then verify the collection actually grew.

    ChromaDB 1.5.9 can leave items in `embeddings_queue` and corrupt HNSW on the
    next reload, and the failure is silent until a later query returns nothing.
    Counting before and after is cheap; discovering a corrupt index during the
    analysis is not.
    """
    from src.config import COLLECTION_NAME

    target = Path(out_dir) if out_dir else OUT_DIR
    todo = _todo_frame(target)
    staging = HARVEST_DIR / "staging_xml"
    if not staging.exists():
        logger.warning("Chroma: no staging dir — run --step pipeline first")
        return {"before": 0, "after": 0, "delta": 0, "ok": False}

    before = chroma_count()
    ok = _run_module(
        "src.orchestration.reindex_chromadb",
        ["--xml-dir", str(staging), "--json-dir", str(PAPER_JSON_DIR),
         "--collection-name", COLLECTION_NAME, "--batch-size", str(batch_size)],
        env_extra={"EMBEDDING_MODEL": os.getenv("EMBEDDING_MODEL",
                                                "allenai/specter2_base")},
    )
    after = chroma_count()
    delta = after - before
    n_papers = len(todo)
    expected_min = n_papers * 20   # deliberately loose; we are catching zero, not tuning

    if delta <= 0:
        logger.error("Chroma count did not increase (%d → %d) — the index may be "
                     "corrupt. Do NOT continue; check .chromadb before re-running.",
                     before, after)
        ok = False
    elif delta < expected_min:
        logger.warning("Chroma grew by only %d chunks for %d papers — expected at "
                       "least %d. Verify before trusting retrieval.",
                       delta, n_papers, expected_min)
    else:
        logger.info("Chroma: %d → %d chunks (+%d for %d papers)",
                    before, after, delta, n_papers)
    return {"before": before, "after": after, "delta": delta, "ok": ok}


# ── steps: enrich, parquet, graph ─────────────────────────────────────────────

def step_enrich(out_dir: Path | None = None) -> bool:
    return _run_module("src.orchestration.enrichment_runner", [])


def backup_analytics() -> Path | None:
    """Snapshot data/analytics before rebuilding it.

    The dashboard and research_query_service read those parquet files live, so a
    rebuild that fails halfway leaves both consumers reading truncated data.
    """
    source = PROJECT_ROOT / "data" / "analytics"
    if not source.exists():
        return None
    backup = PROJECT_ROOT / "data" / f"analytics.bak_{date.today():%Y%m%d}"
    if backup.exists():
        logger.info("analytics backup already exists: %s", backup)
        return backup
    shutil.copytree(source, backup)
    logger.info("analytics backed up → %s", backup)
    return backup


def step_parquet(out_dir: Path | None = None) -> bool:
    """Rebuild the analytics layer, which is stale until this runs."""
    backup_analytics()
    ok = _run_module("src.enrichment.build_parquet_layer", [])
    reset_con()   # the cached DuckDB views point at the old files
    return ok


def step_graph(out_dir: Path | None = None) -> bool:
    """Rebuild the Neo4j graph. MERGE-only; no DELETE reaches the database."""
    from src.config import settings

    password = os.getenv("NEO4J_PASSWORD") or getattr(settings, "NEO4J_PASSWORD", "")
    if not password:
        raise StepError("NEO4J_PASSWORD is not set — cannot build the graph.")
    try:
        requests.get("http://localhost:7474", timeout=5)
    except requests.RequestException as exc:
        raise StepError(
            f"Neo4j is not answering on :7474 ({exc}) — run `docker compose up -d`."
        ) from exc

    return _run_module("src.graph.build_graph", [
        "--uri", os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        "--user", os.getenv("NEO4J_USER", "neo4j"),
        "--password", password,
    ])
