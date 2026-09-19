# Paper 3 — Gap Analysis Design

Companion to `src/paper_audit/PAPER_AUDIT_PLAN.md`. Records what this module does,
the calibration decisions behind its thresholds, and the corrections found while
building it.

---

## 1. What this is for

The manuscript `paper_3_audit/Kakhovka_scientific_report_article_draft_v1.md`
(1 423 lines) has two deliberately empty sections:

- **§7.8 Comparison with literature** — *"Deferred. No systematic literature review
  has been carried out, and this draft deliberately does not assert priority."*
- **§REFERENCES** — *"To be completed."* Eleven documents are cited informally
  (SWOT PIXC PDD D-56411 Rev C, ICESat-2 ATL03/ATL13 ATBDs, Denker 2015 EGG2015,
  EVRF2019 / EPSG:9902, IAG Resolution 16, SWORD v16, McFeeters 1996, Xu 2006,
  Sen 1968 / Theil 1950, the Dnipro reservoirs monograph). None carries a DOI.

The module turns 24 thesis cards into a Gap Evidence Matrix that places each
thesis on an eight-value scale from KNOWN to CANDIDATE_GAP (§4.7) — so the
novelty claim is derived from a validated corpus search rather than asserted.

---

## 2. The problem the design exists to solve

The corpus is flood-inundation-mapping-shaped. Before any harvest, title-level
counts over `data/analytics/papers.parquet` (3 680 rows) were:

| probe | count |
|---|---|
| altimetry | 7 |
| ICESat / ICESat-2 | **0** |
| geoid · vertical datum · water-surface slope · dam removal · permanent tide | **0 each** |
| SWOT · Kakhovka · bathymetry | 19 · 10 · 3 |

Both closest competitors are absent: `10.1029/2025GL120832` (SWOT, Kakhovka
dam-break flood) and `10.1029/2025GL119771` (SWOT-mapped uneven lake WSE).

Running the matrix on this corpus would return "UNKNOWN — 0 papers" for most
theses, which means *our corpus is empty*, not *the literature has a gap*. A live
OpenAlex probe shows the literature exists and is largely open access (2 484 works
for "ICESat-2 water surface slope river", including the IRIS global reach-scale
slope dataset and "Hydraulic River Models From ICESat-2", both OA).

Hence: **Phase 0 harvest first, matrix second**, and a coverage diagnostic that
keeps the two kinds of silence apart.

---

## 3. Data flow

```
theses.yaml ──► harvest_queries ──► harvest_openalex ──► harvest_ingest
   │                (queries.json)     (candidates)        (pdf→xml→json→chroma→
   │                                                        enrich→parquet→graph)
   │                                                              │
   │                                                              ▼
   │                                                        corpus_index
   │                                                        (+ cohort tag)
   │                                                              │
   ▼                                                              ▼
draft_anchors ─────────────────────────────────────────────►  retrieve
(§7.8, §REFERENCES, numeric claims)                     semantic→kg→citation→
                                                        fulltext→facts
                                                              │
                                             classify_relation (Gemini, raw only)
                                                              │
                                             finalize_relations (verify + overrides)
                                                              │
                                    ┌─────────────────────────┼──────────────┐
                                    ▼                         ▼              ▼
                            extract_fields            corpus_index      gap_matrix
                         (PAPER_EXTRACTION)          .build_coverage   (+ novelty)
                                                              │
                                                          publish
```

Each CLI step does exactly one kind of work — HTTP, or XML, or Neo4j writes —
never two, per the CLAUDE.md invariant. Each guards on its own output files, so
the chain resumes rather than restarts.

---

## 4. Calibration decisions

### 4.-1 Positive controls are held out, never injected

A seed DOI exists to test retrieval, not to help it. The first implementation
injected controls straight into the candidate set when semantic retrieval missed
them, which makes recall 1 by construction:

```
we know paper → add as seed → seed enters candidates → recall = 1
```

That measures nothing. Controls are now **hold-out**: the harvest ingests them
into the corpus like any other paper, and retrieval has to find them unaided.
`RETRIEVAL_ORIGINS` deliberately has no `seed` value, and a test pins that.

