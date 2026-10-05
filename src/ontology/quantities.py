"""
quantities.py — normalise extracted quantity names to the quantity ontology and check
units against physical dimensions (docs_v2/EQUATION_KG_PLAN.md, phase 1).

Ontology: src/ontology/quantities.json — each quantity has an id, a label, aliases, a
dimension over L, M, T, Θ (empty = dimensionless) and a typical unit. Statistical
quantities whose dimension is that of the variable (mean, standard deviation, observed
value, …) have typical_unit "" and are never dimension-checked.

normalise(name) → Match(quantity_id, method, score, qualifiers):
    exact        the name or an alias, after light normalisation
    stripped     after removing qualifiers ("observed discharge" → discharge, qualifiers
                 ["observed"]) and trailing locators ("discharge at the outlet")
    embedding    nearest alias by sentence embedding, only above a high threshold with a
                 clear margin (see normalise_many)
unit_dimension(unit) → {"L": "1", "T": "-1"} or None when the unit cannot be read.
check_dimension(quantity_id, unit) → "ok" | "mismatch" | "unknown".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

ONTOLOGY = Path(__file__).with_name("quantities.json")

QUALIFIERS = (
    "observed", "simulated", "predicted", "computed", "calculated", "estimated", "measured", "modelled",
    "modeled", "forecasted", "forecast", "mean", "average", "averaged", "maximum", "max", "minimum", "min",
    "peak", "initial", "final", "total", "daily", "monthly", "annual", "yearly", "hourly", "cumulative",
    "actual", "local", "upstream", "downstream", "lateral", "net", "instantaneous", "design", "corresponding",
    "given", "current", "previous", "reference", "dimensionless", "normalized", "normalised", "relative",
    "mean daily", "mean annual", "average annual", "long-term", "spatially averaged", "depth-averaged",
    "critical", "equilibrium", "nominal", "characteristic", "representative", "effective", "saturated",
)
_LOCATOR = re.compile(r"\s+(?:at|in|of|for|on|over|along|across|within|from|during|between)\s+"
                      r"(?:the|a|an|each|this|that|time|day|cell|node|section|point|station|year|step|grid|i|j|t)\b.*$")


@dataclass
class Match:
    quantity_id: str | None
    method: str
    score: float = 0.0
    qualifiers: list[str] = field(default_factory=list)


def _clean(name: str) -> str:
    s = (name or "").lower().strip()
    s = re.sub(r"[’']s\b", "", s)
    s = s.replace("-", " ").replace("_", " ")
    s = re.sub(r"[^a-z0-9 °/]", " ", s)
    s = re.sub(r"^(?:the|a|an)\s+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _singular(s: str) -> str:
    words = s.split()
    if words and len(words[-1]) > 3 and words[-1].endswith("s") and not words[-1].endswith(("ss", "us", "is", "sis")):
        words[-1] = words[-1][:-1]
    return " ".join(words)


@lru_cache(maxsize=1)
def ontology() -> dict:
    data = json.loads(ONTOLOGY.read_text())
    by_id, alias = {}, {}
    for q in data["quantities"]:
        by_id[q["id"]] = q
        for a in [q["label"]] + q.get("aliases", []):
            for key in {_clean(a), _singular(_clean(a))}:
                alias.setdefault(key, q["id"])
    return {"by_id": by_id, "alias": alias, "version": data.get("version")}


_JUNK = re.compile(r"^(?:of|and|or|to|in|is|are|for|with|by|as|explained|see|given|where|which|that|this|these|those)\b"
                   r"|\bsection\b|\bequation\b|\beq\b|\bfig|^order (?:one|unity)$")
_GENERIC = frozenset({"the", "number of", "large", "small", "value", "values", "variable", "variables", "term",
                      "terms", "function", "parameter", "parameters", "coefficient", "constant", "factor",
                      "same", "following", "above", "it", "which", "other", "total", "sum", "ratio"})
#: too generic to stand as the head of a longer name ("von karman constant" is not any "constant")
_WEAK_HEAD = _GENERIC | {"multiplier", "kernel", "loss", "rate", "number", "index", "model", "parameter", "data", "vector"}


_CLAUSE = re.compile(r"\b(?:and|or|but|not|their|its|is|are|was|which|that|usually|respectively|"
                     r"both|either|neither|when|if|than|being|been|has|have)\b")


def is_junk(name: str) -> bool:
    """Extraction debris that is not the name of a quantity ('the', 'of order one',
    'explained in section 2.2.1'): excluded from coverage, never normalised."""
    s = _clean(name)
    return (not s or s in _GENERIC or bool(re.search(r"\b(?:and|or|of|the|to|in|for|with|by|is)$", s))
            or len(re.sub(r"[^a-z]", "", s)) < 3 or bool(_JUNK.search(s))
            or sum(ch.isdigit() for ch in s) > 3)


def normalise(name: str) -> Match:
    o = ontology()
    s = _clean(name)
    if is_junk(name):
        return Match(None, "not_a_quantity")
    for cand in (s, _singular(s)):
        if cand in o["alias"]:
            return Match(o["alias"][cand], "exact", 1.0)
    quals: list[str] = []
    t = _LOCATOR.sub("", s)
    changed = True
    while changed:
        changed = False
        for q in sorted(QUALIFIERS, key=len, reverse=True):
            if t.startswith(q + " ") and len(t) > len(q) + 2:
                quals.append(q)
                t = t[len(q) + 1:]
                changed = True
                break
        for cand in (t, _singular(t)):
            if cand in o["alias"]:
                return Match(o["alias"][cand], "stripped", 0.9, quals)
    # head of the noun phrase: "routing reach length" → "reach length"; leading words are qualifiers
    # only for a plain noun phrase: a clause ("rain detected but not observed", "satellite estimates
    # and their average") has no single head
    if _CLAUSE.search(t) or t.startswith("of "):
        return Match(None, "unmatched", 0.0, quals)
    words = t.split()
    for k in (5, 4, 3, 2):                     # a multi-word name at the end: "catchment time of concentration"
        if k < len(words) <= k + 2:
            tail = " ".join(words[-k:])
            if tail in o["alias"]:
                return Match(o["alias"][tail], "head", 0.8, quals + [" ".join(words[:-k])])
    first = re.split(r"\s+(?:of|corresponding to|compared to|relative to|due to|at|in|on|for|over|along|"
                     r"across|within|from|during|between|per|with)\s+", t)[0]
    if first != t:
        for cand in (first, _singular(first)):
            if cand in o["alias"] and cand not in _WEAK_HEAD:
                return Match(o["alias"][cand], "head", 0.8, quals)
    words = first.split()
    for k in (3, 2, 1):
        if len(words) > k and len(words) - k <= 2:          # at most two modifier words
            head = " ".join(words[-k:])
            for cand in (head, _singular(head)):
                if cand in o["alias"] and cand not in _WEAK_HEAD and (k > 1 or len(cand) > 4):
                    return Match(o["alias"][cand], "head", 0.8, quals + [" ".join(words[:-k])])
    return Match(None, "unmatched", 0.0, quals)


# ── units ─────────────────────────────────────────────────────────────────────

_U = {   # unit atom → dimension exponents (L, M, T, Θ); scale is irrelevant for the check
    "m": {"L": 1}, "mm": {"L": 1}, "cm": {"L": 1}, "km": {"L": 1}, "µm": {"L": 1}, "um": {"L": 1}, "nm": {"L": 1},
    "ft": {"L": 1}, "in": {"L": 1}, "mi": {"L": 1},
    "s": {"T": 1}, "sec": {"T": 1}, "min": {"T": 1}, "h": {"T": 1}, "hr": {"T": 1}, "hrs": {"T": 1},
    "d": {"T": 1}, "day": {"T": 1}, "days": {"T": 1}, "yr": {"T": 1}, "year": {"T": 1}, "years": {"T": 1},
    "a": {"T": 1}, "month": {"T": 1}, "months": {"T": 1}, "wk": {"T": 1},
    "kg": {"M": 1}, "g": {"M": 1}, "mg": {"M": 1}, "t": {"M": 1}, "mg/l": {"M": 1, "L": -3},
    "k": {"Θ": 1}, "°c": {"Θ": 1}, "°f": {"Θ": 1}, "°": {},
    "j": {"M": 1, "L": 2, "T": -2}, "kj": {"M": 1, "L": 2, "T": -2}, "mj": {"M": 1, "L": 2, "T": -2},
    "w": {"M": 1, "L": 2, "T": -3}, "kw": {"M": 1, "L": 2, "T": -3}, "mw": {"M": 1, "L": 2, "T": -3},
    "pa": {"M": 1, "L": -1, "T": -2}, "kpa": {"M": 1, "L": -1, "T": -2}, "hpa": {"M": 1, "L": -1, "T": -2},
    "mpa": {"M": 1, "L": -1, "T": -2}, "bar": {"M": 1, "L": -1, "T": -2}, "mbar": {"M": 1, "L": -1, "T": -2},
    "n": {"M": 1, "L": 1, "T": -2}, "kn": {"M": 1, "L": 1, "T": -2},
    "ha": {"L": 2}, "l": {"L": 3}, "ml": {"L": 3},
    "%": {}, "-": {}, "‰": {}, "dimensionless": {}, "unitless": {}, "rad": {}, "deg": {}, "db": {}, "ppm": {},
    "hz": {"T": -1},
}
_DIMNOTE = re.compile(r"^\[?\s*(?:[LMTΘ]\s?(?:\^?-?\d+(?:/\d+)?)?\s*/?\s*)+\]?$")


def _add(dim: dict, atom: dict, power: Fraction) -> None:
    for k, v in atom.items():
        dim[k] = dim.get(k, Fraction(0)) + Fraction(v) * power


def _exponent(tok: str) -> Fraction:
    """A unit exponent; "21".."24" are "−1".."−4" whose minus glyph the PDF text layer reads
    as "2" (Copernicus/AMS fonts: "m s 21", "kg m 23")."""
    if re.fullmatch(r"2[1-4]", tok):
        return -Fraction(int(tok) - 20)
    return Fraction(tok)


def unit_dimension(unit: str | None) -> dict | None:
    """Dimension of a unit string, or None if any token cannot be read."""
    if not unit:
        return None
    u = unit.strip().replace("−", "-").replace("·", " ").replace("*", " ")
    u = re.sub(r"\\\(|\\\)|\{|\}|\\,", " ", u)
    u = re.sub(r"\^\s*", "^", u)
    if _DIMNOTE.match(u.replace(" ", " ")) and re.search(r"[LMTΘ]", u):      # "[L T-1]", "L3/T"
        dim: dict = {}
        sign = 1
        for slash, b, e in re.findall(r"(/?)\s*([LMTΘ])\s?\^?(-?\d+(?:/\d+)?)?", u):
            sign = -1 if slash else sign                       # "L3/T": everything after "/" divides
            dim[b] = dim.get(b, Fraction(0)) + Fraction(e or "1") * sign
        return {k: v for k, v in dim.items() if v != 0}
    dim: dict = {}
    sign = 1
    last: dict | None = None
    for tok in re.split(r"\s+|(?=/[a-zµ°%])", u.lower()):
        if not tok:
            continue
        if tok.startswith("/"):
            sign = -1
            tok = tok[1:]
            if not tok:
                continue
        if re.fullmatch(r"-?\d+(?:/\d+)?", tok) and last is not None:   # "m/s 2": exponent as its own token
            _add(dim, last, (_exponent(tok) - 1) * sign)
            continue
        m = re.fullmatch(r"([a-zµ°%‰\-]+?)\^?(-?\d+(?:[./]\d+)?)?", tok)
        if not m:
            return None
        atom, exp = m.group(1), m.group(2)
        if atom not in _U and atom == "ms" and exp:            # "ms^-2" in hydrology papers: m s^-2
            _add(dim, _U["m"], Fraction(sign)); atom = "s"
        if atom not in _U:
            return None
        power = _exponent(exp) if exp else Fraction(1)
        _add(dim, _U[atom], power * sign)
        last = _U[atom]
    return {k: v for k, v in dim.items() if v != 0}


def quantity_dimension(quantity_id: str) -> dict | None:
    q = ontology()["by_id"].get(quantity_id)
    if q is None or q.get("typical_unit", None) == "":
        return None                                   # dimension of the variable it describes
    return {k: Fraction(v) for k, v in q.get("dimension", {}).items()}


#: what the clause parser takes for a unit but is the argument of the symbol: "Q(t)", "h(x)"
_ARGUMENTS = frozenset({"t", "x", "y", "z", "i", "j", "k", "n"})


def quantity_alt_dimensions(quantity_id: str) -> list[dict]:
    """Dimensions accepted by hydrological convention besides the primary one."""
    q = ontology()["by_id"].get(quantity_id) or {}
    return [{k: Fraction(v) for k, v in a["dimension"].items()} for a in q.get("alt_dimensions", [])]


def check_dimension(quantity_id: str | None, unit: str | None) -> str:
    if not quantity_id or not unit or unit.strip() in _ARGUMENTS:
        return "unknown"
    expected = quantity_dimension(quantity_id)
    observed = unit_dimension(unit)
    if expected is None or observed is None:
        return "unknown"
    if observed == expected:
        return "ok"
    if unit.strip() == "L" and expected == {"L": Fraction(3)}:      # litre
        return "ok"
    if any(observed == alt for alt in quantity_alt_dimensions(quantity_id)):
        return "ok_convention"            # mm of storage, mm d-1 of precipitation, …
    # "L" alone is both litre and the dimension symbol for length; accept either reading
    return "mismatch"


def dimension_text(dim: dict | None) -> str | None:
    if dim is None:
        return None
    if not dim:
        return "1"
    return " ".join(f"{k}{'' if v == 1 else v}" for k, v in sorted(dim.items(), key=lambda kv: "LMTΘ".index(kv[0])))


def normalise_many(names: list[str], *, threshold: float = 0.88, margin: float = 0.03,
                   model_name: str = "BAAI/bge-large-en-v1.5") -> dict[str, Match]:
    """normalise() for many names, with an embedding fallback for the unmatched ones."""
    out = {n: normalise(n) for n in names}
    rest = [n for n, m in out.items() if m.quantity_id is None and m.method != "not_a_quantity"]
    if not rest:
        return out
    from sentence_transformers import SentenceTransformer
    import numpy as np
    o = ontology()
    keys = sorted(o["alias"])
    model = SentenceTransformer(model_name, device="cpu")
    ka = model.encode(keys, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    ra = model.encode([_clean(n) for n in rest], batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    sims = ra @ ka.T
    for n, row in zip(rest, sims):
        order = np.argsort(-row)
        best, second = order[0], next((j for j in order[1:] if o["alias"][keys[j]] != o["alias"][keys[order[0]]]), None)
        s1 = float(row[best]); s2 = float(row[second]) if second is not None else 0.0
        if s1 >= threshold and s1 - s2 >= margin:
            out[n] = Match(o["alias"][keys[best]], "embedding", round(s1, 3), out[n].qualifiers)
    return out
