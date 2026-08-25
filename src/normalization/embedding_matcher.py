"""
embedding_matcher.py  —  GeoHydroAI fault-tolerant semantic fallback matcher
=============================================================================

Design contract
---------------
* Returns a well-formed result dict in ALL code paths — never raises.
* Every abnormal path emits a structured log.warning so the caller can
  distinguish:
    - EMPTY_CANDIDATES   no ontology entities for the requested type
    - EMPTY_QUERY        raw_name is blank / whitespace
    - INVALID_QUERY_VEC  encoded query vector is zero / NaN / Inf
    - INVALID_SIMS       similarity array contains NaN / Inf
    - LOW_SIMILARITY     best match below the uncertain threshold
    - SEMANTIC           accepted confident match
    - UNCERTAIN          match between uncertain and accept thresholds

Root causes of the NoneType.__format__ crash (historical, now prevented)
------------------------------------------------------------------------
1. `_build_index()` raised ValueError on empty type → propagated past the
   caller's try/except, left _eid_list/_matrix in a stale state from a
   previous call → next call used the wrong type's index.
2. `sims[best_idx]` can be NaN when the query vector has zero L2-norm
   (empty string → zero vector → NaN cosine similarity).
   NaN passes `if confidence < 0.68` (False in IEEE), lands in the semantic
   branch, and any downstream `f"{NaN:.2f}"` formats fine — but if the value
   was actually None (object array edge-case), that's the crash.
3. The safe fix is a multi-layer validation gate BEFORE float() conversion.

Model preference
----------------
    1. BAAI/bge-large-en-v1.5          (primary, 1024-dim)
    2. sentence-transformers/all-mpnet-base-v2  (fallback, 768-dim)

Thresholds
----------
    >= 0.82  → SEMANTIC   (confident match)
    0.68–0.82 → UNCERTAIN  (returned with low confidence)
    < 0.68   → UNKNOWN
"""

from __future__ import annotations

import logging
import math
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
ACCEPT_THRESHOLD    = 0.82
UNCERTAIN_THRESHOLD = 0.68

_PRIMARY_MODEL  = "BAAI/bge-large-en-v1.5"
_FALLBACK_MODEL = "sentence-transformers/all-mpnet-base-v2"

# ── Module-level singletons ───────────────────────────────────────────────────
_model      = None
_model_name: str = ""
# Current active index (set by _build_index for the last requested type)
_eid_list:  list[str]            = []
_matrix:    Optional[np.ndarray] = None   # shape (N, D), L2-normalised; None = no candidates

# Per-type index cache: cache_key → (eid_list, matrix | None)
_type_cache: dict[str, tuple[list[str], Optional[np.ndarray]]] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

# ── Tier-downgrade телеметрія (Фаза 2.5) ──────────────────────────────────────
# Рахує причини, з яких semantic-tier не дав результату. Якщо прогін пройшов
# зі зламаною моделлю — розподіл match_type зміщується мовчки; цей лічильник
# робить це видимим (normalization_runner логує снапшот наприкінці).
from collections import Counter as _Counter

TELEMETRY: _Counter = _Counter()


def telemetry_snapshot() -> dict[str, int]:
    """Знімок лічильників деградації semantic-tier (per-process)."""
    return dict(TELEMETRY)


def _unknown(raw_name: str, confidence: float = 0.0, reason: str = "") -> dict:
    """Return a well-formed 'unknown' result dict."""
    TELEMETRY[reason or "unspecified"] += 1
    return {
        "raw_name":     raw_name,
        "canonical_id": None,
        "display_name": None,
        "type":         None,
        "match_type":   "unknown",
        "confidence":   round(max(0.0, float(confidence)), 4),
        "reason":       reason,
    }


def _is_finite_scalar(value) -> bool:
    """True iff value is a finite, non-NaN real number."""
    try:
        f = float(value)
        return math.isfinite(f)
    except (TypeError, ValueError):
        return False


def _validate_vec(vec: np.ndarray, label: str) -> bool:
    """
    Return True if vec is a valid L2-normalised embedding.
    Logs a warning and returns False for zero, NaN, or Inf vectors.
    """
    if vec is None:
        log.warning("Embedding validation: %s is None", label)
        return False
    if not isinstance(vec, np.ndarray):
        log.warning("Embedding validation: %s is not ndarray (got %s)", label, type(vec).__name__)
        return False
    if vec.size == 0:
        log.warning("Embedding validation: %s is empty (size=0)", label)
        return False
    if not np.isfinite(vec).all():
        nan_count = int(np.sum(~np.isfinite(vec)))
        log.warning("Embedding validation: %s has %d non-finite elements", label, nan_count)
        return False
    norm = float(np.linalg.norm(vec))
    if norm < 1e-9:
        log.warning("Embedding validation: %s has near-zero L2 norm (%g) — likely empty input", label, norm)
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Model loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_model() -> None:
    global _model, _model_name
    if _model is not None:
        return

    from sentence_transformers import SentenceTransformer

    for model_name in (_PRIMARY_MODEL, _FALLBACK_MODEL):
        try:
            _model      = SentenceTransformer(model_name)
            _model_name = model_name
            if model_name == _FALLBACK_MODEL:
                TELEMETRY["model_fallback_tier"] += 1   # primary недоступна
            log.info("Embedding model loaded: %s", model_name)
            return
        except Exception as exc:
            TELEMETRY["model_load_failed"] += 1
            log.warning("Could not load %s: %s", model_name, exc)

    raise RuntimeError("No embedding model available. Install sentence-transformers.")


