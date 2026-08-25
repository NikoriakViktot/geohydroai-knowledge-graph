"""
judge_normalizer.py  —  GeoHydroAI LLM output schema normalizer
================================================================

Defensive normalization layer between OllamaActor output and
apply_judge_verdict().

Problem this solves
-------------------
LLM outputs are probabilistic.  The pipeline architecture must be
deterministic.  Without a normalizer, malformed LLM output crashes
apply_judge_verdict() and its validators:

  - ``original_value = {…}`` → TypeError in ``original not in VALID_STUDY_TYPES``
    (set membership requires hashable; dict is not hashable)
  - ``{None, original}`` set literal construction crashes if original is a dict
  - ``float(confidence)`` crashes on dict/list values
  - Nested objects stored as primary_country contaminate the paper graph

Design contract
---------------
normalize_judge_verdict() guarantees that every field consumed by
apply_judge_verdict(), is_valid_judge_study_type_verdict(), and
is_valid_judge_task_verdict() has the correct Python type BEFORE those
functions are called.

Recovery-first strategy (not reject-first)
-------------------------------------------
Before falling back to a safe default, the normalizer attempts schema
recovery to preserve valid information nested inside malformed structures:

  Rule 1 — Label recovery (study_type / task value fields)
    If value is a dict → try dict["label"], then dict["value"],
    dict["type"], dict["name"] in that order.
    If value is a list → try the first element recursively.

  Rule 2 — Country recovery (study_country value fields)
    If value is a list[dict] → extract first dict["name"].
    If value is a dict      → extract dict["name"] or dict["country"].

  Rule 3 — Safe fallback
    Only used when recovery is impossible:
      original_value  → "unknown"   (study_type / task)
      corrected_value → None        (all sub-verdicts)
      country values  → None

Guarantee table
---------------
  Field                      Expected type    Recovery then fallback
  ─────────────────────────  ───────────────  ──────────────────────────────
  study_type / task          dict             {}
  study_country              dict             {}
  *.accepted                 bool             False (no recovery needed)
  *.original_value (label)   str | None       label/value/type/name → "unknown"
  *.corrected_value (label)  str | None       label/value/type/name → None
  *.original_value (country) str | None       list[0]["name"] / dict["name"] → None
  *.corrected_value (country)str | None       same recovery → None
  *.confidence               float [0, 1]     0.0 (no recovery needed)
  *.reason                   str | None       best-effort str() → None
  rivers / data_sources      list             []
"""

from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

_LOG_PREFIX = "[JudgeNormalizer]"

# candidate keys tried in order when a dict is given for a label field
_LABEL_KEYS = ("label", "value", "type", "name")

# ── Repair telemetry (Фаза 2.5) ──────────────────────────────────────────────
# Кожен defensive-ремонт LLM-виводу рахується: якщо модель деградує і 40%
# вердиктів потребують ремонту — це має бути видно, а не приховано.
# Лічильник per-process; читається одразу після normalize_judge_verdict()
# у тому ж процесі (Ray worker) через repair-count у самому вердикті.
_repair_events: int = 0


def _warn(msg: str, *args: Any) -> None:
    """log.warning + інкремент лічильника ремонтів (один repair-факт)."""
    global _repair_events
    _repair_events += 1
    log.warning(msg, *args)


# ─────────────────────────────────────────────────────────────────────────────
# primitive coercers (unchanged from v1)
# ─────────────────────────────────────────────────────────────────────────────

def _coerce_bool(value: Any, field: str) -> bool:
    """Return a bool; coerce int 0/1; default False for anything else."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        coerced = bool(value)
        _warn("%s %s: int %d coerced to %s", _LOG_PREFIX, field, value, coerced)
        return coerced
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        coerced = value.lower() == "true"
        _warn("%s %s: string %r coerced to %s", _LOG_PREFIX, field, value, coerced)
        return coerced
    if value is not None:
        _warn(
            "%s %s: invalid type %s → False",
            _LOG_PREFIX, field, type(value).__name__,
        )
    return False


def _coerce_str_or_none(value: Any, field: str, fallback: Optional[str]) -> Optional[str]:
    """
    Simple str-or-None coercer with no structural recovery.

    Used internally by the recovery functions as their last-resort branch.
    For label/country fields, prefer _recover_label_str / _recover_country_str.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    _warn(
        "%s %s: expected string, got %s → %r",
        _LOG_PREFIX, field, type(value).__name__, fallback,
    )
    return fallback


