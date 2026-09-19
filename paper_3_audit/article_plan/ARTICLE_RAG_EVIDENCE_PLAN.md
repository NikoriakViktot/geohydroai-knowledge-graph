# ARTICLE_RAG_EVIDENCE_PLAN — Paper 3 literature layer

State on 2026-09-18 (run `20260916T092929Z`, commit `c3ec671`, tree dirty). Everything in the literature layer is currently **EXPLORATORY / RETRIEVAL_UNVALIDATED**; this document says what exists, what it may support, and what must happen before any sentence about the literature enters the manuscript.

## 1. Architecture relevant to Paper 3 (knoweledg_graf)

```
theses.yaml (24 cards: statement, block A–?, key_terms, negative_terms,
            positive_controls, expected_relations, manuscript_anchor, numeric_anchor)
   │
   ├─ harvest_queries / harvest_openalex   148 queries (sha eb2837fd…) + 4 boolean slices S1–S4 (sha f642c943…)
   │      → 10 498 works discovered, 359 already in corpus, 88 selected & downloaded, 1 409 not OA
   ├─ harvest_ingest → GROBID → paper.json → normalized → ChromaDB flood_papers_768d (SPECTER2 768-d)
   │      corpus at freeze: 3 680 papers / 1 313 665 chunks; today 1 350 310 chunks (119 paper_3 papers ingested 2026-09-18)
   ├─ retrieve (distance gate 0.45, citation depth 1, on-topic rule: key_terms[0] + ≥1 other family, unit = paper)
   ├─ classify_relation (Gemini adjudication; verdicts by fixed rules) → relations.parquet (286)
   ├─ gap_matrix  → GAP_MATRIX_EXPLORATORY_UNVALIDATED.{csv,md}
   ├─ slice_screening → SLICE_SCREENING.csv (1 971 rows) → SLICE_DENOMINATORS.csv
   ├─ briefs/geodesy → SCREENING_SHEET_reviewed.csv (57), GEODESY_B_CALIBRATION_SUBSET.csv (20), technical_sources.yaml
   ├─ control_set / control_plan → CONTROL_WORKSHEET.csv (35 rows; 28 development / 7 holdout; 15 needs reading / 20 to find)
   ├─ acceptance → ACCEPTANCE_GATE.md  (NOT READY)
   └─ v2: literature_claims.yaml (9 L-claims) + claim_map + scientific_claim_status.yaml → LITERATURE_MATRIX, SECTION_7_8_STUB
```

Neo4j and OpenAlex enrichment exist for the corpus but play no role in the Paper 3 verdicts (verdicts are rule-based over `relations.parquet` + slice denominators). Nothing in this pipeline is allowed to assert priority; the strongest possible status is CANDIDATE_GAP and a gap is not a contribution.

## 2. Thesis-extraction strategy

The 24 thesis cards are candidates, not the paper's theses. The article's argument collapses them into the seven AT theses of `ARTICLE_DATA_EVIDENCE_MATRIX.csv`; each AT maps to the thesis IDs that will supply its literature:

| AT | theses feeding it | L-claims |
|---|---|---|
| AT1 transition | T01 T02 T03 T06 T14 T23 | LK1.1, LT1.1, LT1.2 |
| AT2 geometry vs area | T04 T05 T19 T20 T21 T24 | LK1.2, LT1.2 |
| AT3 SWOT+ICESat-2 | T06 T07 T11 T12 T13 T22 | LD1.1 |
| AT4 harmonisation | T08 T09 T10 T11 | LD1.2, LD1.3 |
| AT5 history | T15 T16 T17 T18 | LK1.3, LD1.4 |
| AT6 slope–Q | T01 T23 | (none; limitation) |
| AT7 positioning | all | all |

Critical theses per the gate: T09 T10 T14 T15 T16 T17 T22 — each needs ≥2 verified controls, a holdout and a hard control; currently 0.

## 3. The three layers and what each may say

### Layer K — Kakhovka-specific (slice S1_kakhovka_status_quo)
Denominators from `SLICE_DENOMINATORS.csv`: 1 051 worldwide OpenAlex hits → 481 candidates → 235 off-topic → **226 screened** (17 full text, 209 abstract-only). Topic counts among the screened: extent 82, vegetation 135, WSE 29, morphology 25, bathymetry 7, **WSE slope 0**, harmonises datum 1, historical validation 31.
- LK1.1 (absence: no screened Kakhovka study quantifies a longitudinal WSE gradient) — *answers KAKHOVKA NOVELTY* — permitted wording only after validation: "in the current screened corpus (226 of 481 candidates), none…". Never "does not exist".
- LK1.2 (prevalence: planform quantities dominate) — *KAKHOVKA NOVELTY* — ratio 82/226 extent vs 0/226 slope; screened sample is relevance-ranked, not random.
- LK1.3 (hydrodynamic reconstructions treat vertical reference as secondary) — closest precedent in corpus `10.1111/j.1752-1688.2008.00263.x` (a 2008 dam-failure paper — not Kakhovka; the true competitors `10.1029/2025GL120832` Lehnigk et al. SWOT Kakhovka flood and `10.1029/2025GL119771` are listed as controls "needs reading").
Raw OpenAlex counts (1 051) must never appear as study counts.

