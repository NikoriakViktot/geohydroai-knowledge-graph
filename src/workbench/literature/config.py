"""Paths and closed vocabularies of the literature evidence run (from tools/paper3_audit/config.py).

The paths are configured per run (``configure``); modules read them as ``cfg.NAME`` at call time.
Unconfigured, they are the locations the Paper 3 audit used (paper_unet-case-kakhovka in this
repository), so the old command keeps working until the folder is retired. The workbench step
``literature`` configures them from the paper's manifest and the staging directories.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Paths:
    pub_dir: Path                     # the paper's publication bundle (manuscript, captions, claims, tables)
    out_dir: Path                     # the literature audit: inputs (*.yaml) and deliverables 01–10
    work_dir: Path                    # caches and intermediates, never delivered
    theses_json: Path                 # the paper's theses.json
    chroma_dir: Path
    collection: str
    distro: str = "Ubuntu-24.04"
    repo: str | None = None           # the paper repository, for its .bib and terminology test
    bib: str = "docs/references.bib"
    terminology_test: str | None = "tests/test_terminology_freeze.py"


def legacy() -> Paths:
    bundle = REPO_ROOT / "paper_unet-case-kakhovka"
    out = bundle / "literature_audit_paper3"
    return Paths(pub_dir=bundle / "publication", out_dir=out, work_dir=out / "_work",
                 theses_json=bundle / "publication" / "literature" / "theses.json",
                 chroma_dir=Path(os.getenv("PAPER3_AUDIT_CHROMA_DIR", str(REPO_ROOT / ".chromadb"))),
                 collection=os.getenv("PAPER3_AUDIT_COLLECTION", "flood_papers_768d"),
                 repo="/home/niko/repo/floodstate-eo")


def configure(p: Paths) -> None:
    """Point every module of the run at these locations."""
    g = globals()
    g.update(
        PATHS=p, BUNDLE_DIR=p.pub_dir.parent, PUB_DIR=p.pub_dir, OUT_DIR=p.out_dir, WORK_DIR=p.work_dir,
        AUDIT_CHROMA_DIR=p.chroma_dir, AUDIT_COLLECTION=p.collection,
        THESES_JSON=p.theses_json, THESES_CSV=p.theses_json.with_suffix(".csv"),
        THESES_SUPPLEMENT=p.out_dir / "theses_supplement.yaml", ATOMIC_CLAIMS_YAML=p.out_dir / "atomic_claims.yaml",
        OVERRIDES_YAML=p.out_dir / "overrides.yaml", MANIFEST_JSON=p.out_dir / "run_manifest.json",
        SEARCH_LOG=p.out_dir / "04_search_log.jsonl", NOVELTY_YAML=p.out_dir / "novelty_verdicts.yaml",
        CITATION_KEYS_YAML=p.out_dir / "citation_keys.yaml",
        CLAIMS_THESES_YAML=p.out_dir / "claims_theses.yaml",
        FLOODSTATE_DISTRO=p.distro, FLOODSTATE_REPO=p.repo, FLOODSTATE_BIB=p.bib,
        FLOODSTATE_TERMINOLOGY_TEST=p.terminology_test,
    )


# the kakhovka-report:v1 theses (src/paper_3/theses.yaml) and draft, used only by the moved report code paths
REPORT_THESES_YAML = REPO_ROOT / "src" / "paper_3" / "theses.yaml"
REPORT_DRAFT = REPO_ROOT / "paper_3_audit" / "Kakhovka_scientific_report_article_draft_v1.md"

PATHS: Paths
BUNDLE_DIR = PUB_DIR = OUT_DIR = WORK_DIR = AUDIT_CHROMA_DIR = THESES_JSON = THESES_CSV = None
THESES_SUPPLEMENT = ATOMIC_CLAIMS_YAML = OVERRIDES_YAML = MANIFEST_JSON = SEARCH_LOG = None
NOVELTY_YAML = CITATION_KEYS_YAML = CLAIMS_THESES_YAML = None
AUDIT_COLLECTION = FLOODSTATE_DISTRO = FLOODSTATE_REPO = FLOODSTATE_BIB = FLOODSTATE_TERMINOLOGY_TEST = None
configure(legacy())

#: Evidence roles a source may play for an atomic claim (the brief, §3).
ROLES = ("CONTRASTS", "SUPPORTS", "COMPARATOR", "METHOD_FROM", "LIMITATION",
         "DEFINITION", "DATASET_DOCUMENTATION", "BACKGROUND")
NOT_RELEVANT = "NOT_RELEVANT"
#: Roles that assert what a paper FOUND and therefore need a verbatim quote.
QUOTE_REQUIRED = ("SUPPORTS", "CONTRASTS")

#: Final evidence status per atomic claim / thesis (the brief, §6).
STATUSES = ("VERIFIED_SUPPORTED", "VERIFIED_PARTIAL", "VERIFIED_COMPARATOR_ONLY",
            "CONTRADICTED_OR_QUALIFIED", "NO_EVIDENCE_IN_CORPUS",
            "SOURCE_FOUND_METADATA_UNVERIFIED", "NOT_NEEDED_FOR_MANUSCRIPT")

#: Manuscript relevance decided after evidence collection (the brief, §11).
RELEVANCE = ("CORE", "SUPPORTING", "SUPPLEMENTARY", "DROP")

PASSES = {"A": "high", "B": "medium", "C": "low"}

#: A primary key-term family may not be one of these: they occur in most of the
#: corpus and would make the topical gate trivially satisfiable.
GENERIC_TERMS = frozenset({
    "satellite", "flood", "flooding", "water", "area", "method", "model", "accuracy",
    "precision", "recall", "f1", "km2", "km²", "sentinel", "remote sensing", "dem",
    "river", "data", "map", "mapping", "image", "analysis", "results", "uncertainty",
    "elevation", "extent", "observation", "estimate", "reservoir", "dam",
})

#: Gemini lanes: one model per process. The gateway ignores GEMINI_MODEL and reads
#: GEMINI_MODEL_POOL, so a lane is enforced by overwriting ``ai_gateway.MODEL_POOL``.
LANES = ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite")
QUOTA_DAILY_CAP = 480          # of 500 RPD per model on the free tier
CIRCUIT_BACKOFF_SECONDS = 300

#: Retrieval: reused verbatim from the cascade so the rules version is one.
ROLE_VOCABULARY = "paper3_8roles_v1"
QUERIES_VERSION_KEY = "queries_version"

DELIVERABLES = {
    "audit": "01_scientific_audit.md",
    "evidence": "02_thesis_evidence.csv",
    "ledger": "03_source_ledger.csv",
    "search_log": "04_search_log.jsonl",
    "matrix": "05_claim_citation_matrix.md",
    "unresolved": "06_unresolved.md",
    "bib": "07_references_verified.bib",
    "bib_unresolved": "07b_references_unresolved.md",
    "synthesis": "08_literature_synthesis.md",
    "manuscript": "09_manuscript_literature_revised.md",
    "changelog": "10_change_log.md",
    "numbers": "kakhovka_numbers.csv",
}
