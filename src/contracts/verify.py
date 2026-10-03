"""Contracts of the human verification layer (verify.human_check, migration 0006) and of the
paper inspection endpoints the verification UI reads (regions, file links).

A check is a person's judgement of one piece of parsed or extracted data. It is written only
with the 'verify' scope, which is given to people, never to agents (AGENT_RULES R-SCI-6).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from src.contracts.api import Provenance

TargetKind = Literal["paper", "section", "region", "formula", "table", "figure", "metric_fact", "entity_edge",
                     "claim_evidence", "thesis", "reference", "location"]
Verdict = Literal["correct", "incorrect", "partial", "unsure"]
Problem = Literal["text_missing", "text_garbled", "table_broken", "formula_broken", "crop_wrong", "value_wrong",
                  "unit_wrong", "entity_wrong", "location_wrong", "metadata_wrong", "evidence_not_supporting", "other"]

VERIFY_TARGET_KINDS: tuple[str, ...] = get_args(TargetKind)
VERIFY_VERDICTS: tuple[str, ...] = get_args(Verdict)
VERIFY_PROBLEMS: tuple[str, ...] = get_args(Problem)
#: problems that point at the parser (GROBID / Nougat), as opposed to extraction or interpretation
PARSE_PROBLEMS: tuple[str, ...] = ("text_missing", "text_garbled", "table_broken", "formula_broken", "crop_wrong")


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckIn(_Contract):
    target_kind: TargetKind
    target_id: str = Field(min_length=1, max_length=500, description="region_id, fact_id, span id, paper_id, …")
    field: str | None = Field(None, max_length=100, description="which attribute was judged, e.g. 'value', 'latex'")
    paper_id: str | None = Field(None, max_length=500)
    project_id: str | None = Field(None, max_length=100)
    verdict: Verdict
    problem: Problem | None = None
    corrected: dict[str, Any] | None = Field(None, description="the right value, e.g. {'value': 0.82, 'unit': ''}")
    note: str | None = Field(None, max_length=4000)
    shown: dict[str, Any] | None = Field(None, description="what the person saw when judging (snapshot)")
    supersedes: int | None = None


class CheckBatch(_Contract):
    checks: list[CheckIn] = Field(min_length=1, max_length=200)


class Check(CheckIn):
    check_id: int
    labeler_kind: Literal["human"] = "human"
    labeler: str
    created_at: datetime | None = None


class CheckList(BaseModel):
    items: list[Check]
    count: int
    provenance: Provenance | None = None


class CheckSummary(BaseModel):
    total: int
    by_verdict: dict[str, int]
    by_problem: dict[str, int]
    by_target_kind: dict[str, dict[str, int]]
    parse_problem_papers: list[dict] = Field(description="papers with the most current parse problems")
    provenance: Provenance | None = None


class Region(BaseModel):
    region_id: str
    page: int | None = None
    region_type: str
    bbox: list[float] | None = None
    text: str | None = None
    latex: str | None = None
    source_parser: str | None = None
    crop_url: str | None = Field(None, description="signed link to the PNG crop (no key needed; for people)")


class RegionsResponse(BaseModel):
    paper_id: str
    regions: list[Region]
    counts: dict[str, int]
    pdf_url: str | None = None
    provenance: Provenance | None = None


class LinksRequest(_Contract):
    paper_ids: list[str] = Field(min_length=1, max_length=500)


class PaperLinks(BaseModel):
    paper_id: str
    pdf_url: str | None = None
    doi_url: str | None = None


class LinksResponse(BaseModel):
    links: list[PaperLinks]
    provenance: Provenance | None = None
