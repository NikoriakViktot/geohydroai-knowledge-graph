"""Snapshot of the SWOT-DNIPRO result tables inside this repo.

The scientific analysis lives in another WSL distro (Ubuntu-24.04) and this
repo must never read it live at manuscript-build time: a number in the
manuscript has to point at a file whose bytes are frozen here, with a sha256,
next to the commit that produced them. This module copies a fixed list of
audit registers and result tables into ``data/paper_3_audit/swot_dnipro_snapshot/``
and writes ``SNAPSHOT_MANIFEST.json``.

Cross-distro access goes through ``wsl.exe`` with the script passed on stdin
(inline ``$vars`` are mangled by the interop layer). Every remote call is
funnelled through :func:`run_remote` so tests can replace it with a fake.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.paper_3._utils import OUT_DIR

logger = logging.getLogger(__name__)

WSL_EXE = "/mnt/c/Windows/System32/wsl.exe"
DISTRO = "Ubuntu-24.04"
REPOS = {
    "swot": "/home/niko/repo/SWOT-DNIPRO",
    "icesat": "/home/niko/repo/icesat2-atl13-kakhovka",
}
SNAPSHOT_DIR = OUT_DIR / "swot_dnipro_snapshot"
MANIFEST_NAME = "SNAPSHOT_MANIFEST.json"

#: (repo key, glob relative to the repo root). Globs are expanded remotely.
#: Order is the order of the manifest; add, never reorder, so diffs stay readable.
SNAPSHOT_FILES: tuple[tuple[str, str], ...] = (
    # audit registers (latest run)
    ("swot", "audit_runs/20260918T175108Z/claim_evidence_matrix.csv"),
    ("swot", "audit_runs/20260918T175108Z/findings.csv"),
    ("swot", "audit_runs/20260918T175108Z/checks.csv"),
    ("swot", "audit_runs/20260918T175108Z/literature_sources.csv"),
    ("swot", "audit_runs/20260918T175108Z/literature_search_log.csv"),
    ("swot", "audit_runs/20260918T175108Z/roughness_class_table.csv"),
    ("swot", "audit_runs/20260918T175108Z/dataset_inventory.csv"),
    ("swot", "audit_runs/20260918T175108Z/index_feature_catalog.csv"),
    ("swot", "audit_runs/20260918T175108Z/product_status.csv"),
    ("swot", "audit_runs/20260918T175108Z/scope_manifest.json"),
    ("swot", "audit_runs/20260918T175108Z/checks/zone_nvalid_summary.json"),
    ("swot", "audit_runs/20260918T175108Z/checks/chk020_finding1_result.json"),
    # pilots
    ("swot", "audit_runs/20260918T175108Z/pilots/vegetation/out/*.csv"),
    ("swot", "audit_runs/20260918T175108Z/pilots/vegetation/out/summary.json"),
    ("swot", "audit_runs/20260918T175108Z/pilots/channel/out/*.csv"),
    ("swot", "audit_runs/20260918T175108Z/pilots/channel/out/summary.json"),
    ("swot", "audit_runs/20260918T175108Z/pilots/roughness/out/*.csv"),
    ("swot", "audit_runs/20260918T175108Z/pilots/roughness/out/summary.json"),
    ("swot", "audit_runs/20260918T175108Z/pilots/icesat2/out/*.csv"),
    ("swot", "audit_runs/20260918T175108Z/pilots/icesat2/out/summary.json"),
    # manuscript evidence and hydraulic transition
    ("swot", "outputs/tables/manuscript_evidence_matrix.csv"),
    ("swot", "outputs/tables/kakhovka_transition_statistics.csv"),
    ("swot", "outputs/tables/kakhovka_perdate_slopes_robust.csv"),
    ("swot", "outputs/tables/kakhovka_pre_post_slope_summary.csv"),
    ("swot", "outputs/figure_data/FigG_span_sensitivity.csv"),
    ("swot", "outputs/tables/reservoir_to_river_statistics.csv"),
    ("swot", "outputs/tables/allwater_vs_channel_slopes.csv"),
    ("swot", "outputs/tables/pre_post_fragmentation_statistics.csv"),
    ("swot", "outputs/tables/residual_water_offset_summary.csv"),
    ("swot", "outputs/tables/fragmentation_metrics_by_date.csv"),
    ("swot", "outputs/tables/slope_Q_regression_results.csv"),
    # vertical chain and validation
    ("swot", "outputs/tables/gauge_vertical_reference_summary.csv"),
    ("swot", "outputs/tables/part4_colocated_swot_icesat.csv"),
    ("swot", "outputs/tables/validation_summary_table.csv"),
    ("swot", "outputs/tables/vertical_reference_audit.csv"),
    ("swot", "outputs/figure_data/Fig04_permanent_tide_correctors.csv"),
    ("icesat", "outputs/tables/egg2015_to_evrf2019_by_station.csv"),
    ("icesat", "outputs/tables/kakhovka_datum_comparison.csv"),
    # historical validation and ZONE_1 bed
    ("swot", "outputs/tables/historical_reference_sensitivity.csv"),
    ("swot", "outputs/tables/hist3_datum_in_evrf2019.csv"),
    ("swot", "outputs/tables/hist6_reach_slopes.csv"),
    ("swot", "outputs/tables/hist7_slope_geometry.csv"),
    ("swot", "outputs/tables/hist12_bed_morphology_tests.csv"),
    ("swot", "outputs/tables/hist14_interpolator_cv.csv"),
    ("swot", "outputs/tables/hist14_surface_summary.csv"),
    ("swot", "outputs/tables/hist17_grid_resolution.csv"),
    ("swot", "outputs/tables/hist18_cv_scheme_comparison.csv"),
    ("swot", "outputs/tables/historical_exposure_area_validation.csv"),
    ("swot", "outputs/tables/hypsometric_constraint_test.csv"),
    ("swot", "outputs/tables/hist28_offset_stability.csv"),
    ("swot", "outputs/tables/hist31_dem_constraints.csv"),
    # zone bed DEMs
    ("swot", "outputs/tables/p21_cv_summary_all.csv"),
    ("swot", "outputs/tables/p28_surface_summary_ZONE_*.csv"),
    ("swot", "outputs/tables/p28_interpolator_cv_ZONE_*.csv"),
    # Sentinel-2 indices, classes, shoreline uncertainty
    ("swot", "outputs/tables/p25_zone_spectral_manifest_ZONE_*.csv"),
    ("swot", "outputs/tables/p27_zone_shoreline_uncertainty.csv"),
    # Sentinel-1 classifier study
    ("swot", "outputs/tables/p32_metrics_ZONE_*.csv"),
    ("swot", "outputs/tables/p34_variant_ranking.csv"),
    ("swot", "outputs/tables/p34_references.csv"),
    ("swot", "outputs/tables/p38_references.csv"),
    ("swot", "outputs/tables/p38c_operating_points_ZONE_*_pre.csv"),
    ("swot", "outputs/tables/p37_false_water_summary.csv"),
    # gauges / sea posts
    ("swot", "data/historical/sea_posts_2023/*.csv"),
    ("swot", "outputs/tables/k5_gauge_availability.csv"),
    # ── 2026-09-19 pilots p48b-p61: independent DEM validation, the SWOT
    #    drawdown/flood-wave series, the seamless terrain and the roughness
    #    mosaic. These arrived after the first snapshot and carry the numbers
    #    the two manuscripts were still missing.
    ("swot", "outputs/tables/p48b_gedi_vs_icesat2.csv"),
    ("swot", "outputs/tables/p49_domain_features_by_year.csv"),
    ("swot", "outputs/tables/p49_domain_separability_2022.csv"),
    ("swot", "outputs/tables/p49_flood_event_areas.csv"),
    ("swot", "outputs/tables/p50_never_vs_recession_zones.csv"),
    ("swot", "outputs/tables/p50_water_occurrence_class_shares.csv"),
    ("swot", "outputs/tables/p51_rf_metrics.csv"),
    ("swot", "outputs/tables/p51_rf_areas.csv"),
    ("swot", "outputs/tables/p51_rf_importance.csv"),
    ("swot", "outputs/tables/p51b_common_support_metrics.csv"),
    ("swot", "outputs/tables/p51b_permutation_importance.csv"),
    ("swot", "outputs/tables/p52_pool_dem_vs_icesat2.csv"),
    ("swot", "outputs/tables/p52_fabdem_datum_offset.csv"),
    ("swot", "outputs/tables/p52_zone_dem_shoreline_datum.csv"),
    ("swot", "outputs/tables/p52_dam_seam.csv"),
    ("swot", "outputs/tables/p53_bed_v2_summary.csv"),
    ("swot", "outputs/tables/p53_bed_v2_cv_ZONE_*.csv"),
    ("swot", "outputs/tables/p54_channel_cv.csv"),
    ("swot", "outputs/tables/p54_pool_v1_vs_v2_icesat2.csv"),
    ("swot", "outputs/tables/p55_seam_check.csv"),
    ("swot", "outputs/tables/p56_fabdem_vs_icesat2_below_dam.csv"),
    ("swot", "outputs/tables/p56_fabdem_chain_samples.csv"),
    ("swot", "outputs/tables/p57_dem_accuracy_night.csv"),
    ("swot", "outputs/tables/p58_manning_mosaic_summary.csv"),
    ("swot", "outputs/tables/p59_swot_peak_wse_profile.csv"),
    ("swot", "outputs/tables/p59_swot_vs_kherson.csv"),
    ("swot", "outputs/tables/p59_swot_flood_profiles.csv"),
    ("swot", "outputs/tables/p60_swot_outlet_drawdown.csv"),
    ("swot", "outputs/tables/p60_swot_pool_coverage.csv"),
    ("swot", "outputs/tables/p60_swot_pool_vs_gauges.csv"),
    ("swot", "outputs/tables/p60_swot_pool_profiles.csv"),
    ("swot", "outputs/tables/p61_pool_level_snapshots.csv"),
    ("swot", "outputs/tables/p61_swot_vs_kasperivka.csv"),
    ("swot", "outputs/tables/p61_yi2025_reservoir_area.csv"),
    # p51 rewritten 2026-09-21: nested spatial CV, spatial transfer, calibration.
    # The 2026-09-19 tables it supersedes are NOT snapshotted — they carry an
    # optimistic operating point chosen on the sample it was scored on.
    ("swot", "outputs/tables/p51_rf_metrics.csv"),
    ("swot", "outputs/tables/p51_rf_fold_metrics.csv"),
    ("swot", "outputs/tables/p51_rf_leave_one_zone_out.csv"),
    ("swot", "outputs/tables/p51_rf_block_sensitivity.csv"),
    ("swot", "outputs/tables/p51_rf_block_scale.csv"),
    ("swot", "outputs/tables/p51_rf_calibration.csv"),
    ("swot", "outputs/tables/p51_rf_perm_importance.csv"),
    ("swot", "outputs/tables/p51_rf_input_manifest.csv"),
    ("swot", "outputs/tables/p51_rf_hand_check.csv"),
    ("swot", "outputs/tables/p51_rf_fusion.csv"),
    ("swot", "outputs/tables/p51_s1_observation_support_2.csv"),
    ("swot", "outputs/tables/p51_s1_observation_support_4.csv"),
    ("swot", "outputs/tables/p51c_domain_clipping.csv"),
    ("swot", "outputs/tables/p51_zone4_validity_impact.csv"),
    # p62 GEDI (2026-09-19): a second, orthogonal check on the delivered terrain
    # and the field support for the roughness classes — plus the seam defect it found.
    ("swot", "outputs/tables/p62_gedi_ground_accuracy.csv"),
    ("swot", "outputs/tables/p62_gedi_canopy_by_class.csv"),
    ("swot", "outputs/tables/p62_gedi_canopy_matched_window.csv"),
    ("swot", "outputs/tables/p62_gedi_vs_icesat2_cells.csv"),
    ("swot", "outputs/tables/p62_gedi_growth_paired.csv"),
    ("swot", "config/roughness_classes.yaml"),
    # geometry registry
    ("swot", "config/spatial_domains.yaml"),
)


def run_remote(repo: str, script: str, timeout: int = 300) -> bytes:
    """Run a bash script inside the other distro with ``repo`` as cwd; return stdout."""
    proc = subprocess.run(
        [WSL_EXE, "-d", DISTRO, "--cd", REPOS[repo], "--", "bash", "-s"],
        input=script.encode(), capture_output=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"remote script failed in {repo}: "
                           f"{proc.stderr.decode(errors='replace')[-400:]}")
    return proc.stdout


def remote_listing(repo: str, pattern: str,
                   runner: Callable[[str, str], bytes] = run_remote) -> list[str]:
    """Expand one glob relative to the repo root; sorted, sidecars excluded."""
    out = runner(repo, f"ls -1 -- {pattern} 2>/dev/null || true\n")
    names = [l.strip() for l in out.decode().splitlines() if l.strip()]
    return sorted(n for n in names if not n.endswith(":Zone.Identifier"))


def remote_read(repo: str, rel_path: str,
                runner: Callable[[str, str], bytes] = run_remote) -> bytes:
    """Read one file; base64 on the remote side keeps binary and encoding intact."""
    out = runner(repo, f"base64 -w0 -- '{rel_path}'\n")
    return base64.b64decode(out)


def remote_git_state(repo: str,
                     runner: Callable[[str, str], bytes] = run_remote) -> dict:
    out = runner(repo, "git rev-parse HEAD; git status --porcelain | wc -l\n").decode()
    lines = out.split()
    return {"commit": lines[0] if lines else "", "dirty_paths": int(lines[1]) if len(lines) > 1 else -1}


@dataclass(frozen=True)
class SnapshotEntry:
    repo: str
    rel_path: str
    local_path: str
    sha256: str
    bytes: int


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pull(out_dir: Path = SNAPSHOT_DIR, run_id: str | None = None,
         files: tuple[tuple[str, str], ...] = SNAPSHOT_FILES,
         runner: Callable[[str, str], bytes] = run_remote) -> Path:
    """Copy every listed file into ``out_dir/<repo>/<rel_path>`` and write the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: list[SnapshotEntry] = []
    missing: list[str] = []
    for repo, pattern in files:
        matches = remote_listing(repo, pattern, runner)
        if not matches:
            missing.append(f"{repo}:{pattern}")
            continue
        for rel in matches:
            data = remote_read(repo, rel, runner)
            local = out_dir / repo / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
            entries.append(SnapshotEntry(repo, rel, str(local.relative_to(out_dir)),
                                         sha256_bytes(data), len(data)))
            logger.info("snapshot %s:%s (%d B)", repo, rel, len(data))
    manifest = {
        "run_id": run_id,
        "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repos": {k: {"path": REPOS[k], **remote_git_state(k, runner)} for k in REPOS},
        "n_files": len(entries),
        "missing_patterns": missing,
        "files": [e.__dict__ for e in entries],
    }
    path = out_dir / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if missing:
        logger.warning("snapshot: %d pattern(s) matched nothing: %s", len(missing), missing)
    return path


