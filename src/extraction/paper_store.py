"""
paper_store.py — loads full structured paper objects for the RAG extraction layer.

Resolution order (first match wins):
  1. data/enriched/{paper_id}.json      — has OpenAlex cited_by_count
  2. data/literature/paper_json/{paper_id}.tei.paper.json

After loading, the returned dict is enriched with:
  - paper["abstract"]      — pre-resolved from metadata / sections / front
  - paper["sodb_metrics"]  — list of numeric_facts rows from SODB parquet
  - paper["_paper_id"]     — normalised paper_id used for SODB lookup
  - paper["metadata"]["cited_by_count"] — from OpenAlex if enriched JSON used

Usage::

    from src.extraction.paper_store import PaperStore
    store = PaperStore()
    paper = store.load("10.3390_rs12020266")  # or a chunk filename
    print(paper["abstract"][:120])
    print(paper.get("sodb_metrics", [])[:2])
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from src.extraction.sodb_metrics_loader import load_numeric_facts

logger = logging.getLogger(__name__)

_PROJECT_ROOT   = Path(__file__).resolve().parents[2]
_PAPER_JSON_DIR = _PROJECT_ROOT / "data" / "literature" / "paper_json"
_ENRICHED_DIR   = _PROJECT_ROOT / "data" / "enriched"
_SODB_ROOT      = _PROJECT_ROOT / "data" / "sodb"

# ── Filename normalisation ────────────────────────────────────────────────────

_KNOWN_SUFFIXES = (
    ".tei.paper.json",
    ".paper.json",
    ".tei.json",
    ".json",
    ".tei.paper",
    ".paper",
    ".tei",
)


def normalize_stem(value: str) -> str:
    """
    Strip all known file suffixes iteratively, then collapse whitespace.

    Handles cases like "0000577383.tei.paper.json" → "0000577383",
    or "remotesensing-12-02073-v2.paper.json" → "remotesensing-12-02073-v2".
    Uses an iterative loop so ".tei.paper.json" is stripped as a unit,
    not piece by piece (which would mangle intermediate forms like ".tei.paper").
    """
    s = Path(str(value)).name.strip()
    changed = True
    while changed:
        changed = False
        for suffix in _KNOWN_SUFFIXES:
            if s.lower().endswith(suffix):
                s = s[: -len(suffix)]
                changed = True
                break
    return re.sub(r"\s+", " ", s).strip()


def candidate_stems(value: str) -> list[str]:
    """
    Return normalised filename variants to probe in the paper index.

    Covers: original stem, space↔underscore swaps, lowercase variants.
    De-duplicated and ordered most-specific first.
    """
    s = normalize_stem(value)
    variants = [
        s,
        s.replace(" ", "_"),
        s.replace("_", " "),
        s.lower(),
        s.lower().replace(" ", "_"),
        s.lower().replace("_", " "),
    ]
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


# ── Abstract resolution ───────────────────────────────────────────────────────

def _resolve_abstract(paper: dict) -> str:
    """
    Find the abstract text in any of its known locations.

    Checks (in order):
      1. paper["metadata"]["abstract"]
      2. paper["abstract"]
      3. paper["sections"]["abstract"]
      4. paper["sections"]["front"]
    """
    meta     = paper.get("metadata") or {}
    sections = paper.get("sections") or {}
    return (
        meta.get("abstract")
        or paper.get("abstract")
        or sections.get("abstract")
        or sections.get("front")
        or ""
    )


# ── PaperStore ────────────────────────────────────────────────────────────────

class PaperStore:
    """
    Loads full structured paper dicts for the extraction layer.

    The index is built once at startup (glob of both directories),
    so per-candidate lookups are O(1) dict probes, not O(n) file scans.
    """

    def __init__(
        self,
        paper_json_dir: Path = _PAPER_JSON_DIR,
        enriched_dir:   Path = _ENRICHED_DIR,
        sodb_root:      Path = _SODB_ROOT,
    ) -> None:
        self._paper_json_dir = Path(paper_json_dir)
        self._enriched_dir   = Path(enriched_dir)
        self._sodb_root      = Path(sodb_root)
        # Maps normalised stem → (json_path, paper_id)
        self._index: dict[str, tuple[Path, str]] = self._build_index()
        logger.info(
            "PaperStore index: %d entries (enriched=%d, paper_json=%d)",
            len(self._index),
            sum(1 for _, (p, _) in self._index.items() if "enriched" in str(p)),
            sum(1 for _, (p, _) in self._index.items() if "paper_json" in str(p)),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, filename: str) -> dict | None:
        """
        Load a full paper dict for *filename* (a chunk source filename or paper_id).

        Returns None if no matching paper_json / enriched JSON is found
        (the pipeline will then fall back to chunk-based extraction).
        """
        for stem in candidate_stems(filename):
            item = self._index.get(stem)
            if item:
                path, paper_id = item
                return self._build_paper_dict(path, paper_id)
        logger.debug("PaperStore: no match for '%s'", filename)
        return None

    def size(self) -> int:
        """Number of unique papers in the index."""
        return len(self._index)

    # ── Index construction ────────────────────────────────────────────────────

    def _build_index(self) -> dict[str, tuple[Path, str]]:
        """
        Build stem → (path, paper_id) mapping.

        Enriched JSONs are added first so they win over plain paper_json
        when both exist for the same paper.
        """
        index: dict[str, tuple[Path, str]] = {}

        # 1. Enriched JSONs (richer: has OpenAlex metadata)
        if self._enriched_dir.exists():
            for path in sorted(self._enriched_dir.glob("*.json")):
                paper_id = normalize_stem(path.name)
                for stem in candidate_stems(path.name):
                    index.setdefault(stem, (path, paper_id))

        # 2. Paper JSONs (fallback / additional coverage)
        if self._paper_json_dir.exists():
            for path in sorted(self._paper_json_dir.glob("*.paper.json")):
                paper_id = normalize_stem(path.name)
                for stem in candidate_stems(path.name):
                    index.setdefault(stem, (path, paper_id))

        return index

    # ── Paper dict construction ───────────────────────────────────────────────

    def _build_paper_dict(self, path: Path, paper_id: str) -> dict:
        """
        Load and enrich a paper dict from *path*.

        Handles two on-disk formats:
          - Enriched: {"paper": {...}, "openalex": {...}, "enrichment_meta": {...}}
          - Plain paper_json: top-level dict with "metadata", "sections", "entities"
        """
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("PaperStore: failed to read %s: %s", path, exc)
            return {}

        # Unwrap enriched format
        if "paper" in raw and isinstance(raw["paper"], dict):
            doc = dict(raw["paper"])   # shallow copy to avoid mutating cache
            oa  = raw.get("openalex") or {}
            doc.setdefault("metadata", {})["cited_by_count"] = oa.get("cited_by_count", 0)
        else:
            doc = dict(raw)

        # Pre-resolve abstract into a single canonical key
        doc["abstract"] = _resolve_abstract(doc)

        # Attach SODB numeric facts (metrics from tables)
        doc["sodb_metrics"] = load_numeric_facts(paper_id, self._sodb_root)

        # Expose paper_id for downstream use
        doc["_paper_id"] = paper_id

        return doc