The same rule decides what a control can *be*. A source retrieval could never
reach — no DOI, not in OpenAlex — cannot be a control, because injecting it is the
only way it could ever be "recovered". Such a source is a **curated source**
(`source_type: curated_paper` in `briefs/technical_sources.yaml`): cited, read,
screened into the geodesy mini-corpus, but never counted in recall. The first case
is Стопхай et al. (2026) on the BS-77 → EVRF2019 model, named mandatory by the
author on 2026-09-17; the indexed sibling Trevoho (2021, DOI
10.23939/istcgcap2021.93.013) is the one that *can* serve as a control for T08/T10.

`POSITIVE_CONTROL.csv` names the stage that lost each control, because each has a
different fix:

| `failure_stage` | what to change |
|---|---|
| `not_in_corpus` | run the harvest |
| `primary_family` | the discriminating key term is worded too narrowly |
| `second_family` | primary hits, nothing else does |
| `semantic_retrieval` | the search queries, not the gate |
| `ranking_cutoff` | `MAX_CANDIDATES_PER_THESIS` |
| `prefilter` | the gate thresholds |
| `stage_mismatch` | recovered only via citation when semantic was expected — a weaker result than it looks |

### 4.-0.75 The control set is frozen too

Holding controls out of retrieval, and out of tuning, still leaves one route:

```
pick a holdout → find it is hard → swap in an easier DOI → 100% recall
```

So `--step freeze-controls` hashes the control **identities** —
`doi, role, relevance, difficulty, expected_stage` — into `CONTROL_SET.json`.
`key_terms`, `search_queries` and the gates stay editable afterwards (that is
what development controls are for); the identity of a control does not move.
Reading a control and filling in its quote does not change the hash, and a test
pins that, so verification work is never blocked by the freeze.

A control that turns out on reading not to be what it was taken for is retired
with `excluded: true` + `exclusion_reason` + `replacement_doi`, which keeps the
swap in the audit trail rather than erasing it. Deleting the entry instead shows
up as `REMOVED` drift and is refused.

Freezing an *incomplete* set is refused as well: every control found afterwards
would register as drift, `--force` would become routine, and the freeze would
stop meaning anything. Select first, freeze once.

### 4.-0.5 Development vs holdout — not all controls may be tuned against

Holding a control out of *retrieval* is not enough. This is still overfitting:

```
control not found → change key_terms → control found → recall = 1
```

So controls carry a `role`:

| role | may be used for |
|---|---|
| `development` | debugging and tuning key terms, queries, gates |
| `holdout` | **nothing** until opened once, after the rules are frozen |

Four rules govern which papers may serve:

1. A control must be found **independently of this pipeline** — external search,
   citation chasing, or a paper already known to the researcher. A control the
   pipeline suggested is the pipeline marking its own work.
2. A critical thesis's holdout is a **different paper**, not another passage of
   the development control.
3. Development and holdout should be **methodologically different** — one
   ICESat-2, the other SWOT or classical hydraulics — so recall is not a test of
   one narrow vocabulary.
4. One paper must not be the **sole holdout for several critical theses**: their
   controls would then rise and fall together. The gate checks this
   (`critical holdouts are not correlated`).

For a critical thesis, only `relevance: direct` validates a novelty claim.
METHOD_RELEVANT and ANALOGUE controls are good literature for the Discussion but
cannot make positive-control recall count towards a *direct* gap claim.

Critical theses need at least one holdout, and `holdout_consumed` +
`consumed_at_rules_version` record the moment it was opened. The validator
rejects a consumed holdout with no rules version: a blind test whose result
cannot be tied to a version of the code is not a blind test. Re-tuning after
opening requires a *new* control, not a second look at the same one.

The working sequence is therefore: tune on development controls → freeze a new
`RETRIEVAL_RULES_VERSION` → open the holdouts once → harvest.

### 4.0 The unit of evidence is the paper

Eight supporting sentences in one article are **one supporting paper** with eight
supporting passages. Only paper counts may enter a verdict rule; passage counts are
provenance and are rendered separately in `GAP_MATRIX.md`.

This was a real defect, not a hypothetical: `count_relations` summed rows, and
`classify_relation` keys its resume logic on `(thesis, paper, model)` — so running
once with Gemini and once with Ollama produced two rows per paper and doubled
`n_supports`. Two papers adjudicated twice would have satisfied the three-paper
KNOWN rule. `collapse_to_papers` now reduces to one row per paper before anything
is counted, with a fixed precedence: a human override, then a **verified quote**,
then relation priority, then confidence. Verification has to outrank relation
priority — with the order reversed (as it first was), an unverifiable CONTRADICTS
beat a verified SUPPORTS on priority and was then discarded for lacking a quote,
silently erasing real evidence. A test pins it. Papers whose adjudications
disagreed are counted in `n_model_disagreements` rather than hidden.

