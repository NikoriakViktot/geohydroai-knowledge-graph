"""
formula_structure.py — the structural form of an equation (docs_v2/EQUATION_KG_PLAN.md, phase 2).

Two hashes per equation; the raw LaTeX is never replaced:

    formula_hash             FORMULA_TEXT_HASH: sha256[:16] of the LaTeX without formatting
                             (equation_records.canonical_latex; "tei:<…>" for GROBID text)
    formula_structural_hash  sha256[:16] of the canonical SymPy expression tree:
                             \\frac{Q}{A}, Q/A and {Q \\over A} give one hash; a + b = b + a;
                             0.2 = \\frac{1}{5}; the two sides of "=" in a fixed order

Symbol names are kept (Q = AV and q = av differ); renaming and rearrangement
(Q = AV ↔ V = Q/A) are phase 3 (ALGEBRAIC), not this hash.

Only LaTeX (Nougat page mode) is parsed; GROBID's plain text gets status "no_latex".
Statuses: ok | no_latex | multiline | multiple | too_long | parse_error | timeout | trivial (a lone
symbol, no hash) | degenerate (the parser reduced it to True/False).

Results are cached by formula_hash in data/equations/structure_<PARSER_VERSION>.parquet
(computing them needs SymPy and takes ~20 ms per formula; the graph loader only reads).

Usage:
    python -m src.document.formula_structure [--paper-list FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import signal
import sys
import warnings
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

log = logging.getLogger("geohydro.document.formula_structure")
ROOT = Path(__file__).resolve().parents[2]
SODB = Path(os.getenv("SODB_DIR", str(ROOT / "data" / "sodb")))
PARSER_VERSION = "struct5"
CACHE = ROOT / "data" / "equations" / f"structure_{PARSER_VERSION}.parquet"
MAX_LEN = 1200

#: multi-letter names written bare in LaTeX that would otherwise parse as products (N·S·E)
_NAMES = ("NSE", "KGE", "RMSE", "NRMSE", "MAE", "MSE", "PBIAS", "RSR", "RVE", "CN", "PET", "AET", "SWE",
          "TWI", "NDWI", "MNDWI", "NDVI", "AUC", "CSI", "POD", "FAR", "HAND", "IoU", "IOU", "RMSD", "MAPE",
          # spectral bands and indices
          "NIR", "SWIR", "SWIR1", "SWIR2", "MIR", "TIR", "RED", "Red", "GREEN", "Green", "BLUE", "Blue",
          "EVI", "SAVI", "NDBI", "LST", "AWEI", "WRI")
_NAME_RE = re.compile(r"(?<![A-Za-z\\])(" + "|".join(sorted(_NAMES, key=len, reverse=True)) + r")(?![A-Za-z])")
_SUB = r"(\{(?:[^{}]|\{[^{}]*\})*\}|[A-Za-z0-9])"
_ACCENT = re.compile(r"\\(bar|overline|underline|hat|widehat|tilde|widetilde|dot|ddot|vec|check)\s*"
                     r"\{\s*(\\?[A-Za-z]+)\s*(?:_\s*" + _SUB + r")?\s*\}(?:\s*_\s*" + _SUB + r")?")
_SUBWORD = re.compile(r"\\mathit\{([A-Za-z])([A-Za-z0-9]+)\}_\s*(?:\{([^{}]*)\}|([A-Za-z0-9]))")
_ACCENT_NAME = {"overline": "bar", "widehat": "hat", "widetilde": "tilde"}
_WORD = re.compile(r"\\(?:text|mathrm|textrm|rm|operatorname|mathsf|textit|mathbf|boldsymbol|bm|mathbb|mathcal)\s*"
                   r"\{\s*([^{}]*)\}")
_RM_GROUP = re.compile(r"\{\s*\\(?:rm|it|bf)\s+([^{}]*)\}")
_DROP = re.compile(r"\\(?:left|right|middle|big|Big|bigg|Bigg|bigl|bigr|Bigl|Bigr)(?![A-Za-z])|"
                   r"\\(?:displaystyle|textstyle|scriptstyle|limits|nolimits)(?![A-Za-z])|"
                   r"\\[,;:!]|\\q?quad(?![A-Za-z])|\\(?=\s)|~")
_ENV = re.compile(r"\\(?:begin|end)\s*\{(?:array|split|aligned|align\*?|gathered|equation\*?|eqnarray\*?)\}"
                  r"(?:\s*\{[lcr|]*\})?")


@dataclass
class Structure:
    status: str
    canonical_expression: str | None = None
    structural_hash: str | None = None
    lhs: str | None = None
    symbols: list[str] = field(default_factory=list)
    n_ops: int | None = None
    error: str | None = None


def _sha16(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


_DIGITS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_ESC_RE = re.compile(r"qx(" + "|".join(_DIGITS) + r"|D)xq")


def _mathit(name: str) -> str:
    """\\mathit{…} takes letters only, and its lexer reads "d" + letter as a differential:
    digits and "d" are escaped (qxtwoxq, qxDxq) and restored in _norm_name."""
    name = name.replace("d", "qxDxq")
    return r"\mathit{" + re.sub(r"\d", lambda m: "qx" + _DIGITS[int(m.group())] + "xq", name) + "}"


def _plain(sub: str | None) -> str:
    """Subscript text without markup: {\\mathit{obs}} → obs."""
    return re.sub(r"\\(?:mathit|mathrm|text|rm)|[{}\s]", "", sub or "")


def _subscript(content: str) -> str:
    """Subscript words become one symbol name: Q_{obs,i} → Q_{\\mathit{obsi}}; t-1 stays an expression."""
    plain = re.sub(r"\\(?:mathrm|text|rm|mathit)\s*|[{}\s,]", "", content)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]+", plain) and not re.fullmatch(r"[A-Za-z]\d*", plain):
        return _mathit(plain)
    return content


def _rewrite_subscripts(s: str) -> str:
    """Every _{…} (balanced braces, any depth) through _subscript."""
    out, i = [], 0
    while True:
        j = s.find("_", i)
        if j < 0:
            out.append(s[i:]); break
        k = j + 1
        while k < len(s) and s[k] == " ":
            k += 1
        if k >= len(s) or s[k] != "{":
            out.append(s[i:j + 1]); i = j + 1; continue
        depth, e = 0, k
        while e < len(s):
            depth += {"{": 1, "}": -1}.get(s[e], 0)
            if depth == 0:
                break
            e += 1
        if depth:                                    # unbalanced: leave the rest as it is
            out.append(s[i:]); break
        out.append(s[i:j] + "_{" + _subscript(s[k + 1:e]) + "}")
        i = e + 1
    return "".join(out)


def prepare(latex: str) -> tuple[str | None, str]:
    """LaTeX → a string the SymPy grammar reads, or (None, status)."""
    s = latex.strip()
    s = re.sub(r"\(\s*\d+[a-z]?\s*\)\s*$", "", s).strip()                  # equation number after \]
    s = re.sub(r"^\\\[|\\\]$|^\$\$?|\$\$?$", "", s).strip()
    s = re.sub(r"\\tag\*?\{[^}]*\}|\\label\{[^}]*\}|\\nonumber|\\notag", "", s)
    s = _ENV.sub("", s)
    if "\\\\" in s or r"\begin{cases}" in s:
        return None, "multiline"
    if len(s) > MAX_LEN:
        return None, "too_long"
    s = _DROP.sub(" ", s)
    s = s.replace("&", " ")
    s = re.sub(r"\\%|%", "", s)
    s = re.sub(r"\\exp\s*\^", "e^", s)
    s = re.sub(r"\{\s*([()\[\]|])\s*\}", r"\1", s)                         # \big{(} → (
    s = re.sub(r"\\Sigma(?=\s*_)", r"\\sum", s)
    s = re.sub(r"\\Pi(?=\s*_)", r"\\prod", s)
    s = re.sub(r"\\approx|\\simeq|\\cong|\\equiv|:=|\\coloneqq", "=", s)
    s = re.sub(r"^\s*(?:\\text|\\mathrm)?\s*\{?[A-Za-z][A-Za-z0-9 ]*\}?\s*:(?!=)\s*", "", s)   # "Model 2: Q = …"
    # X^{a}_{b} → X_{b}^{a}  (the grammar drops a subscript after a superscript)
    s = re.sub(r"\^\s*(\{[^{}]*\}|[A-Za-z0-9])\s*_\s*(\{[^{}]*\}|[A-Za-z0-9])", r"_\2^\1", s)
    # primes and stars are part of the name: a^{\prime} → a_{prime}
    s = re.sub(r"(\\?[A-Za-z]+)(?:_\s*\{([^{}]*)\}|_([A-Za-z0-9]))?\s*\^\s*\{?\s*(\\prime|\\ast|\*|-|\+)\s*\}|"
               r"(\\?[A-Za-z]+)(?:_\s*\{([^{}]*)\}|_([A-Za-z0-9]))?(')",
               lambda m: (m.group(1) or m.group(5)) + "_{" + re.sub(r"\s", "", m.group(2) or m.group(3) or m.group(6) or m.group(7) or "")
               + {"\\prime": "prime", "'": "prime", "-": "minus", "+": "plus"}.get(m.group(4) or m.group(8), "star") + "}", s)
    s = _RM_GROUP.sub(lambda m: r"{\text{" + m.group(1) + "}}", s)
    for _ in range(3):                                                     # nested \text{\rm …}
        s = _WORD.sub(lambda m: _mathit(re.sub(r"[^A-Za-z0-9]", "", m.group(1)))
                      if len(re.sub(r"[^A-Za-z]", "", m.group(1))) > 1
                      else re.sub(r"^([A-Za-z])(\d+)$", r"\1_{\2}", re.sub(r"\s", "", m.group(1))), s)
    s = _ACCENT.sub(lambda m: m.group(2) + "_{" + _ACCENT_NAME.get(m.group(1), m.group(1))
                    + _plain(m.group(3)) + _plain(m.group(4)) + "}", s)
    s = _NAME_RE.sub(lambda m: _mathit(m.group(1).upper()), s)        # Green = GREEN, IoU = IOU
    for _ in range(3):                                                     # \mathit{\mathit{NSE}}
        s = re.sub(r"\\mathit\{\s*\\mathit\{([^{}]*)\}\s*\}", r"\\mathit{\1}", s)
    # a sum without limits is a sum over all items: \sum x, \sum_{i} x → \sum_{i=1}^{n} x
    s = re.sub(r"\\(sum|prod)\s*_\s*(?:\{\s*([a-z])\s*\}|([a-z]))(?!\s*\^)",
               lambda m: "\\" + m.group(1) + "_{" + (m.group(2) or m.group(3)) + "=1}^{n}", s)
    s = re.sub(r"\\(sum|prod)(?!\s*_)", r"\\\1_{i=1}^{n}", s)
    s = re.sub(r"(\\?[A-Za-z]+)(?:_\s*\{([^{}]*)\}|_([A-Za-z0-9]))?\s*\^\s*\{\s*(?:\\mathit\{)?([A-Za-z]{2,})\}?\s*\}",
               lambda m: m.group(1) + "_{" + re.sub(r"\s|\\mathit|[{}]", "", m.group(2) or m.group(3) or "") + m.group(4) + "}"
               if m.group(4) not in ("prime",) else m.group(0), s)
    s = re.sub(r"_\s*(\\mathit\{[^{}]*\})", r"_{\1}", s)                    # V_\mathit{cc}
    if re.search(r"\\mathit\{(?:and|or|where|for|if|with|otherwise|when|else)\}",
                 s.replace("qxDxq", "d")):                                                 # "a = b and c = d"
        return None, "multiple"
    # \mathit{Qobs}_{i} (a name with a subscript; the grammar rejects it) → Q_{obsi}
    s = _SUBWORD.sub(lambda m: m.group(1) + "_{" + m.group(2) + re.sub(r"\s", "", m.group(3) or m.group(4) or "") + "}", s)
    # _{…} with words → one name
    s = _rewrite_subscripts(s)
    # {A \over B} → \frac{A}{B}
    for _ in range(5):
        new = re.sub(r"\{([^{}]*)\\over([^{}]*)\}", r"\\frac{\1}{\2}", s)
        if new == s:
            break
        s = new
    s = re.sub(r"[\s,.;]+$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return (s, "ok") if s else (None, "parse_error")


@contextmanager
def _time_limit(seconds: int):
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _raise(*_):
        raise TimeoutError

    old = signal.signal(signal.SIGALRM, _raise)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _top_level_split(s: str, sep: str) -> list[str]:
    """Split on sep outside braces, brackets and parentheses."""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [x.strip() for x in out]


def _norm_name(name: str) -> str:
    name = _ESC_RE.sub(lambda m: "d" if m.group(1) == "D" else str(_DIGITS.index(m.group(1))), name)
    return re.sub(r"[{}\s]", "", name)


def canonical(expr):
    """Exact numbers, normalised symbol names, e → E, pi → π; sides of an equation sorted."""
    import sympy as sp
    rep = {}
    for sym in expr.free_symbols:
        n = _norm_name(sym.name)
        rep[sym] = sp.E if n == "e" else sp.pi if n == "pi" else sp.Symbol(n)
    expr = expr.xreplace(rep)
    floats = {f: sp.Rational(str(f)) for f in expr.atoms(sp.Float)}
    if floats:
        expr = expr.xreplace(floats)
    if isinstance(expr, sp.Equality):
        a, b = expr.lhs, expr.rhs
        a, b = sorted((a, b), key=sp.srepr)
        return sp.Eq(a, b, evaluate=False)
    return expr


def _cancelled(expr, parts: list[str]) -> bool:
    """SymPy evaluates x − x to 0: a side, or a sum's summand, that became 0 although the
    LaTeX wrote something else (Nougat lost the mark that told the two symbols apart)."""
    import sympy as sp
    if any(isinstance(x, sp.Sum) and x.function == 0 for x in sp.preorder_traversal(expr)):
        return True
    if isinstance(expr, sp.Equality):
        zeros = [x for x in (expr.lhs, expr.rhs) if x == 0]
        written_zeros = sum(re.fullmatch(r"\s*0\s*", t) is not None for t in parts)
        return len(zeros) > written_zeros
    return False


def structure(latex: str | None, *, timeout: int = 5) -> Structure:
    if not latex or not isinstance(latex, str):
        return Structure("no_latex")
    s, status = prepare(latex)
    if s is None:
        return Structure(status)
    try:
        with _time_limit(timeout), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import sympy as sp
            from sympy.parsing.latex import parse_latex
            parts = _top_level_split(s, "=")
            if len(parts) > 2:                                             # a = b = c
                exprs = [canonical(parse_latex(x, strict=True)) for x in parts]
                lhs = exprs[0]
                ordered = sorted(exprs, key=sp.srepr)
                can = sp.Tuple(*ordered)
                key = "chain:" + sp.srepr(can)
                text = "Eq(" + ", ".join(sp.sstr(x) for x in ordered) + ")"
            else:
                expr = parse_latex(s, strict=True)
                if isinstance(expr, sp.logic.boolalg.BooleanAtom):
                    return Structure("degenerate")
                lhs = canonical(expr.lhs) if isinstance(expr, sp.Equality) else None
                can = canonical(expr)
                if _cancelled(can, parts):
                    return Structure("degenerate", sp.sstr(can))
                if not isinstance(can, (sp.Equality, sp.core.relational.Relational)) and (
                        len(can.free_symbols) < 2 or sp.count_ops(can) == 0):
                    return Structure("trivial", sp.sstr(can))
                key, text = sp.srepr(can), sp.sstr(can)
            out = Structure("ok", text, _sha16(key), sp.sstr(lhs) if lhs is not None else None,
                            sorted({_norm_name(x.name) for x in can.free_symbols}), int(sp.count_ops(can)))
    except TimeoutError:
        return Structure("timeout")
    except Exception as exc:                                     # the grammar rejects it
        return Structure("parse_error", error=f"{type(exc).__name__}: {str(exc)[:160]}")
    return out


# ── cache ────────────────────────────────────────────────────────────────────

def load_cache(path: Path = CACHE) -> dict[str, dict]:
    if not path.exists():
        return {}
    d = pd.read_parquet(path)
    return {r["formula_hash"]: {k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()}
            for r in d.to_dict("records")}


def build(paper_ids: list[str] | None = None, *, sodb: Path = SODB, cache_path: Path = CACHE) -> dict:
    """Parse every LaTeX formula not yet in the cache; append the new rows."""
    files = sorted(sodb.glob("*/equation_records.parquet"))
    if paper_ids is not None:
        keep = set(paper_ids)
        files = [f for f in files if f.parent.name in keep]
    cache = load_cache(cache_path)
    new = {}
    for f in files:
        d = pd.read_parquet(f, columns=["formula_hash", "latex"])
        for fh, latex in zip(d["formula_hash"], d["latex"]):
            if fh in cache or fh in new or not isinstance(latex, str) or fh.startswith("tei:"):
                continue
            r = asdict(structure(latex))
            r["symbols"] = json.dumps(r["symbols"], ensure_ascii=False)
            new[fh] = {"formula_hash": fh, "latex": latex, "parser_version": PARSER_VERSION, **r}
    if new:
        rows = list(cache.values()) + list(new.values())
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp.parquet")
        pd.DataFrame(rows).to_parquet(tmp, index=False)
        tmp.replace(cache_path)
    allrows = pd.DataFrame(list(cache.values()) + list(new.values()))
    stats = {"new": len(new), "cached": len(allrows)}
    if len(allrows):
        stats["status"] = allrows["status"].value_counts().to_dict()
        ok = allrows[allrows["status"] == "ok"]
        stats["structural_classes"] = int(ok["structural_hash"].nunique())
        stats["formulas_in_shared_classes"] = int(ok["structural_hash"].duplicated(keep=False).sum())
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--paper-list", type=Path)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    ids = [x.strip() for x in args.paper_list.read_text().splitlines() if x.strip()] if args.paper_list else None
    print(json.dumps(build(ids), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
