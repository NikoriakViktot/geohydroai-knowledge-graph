"""
normalization_validator.py  —  GeoHydroAI normalization pipeline smoke test
===========================================================================

Runs a deterministic validation suite against the current alias table and
ontology registry to verify that known entities resolve as expected.

Test types:
    T01  Canonical ID lookup (must resolve with match_type=alias)
    T02  Alias lookup (must resolve to the expected canonical ID)
    T03  Compound mention sub-phrase (e.g., "Sentinel-1 SAR" → sensor.sentinel_1)
    T04  Noise-stripped lookup (e.g., "random forest classifier" → method.random_forest)
    T05  Negative case (must NOT resolve — returns unknown)
    T06  Type filter (alias resolves only when correct expected_type is given)

Output:
    data/ontology_qa/normalization_validation.json — full results
    Prints pass/fail summary.

Usage:
    python -m src.normalization.normalization_validator
    python -m src.normalization.normalization_validator --fail-fast
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_QA_DIR = Path(__file__).resolve().parents[2] / "data" / "ontology_qa"


# ─────────────────────────────────────────────────────────────────────────────
# Test suite definition
# ─────────────────────────────────────────────────────────────────────────────

# Each test: (test_type, input_text, expected_canonical_id_or_None, expected_match_type, expected_type_filter)
_TEST_CASES: list[tuple[str, str, Optional[str], str, Optional[str]]] = [
    # T01 — canonical ID itself
    ("T01", "method.hec_ras",          "method.hec_ras",              "alias",    None),
    ("T01", "sensor.sentinel_1",        "sensor.sentinel_1",           "alias",    None),
    ("T01", "metric.rmse",              "metric.rmse",                 "alias",    None),
    ("T01", "concept.base_flood_elevation", "concept.base_flood_elevation", "alias", None),

    # T02 — alias strings
    ("T02", "HEC-RAS",                  "method.hec_ras",              "alias",    None),
    ("T02", "HEC RAS",                  "method.hec_ras",              "alias",    None),
    ("T02", "hec_ras",                  "method.hec_ras",              "alias",    None),
    ("T02", "Sentinel-1",               "sensor.sentinel_1",           "alias",    None),
    ("T02", "S1",                        "sensor.sentinel_1",           "alias",    None),
    ("T02", "Random Forest",            "method.random_forest",        "alias",    None),
    ("T02", "RF",                        "method.random_forest",        "alias",    None),
    ("T02", "NDVI",                      "data.ndvi",                   "alias",    None),
    ("T02", "NDWI",                      "data.ndwi",                   "alias",    None),
    ("T02", "SAR",                       "data.sar",                    "alias",    None),
    ("T02", "DEM",                       "data.dem",                    "alias",    None),
    ("T02", "LSTM",                      "method.lstm",                 "alias",    None),
    ("T02", "U-Net",                     "method.u_net",                "alias",    None),
    ("T02", "SVM",                       "method.support_vector_machine","alias",    None),
    ("T02", "RMSE",                      "metric.rmse",                 "alias",    None),
    ("T02", "NSE",                       "metric.nse",                  "alias",    None),
    ("T02", "BFE",                       "concept.base_flood_elevation","alias",    None),
    ("T02", "Base Flood Elevation",      "concept.base_flood_elevation","alias",    None),

    # T03 — compound mention sub-phrase extraction
    ("T03", "Sentinel-1 SAR",           "sensor.sentinel_1",           "alias",    None),
    ("T03", "Sentinel-1 SAR imagery",   "sensor.sentinel_1",           "alias",    None),
    ("T03", "HEC-RAS model",            "method.hec_ras",              "alias",    None),
    ("T03", "LSTM network",             "method.lstm",                 "alias",    None),
    ("T03", "random forest classifier", "method.random_forest",        "alias",    None),
    ("T03", "U-Net architecture",       "method.u_net",                "alias",    None),
    ("T03", "digital elevation model",  "data.dem",                    "alias",    None),

    # T04 — noise suffix stripping
    ("T04", "random forest algorithm",  "method.random_forest",        "alias",    None),
    ("T04", "support vector machine",   "method.support_vector_machine","alias",    None),

    # T05 — negative cases (should NOT resolve)
    ("T05", "xyzzy unknown term 9999",  None,                          "unknown",  None),
    ("T05", "",                          None,                          "unknown",  None),
    ("T05", "   ",                       None,                          "unknown",  None),

    # T06 — type filter
    ("T06", "RMSE",  "metric.rmse",    "alias",   "metric"),
    ("T06", "RMSE",  "metric.rmse",    "alias",   None),       # no filter also resolves

    # T07 — dimensionality variants (v2 canonical IDs)
    ("T07", "HEC-RAS-1D",   "method.hec_ras_1d",   "alias",  None),
    ("T07", "HEC-RAS-2D",   "method.hec_ras_2d",   "alias",  None),
    ("T07", "MIKE-11",      "method.mike_11",       "alias",  None),
    ("T07", "MIKE-21",      "method.mike_21",       "alias",  None),
    ("T07", "MIKE-FLOOD",   "method.mike_flood",    "alias",  None),
    ("T07", "LISFLOOD-FP",  "method.lisflood_fp",   "alias",  None),

    # T08 — ambiguous token disambiguation (context-free alias fallback)
    ("T08", "SARIMA",        "method.sarima",       "alias",  None),
    ("T08", "Prophet",       "method.prophet",      "alias",  None),
    ("T08", "WA-LSTM",       "method.wa_lstm",      "alias",  None),
    ("T08", "WANN",          "method.wann",         "alias",  None),
    ("T08", "Mamdani-FIS",   "method.mamdani_fis",  "alias",  None),
    ("T08", "Takagi-Sugeno-FIS", "method.takagi_sugeno_fis", "alias", None),

    # T09 — hybrid model detection
    ("T09", "WA-LSTM",       "method.wa_lstm",      "alias",  None),
    ("T09", "SARIMA-ANN",    "method.sarima_ann",   "alias",  None),
    ("T09", "Wavelet-SVR-Prophet", "method.wavelet_svr_prophet", "alias", None),

    # T10 — coupled model identifiers
    ("T10", "HEC-HMS",       "method.hec_hms",      "alias",  None),
    ("T10", "iRIC",          "method.iric",         "alias",  None),
    ("T10", "SWMM",          "method.swmm",         "alias",  None),
    ("T10", "TELEMAC-MASCARET", "method.telemac_mascaret", "alias", None),

    # T11 — v2 ontology hierarchy labels (metrics and tasks)
    ("T11", "NSE",           "metric.nse",          "alias",  "metric"),
    ("T11", "PBIAS",         "metric.pbias",        "alias",  "metric"),
    ("T11", "CSI",           "metric.csi",          "alias",  "metric"),
    ("T11", "KGE",           "metric.kge",          "alias",  "metric"),
]


# ─────────────────────────────────────────────────────────────────────────────
# T12 — multi-task extraction test cases
# Each entry: (description, text, methods_hint, couplings_hint, expected_task_ids)
# ─────────────────────────────────────────────────────────────────────────────

_TASK_TEST_CASES: list[tuple[str, str, list[dict], list[dict], list[str]]] = [
    (
        "coupled HEC-HMS+HEC-RAS-2D extracts inundation+runoff",
        "We used HEC-HMS coupled with HEC-RAS-2D for 1D-2D coupled flood inundation simulation.",
        [
            {"name": "HEC-HMS",    "kb_metadata": {"category": "physical_based", "subcategory": "hydrological",
                                                    "tasks": ["rainfall_runoff_modeling","flood_forecasting","hydrograph_generation"]}},
            {"name": "HEC-RAS-2D", "kb_metadata": {"category": "physical_based", "subcategory": "hydrodynamic",
                                                    "tasks": ["flood_inundation_mapping","hydrodynamic_simulation"]}},
        ],
        [{"model_a": "HEC-HMS", "model_b": "HEC-RAS-2D", "coupling_type": "hydrological_hydrodynamic"}],
        ["flood_inundation_mapping", "rainfall_runoff_modeling", "hydrodynamic_simulation"],
    ),
    (
        "SARIMA+LSTM with forecasting context gives water_level+flood_forecasting",
        "SARIMA and LSTM were applied for water level forecasting with NSE=0.87.",
        [
            {"name": "SARIMA", "kb_metadata": {"category": "time_series", "subcategory": "statistical",
                                                "tasks": ["flood_forecasting","water_level_forecasting"]}},
            {"name": "LSTM",   "kb_metadata": {"category": "time_series", "subcategory": "machine_learning",
                                                "tasks": ["flood_forecasting","water_level_forecasting"]}},
        ],
        [],
        ["water_level_forecasting", "flood_forecasting"],
    ),
    (
        "text phrase flood inundation detected without methods",
        "The flood inundation mapping was performed using 2D hydrodynamic simulation.",
        [],
        [],
        ["flood_inundation_mapping", "hydrodynamic_simulation"],
    ),
    (
        "WA-LSTM hybrid gives water_level_forecasting",
        "WA-LSTM hybrid model (wavelet + LSTM) was applied for streamflow prediction.",
        [
            {"name": "WA-LSTM", "kb_metadata": {"category": "time_series", "subcategory": "hybrid",
                                                  "tasks": ["water_level_forecasting","streamflow_prediction"]}},
        ],
        [],
        ["water_level_forecasting", "streamflow_prediction"],
    ),
]


def _run_task_case(desc: str, text: str, methods: list[dict],
                   couplings: list[dict], expected_tasks: list[str]) -> dict:
    from src.ingestion.knowledge.knowledge_loader import get_global_kb
    from src.ingestion.knowledge.entity_extractor import EntityExtractor

    kb = get_global_kb()
    ex = EntityExtractor(kb)
    results = ex.extract_tasks(text, methods=methods or None, couplings=couplings or None)
    found_ids = {r["task_id"] for r in results}
    missing = [t for t in expected_tasks if t not in found_ids]
    passed  = len(missing) == 0
    return {
        "test_type":      "T12",
        "description":    desc,
        "expected_tasks": expected_tasks,
        "found_tasks":    sorted(found_ids),
        "missing":        missing,
        "passed":         passed,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def _run_case(test_type: str, text: str, expected_id: Optional[str],
              expected_match: str, expected_type: Optional[str]) -> dict:
    from src.normalization.ontology_matcher import normalize_entity

    result = normalize_entity(text.strip() if text else "", expected_type=expected_type)

    actual_id    = result.get("canonical_id")
    actual_match = result.get("match_type", "unknown")

    if expected_match == "unknown":
        passed = actual_id is None and actual_match == "unknown"
    else:
        passed = (actual_id == expected_id) and (actual_match == expected_match)

    return {
        "test_type":      test_type,
        "input":          text,
        "expected_type_filter": expected_type,
        "expected_id":    expected_id,
        "expected_match": expected_match,
        "actual_id":      actual_id,
        "actual_match":   actual_match,
        "actual_confidence": result.get("confidence", 0.0),
        "passed":         passed,
    }


def run_validation_suite(fail_fast: bool = False) -> dict:
    """
    Execute all test cases and write the QA report.

    Returns:
        Dict with pass/fail counts and full results.
    """
    results = []
    failed  = 0

    for test_args in _TEST_CASES:
        r = _run_case(*test_args)
        results.append(r)
        if not r["passed"]:
            failed += 1
            log.warning(
                "FAIL [%s] %r → expected %s (%s) got %s (%s)",
                r["test_type"], r["input"],
                r["expected_id"], r["expected_match"],
                r["actual_id"],  r["actual_match"],
            )
            if fail_fast:
                break

    # T12 — multi-task extraction
    for task_args in _TASK_TEST_CASES:
        r = _run_task_case(*task_args)
        results.append(r)
        if not r["passed"]:
            failed += 1
            log.warning("FAIL [T12] %s — missing: %s", r["description"], r["missing"])
            if fail_fast:
                break

    total  = len(_TEST_CASES) + len(_TASK_TEST_CASES)
    passed = total - failed
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total":        total,
        "passed":       passed,
        "failed":       failed,
        "pass_rate":    round(passed / total * 100, 1) if total else 0,
        "results":      results,
    }

    _QA_DIR.mkdir(parents=True, exist_ok=True)
    out = _QA_DIR / "normalization_validation.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Validation: %d/%d passed. Report: %s", passed, total, out)
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="GeoHydroAI normalization validator")
    parser.add_argument(
        "--fail-fast", action="store_true",
        help="Stop on first failure",
    )
    args = parser.parse_args()

    summary = run_validation_suite(fail_fast=args.fail_fast)

    print(f"\nNormalization Validation")
    print(f"  Total  : {summary['total']}")
    print(f"  Passed : {summary['passed']}")
    print(f"  Failed : {summary['failed']}")
    print(f"  Rate   : {summary['pass_rate']}%")

    failures = [r for r in summary["results"] if not r["passed"]]
    if failures:
        print(f"\nFailures:")
        for r in failures:
            print(
                f"  [{r['test_type']}] {r['input']!r:40s} "
                f"expected={r['expected_id']} ({r['expected_match']}) "
                f"got={r['actual_id']} ({r['actual_match']})"
            )

    sys.exit(1 if summary["failed"] > 0 else 0)


if __name__ == "__main__":
    _main()
