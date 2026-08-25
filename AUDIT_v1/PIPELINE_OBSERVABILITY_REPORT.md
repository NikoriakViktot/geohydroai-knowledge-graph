# Pipeline Observability Report
**GeoHydroAI Scientific Intelligence Platform — Cross-Layer Synchronization Analysis**
*Generated: 2026-05-21*

---

## Executive Summary

An audit of the GeoHydroAI data pipeline revealed that **22x underreporting of extracted
scientific information** was caused by stale analytics layers, not extraction failure.
After rebuilding the analytics aggregation layer (without re-running any extraction),
apparent corpus coverage increased from 3.4% to 72.2% for tables and from 0.8% to 18.1%
for numeric performance facts.

---

## Before / After Comparison

| Artifact | Papers Before | Papers After | Improvement | Rows Before | Rows After | Row Gain |
|----------|:----------:|:----------:|:-----------:|:-----------:|:----------:|:--------:|
| Tables (sodb_tables) | 126 | 2,664 | **×21** | 463 | 11,631 | **×25** |
| NumericFacts | 30 | 667 | **×22** | 713 | 16,308 | **×23** |
| Formulas | 122 | 2,697 | **×22** | 1,512 | 33,110 | **×22** |
| Regions | 179 | 3,633 | **×20** | 2,922 | 65,577 | **×22** |

*All improvement achieved by re-running `sodb_aggregator --rebuild`. No re-extraction was performed.*

---

## Key Findings

1. **Stale analytics, not extraction failure** — The analytics layer was 14 days behind the
   extraction layer (last aggregator run: 2026-05-07; SODB last updated: 2026-05-21).
   All 667 papers with numeric facts had already been extracted; they were simply invisible.

2. **Analytics consistency collapsed to <4%** — Before sync, the analytics layer reflected
   only 2.7% of actual table rows and 0.2% of numeric fact rows present in SODB.
   After one aggregator rebuild: 100% for all artifact types.

3. **Full Nougat coverage already achieved** — All 3,692 papers have `data/nougat_regions/`
   directories with crop images. The 59 papers with empty `regions.parquet` are legitimately
   text-only documents with no visual tables or formulas.

4. **1,028 papers still need targeted extraction** — These papers have no `tables.parquet`
   in SODB at all (MISSING status, not EMPTY). Targeted Stage 1 re-run required.

5. **Observability gap** — The pipeline lacked any automated mechanism to detect or alert
   when the analytics layer fell out of sync with the extraction layer.

---

## Figure Captions (Publication-Ready)

**Fig 11.1** — Cross-layer synchronization impact on apparent scientific information coverage.
(a) Percentage of total corpus papers (N=3,692) with each artifact type, before and after
analytics rebuild. (b) Total rows extracted per artifact type. Multipliers indicate improvement
ratio. No extraction was re-run; all improvements reflect analytics synchronization only.

**Fig 11.2** — Pipeline layer freshness heatmap. Color encodes age in days relative to the
synchronization event (2026-05-21 10:46 UTC). Green = fresh (<1 day), yellow = recent (<7 days),
red = stale (≥7 days). The core analytics layer (papers.parquet) was 14 days stale at rebuild time.

**Fig 11.3** — Analytics consistency before and after synchronization, defined as
`analytics_rows / sodb_actual_rows × 100%`. Before sync: 0.2–4.0% across artifact types.
After sync: 100% for all artifact types.

**Fig 11.4** — SODB artifact status taxonomy. Each paper's artifact classified as OK
(exists, non-empty), EMPTY (exists, zero rows), or MISSING (file absent). EMPTY regions
represent text-only papers; MISSING tables represent papers awaiting Stage 1 extraction.

**Fig 11.5** — SODB extraction pipeline flow (post-synchronization). Sankey diagram showing
the flow from 3,692 total corpus papers through region detection, table extraction, and
numeric fact derivation stages. Node widths are proportional to paper counts.

**Fig 11.6** — Corpus coverage radar chart. Coverage percentages for nine distinct information
layers spanning metadata (DOI, abstract, OpenAlex), text analytics (metrics, methods), and
SODB structured extraction (tables, formulas, regions, numeric facts).

**Fig 11.7** — Nougat coverage validation. Comparison of nougat_regions image directories
(visual preprocessing) versus regions.parquet non-empty status (structured extraction output).
The discrepancy between crop images and non-empty regions represents text-only papers.

**Fig 11.8** — Geographic and temporal distribution of 1,028 papers requiring targeted
table extraction. These papers have MISSING (not EMPTY) tables.parquet — the Stage 1
extraction pipeline has not yet been run for them.

---

## Implications for Scientific Reproducibility

Any paper or analysis generated from the pre-sync analytics state would have reported:
- "667 papers yield numeric performance metrics" → incorrectly reported as "30 papers"
- "72.2% of corpus has parsed tables" → incorrectly reported as "3.4%"
- "Information loss: 22%" → incorrectly reported as "99.2%"

This demonstrates that **data infrastructure observability is a prerequisite for
scientific reproducibility** in large-scale AI-augmented research workflows.

---

## Recommended Next Steps

| Priority | Action | Impact |
|----------|--------|--------|
| 1 | Schedule `sodb_aggregator` after every SODB pipeline run (cron or pipeline hook) | Prevents recurrence |
| 2 | Add freshness monitoring dashboard widget | Early warning |
| 3 | Run targeted Stage 1 extraction on 1,028 papers with MISSING tables.parquet | +~1,000 papers |
| 4 | Rebuild Neo4j graph from updated analytics | Graph queries reflect actual coverage |
| 5 | Add cross-layer consistency check to CI | Automated regression detection |

---

## Data Files

| File | Description |
|------|-------------|
| `figures/fig_11_1_before_after_sync.{png,svg,pdf}` | Core publication figure |
| `figures/fig_11_2_freshness_heatmap.{png,svg}` | Layer freshness heatmap |
| `figures/fig_11_3_sync_consistency.{png,svg}` | Synchronization consistency chart |
| `figures/fig_11_4_failure_taxonomy.{png,svg}` | Artifact failure taxonomy |
| `figures/fig_11_5_pipeline_sankey.png` | Pipeline flow Sankey diagram |
| `figures/fig_11_6_coverage_radar.{png,svg}` | Multi-layer coverage radar |
| `figures/fig_11_7_nougat_validation.{png,svg}` | Nougat validation chart |
| `figures/fig_11_8_repair_targets.{png,svg}` | Repair target characterization |
| `figures/synchronization_metrics.csv` | Before/after consistency data |
| `figures/freshness_metrics.csv` | Layer freshness timestamps |
| `figures/coverage_metrics.csv` | Per-layer coverage percentages |
| `figures/synchronization_metrics_scorecard.csv` | Observability scorecard |
| `data/analytics/sodb_diagnostics.parquet` | Per-paper artifact status (14,768 rows) |
| `data/analytics/papers_needing_extraction.txt` | 1,028 paper IDs for targeted re-run |
| `notebooks/11_pipeline_observability.ipynb` | Full analysis notebook |
| `assets/notebooks/11_pipeline_observability.html` | Rendered notebook (HTML) |
