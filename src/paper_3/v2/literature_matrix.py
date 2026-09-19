"""The L-series: claims about the literature, resolved by rule, for §7.8.

Three layers that must not be confused:

    V / M / X   what our own data show      — SWOT-DNIPRO evidence matrix, other repo
    T01–T24     what the corpus says        — GAP_MATRIX, per thesis
    L1.x        how the manuscript positions itself against the literature — here

An L row joins the first through `backs` and the second through `thesis_ids`,
and writes nothing back to either. Its status is arithmetic on the gap matrix,
`relations.parquet` and the slice denominators, in a fixed precedence where
retrieval validity always outranks scientific interpretation: a CONTRADICTS on a
thesis whose retrieval was never validated is reported as RETRIEVAL_UNVALIDATED,
not as CONTESTED, because a search that cannot be trusted to find support cannot
be trusted to have found the contradiction either.

The strongest status this module can assign is CANDIDATE_GAP — the gap matrix's
own ceiling. Whether a gap is a contribution is decided by a person who has read
the closest precedents, and the renderer never writes "first" or "novel" for them.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from src.paper_3._utils import OUT_DIR
from src.paper_3.gap_matrix import (
    CANDIDATE_GAP,
    CANONICAL_MATRIX,
    CONTESTED,
    EXPLORATORY_MATRIX,
    RETRIEVAL_INCOMPLETE,
    RETRIEVAL_UNVALIDATED,
)
from src.paper_3.novelty import CONTRIBUTION, ESTABLISHED, PARTIAL as PARTIAL_BAND
from src.paper_3.theses import RELATIONS, Thesis, load_theses
from src.paper_3.v2 import claim_map, slice_screening
from src.paper_3.v2.markers import Marker, render

logger = logging.getLogger(__name__)

CLAIMS_PATH = slice_screening.CLAIMS_PATH

# ── status vocabulary ─────────────────────────────────────────────────────────

PARTIAL = "PARTIAL"
UNRESOLVED = "UNRESOLVED"
SLICE_NOT_RUN = "SLICE_NOT_RUN"
SLICE_UNDERSAMPLED = "SLICE_UNDERSAMPLED"
SLICE_CONTRADICTED = "SLICE_CONTRADICTED"
SLICE_ABSENCE_OBSERVED = "SLICE_ABSENCE_OBSERVED"
SLICE_PREVALENCE_OBSERVED = "SLICE_PREVALENCE_OBSERVED"

#: Precedence for thesis-mode claims. Position is the rule.
THESIS_PRECEDENCE = (RETRIEVAL_INCOMPLETE, RETRIEVAL_UNVALIDATED, CONTESTED, PARTIAL,
                     CANDIDATE_GAP, UNRESOLVED)
SLICE_STATUSES = (SLICE_NOT_RUN, SLICE_UNDERSAMPLED, SLICE_CONTRADICTED,
                  SLICE_ABSENCE_OBSERVED, SLICE_PREVALENCE_OBSERVED)
ALL_STATUSES = THESIS_PRECEDENCE + SLICE_STATUSES

#: Rows that need no PENDING marker in §7.8. Nothing here is a novelty licence.
NO_MARKER_STATUSES = frozenset({CANDIDATE_GAP, SLICE_ABSENCE_OBSERVED,
                                SLICE_PREVALENCE_OBSERVED})

THESIS_MODE, SLICE_ABSENCE, SLICE_PREVALENCE = "thesis", "slice_absence", "slice_prevalence"
EVIDENCE_MODES = (THESIS_MODE, SLICE_ABSENCE, SLICE_PREVALENCE)
GAP_TYPES = ("empirical", "methodological", "conceptual")

#: The three parallel evidence layers, in the order §7.8 presents them. The
#: manuscript proves three different things and each is defended by a different
#: body of literature — and, critically, silence means the opposite thing in
#: layer K than it does in T and D.
#:
#:   (code, heading, question, what an absence of literature would mean)
LAYERS: tuple[tuple[str, str, str, str], ...] = (
    ("K", "What has already been observed at Kakhovka",
     "What has already been studied at this site after the breach?",
     "a gap here is the novelty case"),
    ("T", "What is known about reservoir-to-river hydraulic transitions",
     "Is the physical transition established, and how is it diagnosed?",
     "a gap here is a weakness, not a contribution: this layer should be well populated"),
    ("D", "What the data and methods can measure",
     "What can SWOT, ICESat-2, gauges and legacy surveys measure, and how must "
     "they be harmonised?",
     "a gap here is a weakness, not a contribution: this layer should be well populated"),
)
LAYER_CODES = tuple(code for code, _, _, _ in LAYERS)
#: Layers whose claims must not be read as novelty even at CANDIDATE_GAP.
SUPPORTING_LAYERS = frozenset({"T", "D"})

#: A slice sample smaller than this share of the worldwide hit count cannot
#: carry an absence or prevalence statement, whatever `min_screened` says.
SLICE_MIN_SCREENED_FRACTION = 0.5

#: Words the renderer never emits and claim text may not contain.
FORBIDDEN_NOVELTY_WORDS = ("first", "novel", "unprecedented", "unique", "pioneering")
_FORBIDDEN = re.compile(r"\b(" + "|".join(FORBIDDEN_NOVELTY_WORDS) + r")\b", re.IGNORECASE)

#: Absence / prevalence wording that a thesis-mode claim may not use.
_ABSENCE = re.compile(
    r"\b(none|no study|no studies|no other study|rarely|typically|never|"
    r"do(?:es)? not report|has not been|have not been|not been)\b", re.IGNORECASE)

SECTION = "7.8 Comparison with literature"
MANUSCRIPT_SECTION = SECTION

CANONICAL_STEM = "LITERATURE_MATRIX"
EXPLORATORY_STEM = "LITERATURE_MATRIX_EXPLORATORY_UNVALIDATED"
STUB_FILE = "SECTION_7_8_STUB.md"

#: OPEN markers for L rows are numbered from here so they cannot collide with
#: the audit's own OPEN01… items when the overlay is assembled.
OPEN_MARKER_BASE = 40

_ADEQUACY_RANK = {"absent": 0, "thin": 1, "adequate": 2}

MATRIX_COLUMNS = [
    "claim_id", "layer", "gap_type", "evidence_mode", "slice_id", "target_attribute",
    "claim_text", "thesis_ids", "backs", "thesis_verdicts", "corpus_adequacy",
    "n_papers_bearing", "source_dois", "source_titles",
    "closest_quote_doi", "closest_quote", "closest_quote_section", "n_contradicting",
    "denominator_n_screened", "denominator_n_fulltext", "denominator_n_target",
    "denominator_fraction", "denominator_screened_fraction",
    "validation_status", "status_rationale", "limitations",
    "manuscript_section", "run_id", "generated_at",
]


# ── claim cards ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LiteratureClaim:
    id: str
    layer: str
    gap_type: str
    evidence_mode: str
    claim: str
    thesis_ids: tuple[str, ...]
    backs: tuple[str, ...]
    limitations: str = ""
    slice_id: str = ""
    target_attribute: str = ""
    min_screened: int = 5

    @property
    def is_slice(self) -> bool:
        return self.evidence_mode != THESIS_MODE

    @property
    def layer_rank(self) -> int:
        return LAYER_CODES.index(self.layer) if self.layer in LAYER_CODES else len(LAYER_CODES)


@dataclass(frozen=True)
class UnresolvedCitation:
    text: str
    status: str
    doi: str = ""
    do_not_cite_until_resolved: bool = True
    checked: str = ""
    method: str = ""
    note: str = ""
    extra: dict = field(default_factory=dict)


def _tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


def load_claims(path: Path | None = None
                ) -> tuple[list[LiteratureClaim], list[UnresolvedCitation]]:
    raw = yaml.safe_load(Path(path or CLAIMS_PATH).read_text(encoding="utf-8")) or {}
    claims = [
        LiteratureClaim(
            id=str(c.get("id", "")),
            layer=str(c.get("layer", "") or ""),
            gap_type=str(c.get("gap_type", "")),
            evidence_mode=str(c.get("evidence_mode", THESIS_MODE)),
            claim=" ".join(str(c.get("claim", "")).split()),
            thesis_ids=_tuple(c.get("thesis_ids")),
            backs=_tuple(c.get("backs")),
            limitations=" ".join(str(c.get("limitations", "")).split()),
            slice_id=str(c.get("slice_id", "") or ""),
            target_attribute=str(c.get("target_attribute", "") or ""),
            min_screened=int(c.get("min_screened", 5) or 5),
        )
        for c in (raw.get("claims") or [])
    ]
    claims.sort(key=lambda c: (c.layer_rank, c.id))
    unresolved = [
        UnresolvedCitation(
            text=str(u.get("text", "")),
            status=str(u.get("status", "")),
            doi=str(u.get("doi", "") or ""),
            do_not_cite_until_resolved=bool(u.get("do_not_cite_until_resolved", True)),
            checked=str(u.get("checked", "") or ""),
            method=str(u.get("method", "") or ""),
            note=str(u.get("note", "") or ""),
        )
        for u in (raw.get("unresolved_citations") or [])
    ]
    return claims, unresolved


def validate_claims(
    claims: list[LiteratureClaim],
    theses: list[Thesis],
    draft_claim_ids: list[str] | None,
    slices: dict[str, slice_screening.Slice],
) -> list[str]:
    """Problems with the claim set; empty means well-formed.

    `draft_claim_ids` is the V/M/X set found in the draft. Pass None to skip
    that check (the draft is in the other distro and may not be present).
    """
    problems: list[str] = []
    thesis_ids = {t.id for t in theses}
    seen: set[str] = set()
    for c in claims:
        m = claim_map.CLAIM_ID.fullmatch(f"[{c.id}]")
        if not m or not c.id.startswith("L"):
            problems.append(f"{c.id!r}: id must look like LK1.1")
        if c.layer not in LAYER_CODES:
            problems.append(f"{c.id}: layer {c.layer!r} not in {LAYER_CODES}")
        elif not c.id.startswith(f"L{c.layer}"):
            problems.append(f"{c.id}: id does not carry its layer — expected "
                            f"an L{c.layer} prefix")
        if c.id in seen:
            problems.append(f"{c.id}: duplicate id")
        seen.add(c.id)
        if c.gap_type not in GAP_TYPES:
            problems.append(f"{c.id}: gap_type {c.gap_type!r} not in {GAP_TYPES}")
        if c.evidence_mode not in EVIDENCE_MODES:
            problems.append(f"{c.id}: evidence_mode {c.evidence_mode!r} not in {EVIDENCE_MODES}")
        if not c.claim:
            problems.append(f"{c.id}: empty claim")
        if not c.limitations:
            problems.append(f"{c.id}: empty limitations")
        if not c.thesis_ids:
            problems.append(f"{c.id}: no thesis_ids")
        unknown = sorted(set(c.thesis_ids) - thesis_ids)
        if unknown:
            problems.append(f"{c.id}: unknown thesis ids {unknown}")
        if draft_claim_ids is not None:
            missing = sorted(set(c.backs) - set(draft_claim_ids))
            if missing:
                problems.append(f"{c.id}: backs {missing} not cited in the draft")
        for b in c.backs:
            if not claim_map.CLAIM_ID.fullmatch(f"[{b}]") or b.startswith("L"):
                problems.append(f"{c.id}: backs entry {b!r} is not a V/M/X claim id")
        hit = _FORBIDDEN.search(c.claim)
        if hit:
            problems.append(f"{c.id}: claim contains novelty word {hit.group(0)!r}")
        if c.is_slice:
            if c.slice_id not in slices:
                problems.append(f"{c.id}: unknown slice_id {c.slice_id!r}")
            elif c.target_attribute not in slices[c.slice_id].attribute_names:
                problems.append(f"{c.id}: slice {c.slice_id} has no attribute "
                                f"{c.target_attribute!r}")
            if c.min_screened < 1:
                problems.append(f"{c.id}: min_screened must be >= 1")
        else:
            hit = _ABSENCE.search(c.claim)
            if hit:
                problems.append(f"{c.id}: thesis-mode claim uses absence/prevalence "
                                f"wording {hit.group(0)!r} — make it a slice claim")
            if c.slice_id or c.target_attribute:
                problems.append(f"{c.id}: thesis-mode claim must not name a slice")
    return problems


# ── which gap matrix, and may it be published ─────────────────────────────────

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def matrix_compatible(out_dir: Path) -> tuple[pd.DataFrame | None, bool, list[str]]:
    """The gap matrix belonging to the *current* run, and whether the L layer
    built on it may carry the canonical name.

    A file is compatible only when every row's `run_id` equals the manifest's.
    A canonical matrix left behind by an earlier run is skipped with a reason —
    its existence proves nothing about this run. Eligibility then needs the
    acceptance gate, validated retrieval on every thesis, the frozen rules
    version and the frozen control set all to agree with the manifest.
    """
    from src.paper_3.control_set import CONTROL_SET_FILE
    from src.paper_3.freeze import FREEZE_FILE, RETRIEVAL_RULES_VERSION

    out_dir = Path(out_dir)
    manifest = _read_json(out_dir / "run_manifest.json")
    run_id = str(manifest.get("run_id") or "")
    reasons: list[str] = []
    chosen: pd.DataFrame | None = None
    canonical = False

    for name, is_canonical in ((CANONICAL_MATRIX, True), (EXPLORATORY_MATRIX, False)):
        path = out_dir / name
        if not path.exists():
            continue
        frame = pd.read_parquet(path)
        ids = sorted(set(frame["run_id"].fillna("").astype(str))) if "run_id" in frame else []
        if not run_id or ids != [run_id]:
            reasons.append(f"{name}: stale run_id {ids} ≠ manifest {run_id!r} — skipped")
            continue
        chosen, canonical = frame, is_canonical
        break

    if chosen is None:
        return None, False, reasons

    if not canonical:
        reasons.append("only the exploratory gap matrix belongs to the current run")
    if manifest.get("acceptance_gate_ready") is not True:
        reasons.append("acceptance gate not ready in run_manifest.json")
    validated = chosen.get("retrieval_validated", pd.Series(dtype=bool)).fillna(False).astype(bool)
    if len(validated) == 0 or not bool(validated.all()):
        reasons.append("retrieval not validated for every thesis")
    freeze_record = _read_json(out_dir / FREEZE_FILE)
    if freeze_record.get("retrieval_rules_version") != RETRIEVAL_RULES_VERSION:
        reasons.append(f"frozen retrieval rules {freeze_record.get('retrieval_rules_version')!r} "
                       f"≠ code {RETRIEVAL_RULES_VERSION!r}")
    control = _read_json(out_dir / CONTROL_SET_FILE)
    if not control.get("sha256") or control.get("sha256") != manifest.get("control_set_sha256"):
        reasons.append("control set not frozen, or its sha256 differs from the manifest")

    eligible = canonical and not [r for r in reasons if "skipped" not in r]
    return chosen, eligible, reasons


# ── the arithmetic ────────────────────────────────────────────────────────────

def bearing_papers(relations: pd.DataFrame, thesis_ids: tuple[str, ...]) -> pd.DataFrame:
    """Papers bearing on the theses, one row per paper.

    A verbatim quote is required for SUPPORTS and CONTRADICTS — the relations
    that make a scientific claim — and not for METHOD_RELEVANT or ANALOGUE,
    which characterise the paper and would vanish for no reason without one.
    """
    if relations is None or relations.empty:
        return pd.DataFrame(columns=["key", "doi", "title", "year", "relations",
                                     "quote_doi", "quote", "quote_section", "confidence"])
    sub = relations[relations["thesis_id"].isin(thesis_ids)
                    & relations["relation"].isin(RELATIONS)].copy()
    verified = sub.get("quote_verified", pd.Series(False, index=sub.index)).fillna(False).astype(bool)
    needs_quote = sub["relation"].isin(("SUPPORTS", "CONTRADICTS"))
    sub = sub[~needs_quote | verified]
    if sub.empty:
        return pd.DataFrame(columns=["key", "doi", "title", "year", "relations"])
    doi = sub["doi"].fillna("").astype(str)
    sub["key"] = doi.where(doi != "", sub["paper_id"].astype(str))
    papers = (sub.groupby("key")
              .agg(doi=("doi", "first"), title=("title", "first"), year=("year", "first"),
                   relations=("relation", lambda s: ",".join(sorted(set(s)))))
              .reset_index())
    return papers


def closest_quote(relations: pd.DataFrame, thesis_ids: tuple[str, ...]) -> dict:
    empty = {"doi": "", "quote": "", "section": ""}
    if relations is None or relations.empty:
        return empty
    sub = relations[relations["thesis_id"].isin(thesis_ids)
                    & relations["relation"].isin(RELATIONS)]
    verified = sub.get("quote_verified", pd.Series(False, index=sub.index)).fillna(False).astype(bool)
    quoted = sub["evidence_quote"].fillna("").astype(str).str.strip() != ""
    sub = sub[verified & quoted]
    if sub.empty:
        return empty
    best = sub.loc[sub["confidence"].fillna(0).astype(float).idxmax()]
    return {"doi": str(best.get("doi") or ""),
            "quote": str(best.get("evidence_quote") or "").strip(),
            "section": str(best.get("quote_section") or "")}


def thesis_status(rows: pd.DataFrame, missing: set[str]) -> tuple[str, str]:
    """Apply THESIS_PRECEDENCE. Retrieval validity first, always."""
    if missing:
        return RETRIEVAL_UNVALIDATED, f"{sorted(missing)} absent from the gap matrix"
    verdicts = dict(zip(rows["thesis_id"], rows["verdict"]))
    for v in (RETRIEVAL_INCOMPLETE, RETRIEVAL_UNVALIDATED):
        hit = sorted(t for t, x in verdicts.items() if x == v)
        if hit:
            return v, f"{', '.join(hit)}: {v}"
    contra = rows.get("n_contradicting_papers", pd.Series(0, index=rows.index)).fillna(0).astype(int)
    hit = sorted(t for t, n, x in zip(rows["thesis_id"], contra, rows["verdict"])
                 if n > 0 or x == CONTESTED)
    if hit:
        return CONTESTED, f"{', '.join(hit)}: contradicting paper(s) in the corpus"
    hit = sorted(t for t, x in verdicts.items() if x in (ESTABLISHED | PARTIAL_BAND))
    if hit:
        return PARTIAL, f"{', '.join(hit)}: literature already bears on this ({', '.join(sorted({verdicts[t] for t in hit}))})"
    eligible = rows.get("novelty_eligible", pd.Series(False, index=rows.index)).fillna(False).astype(bool)
    if verdicts and all(x in CONTRIBUTION for x in verdicts.values()) and bool(eligible.all()):
        return CANDIDATE_GAP, "every thesis CANDIDATE_GAP and novelty-eligible; a gap, not yet a contribution"
    return UNRESOLVED, f"verdict mix not interpretable: {', '.join(f'{t}={x}' for t, x in sorted(verdicts.items()))}"


def slice_status(claim: LiteratureClaim, denominators: pd.DataFrame | None
                 ) -> tuple[str, str, dict]:
    nums = {"n_screened": 0, "n_fulltext": 0, "n_target": 0, "fraction": 0.0,
            "screened_fraction": 0.0, "worldwide": 0}
    if denominators is None or denominators.empty \
            or claim.slice_id not in set(denominators["slice_id"]):
        return SLICE_NOT_RUN, f"slice {claim.slice_id} has not been screened", nums
    row = denominators[denominators["slice_id"] == claim.slice_id].iloc[0]
    col = f"{claim.target_attribute}_n"
    nums.update({
        "n_screened": int(row.get("n_screened", 0) or 0),
        "n_fulltext": int(row.get("n_fulltext", 0) or 0),
        "n_target": int(row.get(col, 0) or 0),
        "screened_fraction": float(row.get("screened_fraction", 0.0) or 0.0),
        "worldwide": int(row.get("worldwide_count", 0) or 0),
    })
    nums["fraction"] = round(nums["n_target"] / nums["n_screened"], 4) if nums["n_screened"] else 0.0
    if nums["n_screened"] < claim.min_screened:
        return SLICE_UNDERSAMPLED, (f"{nums['n_screened']} screened < min_screened "
                                    f"{claim.min_screened}"), nums
    if nums["worldwide"] and nums["screened_fraction"] < SLICE_MIN_SCREENED_FRACTION:
        return SLICE_UNDERSAMPLED, (f"screened {nums['n_screened']} of {nums['worldwide']} "
                                    f"worldwide ({nums['screened_fraction']:.2f} < "
                                    f"{SLICE_MIN_SCREENED_FRACTION})"), nums
    if claim.evidence_mode == SLICE_ABSENCE:
        if nums["n_target"] > 0:
            return SLICE_CONTRADICTED, (f"{nums['n_target']} of {nums['n_screened']} screened "
                                        f"works hit {claim.target_attribute}"), nums
        return SLICE_ABSENCE_OBSERVED, (f"0 of {nums['n_screened']} screened works hit "
                                        f"{claim.target_attribute} (keyword screen)"), nums
    return SLICE_PREVALENCE_OBSERVED, (f"{nums['n_target']} of {nums['n_screened']} screened "
                                       f"works hit {claim.target_attribute} "
                                       f"({nums['fraction']:.2f})"), nums


def build(
    claims: list[LiteratureClaim],
    matrix: pd.DataFrame,
    relations: pd.DataFrame | None,
    denominators: pd.DataFrame | None = None,
    run_id: str = "",
) -> pd.DataFrame:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for c in claims:
        sub = matrix[matrix["thesis_id"].isin(c.thesis_ids)] if not matrix.empty else matrix
        missing = set(c.thesis_ids) - set(sub["thesis_id"]) if not sub.empty else set(c.thesis_ids)
        status, rationale = thesis_status(sub, missing)
        nums = {"n_screened": 0, "n_fulltext": 0, "n_target": 0, "fraction": 0.0,
                "screened_fraction": 0.0}
        if c.is_slice and status not in (RETRIEVAL_INCOMPLETE, RETRIEVAL_UNVALIDATED):
            status, rationale, nums = slice_status(c, denominators)
        elif c.is_slice:
            rationale += " — slice denominators not consulted until retrieval is validated"

        papers = bearing_papers(relations, c.thesis_ids)
        quote = closest_quote(relations, c.thesis_ids)
        contra = int(sub.get("n_contradicting_papers", pd.Series(dtype=int)).fillna(0).sum()) \
            if not sub.empty else 0
        adequacy = ""
        if not sub.empty and "corpus_adequacy" in sub:
            adequacy = min(sub["corpus_adequacy"].fillna("thin"),
                           key=lambda a: _ADEQUACY_RANK.get(a, 1))
        rows.append({
            "claim_id": c.id,
            "layer": c.layer,
            "gap_type": c.gap_type,
            "evidence_mode": c.evidence_mode,
            "slice_id": c.slice_id,
            "target_attribute": c.target_attribute,
            "claim_text": c.claim,
            "thesis_ids": "; ".join(c.thesis_ids),
            "backs": "; ".join(c.backs),
            "thesis_verdicts": "; ".join(
                f"{t}:{v}" for t, v in zip(sub["thesis_id"], sub["verdict"])) if not sub.empty else "",
            "corpus_adequacy": adequacy,
            "n_papers_bearing": int(len(papers)),
            "source_dois": "; ".join(d for d in papers["doi"].fillna("").astype(str) if d) if len(papers) else "",
            "source_titles": " | ".join(str(t) for t in papers["title"].fillna("")) if len(papers) else "",
            "closest_quote_doi": quote["doi"],
            "closest_quote": quote["quote"],
            "closest_quote_section": quote["section"],
            "n_contradicting": contra,
            "denominator_n_screened": nums["n_screened"],
            "denominator_n_fulltext": nums["n_fulltext"],
            "denominator_n_target": nums["n_target"],
            "denominator_fraction": nums["fraction"],
            "denominator_screened_fraction": nums["screened_fraction"],
            "validation_status": status,
            "status_rationale": rationale,
            "limitations": c.limitations,
            "manuscript_section": MANUSCRIPT_SECTION,
            "run_id": run_id,
            "generated_at": generated_at,
        })
    return pd.DataFrame(rows, columns=MATRIX_COLUMNS)


# ── rendering ─────────────────────────────────────────────────────────────────

_EXPLORATORY_BANNER = (
    "> **EXPLORATORY — NOT A FINISHED LITERATURE POSITIONING.** This matrix was "
    "produced before the acceptance gate passed. No row here is eligible to become "
    "a novelty claim, and no row may be cited as evidence that a gap exists in the "
    "literature.")


def render_md(frame: pd.DataFrame, unresolved: list[UnresolvedCitation],
              eligible: bool, reasons: list[str],
              denominators: pd.DataFrame | None = None) -> str:
    lines = ["# Literature Evidence Matrix (L-series) — Paper 3", ""]
    if not frame.empty:
        head = frame.iloc[0]
        lines += [f"Generated {head['generated_at']} · run `{head['run_id'] or 'unset'}`", ""]
    lines += [
        "Claims about the literature, resolved by fixed rules over the gap matrix, "
        "paper-level relations and slice denominators — never by a model. "
        "Retrieval validity outranks interpretation in every status; the strongest "
        "status is CANDIDATE_GAP, and a gap is not a contribution.",
        "",
    ]
    if not eligible:
        lines += [_EXPLORATORY_BANNER, ">", "> Reasons:"]
        lines += [f"> - {r}" for r in reasons] or ["> - (none recorded)"]
        lines.append("")

    by_layer: dict[str, list] = {}
    for row in frame.itertuples():
        by_layer.setdefault(row.layer, []).append(row)
    for code, heading, question, meaning in LAYERS:
        rows = by_layer.get(code, [])
        if not rows:
            continue
        lines += [
            f"### {code}. {heading}",
            "",
            f"*{question}* — {meaning}.",
            "",
            "| Claim | Mode | Status | Theses | Papers | Denominator | Closest quote DOI |",
            "|---|---|---|---|---:|---|---|",
        ]
        for r in rows:
            den = ""
            if r.evidence_mode != THESIS_MODE:
                den = (f"{r.denominator_n_target}/{r.denominator_n_screened} "
                       f"({r.denominator_n_fulltext} full text)")
            doi = r.closest_quote_doi
            doi_md = f"[{doi}](https://doi.org/{doi})" if str(doi).startswith("10.") else ""
            lines.append(f"| {r.claim_id} | {r.evidence_mode} | {r.validation_status} | "
                         f"{r.thesis_ids} | {r.n_papers_bearing} | {den} | {doi_md} |")
        lines.append("")

    for r in frame.itertuples():
        lines += [
            f"## {r.claim_id} — layer {r.layer}, {r.gap_type}",
            "",
            f"> {r.claim_text}",
            "",
            f"- status: **{r.validation_status}** — {r.status_rationale}",
            f"- theses: {r.thesis_verdicts or r.thesis_ids}",
            f"- backs (own-data claims): {r.backs}",
            f"- papers bearing: {r.n_papers_bearing}"
            + (f" — {r.source_dois}" if r.source_dois else ""),
        ]
        if r.closest_quote:
            lines.append(f"- closest verified quote [doi:{r.closest_quote_doi}]"
                         + (f" ({r.closest_quote_section})" if r.closest_quote_section else "")
                         + f": \"{r.closest_quote}\"")
        lines += [f"- limitations: {r.limitations}", ""]

    lines += ["## Slice denominators", ""]
    if denominators is None or denominators.empty:
        lines.append("_No slice has been screened (run `--step screen` after the harvest)._")
    else:
        attr_cols = [c for c in denominators.columns
                     if c.endswith("_n") and c != "n_off_topic"]
        lines += [
            "`Screened` counts only works that hit the slice's own topic terms; "
            "`off topic` is what the Boolean query returned that the slice is not "
            "about, kept in SLICE_SCREENING.csv rather than dropped.",
            "",
            "| Slice | Worldwide | Candidates | Off topic | Screened | Full text | " +
            " | ".join(c[:-2] for c in attr_cols) + " |",
            "|---|---:|---:|---:|---:|---:|" + "---:|" * len(attr_cols)]
        for r in denominators.itertuples():
            vals = " | ".join(str(int(getattr(r, c, 0) or 0)) for c in attr_cols)
            lines.append(f"| {r.slice_id} | {r.worldwide_count} | {r.n_candidates} | "
                         f"{getattr(r, 'n_off_topic', 0)} | {r.n_screened} | "
                         f"{r.n_fulltext} | {vals} |")
    lines.append("")

    lines += ["## Citations attempted and unresolved", ""]
    if not unresolved:
        lines.append("_None._")
    for u in unresolved:
        tail = f" → doi:{u.doi}" if u.doi else ""
        lines.append(f"- \"{u.text}\" — **{u.status}**{tail}; checked {u.checked}; {u.method}"
                     + (f"; {u.note}" if u.note else "")
                     + ("; do not cite until resolved" if u.do_not_cite_until_resolved else ""))
    lines.append("")
    return "\n".join(lines)


_NEXT_STEP = {
    RETRIEVAL_UNVALIDATED: "verify a hold-out positive control for {theses}",
    RETRIEVAL_INCOMPLETE: "recover the missed verified direct control for {theses}",
    CONTESTED: "read the contradicting paper(s) for {theses} and narrow the claim",
    PARTIAL: "narrow the claim to what the literature on {theses} leaves open",
    UNRESOLVED: "re-run the matrix for {theses}; the verdict mix is not interpretable",
    SLICE_NOT_RUN: "run --step screen for {slice} after the harvest",
    SLICE_UNDERSAMPLED: "deepen the {slice} harvest, or justify a lower min_screened",
    SLICE_CONTRADICTED: "read the works that hit the target attribute in {slice}; the "
                        "absence claim is false as worded",
}
_BLOCKED = frozenset({RETRIEVAL_UNVALIDATED, RETRIEVAL_INCOMPLETE, SLICE_NOT_RUN,
                      SLICE_UNDERSAMPLED})

#: What to do when a supporting layer turns up empty. This is not a novelty
#: finding: layers T and D exist to be populated, and silence in them means the
#: physical interpretation or the methodology defence has no literature behind it.
_SUPPORTING_GAP_STEP = ("populate layer {layer}: supporting literature was not found, "
                        "which weakens the defence rather than establishing novelty")


def _attribute_label(attr: str) -> str:
    return attr.replace("_", " ")


def needs_marker(layer: str, status: str) -> bool:
    """Whether a row still carries an open item.

    A CANDIDATE_GAP in layer K is the novelty case and needs no marker. The same
    verdict in T or D means the literature that should support the argument was
    not found, which is a problem to fix rather than a result to report.
    """
    if status not in NO_MARKER_STATUSES:
        return True
    return layer in SUPPORTING_LAYERS and status == CANDIDATE_GAP


def render_section_7_8(frame: pd.DataFrame) -> str:
    """The §7.8 block the v2 overlay appends. Deterministic; no dates, no model.

    Every paragraph ends in its `[L1.x]` tag; every row that is not yet
    supportable is followed by an OPEN marker saying what would make it so.
    """
    lines = [
        f"## {SECTION}",
        "",
        "Positioning statements below are constructed from the L-series literature "
        "evidence matrix; each is traceable to its theses, to paper-level relations "
        "and to DOIs with verified passages. No priority is asserted here: where a "
        "claim is not yet supportable, the marker says why.",
        "",
        "The section is organised in three parallel layers, because the study proves "
        "three different things and each rests on a different body of literature. An "
        "absence in the Kakhovka layer is what this study contributes; an absence in "
        "the transition or the data layer is a weakness in its argument, not a finding.",
        "",
    ]
    by_layer: dict[str, list] = {}
    for row in frame.itertuples():
        by_layer.setdefault(row.layer, []).append(row)

    i = 0
    for code, heading, question, meaning in LAYERS:
        rows = by_layer.pop(code, [])
        if not rows:
            continue
        lines += [f"### {code}. {heading}", "", f"*{question}* — {meaning}.", ""]
        i = _render_layer(lines, rows, i)
    for code, rows in sorted(by_layer.items()):
        lines += [f"### {code or 'unassigned'}", ""]
        i = _render_layer(lines, rows, i)

    text = "\n".join(lines)
    hit = _FORBIDDEN.search(text)
    if hit:
        raise ValueError(f"§7.8 renderer emitted a novelty word {hit.group(0)!r}")
    return text


def _render_layer(lines: list[str], rows: list, i: int) -> int:
    for r in rows:
        i += 1
        status = r.validation_status
        if r.evidence_mode == THESIS_MODE:
            para = r.claim_text
            if r.closest_quote_doi:
                para += f" Closest precedent in the corpus: [doi:{r.closest_quote_doi}]."
        else:
            label = _attribute_label(r.target_attribute)
            if status == SLICE_NOT_RUN or status in (RETRIEVAL_UNVALIDATED, RETRIEVAL_INCOMPLETE):
                para = (f"Boolean slice {r.slice_id} has not yet been screened under "
                        f"validated retrieval; the statement “{r.claim_text}” is withheld.")
            else:
                n_abs = r.denominator_n_screened - r.denominator_n_fulltext
                para = (f"Among {r.denominator_n_screened} works recovered by Boolean slice "
                        f"{r.slice_id} and screened ({r.denominator_n_fulltext} in full text, "
                        f"{n_abs} by abstract only), {r.denominator_n_target} hit the "
                        f"vocabulary for {label}.")
                if status in NO_MARKER_STATUSES:
                    para += f" {r.claim_text}"
                elif status == SLICE_UNDERSAMPLED:
                    para += (" The screened sample is too small relative to the worldwide "
                             "count for a positioning statement; it is withheld.")
                elif status == SLICE_CONTRADICTED:
                    para += " The absence statement drafted for this slice does not hold as worded."
        lines += [f"{para} [{r.claim_id}]", ""]

        if needs_marker(r.layer, status):
            theses = r.thesis_ids.replace("; ", ", ")
            supporting_gap = status == CANDIDATE_GAP and r.layer in SUPPORTING_LAYERS
            step = (_SUPPORTING_GAP_STEP if supporting_gap
                    else _NEXT_STEP.get(status, "re-run --step literature"))
            title = (f"§7.8 layer {r.layer} claim {r.claim_id} has no supporting "
                     f"literature ({status})" if supporting_gap
                     else f"§7.8 literature claim {r.claim_id} is not yet "
                          f"supportable ({status})")
            marker = Marker(
                id=f"OPEN{OPEN_MARKER_BASE + i:02d}",
                title=title,
                fields={
                    "backs": r.backs.replace("; ", ", "),
                    "section": SECTION,
                    "next_step": step.format(theses=theses,
                                             slice=r.slice_id or "the slice",
                                             layer=r.layer),
                    "status": "blocked" if status in _BLOCKED else "not_started",
                    "blocks_submission": "yes",
                },
            )
            lines += [render(marker), ""]
    return i


# ── the step ──────────────────────────────────────────────────────────────────

def run(out_dir: Path | None = None, theses: list[Thesis] | None = None) -> Path:
    target = Path(out_dir) if out_dir else OUT_DIR
    theses = theses if theses is not None else load_theses()
    claims, unresolved = load_claims()
    slices = slice_screening.load_slices()

    draft_ids: list[str] | None = None
    try:
        draft_ids = claim_map.claim_ids(claim_map.load_draft())
    except FileNotFoundError as exc:
        logger.warning("%s — `backs` not checked against the draft", exc)
    problems = validate_claims(claims, theses, draft_ids, slices)
    problems += slice_screening.validate_slices(slices)
    if problems:
        raise ValueError("literature_claims.yaml: " + "; ".join(problems))

    matrix, eligible, reasons = matrix_compatible(target)
    if matrix is None:
        raise FileNotFoundError(
            "no gap matrix belongs to the current run_id — run --step matrix first"
            + (f" ({'; '.join(reasons)})" if reasons else ""))

    relations_path = target / "relations.parquet"
    relations = pd.read_parquet(relations_path) if relations_path.exists() else None
    den_path = target / slice_screening.DENOMINATORS_CSV
    denominators = pd.read_csv(den_path) if den_path.exists() else None
    run_id = str(_read_json(target / "run_manifest.json").get("run_id") or "")

    frame = build(claims, matrix, relations, denominators, run_id=run_id)
    stem = CANONICAL_STEM if eligible else EXPLORATORY_STEM
    target.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target / f"{stem}.parquet", index=False)
    frame.to_csv(target / f"{stem}.csv", index=False)
    md_path = target / f"{stem}.md"
    md_path.write_text(render_md(frame, unresolved, eligible, reasons, denominators),
                       encoding="utf-8")
    (target / STUB_FILE).write_text(render_section_7_8(frame), encoding="utf-8")

    logger.info("Literature matrix → %s", md_path.name)
    logger.info("  statuses: %s", frame["validation_status"].value_counts().to_dict())
    if not eligible:
        logger.warning("  NOT publication-eligible: %s", "; ".join(reasons))
    return md_path