### 4.1 The on-topic rule (`Thesis.is_on_topic`)

The first design counted key-term *families* and admitted a paper at two. Measured
against the real corpus, that was useless:

| thesis | papers at "≥2 families" | papers at "primary + 1" |
|---|---:|---:|
| T08 vertical datum | 2 626 | 70 |
| T03 slope as state variable | 1 890 | 113 |
| T12 collocation | 2 515 | 237 |

T08 scored 2 626 because two of its three families were `satellite / gauge /
water level` and `bias / offset / difference` — vocabulary most of this corpus
contains. Two families is trivially satisfiable.

**Rule now in force:** the *first* family is the thesis's distinguishing concept
and must hit, plus at least one other family. `key_terms[0]` is therefore ordered
deliberately, and T04, T11 and T15 were re-ordered so a specific family leads
rather than `water level`, `bias` and `historical` respectively.

Four theses still led with generic families after the first fix and were narrowed
so the primary family names the *combination* the thesis is about, not either half:

| thesis | before | after | what changed |
|---|---:|---:|---|
| T20 connectivity + WSE | 796 | **42** | primary now names the joint use, not "connectivity" |
| T04 uneven lake surface | 784 | **20** | "spatial variability" bound to the water surface |
| T23 persistence | 677 | **168** | "persistent"/"sustained" demoted out of the primary |
| T15 historical validation | 1 501 | **278** | longitudinal-survey vocabulary leads, not "historical" |

Baseline (whole corpus, one pass, `--step coverage --tag baseline`):

```
T09 permanent tide          0    ← absent
T13 seiche / wind setup    11
T04 uneven lake surface    20
T22 prior lake database    22
T20 connectivity + WSE     42
T08 vertical datum         70
…
T19 extent insufficient   616    ← legitimately high: this corpus IS extent mapping
```

That ordering is the expected one: Block C and Block D — the methodological core —
are the thinnest, which is precisely what the harvest has to fix.

### 4.2 Distance gate — not yet calibrated

`DISTANCE_GATE = 0.45` cosine, or `MIN_CHUNKS = 3`. **These numbers are provisional
and must not be defended until the calibration sheet has been labelled.**

Calibration has two halves, and one without the other is misleading:

- **Precision** — `python -m src.paper_3.calibrate sample` writes
  `CALIBRATION_SHEET.csv`, sampling each thesis in three strata: the top of the
  ranking, the middle, and the band just inside the gate (the rows the gate is
  actually deciding). Label the `label` column `relevant` / `partial` /
  `irrelevant`, then `… calibrate report` gives precision per distance bin and
  suggests a gate. Sampling only the top would measure how good the best hits are,
  which nobody doubts.
- **Recall** — `retrieve.positive_control` checks, for every control DOI in
  `theses.yaml`, whether the paper survived each stage, and names *which* stage
  lost it: primary family too narrow / on topic but retrieval missed it /
  retrieved but prefiltered out. Making the first family mandatory raised
  precision; this is what catches it having cut too deep.

Low precision in the **top** stratum is not a gate problem — it means the key
terms or search queries are wrong for that thesis. The report flags those
separately, because the fix is in `theses.yaml`, not in the threshold.

A cheap recall check runs in CI already: every thesis must be `is_on_topic` for
its own statement, rationale and queries. A thesis whose key terms miss its own
wording can never match a paper.

### 4.3 Coverage thresholds

- **absent** — no paper in the corpus is on topic, or OpenAlex reports ≥20
  candidate works while fewer than 3 are present.
- **thin** — coverage ratio < 0.25, or fewer than 5 on-topic papers.
- **adequate** — otherwise.

Printed in `GAP_MATRIX.md` so a reviewer can disagree with them rather than having
to infer them.

Worldwide hits use the **maximum** single-query count for a thesis, not the sum:
a thesis's queries overlap heavily, and summing would inflate the denominator and
make coverage look worse than it is.

### 4.4 What needs a verified quote

SUPPORTS and CONTRADICTS assert what a paper *found*, so they require a quote that
survives `evidence.verify_quote`; a failure sends the row to
`relations_rejected.csv` and it is excluded from every count. METHOD_RELEVANT and
ANALOGUE describe what a paper *is*, which the offered passages already establish,
so a quote failure does not void them.