### Layer T — hydraulic-transition literature (slice S3_hydraulic_transition_from_space)
1 102 → 495 → 314 off-topic → 180 screened (30 full text); dam_transition 180, channelisation 38, satellite_observed 59, in_situ_only 18. LT1.1 (dam removal/failure → channelised hydraulics) and LT1.2 (slope as regime state variable, persistence = shift) answer *PHYSICAL INTERPRETATION*; a thin result here is a weakness of the argument, not a finding. Closest quote so far is about crevasse splays (`10.1029/2018gl077933`) — i.e. the retrieved passage is not yet on point; this layer needs the dam-removal canon (Dam Removal Sciences, Elwha, Condit, Marmot) which is largely not OA and sits in the 1 409 "not OA" list.

### Layer D — data/method literature (slice S2_altimetry_vertical_datum + geodesy mini-corpus)
2 274 → 496 → 122 off-topic → 361 screened (11 full text); gauge_comparison 183, harmonises_datum 51, reports_wse_slope 17, **permanent_tide 1**, spatially_varying_offset 1. Geodesy mini-corpus: 57 screened by density gate → 20 usable / 27 marginal / 10 irrelevant; 20-row calibration subset. `technical_sources.yaml` lists the mission/standard documents (SWOT PIXC PDD D-56411 Rev C, ATL03/ATL13 ATBDs, Denker 2015, EVRF2019 report, IAG Res. 16, EPSG:9902, SWORD) — all `status: url_needed`; the manuscript must cite these, and they can never be positive controls (not retrievable). LD1.1–LD1.4 answer *METHOD VALIDITY*; LD1.3 is the one measured prevalence (1/361 name a permanent-tide convention) and is the paper's strongest "why this matters" statistic once validated.

## 4. Positive controls and the acceptance gate

`ACCEPTANCE_GATE.md`: FAIL on 6 of 10 criteria — 9/24 theses have a control, **0/24 verified**, 15 critical-thesis controls missing, POSITIVE_CONTROL.csv and CALIBRATION_SHEET.csv never produced; PASS only "no holdout consumed" and "manifest frozen". `CONTROL_WORKSHEET.csv`: 15 rows "needs reading", 20 "to find"; the two named competitors (Lehnigk 2026 GRL; the SWOT uneven-lake paper) are development controls for T02/T04 and have not been read.

Consequence (rule 4, 10, 13 of the brief): **no novelty, absence or "closest precedent" sentence may be written now.** The §7.8 stub already carries `[PENDING OPEN41–43]` blocking markers; keep them.

## 5. Retrieval limitations to state in the paper (once validated)
- corpus is flood-inundation-shaped (pre-harvest: 0 ICESat, 0 geoid, 0 dam-removal titles); the 88-paper harvest is what makes S1–S3 answerable at all;
- screening is keyword-based, mostly on abstracts (a paper may report a gradient without naming it);
- S4_historical_data_as_validation is unusable as a slice (51 304 hits, 9 screened, 0.02 % on-topic) — historical-validation positioning must come from hand-curated sources, not from a denominator;
- absence is stated for the screened sample only; the eligibility of every paper is by manifest identity (DOI/slug), and the query set is hashed.

## 6. What may and may not be claimed (decision table)

| statement type | now | after gate passes |
|---|---|---|
| "No Kakhovka study quantified longitudinal WSE slope" | **no** | "none among the N screened studies in slice S1 (recall r on k hold-out controls)" |
| "Planform metrics dominate Kakhovka literature" | no | share with denominator |
| "Dam removal/failure produces channelised hydraulics" (T) | no citation yet | cite verified passages; this is precedent, not novelty |
| "ICESat-2/SWOT can retrieve slope" (D) | ATBD/PDD citations only (technical_sources) | + verified papers |
| "Only 1 of 361 altimetry–datum studies names a tide convention" | no | yes, as measured prevalence with the screening caveat |
| "This study is the first to …" | never | never — replace by the screened-absence wording |

## 7. Actions (mirrored in ARTICLE_OPEN_ITEMS P0-L)
1. Read and verify the 15 "needs reading" controls; find the 20 "to find" (start with the two Kakhovka competitors and the dam-removal canon; hand-add non-OA PDFs under `data/literature/pdf_missing/`).
2. `python -m src.paper_3.cli --step retrieve` → POSITIVE_CONTROL.csv; `python -m src.paper_3.calibrate sample` → CALIBRATION_SHEET.csv.
3. Add holdout + hard controls for T09 T10 T14 T15 T16 T17 T22.
4. Commit before re-freezing (the manifest is only meaningful on a clean tree); re-run `--step publish`.
5. Fill `url_verified` for the technical sources.
6. Import the SWOT-DNIPRO audit statuses into `v2/scientific_claim_status.yaml` (currently all UNKNOWN) so the claim map stops blocking on our own data.
