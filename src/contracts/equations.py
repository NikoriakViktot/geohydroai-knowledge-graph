"""Contracts of /v1/equations, /v1/quantities and /v1/laws (docs/api/endpoints/equations.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.contracts.api import Provenance


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LawLink(_Out):
    law_id: str | None = None
    name: str | None = None
    status: str | None = None
    score: float | None = None
    variant: str | None = None
    s_quantity: float | None = None
    s_math: float | None = None
    s_text: float | None = None
    s_concept: float | None = None
    math_method: str | None = None
    mapping: dict[str, Any] | None = None
    capped: str | None = None


class Parameter(_Out):
    symbol: str | None = None
    symbol_tex: str | None = None
    description: str | None = None
    unit: str | None = None
    value: Any = None
    source: str | None = None
    quantity: str | None = None
    quantity_id: str | None = None
    dimension_check: str | None = None


class EquationInfo(_Out):
    eq_id: str
    paper_id: str | None = None
    xml_id: str | None = None
    equation_number: str | None = None
    page: int | None = None
    latex_raw: str | None = None
    latex: str | None = None
    text_grobid: str | None = None
    formula_text_hash: str | None = None
    formula_structural_hash: str | None = None
    canonical_expression: str | None = None
    structure_status: str | None = None
    purpose: str | None = None
    purpose_source: str | None = None
    section: str | None = None
    context_text: str | None = None
    image_path: str | None = None
    image_sha256: str | None = None


class PaperRef(_Out):
    paper_id: str | None = None
    doi: str | None = None
    title: str | None = None
    year: int | None = None


class Code(_Out):
    target: str | None = None
    form: str | None = None
    check: str | None = None
    args: list[dict[str, Any]] = Field(default_factory=list)
    python: str | None = None
    julia: str | None = None
    python_annotated: str | None = None
    julia_annotated: str | None = None


class Equivalents(_Out):
    exact: list[dict[str, Any]]
    exact_count: int | None = Field(None, description="null when more than 50")
    algebraic: list[dict[str, Any]]


class EquationResponse(_Out):
    equation: EquationInfo
    paper: PaperRef | None = None
    computes: list[dict[str, Any]] = Field(default_factory=list)
    parameters: list[Parameter] | None = None
    laws: list[LawLink] | None = None
    equivalents: Equivalents | None = None
    code: Code | None = None
    provenance: Provenance


class EquationSummary(_Out):
    eq_id: str
    paper_id: str | None = None
    title: str | None = None
    year: int | None = None
    equation_number: str | None = None
    page: int | None = None
    latex_raw: str | None = None
    canonical_expression: str | None = None
    purpose: str | None = None
    structure_status: str | None = None
    laws: list[dict[str, Any]] = Field(default_factory=list)
    matched_parameters: list[dict[str, Any]] = Field(default_factory=list)


class EquationSearchResponse(_Out):
    items: list[EquationSummary]
    count: int
    next_cursor: str | None = None
    resolved: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance


class ChainResponse(_Out):
    equation: EquationInfo
    paper: PaperRef | None = None
    parameters: list[Parameter]
    quantities: list[dict[str, Any]]
    laws: list[LawLink]
    concepts: list[dict[str, Any]]
    reported_values: list[dict[str, Any]]
    provenance: Provenance


class QuantityConcept(_Out):
    quantity_id: str
    label: str
    kind: str | None = None
    dimension: str | None = None
    alt_dimensions: list[str] = Field(default_factory=list)
    typical_unit: str | None = None
    aliases: list[str] = Field(default_factory=list)
    parameters: int | None = None
    equations: int | None = None


class QuantitiesResponse(_Out):
    items: list[QuantityConcept]
    count: int
    ontology_version: str | None = None
    provenance: Provenance


class QuantityResponse(_Out):
    quantity: QuantityConcept
    surface_names: list[dict[str, Any]]
    units: list[dict[str, Any]]
    laws: list[dict[str, Any]]
    counts: dict[str, int | None]
    provenance: Provenance


class NormalizeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=300)
    unit: str | None = Field(None, max_length=100)


class NormalizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[NormalizeItem] = Field(min_length=1, max_length=200)


class NormalizedQuantity(_Out):
    name: str
    unit: str | None = None
    quantity_id: str | None = None
    label: str | None = None
    method: str
    score: float | None = None
    qualifiers: list[str] = Field(default_factory=list)
    dimension: str | None = None
    unit_dimension: str | None = None
    dimension_check: str | None = None


class NormalizeResponse(_Out):
    items: list[NormalizedQuantity]
    ontology_version: str | None = None
    provenance: Provenance


class LawSummary(_Out):
    law_id: str
    name: str
    kind: str | None = None
    reference: str | None = None
    variants: list[str | None] = Field(default_factory=list)
    accepted_equations: int = 0
    accepted_papers: int = 0
    candidate_equations: int = 0


class LawsResponse(_Out):
    items: list[LawSummary]
    laws_version: str
    weights: dict[str, float]
    thresholds: dict[str, float]
    provenance: Provenance


class LawResponse(_Out):
    law: dict[str, Any]
    forms: list[dict[str, Any]]
    quantities: list[dict[str, Any]]
    concepts: list[str]
    instances: list[dict[str, Any]]
    count: int
    next_cursor: str | None = None
    provenance: Provenance


LawStatus = Literal["accepted", "candidate", "any"]
