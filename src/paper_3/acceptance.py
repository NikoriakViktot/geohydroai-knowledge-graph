"""The acceptance gate — what must be true before the full harvest is worth running.

The pipeline being technically ready is not a reason to run it. A gap analysis
whose retrieval has never been shown to work produces `NOT_FOUND` rows that look
exactly like findings, and the difference only surfaces when a reviewer names a
paper the search should have caught.

`check()` returns a pass/fail per criterion with the numbers behind it. Nothing
here blocks anything by force — `--step gate` reports, and the decision to
proceed stays with a person — but the report is written into the published
bundle, so a harvest run over a failing gate is on the record as such.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.paper_3._utils import OUT_DIR
from src.paper_3.theses import CRITICAL_THESES, Thesis, load_theses

logger = logging.getLogger(__name__)

#: Overall share of positive controls that must be recovered by retrieval.
MIN_OVERALL_RECALL = 0.90

#: Critical theses need two controls: one obvious, one that only a working
#: retrieval finds. A pipeline that recovers only the easy one has not been shown
#: to work where it matters.
MIN_CONTROLS_CRITICAL = 2
MIN_CONTROLS_ORDINARY = 1

#: Semantic retrieval is the stage the method claims to rest on, so it is held to
#: its own figure rather than hidden inside the overall number. Below this,
#: the honest description is "citation expansion did the work".
MIN_SEMANTIC_RECALL = 0.70


@dataclass(frozen=True)
class Criterion:
    name: str
    passed: bool
    detail: str
    blocking: bool = True

    @property
    def mark(self) -> str:
        return "PASS" if self.passed else ("FAIL" if self.blocking else "WARN")


def _control_coverage(theses: list[Thesis]) -> tuple[list[Criterion], dict]:
    """Do the theses carry enough verified controls to be testable at all?"""
    verified_counts = {t.id: len(t.verified_controls) for t in theses}
    any_counts = {t.id: len(t.positive_controls) for t in theses}

    missing_any = sorted(t.id for t in theses if not t.positive_controls)
    unverified = sorted(t.id for t in theses
                        if t.positive_controls and not t.verified_controls)
    thin_critical = sorted(
        t.id for t in theses
        if t.is_critical and len(t.verified_controls) < MIN_CONTROLS_CRITICAL)

    hard_missing = sorted(
        t.id for t in theses
        if t.is_critical and not any(c.difficulty == "hard" and c.verified
                                     for c in t.positive_controls))
    holdout_missing = sorted(
        t.id for t in theses if t.is_critical and not t.holdout_controls)
    # For a critical thesis a method or analogue control is fine literature but
    # cannot validate a DIRECT novelty claim — only a direct control can.
    direct_holdout_missing = sorted(
        t.id for t in theses
        if t.is_critical and not any(c.is_holdout and c.is_direct and c.verified
                                     for c in t.positive_controls))
    consumed_early = sorted(
        f"{t.id}/{c.doi}" for t in theses for c in t.holdout_controls
        if c.holdout_consumed)

    from src.paper_3.control_plan import correlated_holdouts

    correlated = correlated_holdouts(theses)

    criteria = [
        Criterion(
            "every thesis has a positive control",
            not missing_any,
            f"{len(theses) - len(missing_any)}/{len(theses)} theses have one"
            + (f"; missing: {', '.join(missing_any)}" if missing_any else "")),
        Criterion(
            "every control has been read and verified",
            not unverified and not missing_any,
            f"{sum(1 for t in theses if t.verified_controls)}/{len(theses)} theses "
            f"have a verified control"
            + (f"; unverified: {', '.join(unverified)}" if unverified else "")),
        Criterion(
            f"critical theses have >={MIN_CONTROLS_CRITICAL} verified controls",
            not thin_critical,
            f"critical: {', '.join(CRITICAL_THESES)}"
            + (f"; short: {', '.join(thin_critical)}" if thin_critical else "")),
        Criterion(
            "critical theses have a holdout control",
            not holdout_missing,
            "a control reserved from tuning, opened once at the end"
            + (f"; missing: {', '.join(holdout_missing)}" if holdout_missing else "")),
        Criterion(
            "critical theses have a verified DIRECT holdout",
            not direct_holdout_missing,
            "a method or analogue control cannot validate a direct novelty claim"
            + (f"; missing: {', '.join(direct_holdout_missing)}"
               if direct_holdout_missing else "")),
        Criterion(
            "no holdout has been consumed yet",
            not consumed_early,
            "a consumed holdout cannot validate a later round of tuning"
            + (f"; consumed: {', '.join(consumed_early)}" if consumed_early else ""),
            blocking=False),
        Criterion(
            "critical holdouts are not correlated",
            not correlated,
            "one paper acting as the sole holdout for several critical theses "
            "measures one thing while appearing to measure several"
            + (f"; shared: {correlated}" if correlated else "")),
        Criterion(
            "critical theses have a hard control",
            not hard_missing,
            "a control whose relevance is only visible in Methods or Discussion"
            + (f"; missing: {', '.join(hard_missing)}" if hard_missing else ""),
            blocking=False),
    ]
    return criteria, {"verified": verified_counts, "any": any_counts}


def _recall(control_results: pd.DataFrame | None,
            theses: list[Thesis]) -> list[Criterion]:
    """Did retrieval actually recover the controls it was never told about?"""
    if control_results is None or control_results.empty:
        return [Criterion(
            "positive-control recall measured",
            False,
            "POSITIVE_CONTROL.csv not found — run `--step retrieve` first")]

    in_corpus = control_results[control_results["in_corpus"]]
    if in_corpus.empty:
        return [Criterion(
            "positive-control recall measured", False,
            f"none of the {len(control_results)} controls is in the corpus yet")]

    def flag(column):
        if column not in in_corpus:
            return pd.Series(False, index=in_corpus.index)
        return in_corpus[column].fillna(False).astype(bool)

    recovered = flag("recovered")
    overall = recovered.mean()
    semantic = flag("recovered_semantically").mean()
    at_stage = flag("recovered_at_expected_stage").mean()

    critical = in_corpus[flag("is_critical")]
    zero_recall = sorted(
        tid for tid, group in critical.groupby("thesis_id")
        if not group["recovered"].fillna(False).astype(bool).any())

    # The rule that protects every future gap claim, thesis by thesis.
    verified_direct = flag("is_direct") & flag("manually_checked")
    incomplete = sorted(
        {str(r.thesis_id) for r in
         in_corpus[verified_direct & ~recovered].itertuples()})

    stages = (in_corpus.loc[~recovered, "failure_stage"]
              .value_counts().to_dict())

    return [
        Criterion(
            f"overall control recall >= {MIN_OVERALL_RECALL:.0%}",
            overall >= MIN_OVERALL_RECALL,
            f"{int(recovered.sum())}/{len(in_corpus)} recovered ({overall:.0%})"
            + (f"; failures by stage: {stages}" if stages else "")),
        Criterion(
            f"semantic-only recall >= {MIN_SEMANTIC_RECALL:.0%}",
            semantic >= MIN_SEMANTIC_RECALL,
            f"{semantic:.0%} found by embedding search alone; {at_stage:.0%} at "
            f"the stage they were expected at. A large gap between these and the "
            f"overall figure means citation expansion is carrying the method.",
            blocking=False),
        Criterion(
            "no critical thesis has zero recall",
            not zero_recall,
            "critical theses whose every control was missed: "
            + (", ".join(zero_recall) if zero_recall else "none")),
        Criterion(
            "every verified direct control recovered",
            not incomplete,
            "theses that may NOT be assigned CANDIDATE_GAP until fixed: "
            + (", ".join(incomplete) if incomplete else "none")),
    ]


def _calibration(out_dir: Path) -> list[Criterion]:
    sheet = out_dir / "CALIBRATION_SHEET.csv"
    if not sheet.exists():
        return [Criterion("retrieval precision calibrated", False,
                          "CALIBRATION_SHEET.csv not found — "
                          "run `python -m src.paper_3.calibrate sample`")]
    try:
        frame = pd.read_csv(sheet).fillna({"label": ""})
    except Exception as exc:
        return [Criterion("retrieval precision calibrated", False,
                          f"calibration sheet unreadable: {exc}")]

    from src.paper_3.calibrate import LABELS
    labelled = int(frame["label"].isin(LABELS).sum())
    return [Criterion(
        "retrieval precision calibrated", labelled >= 30,
        f"{labelled}/{len(frame)} rows labelled "
        f"(30 is the minimum for the curve to mean anything)")]


def _freeze(out_dir: Path) -> list[Criterion]:
    from src.paper_3.freeze import FREEZE_FILE

    path = out_dir / FREEZE_FILE
    if not path.exists():
        return [Criterion("retrieval manifest frozen", False,
                          "RETRIEVAL_MANIFEST.json not found — "
                          "run `--step freeze`")]
    import json
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [Criterion("retrieval manifest frozen", False,
                          f"manifest unreadable: {exc}")]
    return [
        Criterion("retrieval manifest frozen", True,
                  f"rules v{manifest.get('retrieval_rules_version')}, "
                  f"commit {manifest.get('git_short') or 'unknown'}, "
                  f"{manifest.get('corpus', {}).get('n_papers_parquet')} papers"),
        Criterion("working tree committed at freeze time",
                  not manifest.get("git_dirty", True),
                  "a dirty tree means the commit hash does not identify the code "
                  "that ran", blocking=False),
    ]


def _control_set(out_dir: Path, theses: list[Thesis]) -> list[Criterion]:
    """Is the control set frozen, and has it moved since?"""
    from src.paper_3 import control_set

    frozen = control_set.load(out_dir)
    if not frozen.get("frozen"):
        return [Criterion(
            "control set frozen", False,
            "run `--step freeze-controls` before tuning retrieval, or a hard "
            "holdout can be swapped for an easier one and nobody will know")]

    unchanged, drift = control_set.status(theses, out_dir)
    return [Criterion(
        "control set frozen and unchanged", unchanged,
        f"frozen {frozen.get('frozen_at')} under rules "
        f"v{frozen.get('retrieval_rules_version_before_tuning')}, "
        f"{frozen.get('n_controls')} controls"
        + (f"; drift: {'; '.join(drift[:4])}" if drift else ""))]


def check(out_dir: Path | None = None,
          theses: list[Thesis] | None = None) -> tuple[list[Criterion], bool]:
    """Evaluate every criterion. Returns (criteria, ready_to_harvest)."""
    target = Path(out_dir) if out_dir else OUT_DIR
    theses = theses if theses is not None else load_theses()

    control_path = target / "POSITIVE_CONTROL.csv"
    control_results = None
    if control_path.exists():
        try:
            control_results = pd.read_csv(control_path)
        except Exception as exc:
            logger.warning("POSITIVE_CONTROL.csv unreadable: %s", exc)

    criteria: list[Criterion] = []
    coverage_criteria, _stats = _control_coverage(theses)
    criteria += coverage_criteria
    criteria += _recall(control_results, theses)
    criteria += _calibration(target)
    criteria += _control_set(target, theses)
    criteria += _freeze(target)

    ready = all(c.passed for c in criteria if c.blocking)
    return criteria, ready


def render_md(criteria: list[Criterion], ready: bool) -> str:
    lines = [
        "# Acceptance gate — Paper 3",
        "",
        ("**READY** — every blocking criterion passes." if ready else
         "**NOT READY** — at least one blocking criterion fails. "
         "A harvest run now will produce `RETRIEVAL_UNVALIDATED` rows that must "
         "not be read as literature gaps."),
        "",
        "| | criterion | detail |",
        "|---|---|---|",
    ]
    for c in criteria:
        lines.append(f"| {c.mark} | {c.name} | {c.detail} |")
    lines += [
        "",
        "## Why this exists",
        "",
        "Without a hold-out positive control, `0 papers found` has two readings —",
        "a gap in the literature, or a failure of retrieval — and they are not",
        "distinguishable from the output. The gate is what keeps the second from",
        "being published as the first.",
        "",
        "A control is held out: it is ingested into the corpus like any other",
        "paper, and retrieval must find it unaided. Injecting it would make recall",
        "1 by construction and measure nothing.",
        "",
    ]
    return "\n".join(lines) + "\n"


def run(out_dir: Path | None = None, theses: list[Thesis] | None = None) -> bool:
    """Write ACCEPTANCE_GATE.md and log the outcome. Returns readiness."""
    target = Path(out_dir) if out_dir else OUT_DIR
    criteria, ready = check(target, theses)
    target.mkdir(parents=True, exist_ok=True)
    (target / "ACCEPTANCE_GATE.md").write_text(
        render_md(criteria, ready), encoding="utf-8")

    for c in criteria:
        log = logger.info if c.passed else (
            logger.error if c.blocking else logger.warning)
        log("[%s] %s — %s", c.mark, c.name, c.detail)
    if ready:
        logger.info("Acceptance gate: READY to harvest")
    else:
        logger.error("Acceptance gate: NOT READY — see ACCEPTANCE_GATE.md")
    return ready
