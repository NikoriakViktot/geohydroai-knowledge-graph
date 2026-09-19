"""theses.yaml is the source of truth for the whole gap analysis — it must be sound.

These tests run against the real checked-in file, not a fixture: a typo in a key
term silently changes what the corpus is searched for, and that is precisely the
failure nobody notices by reading the output.
"""
from __future__ import annotations

import pytest

from src.paper_3.theses import (
    BLOCK_SIZES,
    BLOCK_TITLES,
    RELATIONS,
    Thesis,
    get_thesis,
    load_theses,
    validate_theses,
)


@pytest.fixture(scope="module")
def theses() -> list[Thesis]:
    return load_theses()


def test_the_checked_in_file_is_valid(theses):
    assert validate_theses(theses) == []


def test_all_ids_present_and_derived_from_block_sizes(theses):
    from src.paper_3.theses import expected_thesis_ids
    assert [t.id for t in theses] == expected_thesis_ids()
    assert len(theses) == sum(BLOCK_SIZES.values()) == 36


def test_new_blocks_have_controls_with_dois(theses):
    # H–K were added 2026-09-18 for the full-transformation manuscript; a thesis
    # without a control can only ever be RETRIEVAL_UNVALIDATED.
    for t in theses:
        if t.block in ("H", "I", "J", "K"):
            assert t.positive_controls, f"{t.id} has no positive control"
            assert all(c.doi.startswith("10.") for c in t.positive_controls), t.id


def test_block_sizes(theses):
    counts: dict[str, int] = {}
    for t in theses:
        counts[t.block] = counts.get(t.block, 0) + 1
    assert counts == BLOCK_SIZES


def test_every_block_has_a_title(theses):
    for t in theses:
        assert t.block in BLOCK_TITLES
        assert t.block_title


def test_query_and_key_term_minimums(theses):
    for t in theses:
        assert len(t.search_queries) >= 3, f"{t.id} has too few search_queries"
        assert len(t.openalex_queries) >= 1, f"{t.id} has no openalex_queries"
        assert len(t.key_terms) >= 2, f"{t.id} has too few key_term families"
        assert all(fam for fam in t.key_terms), f"{t.id} has an empty family"


def test_expected_relations_are_legal(theses):
    for t in theses:
        assert t.expected_relations, f"{t.id} declares no expected relations"
        assert set(t.expected_relations) <= set(RELATIONS), f"{t.id}"


def test_manuscript_anchors_are_present(theses):
    for t in theses:
        assert t.manuscript_anchor, f"{t.id} has no manuscript_anchor"


def test_seed_dois_are_normalised(theses):
    for t in theses:
        for doi in t.seed_dois:
            assert doi == doi.lower()
            assert not doi.startswith("http"), f"{t.id}: {doi} not normalised"
            assert doi.startswith("10."), f"{t.id}: {doi} does not look like a DOI"


def test_known_competitors_are_seeded(theses):
    """The two closest published studies must enter the corpus deterministically."""
    seeded = {doi for t in theses for doi in t.seed_dois}
    assert "10.1029/2025gl119771" in seeded, "Huang & Gao (uneven lake WSE) not seeded"
    assert "10.1029/2025gl120832" in seeded, "Lehnigk (SWOT Kakhovka flood) not seeded"


def test_thesis_is_frozen(theses):
    with pytest.raises(Exception):
        theses[0].statement = "mutated"  # type: ignore[misc]


def test_get_thesis_finds_and_raises(theses):
    assert get_thesis("T07", theses).block == "B"
    with pytest.raises(KeyError):
        get_thesis("T99", theses)


def test_families_hit_counts_distinct_families(theses):
    t = get_thesis("T07", theses)
    text = "We validate SWOT KaRIn LakeSP water surface elevation over lakes."
    assert t.families_hit(text) == 3
    assert t.families_hit("nothing relevant here") == 0


def test_negative_terms_are_detected(theses):
    t = get_thesis("T07", theses)
    assert t.has_negative("a SWOT analysis of the programme")
    assert not t.has_negative("SWOT KaRIn water surface elevation")


def test_negative_terms_do_not_collide_with_their_own_key_terms(theses):
    """A negative term must not be a substring of a key term in the same thesis.

    If it were, the phrase that admits a paper would also be the phrase that
    rejects it, and the thesis could never match anything.
    """
    for t in theses:
        for neg in t.negative_terms:
            for fam in t.key_terms:
                for term in fam:
                    assert neg.lower() not in term.lower(), (
                        f"{t.id}: negative term {neg!r} is inside key term {term!r}"
                    )


def test_validator_catches_a_missing_thesis(theses):
    problems = validate_theses(theses[:-1])
    assert any("missing thesis ids" in p for p in problems)
    assert any(f"block {theses[-1].block}" in p for p in problems)


def test_validator_catches_a_duplicate_id(theses):
    problems = validate_theses(theses + [theses[0]])
    assert any("duplicate" in p or "block" in p for p in problems)


def test_validator_catches_thin_queries(theses):
    import dataclasses
    thin = dataclasses.replace(theses[0], search_queries=("only one",))
    problems = validate_theses([thin] + theses[1:])
    assert any("search_queries" in p for p in problems)


# ── the on-topic rule ─────────────────────────────────────────────────────────

def test_primary_family_is_the_first_one(theses):
    t = get_thesis("T08", theses)
    assert t.primary_family == t.key_terms[0]
    assert "vertical datum" in t.primary_family


def test_on_topic_requires_the_distinguishing_concept(theses):
    """Two generic families must not make a paper count as on topic.

    T08's later families are 'satellite/gauge/water level' and
    'bias/difference/offset' — vocabulary most of this corpus contains.
    """
    t = get_thesis("T08", theses)
    generic = "We compare satellite water level estimates and report the bias."
    assert t.families_hit(generic) >= 2, "the generic text does hit two families"
    assert not t.is_on_topic(generic), "but it is not about vertical datums"


def test_on_topic_accepts_the_real_thing(theses):
    t = get_thesis("T08", theses)
    real = ("We converted ellipsoidal heights to the national vertical datum using "
            "the EGM2008 geoid before comparing satellite water levels with gauges, "
            "and report the residual bias.")
    assert t.is_on_topic(real)


def test_primary_family_alone_is_not_enough(theses):
    t = get_thesis("T08", theses)
    assert t.primary_hit("the EGM2008 geoid model")
    assert not t.is_on_topic("the EGM2008 geoid model")


def test_every_thesis_has_a_non_empty_primary_family(theses):
    for t in theses:
        assert t.primary_family, f"{t.id} has no primary family"


def test_on_topic_min_other_is_configurable(theses):
    t = get_thesis("T06", theses)
    text = "ICESat-2 ATL13 was used to measure the water surface slope of the river."
    assert t.is_on_topic(text, min_other=2)
    assert not t.is_on_topic(text, min_other=5)
