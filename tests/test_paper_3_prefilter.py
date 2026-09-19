"""The structural prefilter and the semantic stage.

The prefilter is what keeps a flood-inundation corpus from answering every
altimetry query with flood-inundation papers. If it loosens, `n_supports` inflates
and every verdict drifts towards KNOWN — the exact confirmation bias the
four-relation scheme exists to prevent.
"""
from __future__ import annotations

import pytest

from src.paper_3.retrieve import (
    DISTANCE_GATE,
    MIN_CHUNKS,
    ThesisCandidate,
    prefilter,
    semantic_gate,
    stage_semantic,
)
from src.paper_3.theses import get_thesis, load_theses


def cand(**over) -> ThesisCandidate:
    base = dict(
        thesis_id="T07", paper_id="p1", n_keyterm_families_hit=2,
        semantic_best_distance=0.30, n_semantic_chunks=5, stage_found="semantic",
    )
    base.update(over)
    return ThesisCandidate(**base)


# ── the key-term gate ─────────────────────────────────────────────────────────

def test_one_family_is_not_enough():
    ok, why, score = prefilter(cand(n_keyterm_families_hit=1))
    assert not ok
    assert "key-term" in why
    assert score == 0.0


def test_zero_families_with_a_perfect_distance_is_still_dropped():
    """A flood paper that embeds close to an altimetry query must not survive."""
    ok, why, _ = prefilter(cand(n_keyterm_families_hit=0, semantic_best_distance=0.01,
                                n_semantic_chunks=99))
    assert not ok
    assert "key-term" in why


def test_two_families_and_a_close_chunk_passes():
    ok, why, score = prefilter(cand())
    assert ok and score > 0
    assert "distance" in why


# ── the semantic gate ─────────────────────────────────────────────────────────

def test_a_distant_match_with_many_chunks_passes():
    ok, why, _ = prefilter(cand(semantic_best_distance=0.80,
                                n_semantic_chunks=MIN_CHUNKS))
    assert ok
    assert "chunks" in why


def test_a_distant_match_with_few_chunks_is_dropped():
    ok, why, _ = prefilter(cand(semantic_best_distance=0.80, n_semantic_chunks=1))
    assert not ok
    assert "weak semantic match" in why


def test_the_distance_gate_boundary():
    assert prefilter(cand(semantic_best_distance=DISTANCE_GATE,
                          n_semantic_chunks=1))[0]
    assert not prefilter(cand(semantic_best_distance=DISTANCE_GATE + 0.01,
                              n_semantic_chunks=1))[0]


@pytest.mark.parametrize("stage", ["kg", "citation"])
def test_structural_candidates_bypass_the_distance_gate(stage):
    ok, why, _ = prefilter(cand(stage_found=stage, semantic_best_distance=None,
                                n_semantic_chunks=0))
    assert ok
    assert stage in why


@pytest.mark.parametrize("stage", ["kg", "citation"])
def test_structural_candidates_do_not_bypass_the_key_term_gate(stage):
    ok, _, _ = prefilter(cand(stage_found=stage, n_keyterm_families_hit=1,
                              semantic_best_distance=None, n_semantic_chunks=0))
    assert not ok, "a structural link is not evidence the paper is on topic"


def test_score_rewards_more_families_and_structure():
    weak = prefilter(cand(n_keyterm_families_hit=2, n_semantic_chunks=1))[2]
    strong = prefilter(cand(n_keyterm_families_hit=4, n_semantic_chunks=10))[2]
    assert strong > weak


def test_semantic_gate_accepts_close_or_plentiful():
    assert semantic_gate({"best_distance": 0.2, "n_chunks": 1})
    assert semantic_gate({"best_distance": 0.9, "n_chunks": MIN_CHUNKS})
    assert not semantic_gate({"best_distance": 0.9, "n_chunks": 1})


# ── the semantic stage over a fake store ──────────────────────────────────────

class _FakeEmbedder:
    def embed_query(self, q):
        return [0.0, 1.0]


class _FakeStore:
    """Returns a fixed hit list, and records whether a `where` filter was used."""

    def __init__(self, hits):
        self.hits = hits
        self.where_values = []

    def query(self, vector, top_k=10, where=None):
        self.where_values.append(where)
        return self.hits


def _hit(pid, dist, chunk):
    return {"paper_id": pid, "distance": dist, "chunk_id": chunk}


@pytest.fixture(scope="module")
def thesis():
    return get_thesis("T07", load_theses())


def test_semantic_stage_aggregates_by_paper_and_keeps_the_best_distance(thesis):
    store = _FakeStore([_hit("a", 0.4, "c1"), _hit("a", 0.2, "c2"), _hit("b", 0.9, "c3")])
    out = stage_semantic(thesis, store, _FakeEmbedder(), top_k=10)
    assert out["a"]["best_distance"] == 0.2
    assert out["a"]["n_chunks"] == 2, "two distinct chunks from the same paper"
    assert out["b"]["n_chunks"] == 1
    assert set(out) == {"a", "b"}


def test_semantic_stage_counts_distinct_chunks_across_queries(thesis):
    store = _FakeStore([_hit("a", 0.4, "c1")])
    out = stage_semantic(thesis, store, _FakeEmbedder(), top_k=10)
    # Every query returns the same chunk id, so it must be counted exactly once.
    assert out["a"]["n_chunks"] == 1


def test_semantic_stage_never_passes_a_where_filter(thesis):
    """A 500-id filter would amputate exactly the newly harvested papers."""
    store = _FakeStore([_hit("a", 0.4, "c1")])
    stage_semantic(thesis, store, _FakeEmbedder(), top_k=10)
    assert store.where_values, "expected at least one query"
    assert all(w is None for w in store.where_values)


def test_semantic_stage_queries_once_per_search_query(thesis):
    store = _FakeStore([])
    stage_semantic(thesis, store, _FakeEmbedder(), top_k=10)
    assert len(store.where_values) == len(thesis.search_queries)


def test_semantic_stage_survives_a_failing_store(thesis):
    class _Broken:
        def query(self, *a, **k):
            raise RuntimeError("chroma is down")

    assert stage_semantic(thesis, _Broken(), _FakeEmbedder()) == {}


def test_semantic_stage_falls_back_to_filename_when_paper_id_is_absent(thesis):
    store = _FakeStore([{"filename": "some_paper", "distance": 0.3, "chunk_id": "c1"}])
    out = stage_semantic(thesis, store, _FakeEmbedder())
    assert "some_paper" in out


def test_semantic_stage_skips_hits_without_any_identity(thesis):
    store = _FakeStore([{"distance": 0.3, "chunk_id": "c1"}])
    assert stage_semantic(thesis, store, _FakeEmbedder()) == {}
