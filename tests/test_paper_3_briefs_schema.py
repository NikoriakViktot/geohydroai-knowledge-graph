"""The one rule: abstract = breadth, full text = evidence.

If this file passes and the rest of the package is wrong, a brief is merely
incomplete. If this file fails, a number lifted from an abstract can reach a
quantitative table in the manuscript, and the comparison it enters is invalid.
"""
from __future__ import annotations

import pytest

from src.paper_3.briefs import schema
from src.paper_3.briefs.schema import EvidenceRecord, normalise_metric_family
from src.paper_3.briefs.topics import get_topic, load_topics, validate_topics


def record(**over) -> EvidenceRecord:
    base = dict(
        paper_id="p1", doi="10.1/x", topic="C",
        source_corpus="old_fulltext", evidence_level="full_text",
        evidence_role="quantitative_support", quote_verified=True,
        value="0.12", unit="m", metric="RMSE", metric_family="RMSE",
        quote="The RMSE against 27 gauges was 0.12 m.",
    )
    base.update(over)
    return EvidenceRecord(**base)


# ── the rule ──────────────────────────────────────────────────────────────────

def test_full_text_quantitative_verified_may_be_cited():
    assert record().numeric_claim_allowed


def test_an_abstract_may_never_carry_a_citable_number():
    """The whole point of the package."""
    assert not record(evidence_level="abstract",
                      evidence_role="qualitative_support").numeric_claim_allowed
    assert not record(evidence_level="abstract",
                      evidence_role="discovery").numeric_claim_allowed


def test_an_unverified_quote_voids_the_number():
    assert not record(quote_verified=False).numeric_claim_allowed


def test_a_qualitative_full_text_record_is_not_citable_as_a_number():
    assert not record(evidence_role="qualitative_support").numeric_claim_allowed


def test_the_validator_rejects_a_quantitative_abstract():
    problems = schema.validate_record(
        record(evidence_level="abstract", evidence_role="quantitative_support"))
    assert any("cannot be quantitative_support" in p for p in problems)


def test_a_non_discovery_record_needs_a_quote():
    problems = schema.validate_record(record(quote=""))
    assert any("no quote" in p for p in problems)


def test_a_discovery_record_needs_no_quote():
    assert schema.validate_record(
        record(evidence_role="discovery", evidence_level="abstract",
               source_corpus="new_harvest", quote="", value="")) == []


def test_a_value_is_kept_but_not_citable_when_the_level_is_wrong():
    """Context is preserved; permission is not."""
    row = record(evidence_level="abstract", evidence_role="qualitative_support",
                 value="0.12")
    assert row.has_value
    assert not row.numeric_claim_allowed


def test_citable_numbers_filters_correctly():
    rows = [
        record(doi="10.1/ok"),
        record(doi="10.1/abs", evidence_level="abstract",
               evidence_role="qualitative_support"),
        record(doi="10.1/unverified", quote_verified=False),
        record(doi="10.1/novalue", value=""),
    ]
    citable = schema.citable_numbers(rows)
    assert [r.doi for r in citable] == ["10.1/ok"]


def test_numeric_claim_allowed_is_derived_not_settable():
    """It must not be a field anyone can set to make a table look fuller."""
    field_names = {f for f in schema.COLUMNS}
    assert "numeric_claim_allowed" in field_names, "it is emitted"
    with pytest.raises(TypeError):
        EvidenceRecord(paper_id="p", doi="d", topic="A",
                       source_corpus="old_fulltext", evidence_level="full_text",
                       evidence_role="discovery",
                       numeric_claim_allowed=True)  # type: ignore[call-arg]


def test_as_row_emits_the_derived_flag():
    assert record().as_row()["numeric_claim_allowed"] is True


# ── metric families ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("reported,expected", [
    ("RMSE", "RMSE"), ("rmse", "RMSE"), ("root mean square error", "RMSE"),
    ("MAE", "MAE"), ("mean absolute error", "MAE"),
    ("NMAD", "NMAD"), ("normalised median absolute deviation", "NMAD"),
    ("bias", "bias"), ("mean difference", "bias"), ("systematic difference", "bias"),
    ("standard deviation", "SD"),
    ("", "not_stated"), ("R²", "other"),
])
def test_metric_families_are_recognised(reported, expected):
    assert normalise_metric_family(reported) == expected


