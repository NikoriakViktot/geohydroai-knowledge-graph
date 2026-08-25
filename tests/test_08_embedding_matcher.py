"""
test_08_embedding_matcher.py
============================
Unit tests for the fault-tolerant embedding_matcher layer.

Tests are fully isolated: the embedding model and ontology registry are
monkey-patched so no GPU / network / disk access is required.

Failure modes covered
---------------------
  - Empty query (blank / whitespace)
  - Zero-norm query vector
  - NaN / Inf query vector
  - Encode failure (model raises)
  - All-NaN similarity array
  - Mixed NaN/finite similarity array (best finite used)
  - Empty candidate list for a type
  - Stale-globals bug (consecutive calls with different types)
  - Ambiguous acronyms: SAR / SMA / RF / ML / SCS / DEM / NDVI
  - High-confidence match
  - Low-confidence match (below UNCERTAIN_THRESHOLD)
"""
from __future__ import annotations

import contextlib
import math
from contextlib import ExitStack
from typing import Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import src.normalization.embedding_matcher as em
from src.normalization.embedding_matcher import (
    _is_finite_scalar,
    _unknown,
    _validate_vec,
    cache_info,
    semantic_match,
    top_k_semantic,
    ACCEPT_THRESHOLD,
    UNCERTAIN_THRESHOLD,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_DIM = 4


def _fake_registry(type_: str = "method", n: int = 3) -> dict:
    return {
        f"{type_}.entity_{i}": {
            "display_name": f"Entity {i}",
            "type":         type_,
            "aliases":      [f"alias_{i}"],
            "definition":   f"Definition of entity {i}",
        }
        for i in range(n)
    }


def _normalised(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v)
    return v / norm if norm > 0 else v


class _FakeModel:
    def __init__(self, vec: Optional[np.ndarray] = None):
        self._vec = vec

    def encode(self, texts: list[str], **kwargs) -> np.ndarray:
        if self._vec is None:
            raise RuntimeError("FakeModel: encode deliberately failing")
        return np.tile(self._vec, (len(texts), 1))


@contextlib.contextmanager
def _patched(
    registry: dict | None = None,
    query_vec: np.ndarray | None = None,
    fail_encode: bool = False,
):
    """Patch _model, alias_resolver registry, and get_entity_by_id."""
    if registry is None:
        registry = _fake_registry()

    np.random.seed(42)

    if fail_encode:
        model = _FakeModel(vec=None)
    else:
        q = query_vec if query_vec is not None else _normalised(
            np.random.randn(_DIM).astype(np.float32)
        )
        model = _FakeModel(vec=q)

    def fake_load_registry():
        return registry

    def fake_get_entity(eid):
        return registry.get(eid)

    with ExitStack() as stack:
        stack.enter_context(patch.object(em, "_model", model))
        stack.enter_context(patch.object(em, "_model_name", "fake-model"))
        # _build_index and semantic_match do local imports from alias_resolver
        stack.enter_context(
            patch("src.normalization.alias_resolver.load_ontology_registry",
                  fake_load_registry)
        )
        stack.enter_context(
            patch("src.normalization.alias_resolver.get_entity_by_id",
                  fake_get_entity)
        )
        yield


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: reset module globals between tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_globals():
    em._type_cache.clear()
    em._eid_list   = []
    em._matrix     = None
    em._model      = None
    em._model_name = ""
    yield
    em._type_cache.clear()
    em._eid_list   = []
    em._matrix     = None
    em._model      = None
    em._model_name = ""


# ─────────────────────────────────────────────────────────────────────────────
# 1. _is_finite_scalar
# ─────────────────────────────────────────────────────────────────────────────

class TestIsFiniteScalar:
    def test_float_ok(self):   assert _is_finite_scalar(0.75)
    def test_zero_ok(self):    assert _is_finite_scalar(0.0)
    def test_nan(self):        assert not _is_finite_scalar(float("nan"))
    def test_inf(self):        assert not _is_finite_scalar(float("inf"))
    def test_none(self):       assert not _is_finite_scalar(None)
    def test_non_numeric(self):assert not _is_finite_scalar("abc")
    def test_numpy_nan(self):  assert not _is_finite_scalar(np.nan)


# ─────────────────────────────────────────────────────────────────────────────
# 2. _validate_vec
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateVec:
    def test_valid(self):
        v = _normalised(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32))
        assert _validate_vec(v, "test")

    def test_none(self):      assert not _validate_vec(None, "test")
    def test_empty(self):     assert not _validate_vec(np.array([]), "test")
    def test_zero_norm(self): assert not _validate_vec(np.zeros(4, dtype=np.float32), "test")

    def test_nan(self):
        v = np.array([float("nan"), 1.0, 0.0, 0.0], dtype=np.float32)
        assert not _validate_vec(v, "test")

    def test_inf(self):
        v = np.array([float("inf"), 1.0, 0.0, 0.0], dtype=np.float32)
        assert not _validate_vec(v, "test")


