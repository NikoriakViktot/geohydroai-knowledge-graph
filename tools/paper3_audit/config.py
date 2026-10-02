"""Paths and closed vocabularies of the Paper 3 literature audit."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_DIR = REPO_ROOT / "paper_unet-case-kakhovka"
PUB_DIR = BUNDLE_DIR / "publication"
OUT_DIR = BUNDLE_DIR / "literature_audit_paper3"
WORK_DIR = OUT_DIR / "_work"

THESES_CSV = PUB_DIR / "literature" / "theses.csv"
THESES_JSON = PUB_DIR / "literature" / "theses.json"
#: Theses for results added to the bundle after theses.csv was generated (audit-owned).
THESES_SUPPLEMENT = OUT_DIR / "theses_supplement.yaml"
ATOMIC_CLAIMS_YAML = OUT_DIR / "atomic_claims.yaml"
OVERRIDES_YAML = OUT_DIR / "overrides.yaml"
MANIFEST_JSON = OUT_DIR / "run_manifest.json"
SEARCH_LOG = OUT_DIR / "04_search_log.jsonl"

#: The sibling repo that generated the bundle; reachable only through wsl.exe.
FLOODSTATE_DISTRO = "Ubuntu-24.04"
FLOODSTATE_REPO = "/home/niko/repo/floodstate-eo"
FLOODSTATE_BIB = "docs/references.bib"
FLOODSTATE_TERMINOLOGY_TEST = "tests/test_terminology_freeze.py"

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

#: Retrieval: reused verbatim from src.paper_3.retrieve so the rules version is one.
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
