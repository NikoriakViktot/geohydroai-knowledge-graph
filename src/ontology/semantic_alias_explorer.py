"""
semantic_alias_explorer.py  —  GeoHydroAI semantic alias QA tool
================================================================

Interactive and programmatic tool for a human ontology engineer to:

    1. Find ontology entities that are SEMANTICALLY close to a query term
       but have NO alias that would catch it deterministically.
       → These are alias expansion candidates.

    2. Scan a list of raw mentions and produce a ranked table of
       (mention, best_semantic_match, confidence, has_alias_coverage)
       → Used to prioritise which aliases to add.

    3. Generate data/ontology_qa/low_confidence_matches.json
       with mentions that hit 0.68–0.82 (uncertain band) for human review.

Rules:
    - Uses embedding_matcher for similarity — NEVER for identity assignment.
    - Does NOT write to registry or alias table.
    - Output files are for HUMAN REVIEW only.

Usage:
    # Interactive exploration
    python -m src.ontology.semantic_alias_explorer --query "flood extent mapping"

    # Batch scan of unresolved mentions
    python -m src.ontology.semantic_alias_explorer --scan-unresolved

    # Top-k for a specific term
    python -m src.ontology.semantic_alias_explorer --query "insar coherence" --top-k 10
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_QA_DIR      = Path(__file__).resolve().parents[2] / "data" / "ontology_qa"
_ENRICHED_DIR = Path(__file__).resolve().parents[2] / "data" / "enriched"


# ─────────────────────────────────────────────────────────────────────────────
# Core exploration functions
# ─────────────────────────────────────────────────────────────────────────────

def explore_query(query: str, top_k: int = 5, expected_type: Optional[str] = None) -> list[dict]:
    """
    Return the top-k semantically closest ontology entities to the query.

    Annotates each result with whether the query would be caught by the
    current alias table (deterministic path) or only by embeddings.

    Args:
        query:         Raw text to probe.
        top_k:         Number of results to return.
        expected_type: Restrict search to this entity type.

    Returns:
        List of result dicts with fields:
            canonical_id, display_name, type, confidence,
            alias_would_catch (bool), closest_alias (str or None)
    """
    from src.normalization.embedding_matcher import top_k_semantic
    from src.normalization.alias_resolver import _slugify, _get_alias_table

    results = top_k_semantic(query, k=top_k, expected_type=expected_type)

    alias_table = _get_alias_table()
    query_slug  = _slugify(query)

    for r in results:
        eid = r["canonical_id"]
        # Check if any alias slug is a substring of query slug or exact match
        matching_aliases = [
            alias_slug for alias_slug, mapped_id in alias_table.items()
            if mapped_id == eid and (alias_slug == query_slug or alias_slug in query_slug)
        ]
        r["alias_would_catch"] = len(matching_aliases) > 0
        r["closest_alias"]     = matching_aliases[0] if matching_aliases else None

    return results


def scan_low_confidence(
    mentions: list[str],
    low_thresh: float = 0.68,
    high_thresh: float = 0.82,
) -> list[dict]:
    """
    Find mentions that land in the uncertain band (low_thresh ≤ score < high_thresh).

    These are alias expansion candidates: the semantic model finds a good
    match but the deterministic resolver would say "unknown".

    Returns:
        List of dicts sorted by confidence descending.
    """
    from src.normalization.embedding_matcher import semantic_match
    from src.normalization.alias_resolver import _get_alias_table, _slugify

    alias_table = _get_alias_table()
    uncertain   = []

    for mention in mentions:
        if not mention or not mention.strip():
            continue

        # Check if deterministic path already handles it
        slug = _slugify(mention)
        if slug in alias_table:
            continue  # already resolved deterministically

        result = semantic_match(mention)
        conf   = result.get("confidence", 0.0)

        if low_thresh <= conf < high_thresh:
            uncertain.append({
                "mention":      mention,
                "canonical_id": result.get("canonical_id"),
                "display_name": result.get("display_name"),
                "type":         result.get("type"),
                "confidence":   conf,
                "suggestion":   (
                    f"Consider adding '{mention}' as alias for {result.get('canonical_id')}"
                    if result.get("canonical_id") else "No close match found"
                ),
            })

    uncertain.sort(key=lambda x: -x["confidence"])
    return uncertain


def generate_low_confidence_report(
    mentions: Optional[list[str]] = None,
) -> Path:
    """
    Generate data/ontology_qa/low_confidence_matches.json.

    If mentions is None, loads unresolved mentions from the QA file
    written by unresolved_entity_detector.
    """
    if mentions is None:
        unresolved_path = _QA_DIR / "unresolved_entities.json"
        if not unresolved_path.exists():
            raise FileNotFoundError(
                f"Run unresolved_entity_detector first: {unresolved_path}"
            )
        data     = json.loads(unresolved_path.read_text(encoding="utf-8"))
        mentions = [r["raw_mention"] for r in data.get("records", [])]

    log.info("Scanning %d mentions for low-confidence band...", len(mentions))
    uncertain = scan_low_confidence(mentions)

    _QA_DIR.mkdir(parents=True, exist_ok=True)
    out = _QA_DIR / "low_confidence_matches.json"
    out.write_text(
        json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_scanned": len(mentions),
            "uncertain_count": len(uncertain),
            "note": (
                "Mentions in the 0.68–0.82 confidence band. "
                "Review and add as aliases where appropriate. "
                "Do NOT auto-import."
            ),
            "matches": uncertain,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Low-confidence report: %d matches → %s", len(uncertain), out)
    return out


def generate_alias_coverage_report(registry: dict[str, dict] | None = None) -> Path:
    """
    For each entity, report how many unique alias slugs it has and
    which might benefit from additional coverage.

    Output: data/ontology_qa/ontology_coverage_report.json
    """
    from src.normalization.alias_resolver import load_ontology_registry, _slugify

    if registry is None:
        registry = load_ontology_registry()

    rows = []
    for eid, ent in registry.items():
        aliases    = ent.get("aliases", [])
        slug_count = len({_slugify(a) for a in aliases})
        rows.append({
            "id":              eid,
            "display_name":    ent.get("display_name"),
            "type":            ent.get("type"),
            "type_group":      ent.get("type_group"),
            "alias_count":     len(aliases),
            "unique_slugs":    slug_count,
            "is_generic":      ent.get("is_generic", False),
            "has_definition":  bool(ent.get("definition", "").strip()),
            "coverage_score":  round(min(slug_count / 5.0, 1.0), 2),  # 5+ slugs = full coverage
        })

    rows.sort(key=lambda r: (r["coverage_score"], r["id"]))

    _QA_DIR.mkdir(parents=True, exist_ok=True)
    out = _QA_DIR / "ontology_coverage_report.json"
    out.write_text(
        json.dumps({
            "generated_at":   datetime.now(timezone.utc).isoformat(),
            "entity_count":   len(rows),
            "low_coverage":   [r for r in rows if r["coverage_score"] < 0.4],
            "full_coverage":  sum(1 for r in rows if r["coverage_score"] >= 1.0),
            "all_entities":   rows,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Coverage report: %d entities → %s", len(rows), out)
    return out


def generate_generic_entities_report(registry: dict[str, dict] | None = None) -> Path:
    """
    Output: data/ontology_qa/generic_entities_report.json
    """
    from src.normalization.alias_resolver import load_ontology_registry
    from src.ontology.refinement.hierarchy_builder import GENERIC_ENTITIES

    if registry is None:
        registry = load_ontology_registry()

    report_rows = []
    for eid, reason in GENERIC_ENTITIES.items():
        ent = registry.get(eid, {})
        report_rows.append({
            "id":           eid,
            "display_name": ent.get("display_name"),
            "type":         ent.get("type"),
            "reason":       reason,
            "in_registry":  eid in registry,
            "is_flagged":   ent.get("is_generic", False),
        })

    _QA_DIR.mkdir(parents=True, exist_ok=True)
    out = _QA_DIR / "generic_entities_report.json"
    out.write_text(
        json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count":        len(report_rows),
            "note": (
                "These entities are too coarse for graph node identity. "
                "Use the specific child entities listed in their 'reason' field."
            ),
            "entities": report_rows,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Generic entities report: %d → %s", len(report_rows), out)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="GeoHydroAI semantic alias explorer")
    parser.add_argument("--query", type=str, help="Single query to explore")
    parser.add_argument("--top-k", type=int, default=5, help="Number of results (default 5)")
    parser.add_argument("--type", type=str, default=None, help="Filter by entity type")
    parser.add_argument("--scan-unresolved", action="store_true",
                        help="Scan unresolved_entities.json for low-confidence matches")
    parser.add_argument("--coverage", action="store_true",
                        help="Generate ontology coverage report")
    parser.add_argument("--generics", action="store_true",
                        help="Generate generic entities report")
    args = parser.parse_args()

    if args.query:
        results = explore_query(args.query, top_k=args.top_k, expected_type=args.type)
        print(f"\nTop-{args.top_k} semantic matches for: '{args.query}'")
        print(f"{'Rank':4} {'Score':6} {'Alias?':7} {'ID':35} Display Name")
        print("-" * 90)
        for i, r in enumerate(results, 1):
            alias_tag = "YES" if r.get("alias_would_catch") else "no"
            print(
                f"  {i:2}  {r['confidence']:.4f}  {alias_tag:7}  "
                f"{r['canonical_id']:35}  {r['display_name']}"
            )
        print()

    if args.scan_unresolved:
        out = generate_low_confidence_report()
        print(f"Low-confidence report written: {out}")

    if args.coverage:
        out = generate_alias_coverage_report()
        print(f"Coverage report written: {out}")

    if args.generics:
        out = generate_generic_entities_report()
        print(f"Generic entities report written: {out}")

    if not any([args.query, args.scan_unresolved, args.coverage, args.generics]):
        parser.print_help()


if __name__ == "__main__":
    _main()
