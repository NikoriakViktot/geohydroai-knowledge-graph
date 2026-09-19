"""The evidence record, and the one rule that governs it.

`numeric_claim_allowed` is derived, never assigned. That is deliberate: if it
were a field someone could set, it would eventually be set to True on an
abstract-level row to make a table look fuller, and the distinction the whole
package rests on would quietly stop holding.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields as dataclass_fields

TOPICS = ("A", "B", "C")

TOPIC_NAMES = {
    "A": "bathymetry",
    "B": "vertical_reference",
    "C": "validation_accuracy",
}

TOPIC_TITLES = {
    "A": "Bathymetry: why it matters and how it is built",
    "B": "Vertical reference: bringing heights into one system",
    "C": "Satellite validation and measurement accuracy",
}

SOURCE_CORPORA = ("old_fulltext", "new_harvest")
EVIDENCE_LEVELS = ("abstract", "full_text")

#: What a record is allowed to do in the manuscript.
#:
#: discovery            — the paper exists and is on topic. Names a method, not a result.
#: qualitative_support  — the paper states a methodological finding in words.
#: quantitative_support — the paper reports a number that may enter a table.
EVIDENCE_ROLES = ("discovery", "qualitative_support", "quantitative_support")

#: Metric families are never converted into one another. An MAE and an RMSE over
#: the same residuals differ by a factor that depends on their distribution, so
#: putting them in one column would invent a comparison the papers did not make.
METRIC_FAMILIES = ("bias", "MAE", "RMSE", "NMAD", "SD", "other", "not_stated")

_METRIC_PATTERNS = {
    "RMSE": ("rmse", "root mean square", "root-mean-square", "rms error"),
    "MAE": ("mae", "mean absolute error", "mean abs. error"),
    "NMAD": ("nmad", "normalised median absolute", "normalized median absolute",
             "median absolute deviation"),
    "bias": ("bias", "mean difference", "mean error", "systematic difference",
             "mean offset", "median difference"),
    "SD": ("standard deviation", "std dev", "stdev", " sd ", "σ"),
}


def normalise_metric_family(reported: str) -> str:
    """Map a reported metric name onto a family. Never converts a value.

    Order matters: NMAD is checked before SD and before bias, because "normalised
    median absolute deviation" contains neither but its expansions overlap with
    both once lowercased.
    """
    text = f" {(reported or '').strip().lower()} "
    if not text.strip():
        return "not_stated"
    for family in ("NMAD", "RMSE", "MAE", "bias", "SD"):
        if any(token in text for token in _METRIC_PATTERNS[family]):
            return family
    return "other"


@dataclass(frozen=True)
class EvidenceRecord:
    """One extracted piece of evidence about one topic from one paper."""

    paper_id: str
    doi: str
    topic: str                      # A | B | C
    source_corpus: str              # old_fulltext | new_harvest
    evidence_level: str             # abstract | full_text
    evidence_role: str              # discovery | qualitative_support | quantitative_support
    quote_verified: bool = False

    title: str = ""
    year: int | None = None
    journal: str = ""

    # What the paper did
    method: str = "not_stated"
    sensor: str = "not_stated"
    waterbody_type: str = "not_stated"

    # The number, if any. Only meaningful when numeric_claim_allowed is True.
    value: str = ""
    unit: str = ""
    metric: str = ""                # as the paper reported it
    metric_family: str = "not_stated"   # normalised, never converted

    # Topic-B and topic-C specifics
    vertical_datum: str = "not_stated"
    geoid_model: str = "not_stated"
    permanent_tide: str = "not_stated"
    collocation_rule: str = "not_stated"
    n_gauges: str = "not_stated"
    independent_unit: str = "not_stated"

    limitations: str = ""
    quote: str = ""
    section: str = ""
    page: str = ""

    extraction_model: str = ""
    run_id: str = ""

    @property
    def numeric_claim_allowed(self) -> bool:
        """May this record's number enter a quantitative table in the manuscript?

        Three conditions, all required:

        * the evidence came from full text — an abstract states that a paper
          validated something, not how, against what, or at what tolerance;
        * the role is quantitative_support — the extractor found an actual figure;
        * the quote was verified — an unverifiable number is not a number.
        """
        return (self.evidence_level == "full_text"
                and self.evidence_role == "quantitative_support"
                and bool(self.quote_verified))

    @property
    def has_value(self) -> bool:
        return bool(str(self.value).strip())

    def as_row(self) -> dict:
        row = {f.name: getattr(self, f.name) for f in dataclass_fields(self)}
        row["numeric_claim_allowed"] = self.numeric_claim_allowed
        return row


#: Column order for the emitted parquet/CSV. `numeric_claim_allowed` sits right
#: after the two fields it is derived from, so a reader sees the rule in the data.
COLUMNS = [
    "paper_id", "doi", "topic", "title", "year", "journal",
    "source_corpus", "evidence_level", "evidence_role",
    "quote_verified", "numeric_claim_allowed",
    "method", "sensor", "waterbody_type",
    "value", "unit", "metric", "metric_family",
    "vertical_datum", "geoid_model", "permanent_tide",
    "collocation_rule", "n_gauges", "independent_unit",
    "limitations", "quote", "section", "page",
    "extraction_model", "run_id",
]


def validate_record(record: EvidenceRecord) -> list[str]:
    """Problems with one record. Empty means it is admissible."""
    problems: list[str] = []

    if record.topic not in TOPICS:
        problems.append(f"{record.doi}: illegal topic {record.topic!r}")
    if record.source_corpus not in SOURCE_CORPORA:
        problems.append(f"{record.doi}: illegal source_corpus {record.source_corpus!r}")
    if record.evidence_level not in EVIDENCE_LEVELS:
        problems.append(f"{record.doi}: illegal evidence_level {record.evidence_level!r}")
    if record.evidence_role not in EVIDENCE_ROLES:
        problems.append(f"{record.doi}: illegal evidence_role {record.evidence_role!r}")
    if record.metric_family not in METRIC_FAMILIES:
        problems.append(f"{record.doi}: illegal metric_family {record.metric_family!r}")

    # The rule, asserted rather than assumed.
    if record.evidence_level == "abstract" \
            and record.evidence_role == "quantitative_support":
        problems.append(
            f"{record.doi}: an abstract-level record cannot be "
            f"quantitative_support — an abstract states that a paper measured "
            f"something, not how it was measured or against what")

    if record.has_value and not record.numeric_claim_allowed:
        # Not an error: the value is kept for context, but it must not be
        # mistaken for something the manuscript may cite.
        pass

    if record.evidence_role != "discovery" and not record.quote.strip():
        problems.append(f"{record.doi}: {record.evidence_role} with no quote")

    return problems


def citable_numbers(records) -> list[EvidenceRecord]:
    """The subset whose numbers may appear in a manuscript table."""
    return [r for r in records if r.numeric_claim_allowed and r.has_value]