# ─────────────────────────────────────────────────────────────────────────────
# 3. _unknown helper
# ─────────────────────────────────────────────────────────────────────────────

class TestUnknownHelper:
    def test_structure(self):
        r = _unknown("SAR", confidence=0.5, reason="LOW_SIMILARITY")
        assert r["match_type"] == "unknown"
        assert r["canonical_id"] is None
        assert isinstance(r["confidence"], float)
        assert math.isfinite(r["confidence"])

    def test_confidence_clipped_at_zero(self):
        assert _unknown("X", confidence=-1.0)["confidence"] == 0.0

    def test_confidence_formattable(self):
        r = _unknown("X")
        _ = f"{r['confidence']:.2f}"   # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# 4. Gate 0: empty / blank query
# ─────────────────────────────────────────────────────────────────────────────

class TestEmptyQuery:
    def test_empty_string(self):
        r = semantic_match("")
        assert r["match_type"] == "unknown"
        assert r["reason"] == "EMPTY_QUERY"

    def test_whitespace(self):
        r = semantic_match("   ")
        assert r["match_type"] == "unknown"

    def test_confidence_formattable(self):
        r = semantic_match("")
        assert isinstance(r["confidence"], float)
        assert f"{r['confidence']:.2f}" == "0.00"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Gate 1: empty candidates
# ─────────────────────────────────────────────────────────────────────────────

class TestEmptyCandidates:
    def test_unknown_type_returns_unknown(self):
        registry = _fake_registry(type_="sensor")
        q = _normalised(np.ones(_DIM, dtype=np.float32))
        with _patched(registry=registry, query_vec=q):
            r = semantic_match("SAR", expected_type="nonexistent_type")
        assert r["match_type"] == "unknown"
        assert r["reason"] == "EMPTY_CANDIDATES"

    def test_stale_globals_bug(self):
        """After an empty-type call, a valid-type call must use fresh globals."""
        reg = {**_fake_registry(type_="sensor", n=2),
               **_fake_registry(type_="method", n=2)}
        q = _normalised(np.ones(_DIM, dtype=np.float32))
        with _patched(registry=reg, query_vec=q):
            r1 = semantic_match("X", expected_type="nonexistent")
            assert r1["match_type"] == "unknown"

            r2 = semantic_match("some method", expected_type="method")
            assert isinstance(r2, dict)
            assert isinstance(r2["confidence"], float)
            assert math.isfinite(r2["confidence"])


# ─────────────────────────────────────────────────────────────────────────────
# 6. Gate 2: bad query vector
# ─────────────────────────────────────────────────────────────────────────────

