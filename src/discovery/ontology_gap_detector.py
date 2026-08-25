"""
ontology_gap_detector.py  —  GeoHydroAI ontology gap discovery
===============================================================

Collects extracted terms that could NOT be resolved by alias or semantic
matching, clusters them to identify emerging terminology patterns, and
writes a human-review file.

CRITICAL RULES:
    - Clustering is for DISCOVERY ONLY — not for assigning canonical identity.
    - Discovered clusters are written to data/ontology_review/ for human review.
    - Do NOT write discovered clusters directly into the ontology registry.
    - Canonical identity must come from a human decision after review.

Usage:
    from src.discovery.ontology_gap_detector import detect_gaps

    detect_gaps(unmatched_terms)
    # writes data/ontology_review/unknown_method_clusters.json
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_REVIEW_DIR = Path(__file__).resolve().parents[2] / "data" / "ontology_review"

_MIN_CLUSTER_SIZE    = 2     # ignore singleton clusters in output
_HDBSCAN_MIN_SAMPLES = 2
_MAX_TERMS_SKLEARN   = 5000  # sklearn KMeans cap (not used for identity)


# ─────────────────────────────────────────────────────────────────────────────
# Text normalisation
# ─────────────────────────────────────────────────────────────────────────────

def _clean(term: str) -> str:
    return re.sub(r"\s+", " ", term.strip().lower())


# ─────────────────────────────────────────────────────────────────────────────
# Clustering for discovery (NEVER for identity)
# ─────────────────────────────────────────────────────────────────────────────

def _cluster_terms(terms: list[str]) -> list[dict]:
    """
    Group unmatched terms by textual similarity using HDBSCAN or agglomerative
    clustering.  Returns a list of cluster dicts for human review.

    This clustering DOES NOT assign canonical IDs.
    It is a tool for a human ontology engineer to decide:
        - Is this a real new concept worth adding?
        - Is it a spelling variant of an existing entity?
        - Is it domain-specific jargon with no canonical referent?
    """
    if len(terms) < 2:
        return [{"terms": terms, "label": "singleton", "size": len(terms)}]

    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np

        model      = SentenceTransformer("sentence-transformers/all-mpnet-base-v2")
        embeddings = model.encode(terms, normalize_embeddings=True, show_progress_bar=False)

        try:
            import hdbscan
            clusterer = hdbscan.HDBSCAN(
                min_cluster_size=_HDBSCAN_MIN_SAMPLES,
                metric="euclidean",
                prediction_data=False,
            )
            labels = clusterer.fit_predict(embeddings)
        except ImportError:
            # Fallback to scikit-learn agglomerative clustering
            from sklearn.cluster import AgglomerativeClustering
            n_clusters = max(2, min(len(terms) // 3, 30))
            clusterer  = AgglomerativeClustering(
                n_clusters=n_clusters,
                metric="cosine",
                linkage="average",
            )
            labels = clusterer.fit_predict(embeddings)

        # Group by label
        groups: dict[int, list[str]] = defaultdict(list)
        for term, label in zip(terms, labels):
            groups[int(label)].append(term)

        clusters = []
        for label, group_terms in sorted(groups.items()):
            if label == -1:  # HDBSCAN noise
                for t in group_terms:
                    clusters.append({
                        "terms":        [t],
                        "label":        "noise",
                        "size":         1,
                        "review_notes": "Could not be grouped — isolated term",
                    })
            else:
                clusters.append({
                    "terms":        sorted(group_terms),
                    "label":        f"cluster_{label:03d}",
                    "size":         len(group_terms),
                    "review_notes": "",
                })

        return [c for c in clusters if c["size"] >= _MIN_CLUSTER_SIZE]

    except Exception as exc:
        log.warning("Clustering failed (%s) — returning ungrouped terms", exc)
        return [{"terms": terms, "label": "ungrouped", "size": len(terms)}]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def detect_gaps(
    unmatched_terms: list[str],
    expected_type: Optional[str] = None,
    output_filename: str = "unknown_method_clusters.json",
) -> Path:
    """
    Cluster unmatched extracted terms and write a human-review file.

    Args:
        unmatched_terms:  List of raw mentions that failed alias + semantic match.
        expected_type:    If known, the type context ("method", "sensor", etc.).
        output_filename:  Output filename under data/ontology_review/.

    Returns:
        Path to the written review file.

    IMPORTANT:
        The output file is for HUMAN REVIEW only.
        Do not read it back as ontology input automatically.
    """
    _REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _REVIEW_DIR / output_filename

    # Deduplicate and clean
    cleaned = sorted({_clean(t) for t in unmatched_terms if t.strip()})
    log.info("Detecting gaps for %d unique unmatched terms (type=%s)", len(cleaned), expected_type)

    clusters = _cluster_terms(cleaned)

    output = {
        "metadata": {
            "generated_at":  datetime.now(timezone.utc).isoformat(),
            "term_count":    len(cleaned),
            "cluster_count": len(clusters),
            "expected_type": expected_type,
            "note": (
                "REVIEW FILE ONLY. Do not import clusters directly into ontology. "
                "Each cluster requires human review to determine canonical identity."
            ),
        },
        "clusters": clusters,
        "all_unmatched_terms": cleaned,
    }

    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Gap review file written: %s (%d clusters)", out_path, len(clusters))
    return out_path


def collect_unmatched_from_papers(papers_dir: Path) -> list[str]:
    """
    Walk through enriched paper JSONs and collect entity mentions that
    failed normalisation (stored as raw extraction strings).

    Looks for any `entities.methods`, `entities.satellites` etc. lists
    in paper JSONs and runs them through normalize_entity, collecting
    the ones with match_type="unknown".
    """
    from src.normalization.ontology_matcher import normalize_entity

    unmatched: list[str] = []

    for path in papers_dir.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        paper    = doc.get("paper", doc)
        entities = paper.get("entities", {})

        for field_name, values in entities.items():
            if not isinstance(values, list):
                values = [values] if values else []
            for raw_mention in values:
                if not isinstance(raw_mention, str):
                    continue
                result = normalize_entity(raw_mention)
                if result["match_type"] == "unknown":
                    unmatched.append(raw_mention)

    return unmatched
