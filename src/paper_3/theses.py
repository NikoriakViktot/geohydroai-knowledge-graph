"""T01–T24 thesis cards: frozen dataclass, loader, validator.

theses.yaml is the single source of truth. Nothing downstream may invent a thesis,
a search query or a key term that is not written there — the query set, the
prefilter and the gap matrix are all derived from this one file, so that a reviewer
can read it and know exactly what was searched for.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.paper_3._utils import THESES_PATH, normalize_doi

#: The four relation labels. CONTRADICTS is listed first on purpose — it is the
#: order used in the adjudication prompt, to blunt the default pull toward SUPPORTS.
RELATIONS = ("CONTRADICTS", "SUPPORTS", "METHOD_RELEVANT", "ANALOGUE")
NOT_RELEVANT = "NOT_RELEVANT"
ALL_LABELS = RELATIONS + (NOT_RELEVANT,)

#: Blocks A–G and their fixed sizes; asserted by validate_theses().
#: Thesis ids are T01…T{sum of BLOCK_SIZES}; adding a block extends the range.
BLOCK_SIZES = {"A": 3, "B": 4, "C": 4, "D": 3, "E": 4, "F": 4, "G": 2,
               "H": 4, "I": 3, "J": 3, "K": 2}

BLOCK_TITLES = {
    "A": "What a reservoir-to-river hydraulic transition means",
    "B": "Satellites must see surface geometry, not a single level",
    "C": "Vertical-datum harmonisation",
    "D": "Collocation and the independent statistical unit",
    "E": "Historical hydraulics as a validation layer",
    "F": "Morphology and hydraulic fragmentation",
    "G": "Regime shift versus transient disturbance",
    "H": "Surface transformation: vegetation succession and woody encroachment",
    "I": "Bed reconstruction across zones and its validation limits",
    "J": "Channel adjustment and roughness translation",
    "K": "SAR flood-water detection over a drained bed",
}


def expected_thesis_ids() -> list[str]:
    return [f"T{i:02d}" for i in range(1, sum(BLOCK_SIZES.values()) + 1)]

SYSTEM_CLASSES = (
    "reservoir", "river", "lake", "dam_removal", "breach",
    "multiple", "other", "not_stated",
)

#: How a positive control was found. `expert_known` and `citation` are stronger
#: evidence that the control is genuinely relevant than `external_search`, which
#: is only a search engine agreeing with a search engine.
CONTROL_SOURCES = ("expert_known", "citation", "external_search")

#: What the control is expected to be, once retrieved.
CONTROL_RELEVANCE = ("direct", "method", "analogue")

#: The stage a control is expected to survive. A control that should only be
#: reachable through the citation graph is a legitimate hard case, and saying so
#: up front stops a citation-only hit from reading as a semantic success.
CONTROL_STAGES = ("semantic_retrieval", "citation_expansion", "kg_expansion")

#: Theses where a novelty claim is plausible, so a single positive control is not
#: enough: they need an easy one and a hard one. Derived from the manuscript's
#: own novelty language (§7.2 marks the historical-documentation claims
#: preliminary) plus the blocks with the thinnest corpus coverage.
CRITICAL_THESES = ("T09", "T10", "T14", "T15", "T16", "T17", "T22",
                   "T25", "T27", "T31", "T33")

#: Theses kept in the evidence system but confined to the supplement (a
#: future remote-sensing methods paper), decided 2026-09-19 so the manuscript
#: keeps one scientific axis: vertical frame → WSE geometry → transition →
#: reconstructed bed → hydraulic consequence of the surface transformation.
SUPPLEMENT_ONLY_THESES = ("T35", "T36")


#: What a control may be used for.
#:
#: `development` — may be looked at while tuning key terms, queries and gates.
#: `holdout`     — must NOT influence any of those until it is opened once, at
#:                 the end. Tuning against a control turns it from a test into a
#:                 training example: retrieval that was adjusted until it found
#:                 the paper proves only that it can be adjusted.
CONTROL_ROLES = ("development", "holdout")


@dataclass(frozen=True)
class PositiveControl:
    """A paper that MUST be recovered by a thesis's retrieval, used as a hold-out.

    A control is never fed into retrieval as a hint. It is ingested into the
    corpus like any other paper and then retrieval has to find it unaided —
    otherwise recall is measured against a search that was told the answer.

    `role` splits that further. Development controls can be iterated against;
    holdout controls are opened once, after the rules are frozen, and
    `holdout_consumed` records that it happened so the same paper cannot quietly
    become a tuning target on the next pass.
    """

    doi: str
    role: str = "development"
    relevance: str = "direct"
    expected_stage: str = "semantic_retrieval"
    difficulty: str = "easy"          # easy | hard
    source_of_control: str = "external_search"
    manually_checked: bool = False
    quote: str = ""
    section: str = ""
    page: str = ""
    #: What the quote actually establishes for THIS thesis.
    supports_what: str = ""
    #: What it must not be stretched to mean. Written at verification time, when
    #: the paper is fresh, so that three weeks later a vertical-datum paper
    #: cannot quietly become evidence for a whole regime transition.
    does_not_support: str = ""
    note: str = ""
    #: A control found, on reading, not to be what it was taken for. Excluded
    #: rather than deleted — a control that vanishes leaves no audit trail.
    excluded: bool = False
    exclusion_reason: str = ""
    replacement_doi: str = ""
    #: Set to true once this holdout has been evaluated. Re-tuning afterwards
    #: requires a new control, not a second look at this one.
    holdout_consumed: bool = False
    #: The RETRIEVAL_RULES_VERSION in force when the holdout was opened.
    consumed_at_rules_version: str = ""

    @property
    def verified(self) -> bool:
        """Only a control a human has read, and has not excluded, counts."""
        return bool(self.manually_checked) and not self.excluded

    #: Retained for the older field name.
    @property
    def source_of_seed(self) -> str:
        return self.source_of_control

    @property
    def is_holdout(self) -> bool:
        return self.role == "holdout"

    @property
    def is_direct(self) -> bool:
        """Direct controls are the ones a gap claim depends on.

        A method or analogue control being missed weakens confidence; a *direct*
        one being missed means the search cannot be trusted to have found direct
        evidence, which is exactly what a gap claim asserts does not exist.
        """
        return self.relevance == "direct"


@dataclass(frozen=True)
class Thesis:
    """One thesis card. Frozen — nothing downstream may mutate a loaded thesis."""

    id: str
    block: str
    statement: str
    rationale: str
    search_queries: tuple[str, ...]      # natural language, for SPECTER2
    openalex_queries: tuple[str, ...]    # keyword forms, for OpenAlex search
    key_terms: tuple[tuple[str, ...], ...]   # term FAMILIES; prefilter needs >=1 per family
    negative_terms: tuple[str, ...]
    positive_controls: tuple[PositiveControl, ...]
    expected_relations: tuple[str, ...]
    manuscript_anchor: str
    numeric_anchor: str | None = None

    @property
    def seed_dois(self) -> tuple[str, ...]:
        """DOIs to ingest during the harvest.

        Every positive control must be in the corpus for recall to be testable
        at all — but being in the corpus is all a control gets. Retrieval is
        never told which papers these are.
        """
        return tuple(c.doi for c in self.positive_controls)

    @property
    def verified_controls(self) -> tuple[PositiveControl, ...]:
        return tuple(c for c in self.positive_controls if c.verified)

    @property
    def development_controls(self) -> tuple[PositiveControl, ...]:
        return tuple(c for c in self.positive_controls if not c.is_holdout)

    @property
    def holdout_controls(self) -> tuple[PositiveControl, ...]:
        return tuple(c for c in self.positive_controls if c.is_holdout)

    @property
    def verified_direct_controls(self) -> tuple[PositiveControl, ...]:
        """The controls a CANDIDATE_GAP verdict depends on."""
        return tuple(c for c in self.positive_controls if c.verified and c.is_direct)

    @property
    def is_critical(self) -> bool:
        return self.id in CRITICAL_THESES

    @property
    def block_title(self) -> str:
        return BLOCK_TITLES.get(self.block, "")

    @property
    def primary_family(self) -> tuple[str, ...]:
        """The first family: the thesis's distinguishing concept.

        The families after it are domain and qualifier vocabulary — "satellite",
        "reservoir", "difference" — which occur in most papers in a corpus like
        this one. Counting families without privileging the first makes the
        topical test trivially satisfiable, so `is_on_topic` requires this one.
        """
        return self.key_terms[0] if self.key_terms else ()

    def families_hit(self, text: str) -> int:
        """How many key-term families occur in `text` (case-insensitive)."""
        low = text.lower()
        return sum(1 for fam in self.key_terms
                   if any(term.lower() in low for term in fam))

    def primary_hit(self, text: str) -> bool:
        low = text.lower()
        return any(term.lower() in low for term in self.primary_family)

    def is_on_topic(self, text: str, min_other: int = 1,
                    fallback_families: int | None = None) -> bool:
        """The distinguishing concept, plus at least `min_other` other families.

        This is the gate used by the prefilter and the coverage diagnostic. A
        paper that mentions "satellite" and "difference" is not thereby about
        vertical-datum harmonisation.

        `fallback_families` exists because the primary families were calibrated
        against full text. Several of them are multi-word conjunctions — "joint
        use", "connectivity and water surface", "longitudinal survey" — that a
        thirty-page paper may contain somewhere but a two-thousand-character
        abstract essentially never does. Passing a number here admits a text
        that hits that many *distinct* families even without the primary one:
        a weaker signal, applied uniformly to every thesis, and recorded in the
        retrieval manifest so the relaxation is visible rather than assumed.
        """
        if self.primary_hit(text):
            return (self.families_hit(text) - 1) >= min_other
        if fallback_families is None:
            return False
        return self.families_hit(text) >= fallback_families

    def has_negative(self, text: str) -> bool:
        low = text.lower()
        return any(term.lower() in low for term in self.negative_terms)


def _as_tuple(value, name: str, tid: str) -> tuple:
    if value is None:
        return ()
    if isinstance(value, str):
        raise ValueError(f"{tid}.{name} must be a list, got a bare string")
    return tuple(value)


def load_theses(path: Path | str = THESES_PATH) -> list[Thesis]:
    """Parse theses.yaml into Thesis objects. Raises on a malformed file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a top-level list of theses")

    out: list[Thesis] = []
    for entry in raw:
        tid = entry.get("id", "<missing id>")
        key_terms = tuple(
            tuple(fam) for fam in _as_tuple(entry.get("key_terms"), "key_terms", tid)
        )
        controls = []
        for c in _as_tuple(entry.get("positive_controls"), "positive_controls", tid):
            if isinstance(c, str):      # bare DOI shorthand
                c = {"doi": c}
            # `evidence:` is the preferred nested form; the flat keys are
            # accepted so earlier entries keep loading.
            ev = c.get("evidence") or {}
            controls.append(PositiveControl(
                doi=normalize_doi(c.get("doi", "")),
                role=c.get("role", "development"),
                relevance=c.get("relevance", "direct"),
                expected_stage=c.get("expected_stage", "semantic_retrieval"),
                difficulty=c.get("difficulty", "easy"),
                source_of_control=(c.get("source_of_control")
                                   or c.get("source_of_seed")
                                   or "external_search"),
                manually_checked=bool(c.get("manually_checked", False)),
                quote=(ev.get("quote") or c.get("quote") or "").strip(),
                section=(ev.get("section") or c.get("section") or "").strip(),
                page=str(ev.get("page") or c.get("page") or "").strip(),
                supports_what=(ev.get("supports_what") or "").strip(),
                does_not_support=(ev.get("does_not_support") or "").strip(),
                note=(c.get("note") or "").strip(),
                excluded=bool(c.get("excluded", False)),
                exclusion_reason=(c.get("exclusion_reason") or "").strip(),
                replacement_doi=normalize_doi(c.get("replacement_doi", "")),
                holdout_consumed=bool(c.get("holdout_consumed", False)),
                consumed_at_rules_version=(
                    c.get("consumed_at_rules_version") or "").strip(),
            ))
        out.append(Thesis(
            id=tid,
            block=entry.get("block", ""),
            statement=(entry.get("statement") or "").strip(),
            rationale=(entry.get("rationale") or "").strip(),
            search_queries=_as_tuple(entry.get("search_queries"), "search_queries", tid),
            openalex_queries=_as_tuple(entry.get("openalex_queries"), "openalex_queries", tid),
            key_terms=key_terms,
            negative_terms=_as_tuple(entry.get("negative_terms"), "negative_terms", tid),
            positive_controls=tuple(controls),
            expected_relations=_as_tuple(
                entry.get("expected_relations"), "expected_relations", tid),
            manuscript_anchor=(entry.get("manuscript_anchor") or "").strip(),
            numeric_anchor=entry.get("numeric_anchor"),
        ))
    return out