# ─────────────────────────────────────────────────────────────────────────────
# Ontology index (lazy, per-type cache)
# ─────────────────────────────────────────────────────────────────────────────

def _build_index(expected_type: Optional[str] = None) -> None:
    """
    Populate the global _eid_list / _matrix for the given type.

    Critical invariant: this function ALWAYS updates _eid_list and _matrix
    before returning — even on the empty-candidates path — so semantic_match()
    never sees stale globals from a previous call with a different type.

    Cache entry ([], None) is stored for types with no candidates so we don't
    re-scan the registry on every call for the same unknown type.
    """
    global _eid_list, _matrix

    cache_key = expected_type if expected_type is not None else "__all__"

    if cache_key in _type_cache:
        _eid_list, _matrix = _type_cache[cache_key]
        return

    _load_model()

    from src.normalization.alias_resolver import load_ontology_registry
    registry = load_ontology_registry()

    entities = [
        (eid, ent) for eid, ent in registry.items()
        if expected_type is None or ent.get("type") == expected_type
    ]

    if not entities:
        log.warning(
            "EMPTY_CANDIDATES: no ontology entities for type=%s "
            "— semantic match will return unknown",
            expected_type,
        )
        _eid_list, _matrix = [], None
        _type_cache[cache_key] = ([], None)
        return

    texts: list[str] = []
    eids:  list[str] = []
    for eid, ent in entities:
        parts = [ent.get("display_name", "")]
        parts += ent.get("aliases", [])[:3]
        parts.append(ent.get("definition", "")[:120])
        texts.append(" ".join(p for p in parts if p))
        eids.append(eid)

    log.info(
        "Building embedding index: %d entities (type=%s, model=%s)",
        len(eids), expected_type or "all", _model_name,
    )
    try:
        embeddings: np.ndarray = _model.encode(  # type: ignore[union-attr]
            texts, normalize_embeddings=True, show_progress_bar=False,
        )
    except Exception as exc:
        log.warning(
            "ENCODE_FAILURE in _build_index: cannot encode candidates "
            "(type=%s): %s — semantic match disabled for this type",
            expected_type, exc,
        )
        _eid_list, _matrix = [], None
        _type_cache[cache_key] = ([], None)
        return

    # Validate the candidate matrix
    if not isinstance(embeddings, np.ndarray) or embeddings.size == 0:
        log.warning("INVALID_CANDIDATE_MATRIX: encode returned unusable result for type=%s", expected_type)
        _eid_list, _matrix = [], None
        _type_cache[cache_key] = ([], None)
        return

    if not np.isfinite(embeddings).all():
        nan_rows = int(np.sum(~np.isfinite(embeddings).all(axis=1)))
        log.warning(
            "INVALID_CANDIDATE_MATRIX: %d / %d candidate rows contain NaN/Inf (type=%s)",
            nan_rows, len(eids), expected_type,
        )
        # Mask out bad rows rather than discarding the whole matrix
        valid = np.isfinite(embeddings).all(axis=1)
        eids  = [e for e, ok in zip(eids, valid) if ok]
        embeddings = embeddings[valid]
        if len(eids) == 0:
            _eid_list, _matrix = [], None
            _type_cache[cache_key] = ([], None)
            return

    _eid_list = eids
    _matrix   = embeddings
    _type_cache[cache_key] = (_eid_list, _matrix)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def semantic_match(
    raw_name: str,
    expected_type: Optional[str] = None,
) -> dict:
    """
    Find the nearest ontology entity to `raw_name` via embedding similarity.

    Never raises — returns match_type="unknown" on every failure path.

    Returns:
        {
          "raw_name":     str,
          "canonical_id": str | None,
          "display_name": str | None,
          "type":         str | None,
          "match_type":   "semantic" | "uncertain" | "unknown",
          "confidence":   float,          # always finite, always [0.0, 1.0]
          "reason":       str,            # populated on non-semantic paths
        }
    """
    # ── Gate 0: empty / blank input ──────────────────────────────────────────
    if not raw_name or not raw_name.strip():
        log.warning("EMPTY_QUERY: raw_name is blank — returning unknown")
        return _unknown(raw_name, reason="EMPTY_QUERY")

    # ── Build / recall index ─────────────────────────────────────────────────
    _build_index(expected_type)
    _load_model()

    # ── Gate 1: no candidates for this type ─────────────────────────────────
    if _matrix is None or not _eid_list:
        log.debug(
            "EMPTY_CANDIDATES: no index for type=%s, entity=%r",
            expected_type, raw_name,
        )
        return _unknown(raw_name, reason="EMPTY_CANDIDATES")

    # ── Gate 2: encode query and validate ───────────────────────────────────
    try:
        query_vec: np.ndarray = _model.encode(  # type: ignore[union-attr]
            [raw_name], normalize_embeddings=True,
        )[0]
    except Exception as exc:
        log.warning(
            "ENCODE_FAILURE: model.encode failed for entity=%r type=%s: %s",
            raw_name, expected_type, exc,
        )
        return _unknown(raw_name, reason=f"ENCODE_FAILURE:{exc}")

    if not _validate_vec(query_vec, label=f"query({raw_name!r})"):
        return _unknown(raw_name, reason="INVALID_QUERY_VEC")

    # ── Gate 3: compute similarities and validate ────────────────────────────
    try:
        sims: np.ndarray = _matrix @ query_vec
    except Exception as exc:
        log.warning(
            "SIMILARITY_FAILURE: matrix multiply failed entity=%r type=%s: %s",
            raw_name, expected_type, exc,
        )
        return _unknown(raw_name, reason=f"SIMILARITY_FAILURE:{exc}")

    if not np.isfinite(sims).any():
        log.warning(
            "INVALID_SIMS: all similarities are NaN/Inf for entity=%r type=%s "
            "(matrix shape=%s, query_vec shape=%s)",
            raw_name, expected_type, _matrix.shape, query_vec.shape,
        )
        return _unknown(raw_name, reason="INVALID_SIMS")

    # Replace any NaN/Inf similarities with -1 before argmax
    sims_safe = np.where(np.isfinite(sims), sims, -1.0)

    best_idx = int(np.argmax(sims_safe))
    raw_score = sims_safe[best_idx]

    # ── Gate 4: safe score extraction ───────────────────────────────────────
    if not _is_finite_scalar(raw_score):
        log.warning(
            "INVALID_SCORE: best similarity score=%s is not finite "
            "for entity=%r type=%s",
            raw_score, raw_name, expected_type,
        )
        return _unknown(raw_name, reason="INVALID_SCORE")

    confidence = float(raw_score)

    # ── Gate 5: threshold decision ───────────────────────────────────────────
    if confidence < UNCERTAIN_THRESHOLD:
        log.debug(
            "LOW_SIMILARITY: entity=%r type=%s best_score=%.4f < %.2f",
            raw_name, expected_type, confidence, UNCERTAIN_THRESHOLD,
        )
        return _unknown(raw_name, confidence=confidence, reason="LOW_SIMILARITY")

    from src.normalization.alias_resolver import get_entity_by_id
    best_id = _eid_list[best_idx]
    entity  = get_entity_by_id(best_id) or {}

    match_type = "semantic" if confidence >= ACCEPT_THRESHOLD else "uncertain"

    log.debug(
        "%s: entity=%r → %s (confidence=%.4f, type=%s)",
        match_type.upper(), raw_name, best_id, confidence, expected_type,
    )
    return {
        "raw_name":     raw_name,
        "canonical_id": best_id,
        "display_name": entity.get("display_name"),
        "type":         entity.get("type"),
        "match_type":   match_type,
        "confidence":   round(confidence, 4),
        "reason":       "",
    }