def load_manifest(out_dir: Path = SNAPSHOT_DIR) -> dict:
    return json.loads((out_dir / MANIFEST_NAME).read_text(encoding="utf-8"))


def verify(out_dir: Path = SNAPSHOT_DIR) -> list[str]:
    """Return sha256 mismatches / missing files; empty list means the snapshot is intact."""
    problems: list[str] = []
    for f in load_manifest(out_dir)["files"]:
        p = out_dir / f["local_path"]
        if not p.exists():
            problems.append(f"missing: {f['local_path']}")
        elif sha256_bytes(p.read_bytes()) != f["sha256"]:
            problems.append(f"sha256 mismatch: {f['local_path']}")
    return problems


def resolve(name: str, out_dir: Path = SNAPSHOT_DIR) -> Path:
    """Find a snapshot file by basename or by repo-relative path (unique match required)."""
    files = load_manifest(out_dir)["files"]
    hits = [f for f in files if f["rel_path"] == name or Path(f["rel_path"]).name == name]
    if len(hits) != 1:
        raise FileNotFoundError(f"{name}: {len(hits)} snapshot matches")
    return out_dir / hits[0]["local_path"]


def entry_for(name: str, out_dir: Path = SNAPSHOT_DIR) -> dict:
    p = resolve(name, out_dir)
    for f in load_manifest(out_dir)["files"]:
        if out_dir / f["local_path"] == p:
            return f
    raise FileNotFoundError(name)