class TestInvalidQueryVec:
    def _inject_valid_index(self, eids: list[str], registry: dict) -> None:
        """Pre-build a valid matrix so _build_index() is bypassed in the test."""
        np.random.seed(7)
        vecs = [_normalised(np.random.randn(_DIM).astype(np.float32)) for _ in eids]
        matrix = np.stack(vecs)
        em._eid_list = eids
        em._matrix   = matrix
        em._type_cache["__all__"] = (eids, matrix)

    def test_zero_vector(self):
        registry = _fake_registry()
        valid_q  = _normalised(np.ones(_DIM, dtype=np.float32))
        with _patched(registry=registry, query_vec=valid_q):
            self._inject_valid_index(list(registry), registry)
            em._model.encode = lambda texts, **kw: np.tile(
                np.zeros(_DIM, dtype=np.float32), (len(texts), 1)
            )
            r = semantic_match("SAR")
        assert r["match_type"] == "unknown"
        assert r["reason"] == "INVALID_QUERY_VEC"

    def test_nan_vector(self):
        registry = _fake_registry()
        valid_q  = _normalised(np.ones(_DIM, dtype=np.float32))
        bad_v    = np.array([float("nan"), 1.0, 0.0, 0.0], dtype=np.float32)
        with _patched(registry=registry, query_vec=valid_q):
            self._inject_valid_index(list(registry), registry)
            em._model.encode = lambda texts, **kw: np.tile(bad_v, (len(texts), 1))
            r = semantic_match("SMA")
        assert r["match_type"] == "unknown"
        assert r["reason"] == "INVALID_QUERY_VEC"

    def test_inf_vector(self):
        registry = _fake_registry()
        valid_q  = _normalised(np.ones(_DIM, dtype=np.float32))
        bad_v    = np.array([float("inf"), 1.0, 0.0, 0.0], dtype=np.float32)
        with _patched(registry=registry, query_vec=valid_q):
            self._inject_valid_index(list(registry), registry)
            em._model.encode = lambda texts, **kw: np.tile(bad_v, (len(texts), 1))
            r = semantic_match("RF")
        assert r["match_type"] == "unknown"
        assert r["reason"] == "INVALID_QUERY_VEC"

    def test_encode_failure(self):
        """Model fails on BOTH index build and query → EMPTY_CANDIDATES (graceful)."""
        with _patched(fail_encode=True):
            r = semantic_match("ML")
        # Index build also fails → no candidates → EMPTY_CANDIDATES
        assert r["match_type"] == "unknown"
        assert r["reason"] in ("EMPTY_CANDIDATES", "ENCODE_FAILURE")

    def test_encode_failure_query_only(self):
        """Valid index, encode fails only at query time → ENCODE_FAILURE."""
        registry = _fake_registry()
        valid_q  = _normalised(np.ones(_DIM, dtype=np.float32))
        with _patched(registry=registry, query_vec=valid_q):
            self._inject_valid_index(list(registry), registry)
            em._model.encode = lambda texts, **kw: (_ for _ in ()).throw(
                RuntimeError("query encode failed")
            )
            r = semantic_match("ML")
        assert r["match_type"] == "unknown"
        assert "ENCODE_FAILURE" in r["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# 7. Gate 3: bad similarity array
# ─────────────────────────────────────────────────────────────────────────────

class TestInvalidSims:
    def test_all_nan_sims(self):
        q = _normalised(np.ones(_DIM, dtype=np.float32))
        with _patched(query_vec=q):
            em._matrix   = np.full((2, _DIM), float("nan"), dtype=np.float32)
            em._eid_list = ["method.entity_0", "method.entity_1"]
            em._type_cache["__all__"] = (em._eid_list, em._matrix)
            r = semantic_match("entity")
        # NaN sims → replaced with -1.0 → LOW_SIMILARITY
        assert r["match_type"] == "unknown"
        assert math.isfinite(r["confidence"])

    def test_mixed_nan_sims_returns_finite(self):
        q = _normalised(np.ones(_DIM, dtype=np.float32))
        with _patched(query_vec=q):
            good_vec = _normalised(np.ones(_DIM, dtype=np.float32))
            bad_vec  = np.array([float("nan"), 0.0, 0.0, 0.0], dtype=np.float32)
            em._matrix   = np.stack([good_vec, bad_vec, bad_vec])
            em._eid_list = ["method.entity_0", "method.entity_1", "method.entity_2"]
            em._type_cache["__all__"] = (em._eid_list, em._matrix)
            r = semantic_match("entity")
        assert isinstance(r, dict)
        assert math.isfinite(r["confidence"])


# ─────────────────────────────────────────────────────────────────────────────
# 8. Normal paths
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalPaths:
    def test_high_confidence_match(self):
        """Query identical to entity_0 vector → perfect match."""
        registry = _fake_registry(n=3)
        np.random.seed(42)
        vecs = {eid: _normalised(np.random.randn(_DIM).astype(np.float32))
                for eid in registry}
        q = vecs["method.entity_0"]

        model = MagicMock()
        model.encode.side_effect = lambda texts, **kw: np.tile(q, (len(texts), 1))

        eids   = list(vecs.keys())
        matrix = np.stack(list(vecs.values()))

        with patch.object(em, "_model", model), \
             patch.object(em, "_model_name", "fake"), \
             patch("src.normalization.alias_resolver.get_entity_by_id",
                   lambda eid: registry.get(eid)):
            em._eid_list = eids
            em._matrix   = matrix
            em._type_cache["__all__"] = (eids, matrix)
            r = semantic_match("entity_0")

        assert r["match_type"] in ("semantic", "uncertain")
        assert r["canonical_id"] == "method.entity_0"
        assert isinstance(r["confidence"], float)
        assert math.isfinite(r["confidence"])
        _ = f"{r['confidence']:.2f}"   # must not raise

    def test_low_confidence_returns_unknown(self):
        q = _normalised(np.ones(_DIM, dtype=np.float32))
        with _patched(query_vec=q):
            # Orthogonal candidates → sim = 0 with q=[.5,.5,.5,.5]
            em._matrix   = np.array([[1.0, 0.0, 0.0, 0.0],
                                      [0.0, 1.0, 0.0, 0.0]], dtype=np.float32)
            q_orth = np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32)
            em._eid_list = ["method.entity_0", "method.entity_1"]
            em._type_cache["__all__"] = (em._eid_list, em._matrix)
            em._model.encode = lambda texts, **kw: np.tile(q_orth, (len(texts), 1))
            r = semantic_match("unknown_entity")

        assert r["match_type"] == "unknown"
        assert r["reason"] == "LOW_SIMILARITY"
        assert math.isfinite(r["confidence"])
        _ = f"{r['confidence']:.2f}"