`FUZZY_THRESHOLD = 0.92` absorbs PDF artefacts (ligatures, soft hyphens, smart
quotes) across a ~200-character sentence while rejecting a paraphrase, which
scores below 0.85. A fuzzy acceptance stores the **passage's** wording, never the
model's, so a near-miss cannot be laundered into the output as a quotation.

---

### 4.5 Citation expansion is one hop, and its direction is recorded

Depth is fixed at 1 and is not a parameter. Two hops through a highly cited
methodological paper reaches most of the corpus, and a neighbourhood that large
has stopped being about any particular thesis. Depth 2 is a separate manual
investigation, not part of the automated evidence base.

Every candidate carries `retrieval_origin` from a closed vocabulary:

| origin | meaning |
|---|---|
| `semantic` | SPECTER2 chunk similarity |
| `references` | appears in the reference list of a semantically retrieved paper — intellectual ancestry |
| `cited_by` | cites a semantically retrieved paper — descent |
| `graph_neighbor` | shares sensors / methods / study area in Neo4j |

There is deliberately no `seed` origin (§4.-1): a positive control that entered the
candidate set because we named it would prove nothing about retrieval. Expansion
starts from what semantic retrieval found, never from `theses.yaml`.

The first labelling of these was **inverted** — backward expansion (papers the
retrieved paper cites) was called `cites_seed` and forward (papers citing it)
`cited_by_seed`, both the wrong way round. Fixed, and pinned by a test.

### 4.6 Three recall figures, not one

"Recovered" hides a distinction that matters for Methods:

| metric | question |
|---|---|
| `control_recall` | did it end up in the candidate set at all |
| `expected_stage_recall` | did the stage we predicted deliver it |
| `semantic_recall` | did embedding search find it unaided |

A control that arrived only through citation expansion scores 1 / 0 / 0. That is
not a failed search, but it does mean semantic retrieval is weaker than the
overall number suggests, and `failure_stage = stage_mismatch` says so. The gate
holds semantic recall to its own threshold (70%, warning) so the honest sentence
— "citation expansion is carrying the method" — cannot hide inside a 95% overall
figure.

### 4.7 Verdicts, and what may become a novelty claim

`KNOWN / PARTIALLY_KNOWN / UNKNOWN / YOUR_CONTRIBUTION` collapsed several very
different situations into one another. The vocabulary is now:

| verdict | meaning |
|---|---|
| `KNOWN` | ≥3 supporting papers, ≥1 a same-system own result |
| `SUPPORTED_BUT_SPARSE` | 1–2 direct papers — real but thin |
| `CONTESTED` | substantive support *and* contradiction |
| `METHOD_ONLY` | the method exists; the hypothesis is untested |
| `ANALOGUE_ONLY` | established elsewhere, never tested in this system |
| `NOT_FOUND` | retrieval validated, too little of anything found |
| `RETRIEVAL_UNVALIDATED` | no control passed — a null result is uninterpretable |
| `RETRIEVAL_INCOMPLETE` | a **verified direct** control for *this* thesis was missed |
| **`CANDIDATE_GAP`** | **retrieval validated, nothing bearing found — the only verdict eligible for a novelty claim** |

**The local rule.** A 90% global recall does not license a gap claim on a thesis
whose own direct control was missed. `RETRIEVAL_INCOMPLETE` fires per thesis and
blocks exactly the null verdicts — CANDIDATE_GAP and NOT_FOUND — while leaving
KNOWN, CONTESTED and the rest alone: papers that *were* found stay found,
whatever else the search missed. So

```
T15  2 verified direct controls  2/2 recovered  → gap evaluation allowed
T16  2 verified direct controls  1/2 recovered  → RETRIEVAL_INCOMPLETE
```

which is a direct answer to the reviewer's question *"how do you know no studies
were found because the literature is absent rather than because your retrieval
failed?"*. Unverified controls neither license nor block: a guess is not evidence
in either direction.

`NOVELTY_ELIGIBLE = (CANDIDATE_GAP,)`, and even a CANDIDATE_GAP is a *candidate*:
**a gap is not a contribution**. What this study adds is a judgement made after
reading the closest precedents, which is why `NOVELTY_STATEMENT.md` keeps
"what the corpus establishes", "where this manuscript contributes" and "claims
this analysis cannot yet make" in separate sections.

A paper whose verified evidence both supports and contradicts a thesis is
`MIXED` — counted towards neither side, because a paper that says both says
neither cleanly. MIXED is a paper-level label only; no model may return it.