def _coerce_confidence(value: Any, field: str) -> float:
    """
    Return a float in [0.0, 1.0].

    Anything that cannot be converted, or lies outside [0, 1], becomes 0.0.
    Values are NOT clamped — an out-of-range value (e.g., 95) signals a
    malformed response and should not silently become 1.0.
    """
    if value is None:
        return 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        _warn(
            "%s %s: cannot convert %r to float → 0.0",
            _LOG_PREFIX, field, value,
        )
        return 0.0
    if not (0.0 <= f <= 1.0):
        _warn(
            "%s %s: confidence %.4g is outside [0, 1] → 0.0",
            _LOG_PREFIX, field, f,
        )
        return 0.0
    return round(f, 4)


def _coerce_str_reason(value: Any, field: str) -> Optional[str]:
    """Return reason as string or None; best-effort str() for non-strings."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    _warn(
        "%s %s: reason is %s, coercing to str",
        _LOG_PREFIX, field, type(value).__name__,
    )
    try:
        return str(value)[:500]
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Rule 1 — label field recovery  (study_type / task)
# ─────────────────────────────────────────────────────────────────────────────

def _recover_label_str(
    value: Any,
    field: str,
    fallback: Optional[str],
) -> Optional[str]:
    """
    Return a label string from potentially malformed LLM output.

    Recovery priority for dict values:
        1. dict["label"]  — most common LLM wrapping pattern
        2. dict["value"]  — second most common
        3. dict["type"]   — seen in some structured outputs
        4. dict["name"]   — generic fallback key

    For list values, attempt recovery on the first element.

    Falls back to `fallback` only when no recovery is possible, logging
    the failure as a warning with a distinct message.
    """
    # fast path: already correct type
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None

    # Rule 1a: dict → probe candidate keys
    if isinstance(value, dict):
        for key in _LABEL_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                recovered = candidate.strip()
                _warn(
                    "%s recovered %s from dict.%s=%r",
                    _LOG_PREFIX, field, key, recovered,
                )
                return recovered
        # all candidate keys empty or absent
        _warn(
            "%s failed to recover %s from dict (keys=%s) → %r",
            _LOG_PREFIX, field, list(value.keys()), fallback,
        )
        return fallback

    # Rule 1b: list → try first element
    if isinstance(value, list) and value:
        recovered = _recover_label_str(value[0], field, fallback)
        if recovered != fallback:
            _warn(
                "%s recovered %s from list[0]=%r",
                _LOG_PREFIX, field, recovered,
            )
        else:
            _warn(
                "%s failed to recover %s from list → %r",
                _LOG_PREFIX, field, fallback,
            )
        return recovered

    # Rule 3: nothing worked
    _warn(
        "%s failed to recover %s from %s → %r",
        _LOG_PREFIX, field, type(value).__name__, fallback,
    )
    return fallback


# ─────────────────────────────────────────────────────────────────────────────
# Rule 2 — country field recovery  (study_country)
# ─────────────────────────────────────────────────────────────────────────────

# keys tried when extracting a country name from a dict
_COUNTRY_KEYS = ("name", "country", "label", "value")


def _recover_country_str(value: Any, field: str) -> Optional[str]:
    """
    Return a country name string from potentially malformed LLM output.

    Recovery priority:
        list[dict] → first item's ["name"] / ["country"] / ["label"] / ["value"]
        list[str]  → first non-empty string
        dict       → ["name"] / ["country"] / ["label"] / ["value"]

    Country fields always fall back to None (not "unknown"), because
    an absent country is meaningful and less harmful than a wrong one.
    """
    # fast path
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None

    # Rule 2a: list → extract first valid name
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                recovered = item.strip()
                _warn(
                    "%s recovered %s from list[str]=%r",
                    _LOG_PREFIX, field, recovered,
                )
                return recovered
            if isinstance(item, dict):
                for key in _COUNTRY_KEYS:
                    candidate = item.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        recovered = candidate.strip()
                        _warn(
                            "%s recovered %s from list[0].%s=%r",
                            _LOG_PREFIX, field, key, recovered,
                        )
                        return recovered
        _warn(
            "%s failed to recover %s from list (len=%d) → None",
            _LOG_PREFIX, field, len(value),
        )
        return None

    # Rule 2b: dict → probe candidate keys
    if isinstance(value, dict):
        for key in _COUNTRY_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                recovered = candidate.strip()
                _warn(
                    "%s recovered %s from dict.%s=%r",
                    _LOG_PREFIX, field, key, recovered,
                )
                return recovered
        _warn(
            "%s failed to recover %s from dict (keys=%s) → None",
            _LOG_PREFIX, field, list(value.keys()),
        )
        return None

    _warn(
        "%s failed to recover %s from %s → None",
        _LOG_PREFIX, field, type(value).__name__,
    )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# sub-verdict normalizer (study_type / task / study_country)
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_sub_verdict(raw: Any, name: str) -> dict:
    """
    Normalize a single verdict sub-field (study_type, task, study_country).

    Routes original_value / corrected_value through the appropriate recovery
    function based on sub-verdict name:
      - "study_country" → _recover_country_str  (Rule 2)
      - everything else → _recover_label_str     (Rule 1)

    Returns an empty dict if raw is not a dict — apply_judge_verdict() skips
    empty sub-verdicts gracefully via the ``if st_verdict:`` guards.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _warn(
            "%s %s: expected dict, got %s → skipped",
            _LOG_PREFIX, name, type(raw).__name__,
        )
        return {}

    if name == "study_country":
        original_value  = _recover_country_str(raw.get("original_value"),  f"{name}.original_value")
        corrected_value = _recover_country_str(raw.get("corrected_value"), f"{name}.corrected_value")
    else:
        # study_type, task
        original_value  = _recover_label_str(raw.get("original_value"),  f"{name}.original_value",  fallback="unknown")
        corrected_value = _recover_label_str(raw.get("corrected_value"), f"{name}.corrected_value", fallback=None)

    return {
        "accepted":        _coerce_bool(raw.get("accepted"), f"{name}.accepted"),
        "original_value":  original_value,
        "corrected_value": corrected_value,
        "confidence":      _coerce_confidence(raw.get("confidence"), f"{name}.confidence"),
        "reason":          _coerce_str_reason(raw.get("reason"), f"{name}.reason"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# list normalizer (rivers / data_sources)
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_list(raw: Any, name: str) -> list:
    """Return a list; replace non-list with [] and log a warning."""
    if isinstance(raw, list):
        return raw
    if raw is not None:
        _warn(
            "%s %s: expected list, got %s → []",
            _LOG_PREFIX, name, type(raw).__name__,
        )
    return []


# ─────────────────────────────────────────────────────────────────────────────
# public API
# ─────────────────────────────────────────────────────────────────────────────

def normalize_judge_verdict(raw: Any) -> dict:
    """
    Normalize a raw OllamaActor judge response into a schema-safe dict.

    This function MUST be called between OllamaActor.judge() and
    apply_judge_verdict().  It guarantees:
      1. Fault tolerance  — no crash regardless of LLM output shape.
      2. Information preservation — structured recovery before fallback.
      3. Determinism  — identical input always produces identical output.

    Args:
        raw: The dict returned by OllamaActor.judge() / OllamaJudge.judge().
             May be completely malformed.

    Returns:
        A normalised verdict dict safe to pass to apply_judge_verdict().
        If raw is not a dict at all, returns {"status": "failed"} so that
        apply_judge_verdict() short-circuits without modifying the paper.
    """
    global _repair_events
    _repair_events = 0   # per-verdict counter (читається з результату)

    if not isinstance(raw, dict):
        _warn(
            "%s verdict is not a dict (got %s) → status=failed",
            _LOG_PREFIX, type(raw).__name__,
        )
        return {"status": "failed", "normalizer_repairs": _repair_events}

    out: dict = {}

    # ── pass-through scalar top-level fields ──────────────────────────────
    status = raw.get("status")
    if isinstance(status, str):
        out["status"] = status

    paper_id = raw.get("paper_id")
    if isinstance(paper_id, str):
        out["paper_id"] = paper_id

    # ── normalise sub-verdicts (recovery-first) ───────────────────────────
    out["study_type"]    = _normalize_sub_verdict(raw.get("study_type"),    "study_type")
    out["study_country"] = _normalize_sub_verdict(raw.get("study_country"), "study_country")
    out["task"]          = _normalize_sub_verdict(raw.get("task"),          "task")

    # ── normalise list fields ─────────────────────────────────────────────
    out["rivers"]       = _normalize_list(raw.get("rivers"),       "rivers")
    out["data_sources"] = _normalize_list(raw.get("data_sources"), "data_sources")

    # Телеметрія (Фаза 2.5): скільки defensive-ремонтів знадобилось цьому
    # вердикту. 0 = LLM повернув чистий JSON; зростання середнього по
    # корпусу = деградація моделі, яку раніше нічим було помітити.
    out["normalizer_repairs"] = _repair_events

    return out