# ─────────────────────────────────────────────────────────────────────────────
# 9. Ambiguous acronyms — crash guarantee
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("acronym", ["SAR", "SMA", "RF", "ML", "SCS", "DEM", "NDVI"])
class TestAmbiguousAcronyms:
    def test_does_not_crash(self, acronym):
        q = _normalised(np.random.randn(_DIM).astype(np.float32))
        with _patched(registry=_fake_registry(n=5), query_vec=q):
            r = semantic_match(acronym, expected_type="method")
        assert isinstance(r, dict)
        assert r["match_type"] in ("semantic", "uncertain", "unknown")
        assert isinstance(r["confidence"], float)
        assert math.isfinite(r["confidence"])
        _ = f"{r['confidence']:.2f}"   # the crash this whole layer prevents

    def test_confidence_always_float_on_empty_registry(self, acronym):
        with _patched(registry={}):
            r = semantic_match(acronym, expected_type="method")
        assert isinstance(r["confidence"], float)
        assert math.isfinite(r["confidence"])


# ─────────────────────────────────────────────────────────────────────────────
# 10. top_k_semantic
# ─────────────────────────────────────────────────────────────────────────────

class TestTopKSemantic:
    def test_returns_list(self):
        q = _normalised(np.ones(_DIM, dtype=np.float32))
        with _patched(registry=_fake_registry(n=5), query_vec=q):
            results = top_k_semantic("flood model", k=3)
        assert isinstance(results, list)

    def test_empty_registry_returns_empty(self):
        with _patched(registry={}):
            assert top_k_semantic("SAR", k=3) == []

    def test_no_nan_in_confidence(self):
        q = _normalised(np.ones(_DIM, dtype=np.float32))
        with _patched(registry=_fake_registry(n=5), query_vec=q):
            for r in top_k_semantic("entity", k=5):
                assert math.isfinite(r["confidence"])


# ─────────────────────────────────────────────────────────────────────────────
# 11. cache_info
# ─────────────────────────────────────────────────────────────────────────────

class TestCacheInfo:
    def test_structure(self):
        info = cache_info()
        assert set(info) >= {"cached_types", "active_eid_count",
                             "active_matrix_shape", "model"}