def get_thesis(tid: str, theses: list[Thesis] | None = None) -> Thesis:
    for t in (theses if theses is not None else load_theses()):
        if t.id == tid:
            return t
    raise KeyError(f"no such thesis: {tid}")


def validate_theses(theses: list[Thesis]) -> list[str]:
    """Return a list of problems; empty list means the set is well-formed."""
    problems: list[str] = []

    ids = [t.id for t in theses]
    expected_ids = expected_thesis_ids()
    if sorted(ids) != expected_ids:
        missing = set(expected_ids) - set(ids)
        extra = set(ids) - set(expected_ids)
        if missing:
            problems.append(f"missing thesis ids: {sorted(missing)}")
        if extra:
            problems.append(f"unexpected thesis ids: {sorted(extra)}")
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        problems.append(f"duplicate thesis ids: {dupes}")

    counts: dict[str, int] = {}
    for t in theses:
        counts[t.block] = counts.get(t.block, 0) + 1
    for block, want in BLOCK_SIZES.items():
        got = counts.get(block, 0)
        if got != want:
            problems.append(f"block {block}: expected {want} theses, found {got}")
    for block in sorted(set(counts) - set(BLOCK_SIZES)):
        problems.append(f"unknown block: {block!r}")

    for t in theses:
        if not t.statement:
            problems.append(f"{t.id}: empty statement")
        if len(t.search_queries) < 3:
            problems.append(
                f"{t.id}: needs >=3 search_queries, has {len(t.search_queries)}")
        if len(t.openalex_queries) < 1:
            problems.append(f"{t.id}: needs >=1 openalex_queries")
        if len(t.key_terms) < 2:
            problems.append(
                f"{t.id}: needs >=2 key_terms families, has {len(t.key_terms)}")
        for fam in t.key_terms:
            if not fam:
                problems.append(f"{t.id}: empty key_terms family")
        bad = [r for r in t.expected_relations if r not in RELATIONS]
        if bad:
            problems.append(f"{t.id}: illegal expected_relations {bad}")
        if not t.manuscript_anchor:
            problems.append(f"{t.id}: empty manuscript_anchor")

        for c in t.positive_controls:
            if not c.doi.startswith("10."):
                problems.append(f"{t.id}: control DOI {c.doi!r} is malformed")
            if c.relevance not in CONTROL_RELEVANCE:
                problems.append(f"{t.id}: control {c.doi} has illegal "
                                f"relevance {c.relevance!r}")
            if c.expected_stage not in CONTROL_STAGES:
                problems.append(f"{t.id}: control {c.doi} has illegal "
                                f"expected_stage {c.expected_stage!r}")
            if c.excluded and not c.exclusion_reason:
                problems.append(f"{t.id}: control {c.doi} is excluded with no "
                                f"reason — an exclusion with no record is a "
                                f"deletion")
            if c.manually_checked and not c.excluded and not c.supports_what:
                problems.append(f"{t.id}: control {c.doi} is verified but does "
                                f"not say what it supports for this thesis")
            if c.source_of_control not in CONTROL_SOURCES:
                problems.append(f"{t.id}: control {c.doi} has illegal "
                                f"source_of_seed {c.source_of_seed!r}")
            if c.difficulty not in ("easy", "hard"):
                problems.append(f"{t.id}: control {c.doi} has illegal "
                                f"difficulty {c.difficulty!r}")
            if c.role not in CONTROL_ROLES:
                problems.append(f"{t.id}: control {c.doi} has illegal "
                                f"role {c.role!r}")
            if c.holdout_consumed and not c.is_holdout:
                problems.append(f"{t.id}: control {c.doi} is marked consumed but "
                                f"is not a holdout")
            if c.holdout_consumed and not c.consumed_at_rules_version:
                problems.append(f"{t.id}: holdout {c.doi} is consumed but does not "
                                f"record the rules version it was opened under — "
                                f"without it the result cannot be tied to any code")
            if c.manually_checked and not c.quote:
                problems.append(f"{t.id}: control {c.doi} is marked checked but "
                                f"carries no quote — a check with no evidence "
                                f"of checking is not a check")
        dois = [c.doi for c in t.positive_controls]
        if len(dois) != len(set(dois)):
            problems.append(f"{t.id}: duplicate positive-control DOIs")
        # Whether a critical thesis has a holdout is a readiness question, not a
        # structural one — it belongs to the acceptance gate, so that loading the
        # file still works while the controls are being filled in.

    return problems