def top_k_semantic(
    raw_name: str,
    k: int = 5,
    expected_type: Optional[str] = None,
) -> list[dict]:
    """
    Return the top-k nearest ontology entities by embedding similarity.
    Useful for human review of uncertain matches.
    Never raises — returns [] on any failure.
    """
    try:
        _build_index(expected_type)
        _load_model()

        if _matrix is None or not _eid_list:
            return []

        query_vec: np.ndarray = _model.encode(  # type: ignore[union-attr]
            [raw_name], normalize_embeddings=True,
        )[0]

        if not _validate_vec(query_vec, label=f"top_k query({raw_name!r})"):
            return []

        sims     = _matrix @ query_vec
        sims_safe = np.where(np.isfinite(sims), sims, -1.0)
        top_k_idx = np.argsort(-sims_safe)[:k]

        from src.normalization.alias_resolver import get_entity_by_id
        results = []
        for idx in top_k_idx:
            eid    = _eid_list[idx]
            entity = get_entity_by_id(eid) or {}
            score  = float(sims_safe[idx])
            results.append({
                "canonical_id": eid,
                "display_name": entity.get("display_name"),
                "type":         entity.get("type"),
                "confidence":   round(score, 4),
            })
        return results
    except Exception as exc:
        log.warning("top_k_semantic failed for %r: %s", raw_name, exc)
        return []


def cache_info() -> dict:
    """Diagnostic: return current cache state."""
    return {
        "cached_types": list(_type_cache.keys()),
        "active_eid_count": len(_eid_list),
        "active_matrix_shape": list(_matrix.shape) if _matrix is not None else None,
        "model": _model_name or None,
    }