def test_nmad_is_not_swallowed_by_sd_or_bias():
    """Its expansion overlaps both once lowercased, so order matters."""
    assert normalise_metric_family("normalized median absolute deviation") == "NMAD"


def test_metric_families_are_never_converted():
    """Two families over the same residuals are different numbers, not one."""
    mae = record(metric="MAE", metric_family="MAE", value="0.10")
    rmse = record(metric="RMSE", metric_family="RMSE", value="0.12")
    assert mae.metric_family != rmse.metric_family
    # There is deliberately no conversion helper anywhere in the module.
    assert not any(name.startswith("convert") or name.startswith("to_rmse")
                   for name in dir(schema))


# ── topics and their source policy ────────────────────────────────────────────

@pytest.fixture(scope="module")
def topics():
    return load_topics()


def test_the_checked_in_topics_are_valid(topics):
    assert validate_topics(topics) == []
    assert [t.id for t in topics] == ["A", "B", "C"]


def test_bathymetry_lets_abstracts_be_qualitative(topics):
    """Taxonomy of methods can come from abstracts; numbers cannot."""
    assert get_topic("A", topics).abstract_role == "qualitative_support"


def test_datum_and_validation_abstracts_are_discovery_only(topics):
    for topic_id in ("B", "C"):
        assert get_topic(topic_id, topics).abstract_role == "discovery"


def test_no_topic_may_let_an_abstract_be_quantitative(topics):
    for topic in topics:
        assert topic.abstract_role != "quantitative_support"


def test_cap_role_demotes_an_abstract_for_a_discovery_topic(topics):
    topic = get_topic("C", topics)
    assert topic.cap_role("quantitative_support", "abstract") == "discovery"
    assert topic.cap_role("qualitative_support", "abstract") == "discovery"


def test_cap_role_allows_qualitative_abstracts_for_bathymetry(topics):
    topic = get_topic("A", topics)
    assert topic.cap_role("qualitative_support", "abstract") == "qualitative_support"
    assert topic.cap_role("quantitative_support", "abstract") == "qualitative_support"


def test_cap_role_leaves_full_text_alone(topics):
    for topic in topics:
        assert topic.cap_role("quantitative_support", "full_text") == "quantitative_support"


def test_cap_role_handles_nonsense_from_an_extractor(topics):
    topic = get_topic("A", topics)
    assert topic.cap_role("definitely_true", "full_text") == "discovery"
    assert topic.cap_role("definitely_true", "abstract") == "discovery"


# ── the geodesy vocabulary the corpus was missing ─────────────────────────────

def test_topic_b_covers_the_geodesy_terms_the_theses_missed(topics):
    """T09 returned zero papers. That was the corpus, not the literature — so
    the vocabulary is spelled out here rather than inherited from the thesis."""
    terms = {t.lower() for fam in get_topic("B", topics).key_terms for t in fam}
    for expected in ("permanent tide", "zero tide", "mean tide", "tide-free",
                     "gnss levelling", "datum transformation", "geolocation",
                     "height reference", "itrf", "normal height"):
        assert expected in terms, f"topic B is missing {expected!r}"


def test_topic_c_covers_the_atl_products(topics):
    terms = {t.lower() for fam in get_topic("C", topics).key_terms for t in fam}
    for expected in ("atl13", "atl08", "pixc", "riversp", "lakesp", "nmad"):
        assert expected in terms


def test_topic_a_covers_the_method_taxonomy(topics):
    terms = {t.lower() for fam in get_topic("A", topics).key_terms for t in fam}
    for expected in ("multibeam", "satellite-derived bathymetry", "kriging",
                     "inverse distance", "exposed bed", "nautical chart"):
        assert expected in terms


def test_on_topic_needs_the_primary_family(topics):
    topic = get_topic("C", topics)
    generic = "We studied a reservoir and a river over a long period of time."
    assert not topic.is_on_topic(generic)

    real = ("We validated ICESat-2 ATL13 water surface elevation against 27 "
            "gauges and report an RMSE of 0.12 m.")
    assert topic.is_on_topic(real)


def test_negative_terms_keep_out_the_wrong_field(topics):
    topic = get_topic("C", topics)
    assert topic.has_negative("validation of sea surface height validation")