### 4.8 The freeze

`--step freeze` writes `RETRIEVAL_MANIFEST.json` + `.md`: git commit and dirty
flag, corpus size, SHA-256 of `papers.parquet`, `references.parquet` and
`theses.yaml`, ChromaDB collection and chunk count, embedding model, every gate
and threshold, and `RETRIEVAL_RULES_VERSION` with its changelog. Re-freezing
appends the previous state to `history` rather than replacing it, and warns when
`theses.yaml` has changed — results from before that point are not comparable.

Run it before the harvest (`--tag pre_harvest`) and again after. The pre-harvest
freeze is already on record: 3 680 papers, 1 313 665 chunks, rules v1.2.0.

---

## 5. Corrections to the original plan, found while building

1. **Citation expansion works, and needs no hash resolver.**
   The plan flagged `data/analytics/references.parquet` (0 rows, reference-hash
   schema) as a blocker. The populated table is
   **`data/parquet/references.parquet`** — 320 633 rows of
   `source_paper_id → referenced_doi`, 4 120 source papers, 64 010 distinct
   referenced DOIs, 1 339 of which are already in the corpus. `corpus_index`
   uses that directly. Roughly two thirds of reference rows carry no DOI; those
   are dropped and counted, never guessed at.

2. **`--step download` needs no second OpenAlex call.** The PDF URL is already
   captured during discovery, so `harvest_ingest` never re-queries the API. This
   also means `src/paper_audit/recover_missing.py` did not have to be modified —
   its DOI-seeded steps are untouched and Article 1's behaviour is unchanged.

3. **All 24 manuscript anchors resolve exactly** against the draft's 77 headings
   (`--step draft`). No thesis points at a section that does not exist.

4. **`_get_paper_ids(fs, limit=500)` must not be used for retrieval.** It applies
   a LIMIT with no ORDER BY, so its result is arbitrary, and it would exclude
   exactly the newly harvested papers. `retrieve.stage_semantic` passes
   `where=None` and filters afterwards in pandas. A test pins this.

5. **`Embedder.embed_query`, not `research_query_service._get_specter2`.** The
   latter calls `model.encode` without the L2 normalisation the index was built
   with.

---

## 6. Protecting Article 1

New papers enter the **main** corpus (same Chroma collection, same Neo4j), tagged
`cohort="paper_3"`:

- `data/paper_3_audit/cohort_paper_3.csv` is the authority on what is new.
- `corpus_index.parquet` carries a `cohort` column, so any query can include or
  exclude them.
- `step_parquet` snapshots `data/analytics/` to `data/analytics.bak_YYYYMMDD`
  before rebuilding, because the dashboard and `research_query_service` read those
  files live.
- `data/paper_audit/frozen_20260612/` and `frozen_presubmission/` are untouched.
  **When `src/paper_audit/recount.py` is next run it must exclude
  `cohort == "paper_3"`**, or Article 1's frozen counts stop being reproducible.

---

## 7. Running it

```bash
docker compose up -d                      # GROBID :8070 + Neo4j :7687
ollama serve &                            # LLM judge for the ingest pipeline

python -m src.paper_3.cli --dry-run                    # see the step graph
python -m src.paper_3.cli --step draft --step queries  # review queries.json first
python -m src.paper_3.cli --step coverage --tag baseline
python -m src.paper_3.cli --step freeze --tag pre_harvest
git commit -am "paper_3: freeze before harvest"        # the manifest records the commit

python -m src.paper_3.cli --phase harvest --workers 3  # ~6-8 h, restartable

# Calibrate BEFORE adjudicating — the gate is provisional until this is done.
python -m src.paper_3.cli --step index --step retrieve
python -m src.paper_3.calibrate sample                 # label the sheet by hand
python -m src.paper_3.calibrate report
# … adjust DISTANCE_GATE or theses.yaml, bump RETRIEVAL_RULES_VERSION, re-freeze …

python -m src.paper_3.cli --phase analyse              # ~2-3 h
```

`--llm ollama` adjudicates locally if the Gemini quota runs out; the output is
marked `adjudication_tier="local_only"`.

---

## 7.5 The acceptance gate — what must hold before the harvest

`python -m src.paper_3.cli --step gate` writes `ACCEPTANCE_GATE.md` and exits
non-zero when a blocking criterion fails. `--force` proceeds anyway and records
that choice in the run manifest.

