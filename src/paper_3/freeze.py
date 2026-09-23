"""Freeze the retrieval configuration before a harvest.

A year from now the question will be "which corpus produced these 47 supporting
papers?", and "the corpus as it was" is not an answer. This records the inputs
that determine the result — corpus size, file hashes, embedding model, gate
values, rule versions — so a later run can be compared against this one rather
than merely repeated.

Written once before the harvest (`--step freeze`) and again after it, so the
two states are both on record. Refuses to overwrite a frozen manifest without
`--force`: a freeze that silently moves is not a freeze.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.paper_3._utils import (
    PARQUET_DIR,
    NORMALIZED_DIR,
    OUT_DIR,
    PROJECT_ROOT,
    THESES_PATH,
)

logger = logging.getLogger(__name__)

FREEZE_FILE = "RETRIEVAL_MANIFEST.json"

#: Bumped by hand whenever a rule that changes which papers qualify is altered.
#: A result produced under a different version is not comparable with this one.
RETRIEVAL_RULES_VERSION = "1.2.0"

RULES_CHANGELOG = {
    "1.0.0": "Initial: >=2 key-term families, distance gate 0.45 or >=3 chunks.",
    "1.1.0": ("Mandatory discriminating family (key_terms[0] must hit, plus >=1 "
              "other); paper-level counting replaces passage-level; citation "
              "depth fixed at 1; retrieval_origin recorded."),
    "1.2.0": ("Positive controls held out, never injected; development/holdout "
              "roles; three recall metrics (overall, expected-stage, semantic); "
              "MIXED paper relation; eight-value verdict scale with "
              "RETRIEVAL_INCOMPLETE blocking CANDIDATE_GAP when a verified "
              "direct control was missed."),
}


def _sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(PROJECT_ROOT),
                             capture_output=True, text=True, timeout=15)
        return out.stdout.strip()
    except Exception:
        return ""


def _chroma_state() -> dict:
    try:
        from src.config import CHROMA_DIR, COLLECTION_NAME, EMBEDDING_MODEL
        from src.vectorstore.chroma_store import VectorStore
        return {
            "collection": COLLECTION_NAME,
            "persist_dir": str(CHROMA_DIR),
            "embedding_model": EMBEDDING_MODEL,
            "n_chunks": VectorStore(collection_name=COLLECTION_NAME).count(),
        }
    except Exception as exc:
        logger.warning("could not read ChromaDB state: %s", exc)
        return {"collection": "", "n_chunks": None, "error": str(exc)}


def build(out_dir: Path | None = None, tag: str = "") -> dict:
    """Collect everything that determines what retrieval will find."""
    from src.paper_3 import retrieve
    from src.paper_3.corpus_index import (
        ABSENT_IN_CORPUS_MAX,
        ABSENT_WORLDWIDE_MIN,
        REFERENCES_PARQUET,
        THIN_COVERAGE_RATIO,
        THIN_KEYTERM_PAPERS,
    )
    from src.paper_3.evidence import FUZZY_THRESHOLD, MIN_QUOTE_CHARS
    from src.paper_3.theses import load_theses

    target = Path(out_dir) if out_dir else OUT_DIR
    papers_parquet = PARQUET_DIR / "papers.parquet"

    corpus_size = None
    if papers_parquet.exists():
        try:
            corpus_size = len(pd.read_parquet(papers_parquet, columns=["paper_id"]))
        except Exception as exc:
            logger.warning("could not size papers.parquet: %s", exc)

    theses = load_theses()

    return {
        "tag": tag,
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_short": _git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "retrieval_rules_version": RETRIEVAL_RULES_VERSION,
        "rules_changelog": RULES_CHANGELOG,
        "corpus": {
            "n_papers_parquet": corpus_size,
            "n_normalized_files": (len(list(NORMALIZED_DIR.glob("*.json")))
                                   if NORMALIZED_DIR.exists() else None),
            "papers_parquet_sha256": _sha256_file(papers_parquet),
            "references_parquet_sha256": _sha256_file(REFERENCES_PARQUET),
            "references_parquet_path": str(REFERENCES_PARQUET),
        },
        "theses": {
            "path": str(THESES_PATH),
            "sha256": _sha256_file(THESES_PATH),
            "n_theses": len(theses),
            "ids": [t.id for t in theses],
        },
        "chroma": _chroma_state(),
        "retrieval_rules": {
            "controls_held_out": True,
            "distance_gate": retrieve.DISTANCE_GATE,
            "min_chunks": retrieve.MIN_CHUNKS,
            "top_k_chunks": retrieve.TOP_K_CHUNKS,
            "max_candidates_per_thesis": retrieve.MAX_CANDIDATES_PER_THESIS,
            "citation_depth": retrieve.CITATION_DEPTH,
            "retrieval_origins": list(retrieve.RETRIEVAL_ORIGINS),
            "on_topic_rule": "key_terms[0] must hit, plus >=1 other family",
        },
        "controls": {
            "n_theses_with_controls": sum(1 for t in theses if t.positive_controls),
            "n_verified": sum(len(t.verified_controls) for t in theses),
            "n_holdout": sum(len(t.holdout_controls) for t in theses),
            "n_holdout_consumed": sum(
                1 for t in theses for c in t.holdout_controls if c.holdout_consumed),
        },
        "evidence_rules": {
            "fuzzy_threshold": FUZZY_THRESHOLD,
            "min_quote_chars": MIN_QUOTE_CHARS,
            "quote_required_for": ["SUPPORTS", "CONTRADICTS"],
            "unit_of_evidence": "paper",
        },
        "coverage_thresholds": {
            "absent_worldwide_min": ABSENT_WORLDWIDE_MIN,
            "absent_in_corpus_max": ABSENT_IN_CORPUS_MAX,
            "thin_coverage_ratio": THIN_COVERAGE_RATIO,
            "thin_keyterm_papers": THIN_KEYTERM_PAPERS,
        },
    }


def run(out_dir: Path | None = None, tag: str = "", force: bool = False) -> Path:
    """Write RETRIEVAL_MANIFEST.json. Appends to `history` rather than replacing."""
    target = Path(out_dir) if out_dir else OUT_DIR
    target.mkdir(parents=True, exist_ok=True)
    path = target / FREEZE_FILE

    current = build(target, tag=tag)

    history = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            history = existing.get("history", [])
            previous = {k: v for k, v in existing.items() if k != "history"}
            if not force and previous.get("theses", {}).get("sha256") \
                    != current["theses"]["sha256"]:
                logger.warning(
                    "theses.yaml has changed since the last freeze "
                    "(%s → %s). The previous freeze is kept in `history`; "
                    "results from before this point are not comparable.",
                    previous.get("theses", {}).get("sha256", "")[:12],
                    current["theses"]["sha256"][:12])
            history.append(previous)
        except Exception as exc:
            logger.warning("existing manifest unreadable (%s) — starting fresh", exc)

    payload = {**current, "history": history}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8")

    logger.info("Frozen: corpus %s papers, chroma %s chunks, rules v%s, commit %s%s",
                current["corpus"]["n_papers_parquet"],
                current["chroma"].get("n_chunks"),
                current["retrieval_rules_version"],
                current["git_short"] or "unknown",
                " (working tree dirty)" if current["git_dirty"] else "")
    if current["git_dirty"]:
        logger.warning("Working tree is dirty — commit before a harvest you intend "
                       "to cite, or the commit hash will not identify the code that ran.")
    return path


def render_md(out_dir: Path | None = None) -> Path:
    """Human-readable freeze record for the published bundle."""
    target = Path(out_dir) if out_dir else OUT_DIR
    path = target / FREEZE_FILE
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run --step freeze first")
    m = json.loads(path.read_text(encoding="utf-8"))

    corpus, chroma = m["corpus"], m["chroma"]
    lines = [
        "# Retrieval manifest — Paper 3",
        "",
        f"Frozen {m['frozen_at']}"
        + (f" · tag `{m['tag']}`" if m.get("tag") else "")
        + f" · commit `{m.get('git_short') or 'unknown'}`"
        + (" · **working tree dirty**" if m.get("git_dirty") else ""),
        "",
        "This is the answer to *\"which corpus produced these numbers?\"*.",
        "",
        "| | |",
        "|---|---|",
        f"| retrieval rules version | `{m['retrieval_rules_version']}` |",
        f"| papers in corpus | {corpus['n_papers_parquet']} |",
        f"| normalised files on disk | {corpus['n_normalized_files']} |",
        f"| ChromaDB collection | `{chroma.get('collection')}` |",
        f"| ChromaDB chunks | {chroma.get('n_chunks')} |",
        f"| embedding model | `{chroma.get('embedding_model')}` |",
        f"| theses.yaml sha256 | `{m['theses']['sha256'][:16]}…` |",
        f"| papers.parquet sha256 | `{corpus['papers_parquet_sha256'][:16]}…` |",
        f"| references.parquet sha256 | `{corpus['references_parquet_sha256'][:16]}…` |",
        f"| distance gate | {m['retrieval_rules']['distance_gate']} |",
        f"| citation depth | {m['retrieval_rules']['citation_depth']} |",
        f"| on-topic rule | {m['retrieval_rules']['on_topic_rule']} |",
        f"| unit of evidence | {m['evidence_rules']['unit_of_evidence']} |",
        f"| controls held out | {m['retrieval_rules'].get('controls_held_out')} |",
        f"| controls verified / holdout / consumed | "
        f"{m.get('controls', {}).get('n_verified')} / "
        f"{m.get('controls', {}).get('n_holdout')} / "
        f"{m.get('controls', {}).get('n_holdout_consumed')} |",
        f"| quote fuzzy threshold | {m['evidence_rules']['fuzzy_threshold']} |",
        "",
        f"## Rules v{m['retrieval_rules_version']}",
        "",
        m["rules_changelog"].get(m["retrieval_rules_version"], "—"),
        "",
    ]
    if m.get("history"):
        lines += ["## Earlier freezes", ""]
        for h in m["history"]:
            lines.append(
                f"- {h.get('frozen_at', '—')} · tag `{h.get('tag') or '—'}` · "
                f"rules v{h.get('retrieval_rules_version', '—')} · "
                f"{h.get('corpus', {}).get('n_papers_parquet', '—')} papers")
        lines.append("")

    md_path = target / "RETRIEVAL_MANIFEST.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path
