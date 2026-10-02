#!/usr/bin/env bash
# Paper 1: rebuild every analysis, figure, reference and the manuscript (EN .md + .docx), in dependency order.
# Inputs are the staged, hashed copies under data/paper_1_audit/*/inputs and the SWOT archive on F:.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=/home/niko/miniforge3/envs/geohydroai/bin/python     # analysis env (rasterio, geopandas, pyogrio, scipy)
VENV=.venv/bin/python                                    # project env (docx builder, paper_3 pipeline)
export PROJ_NETWORK=ON
step() { echo "== $(date -u +%H:%M:%S) $*"; }

step "1/7 correlation";            $PY -u scripts/paper1_correlation.py            | tail -3
step "2/7 SWOT-ICESat whole zone"; $PY -u scripts/paper1_swot_icesat_zone.py      | tail -2
step "3/7 LakeSP + maps";          $PY -u scripts/paper1_swot_icesat_lakes_maps.py | tail -2
step "4/7 vertical closure";       $PY -u scripts/paper1_vertical_closure.py       | tail -2
step "5/7 slope frame audit";      $PY -u scripts/paper1_slope_frame_audit.py      | tail -2
step "6/7 station panels";         $PY -u scripts/paper1_station_panels.py         | tail -2
step "7/7 downstream posts 2023";  $PY -u scripts/paper1_downstream_posts_2023.py  | tail -2

step "copy analysis figures into the manuscript figure set"
F=data/paper_3_audit/figures; O=outputs/paper_1
cp $O/vertical_closure/F_V02_closure_offsets.png                 $F/F16_closure_offsets.png
cp $O/vertical_closure/F_V01_reference_surfaces_map.png          $F/F17_reference_surfaces.png
cp $O/swot_icesat_zone/F_Z05_map_whole_zone_pre_post.png         $F/F18_swot_icesat_whole_zone_map.png
cp $O/vertical_closure/F_V03_swot_icesat_egg2015_along_system.png $F/F19_swot_icesat_egg2015_along_system.png
cp $O/swot_icesat_zone/F_Z04_frame_proof.png                     $F/F20_vertical_chains.png
cp $O/correlation/F_C03_satellite_gauge_scatter.png              $F/F21_covariability_scatter.png
cp $O/correlation/F_C02_gauge_correlation_heatmap.png            $F/F22_gauge_network_correlation.png
cp $O/slope_frame_audit/F_S01_slope_frame_invariance.png         $F/F23_slope_frame_invariance.png
cp $O/station_panels/F_ST01_station_panels.png                   $F/F24_station_panels.png
cp $O/downstream_posts_2023/F_DP02_breach_fortnight.png          $F/F25_breach_fortnight_posts.png
cp $O/downstream_posts_2023/F_DP01_downstream_posts_2023.png     $F/F26_downstream_posts_2023.png

step "derived evidence tables (needed by F15)"; $PY -c "from src.paper_3 import derived; derived.build()"
step "snapshot-drawn manuscript figures"; $PY -c "from src.paper_3 import figures; print([p.name for p in figures.build()])"
step "reference registry (CrossRef)";     $PY -c "from src.paper_3.v2 import reference_registry as rr; rr.run()"
step "OWN_EVIDENCE";                       $PY -c "from src.paper_3 import own_evidence as oe; oe.run()"
step "assemble Paper 1 (EN)";              $PY -c "from src.paper_3.v2 import assemble as A; print(A.assemble(paper=1))"
step "docx";                               $VENV -c "from pathlib import Path; from src.paper_3.v2 import assemble as A; print(A.to_docx(Path('data/paper_3_audit/paper1_manuscript_en.md')))"
step "done"