| criterion | threshold |
|---|---|
| every thesis has a positive control | 24/24 |
| every control read and verified (carries a quote) | 24/24 |
| critical theses have ≥2 verified controls | T09 T10 T14 T15 T16 T17 T22 |
| critical theses have a *hard* control | warn only |
| overall hold-out recall | ≥ 90% |
| no critical thesis at zero recall | 0 |
| calibration sheet labelled | ≥ 30 rows |
| retrieval manifest frozen | required |
| working tree clean at freeze | warn only |

**Status as of the last run: NOT READY.**

```
FAIL  every thesis has a positive control — 9/24; missing T08–T18, T20, T21, T23, T24
FAIL  every control read and verified — 0/24
FAIL  critical theses have >=2 verified controls — all 7 short
FAIL  positive-control recall measured — retrieval not run yet
FAIL  retrieval precision calibrated — sheet not labelled
PASS  retrieval manifest frozen — rules v1.2.0, 3 680 papers
WARN  working tree dirty at freeze time
```

The "critical" set is where a novelty claim is most plausible and the corpus is
thinnest — exactly where an uninterpretable null result would do the most damage.
Each needs an easy control and a hard one (relevance visible only in Methods or
Discussion); recovering only the easy one does not demonstrate recall.

---

## 8. Controls to run before believing the output

**Gap ≠ contribution.** The matrix says where the literature stops. What this
study adds is a separate judgement made after reading the closest precedents, and
`NOVELTY_STATEMENT.md` keeps the two in separate sections on purpose. A thesis
marked YOUR_CONTRIBUTION is a candidate for a novelty claim, not a licence for one.

1. **Positive control (automated).** `retrieve.run` writes `POSITIVE_CONTROL.csv`
   every time. After the harvest, `10.1029/2025gl120832` and `10.1029/2025gl119771`
   must appear in `corpus_index.parquet` and reach `status == "ok"`. If Huang & Gao
   does not land as SUPPORTS or CONTRADICTS on **T07**, the cascade is broken —
   not the literature. Same for Lehnigk on **T02 / T19**. Add a control DOI to any
   thesis that has none: a thesis without a positive control has an untrustworthy
   UNKNOWN.

   **Current control coverage: 9 of 24 theses, 0 verified** (blocks A, B and F;
   5 unique DOIs, all found by external search and none yet read). Blocks
   **C, D, E and G carry no positive control at all**. `THESES_WITHOUT_CONTROLS`
   in `tests/test_paper_3_calibration.py` pins the list so it cannot grow
   unnoticed, and `manually_checked: true` without a `quote` is rejected by the
   validator — a claim of checking is not a check.

   **The arithmetic: 31 control records are required** — 24 theses × ≥1, plus a
   second for each of the 7 critical theses. 11 of the 31 are currently filled,
   so **20 are still to find**, and all 15 listed records still need reading
   (`--step controls` writes `CONTROL_WORKSHEET.csv` with one row per required
   record). Unique papers may be fewer, since one DOI can serve several theses —
   but verification is thesis-specific: a quote, relevance and role established
   for T09 are not automatically valid for T10.

   Closing these holes is the single highest-value thing to do before the
   harvest. The criterion is the researcher's, not a search engine's: *I read the
   abstract or full text and know this paper should be found by this thesis's
   query.* Record `source_of_seed` (`expert_known` > `citation` >
   `external_search`) so a reader can see how much independence each control has.
2. **Negative control.** Add a scratch thesis T99 ("urban traffic congestion
   forecasting from mobile phone data"). It must return `papers_found == 0` or all
   NOT_RELEVANT. A flood paper scoring SUPPORTS on T99 means the prefilter is loose.
3. **Known-answer check.** Read five (thesis, paper) pairs yourself and compare.
   Record the agreement rate in `RUN_MANIFEST.md`.
4. **Quote check.** Grep ten random `supporting_quote` values against their
   `quote_source_file`. Zero misses is the pass bar.
5. **Rejection rate.** `finalize` prints it per model. Above ~15% means the model
   is writing quotes rather than copying them — change model or shorten passages.

---

## 9. Tests

`tests/test_paper_3_{theses,evidence,harvest,prefilter,gap_matrix,classify,cli}.py`
— 154 tests, no GPU, Ollama, Ray or network. The suite as a whole is at 925.

The load-bearing ones are in `test_paper_3_evidence.py`: if `verify_quote` accepts
a paraphrase, a fabricated sentence reaches the manuscript wearing quotation marks.
