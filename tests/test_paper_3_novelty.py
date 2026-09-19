"""`novelty` must import, and must stay in step with the verdict scale.

This file exists because it did not. When `gap_matrix`'s verdict scale widened from
four values to nine, `novelty.py` kept importing `PARTIALLY_KNOWN`,
`UNKNOWN` and `YOUR_CONTRIBUTION`, and the whole module became unreachable —
`render_statement` and `reference_candidates` were dead code for as long as nobody
tried to call them. The 1 040-test suite stayed green throughout, because no test
imported the module at collection time.

The first test below is the cheap insurance that was missing.
"""
from __future__ import annotations

import importlib

import pandas as pd
import pytest

from src.paper_3 import gap_matrix
from src.paper_3.theses import load_theses


def test_module_imports():
    """The regression that motivated this file. Keep it first and keep it dumb."""
    module = importlib.import_module("src.paper_3.novelty")
    assert hasattr(module, "render_statement")
    assert hasattr(module, "reference_candidates")


@pytest.fixture(scope="module")
def novelty():
    return importlib.import_module("src.paper_3.novelty")


@pytest.fixture(scope="module")
def theses():
    return load_theses()


# ── the bands must cover the scale exactly ────────────────────────────────────

def test_bands_partition_the_verdict_scale(novelty):
    """Every verdict belongs to exactly one band.

    A verdict in no band renders nowhere and disappears from the statement; a
    verdict in two bands is reported twice. Both are silent failures.
    """
    bands = (novelty.ESTABLISHED, novelty.PARTIAL,
             novelty.UNRESOLVED, novelty.CONTRIBUTION)
    union: set[str] = set()
    for band in bands:
        assert not (union & band), f"verdict in two bands: {union & band}"
        union |= band
    assert union == set(gap_matrix.VERDICTS)


def test_contribution_band_matches_the_novelty_gate(novelty):
    """The narrative's 'contributes' band and the matrix's eligibility must agree."""
    assert novelty.CONTRIBUTION == frozenset(gap_matrix.NOVELTY_ELIGIBLE)


def test_retrieval_failures_are_unresolved_not_established(novelty):
    """'We could not tell' must never read as 'the literature is silent'."""
    for verdict in (gap_matrix.RETRIEVAL_UNVALIDATED,
                    gap_matrix.RETRIEVAL_INCOMPLETE):
        assert verdict in novelty.UNRESOLVED
        assert verdict not in novelty.ESTABLISHED
        assert verdict not in novelty.CONTRIBUTION


# ── render_statement over a real matrix shape ─────────────────────────────────

def _matrix(theses, **overrides):
    """A matrix with the real column set, one row per thesis."""
    empty = pd.DataFrame(columns=["thesis_id", "paper_id", "relation",
                                  "quote_verified", "is_own_result",
                                  "system_class", "confidence", "doi",
                                  "title", "year", "evidence_quote",
                                  "quote_similarity"])
    matrix = gap_matrix.build(empty, theses=theses)
    for column, value in overrides.items():
        matrix[column] = value
    return matrix


def test_render_statement_on_an_all_unvalidated_matrix(novelty, theses):
    """Today's expected shape: nothing established, nothing claimed."""
    matrix = _matrix(theses)
    assert set(matrix["verdict"]) == {gap_matrix.RETRIEVAL_UNVALIDATED}

    text = novelty.render_statement(matrix, pd.DataFrame())
    assert "No thesis reached the KNOWN threshold" in text
    assert "must not assert priority" in text


def test_render_statement_never_claims_priority_without_candidate_gap(novelty, theses):
    """The guard that matters: no CANDIDATE_GAP, no novelty language."""
    text = novelty.render_statement(_matrix(theses), pd.DataFrame())
    lowered = text.lower()
    for phrase in ("for the first time", "unprecedented", "no previous study"):
        assert phrase not in lowered


def test_render_statement_on_an_empty_matrix(novelty):
    assert "No theses were evaluated" in novelty.render_statement(
        pd.DataFrame(), pd.DataFrame())


def test_render_statement_lists_every_thin_thesis(novelty, theses):
    """Thin or absent coverage must be disclosed, not quietly omitted."""
    matrix = _matrix(theses, corpus_adequacy=gap_matrix.THIN)
    text = novelty.render_statement(matrix, pd.DataFrame())
    for thesis in theses:
        assert thesis.id in text


def test_render_statement_reports_a_contribution_when_one_exists(novelty, theses):
    matrix = _matrix(theses)
    matrix.loc[matrix["thesis_id"] == "T09", "verdict"] = gap_matrix.CANDIDATE_GAP
    matrix.loc[matrix["thesis_id"] == "T09", "papers_found"] = 7
    matrix.loc[matrix["thesis_id"] == "T09", "n_method_relevant_papers"] = 3

    text = novelty.render_statement(matrix, pd.DataFrame())
    assert "### T09" in text
    assert "must not assert priority" not in text
