"""
repair_accuracy_percent.py — Restore metric names for 'Percent' entries where the
evidence snippet provably contains an explicit metric name keyword near the value.

Rule: ONLY reclassify when the metric name (e.g. "overall accuracy", "F1") appears
in the 80-char window immediately before the value in the evidence snippet.
Never infer from topic keywords alone. This is restoration, not guessing.

Safety guards:
  - F1: excluded if "commission" or "omission error" appears in the SAME sentence
    as the F1 keyword before the value (same-sentence = no period separator).
  - All metrics: excluded if another percentage appears between the keyword and the value
    (indicates the keyword belongs to a different, earlier value in a list).
  - All metrics: excluded if another percentage appears BEFORE the keyword in the window
    (indicates the value follows a different metric in a list context).

Only modifies papers listed in doi_map.parquet (the 87-paper audit set).
Idempotent: re-running produces no further changes.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_ANALYTICS_DIR = Path("data/analytics")
_DOI_MAP_PATH  = Path("data/paper_audit/doi_map.parquet")

# Canonical info for each reclassifiable type
_RECLASSIFY_RULES: list[tuple[str, str, str, str, str | None]] = [
    # (type, display_name, canonical_id, keyword_pattern, exclusion_in_same_sentence_pattern)
    ("OA",        "Overall Accuracy",        "metric.oa",
     r"overall accura(?:cy|cies)\b|global accuracy\b|classification accuracy\b|\boa\b",
     None),
    ("F1",        "F1-score",                "metric.f1",
     r"\bf1[\s\-]?score\b|\bf1\b",
     r"commission|omission\s+error"),
    ("IoU",       "Intersection over Union", "metric.iou",
     r"\biou\b|intersection over union",
     None),
    ("Kappa",     "Cohen's Kappa",           "metric.kappa",
     r"\bkappa\b",
     None),
    ("Precision", "Precision",               "metric.precision",
     r"user.?s?\s+accuracy",
     None),
    ("Recall",    "Recall",                  "metric.recall",
     r"producer.?s?\s+accuracy",
     None),
]


def _find_value_idx(snippet: str, value: float) -> int:
    """Return start index of the value (as a percentage) in snippet, or -1."""
    val_pct = round(float(value) * 100, 10)
    for fmt in (f"{val_pct:g}", f"{round(val_pct, 2)}", f"{round(val_pct, 1)}", f"{int(round(val_pct))}"):
        m = re.search(r"\b" + re.escape(fmt) + r"\s*%", snippet)
        if m:
            return m.start()
    return -1


def _reclassify_percent(m: dict) -> dict | None:
    """
    Try to restore the true metric type from the stored evidence snippet.
    Returns a dict of fields to update, or None if no safe reclassification found.
    """
    snippet = (m.get("evidence") or {}).get("snippet", "")
    if not snippet:
        return None
    value = m.get("value")
    if value is None:
        return None

    idx = _find_value_idx(snippet, value)
    if idx == -1:
        return None

    before = snippet[max(0, idx - 80): idx]

    for metric_type, name, cid, kw_pat, excl_pat in _RECLASSIFY_RULES:
        # Find LAST occurrence of keyword in before (more likely to be the referring term)
        kw_matches = list(re.finditer(kw_pat, before, re.I))
        if not kw_matches:
            continue
        km = kw_matches[-1]

        pre_kw  = before[:km.start()]
        post_kw = before[km.end():]

        # Guard 1: another percentage before the keyword → keyword belongs to an earlier value.
        # Skip a match at position 0: the 80-char window may start mid-number ("8%" is really
        # the tail of "98%" from the preceding sentence) — that is not a real separate metric.
        pct_pre = re.search(r"\d+(?:\.\d+)?\s*%", pre_kw)
        if pct_pre and pct_pre.start() > 0:
            continue

        # Guard 2: another percentage between keyword and current value → ambiguous list
        if re.search(r"\d+(?:\.\d+)?\s*%", post_kw):
            continue

        # Guard 3 (F1 only): exclusion keyword in pre_kw in the SAME sentence as keyword
        if excl_pat:
            excl_m = re.search(excl_pat, pre_kw, re.I)
            if excl_m:
                between = pre_kw[excl_m.end():]
                if "." not in between:
                    continue  # same sentence → real conflict

        return {
            "type":         metric_type,
            "name":         name,
            "canonical_id": cid,
            "confidence":   0.7,
            "unit":         "-",
        }

    return None


def _patch_paper(paper: dict, dry_run: bool) -> int:
    """Patch entities.metrics in-place. Returns number of entries updated."""
    metrics = (paper.get("entities") or {}).get("metrics")
    if not metrics:
        return 0
    updated = 0
    for m in metrics:
        if m.get("name") not in ("Percent", "", None):
            continue
        if m.get("type") not in ("Percent", "", None):
            continue
        fix = _reclassify_percent(m)
        if fix is None:
            continue
        log.debug(
            "  reclassify val=%s  Percent → %s  evidence: %s",
            m.get("value"), fix["type"],
            ((m.get("evidence") or {}).get("snippet") or "")[:80],
        )
        if not dry_run:
            m.update(fix)
        updated += 1
    return updated


def _load_paper_json(paper_id: str) -> tuple[Path, dict | None, bool]:
    """Return (path, paper_dict, is_enriched_wrapper)."""
    enriched = Path("data/enriched") / f"{paper_id}.json"
    if enriched.exists():
        raw = json.loads(enriched.read_text(encoding="utf-8"))
        return enriched, raw.get("paper", raw), True
    plain = Path("data/literature/paper_json") / f"{paper_id}.json"
    if plain.exists():
        raw = json.loads(plain.read_text(encoding="utf-8"))
        return plain, raw, False
    return Path(), None, False


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        description="Restore metric names for 'Percent' entries using evidence snippets."
    )
    ap.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not _DOI_MAP_PATH.exists():
        log.error("doi_map.parquet not found; run paper_audit step 1 first")
        return

    doi_map = pd.read_parquet(_DOI_MAP_PATH)
    paper_ids = [pid for pid in doi_map["paper_id"].dropna().unique() if pid]

    total_files = total_entries = 0
    for paper_id in paper_ids:
        path, paper, is_enriched = _load_paper_json(paper_id)
        if paper is None:
            continue

        n = _patch_paper(paper, dry_run=args.dry_run)
        if n == 0:
            continue

        total_files += 1
        total_entries += n
        log.info("%-40s  %d entries updated", paper_id, n)

        if not args.dry_run:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if is_enriched:
                raw["paper"] = paper
            else:
                raw.update(paper)
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    action = "Would update" if args.dry_run else "Updated"
    log.info("%s %d entries across %d files", action, total_entries, total_files)


if __name__ == "__main__":
    main()
