"""
formula_code.py — executable Python and Julia code for each equation
(docs_v2/EQUATION_KG_PLAN.md, phase 4a).

Code is generated from the canonical SymPy tree (formula_structure), never by an LLM, and is
stored once per structural hash; the per-equation docstring (meaning, unit and quantity of every
argument, source paper/page/equation number) is added by annotate().

Per structure:
    compute_<target>(…)   the target in closed form, when it is
                          · a symbol alone on one side            (Q = A R^{2/3} S^{1/2} / n)
                          · a derivative alone on one side         (dS/dt = P − E − Q → compute_dS_dt)
                          · the single solution for a symbol of the original left side
    residual(…)           lhs − rhs, for every equation (implicit ones, root finding, checks)

Translation:
    Σ_{i=lo}^{hi} body    a loop; symbols indexed by i become arrays (O_i → O[i], Q_obsi → Q_obs[i]);
                          an upper limit n used only as a limit becomes length(array)
    ∂h/∂x                 an input named dh_dx (the caller supplies the derivative)
    Q(t − 1)              an input named Q_of_t_1 (the value of the function there)
    ∫                     not translated: status "unsupported"
Identifiers are made safe for both languages (lambda → lambda_, end → end_).

Check (Python only; Julia is generated but not executed — no Julia on this machine):
    · formulas without arrays: compute() equals SymPy's value at random positive points
    · always: residual(compute(…), …) ≈ 0
Statuses: ok | residual_only | unsupported | failed;  check: passed | failed | skipped.

Usage:
    python -m src.document.formula_code                     # build data/equations/code_<CODEGEN_VERSION>.parquet
    python -m src.document.formula_code --eq-id ID [--lang python|julia]   # annotated code of one equation
"""
from __future__ import annotations

import argparse
import json
import keyword
import logging
import math
import os
import random
import re
import sys
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from src.document.formula_structure import CACHE, _time_limit, load_cache

log = logging.getLogger("geohydro.document.formula_code")
ROOT = Path(__file__).resolve().parents[2]
SODB = Path(os.getenv("SODB_DIR", str(ROOT / "data" / "sodb")))
CODEGEN_VERSION = "code3"
OUT = ROOT / "data" / "equations" / f"code_{CODEGEN_VERSION}.parquet"

_JULIA_RESERVED = {"begin", "while", "if", "for", "try", "return", "break", "continue", "function", "macro",
                   "quote", "let", "local", "global", "const", "do", "struct", "module", "baremodule", "using",
                   "import", "export", "end", "else", "elseif", "catch", "finally", "true", "false", "abstract",
                   "primitive", "type", "mutable", "where", "in", "isa", "outer"}
_RESERVED = set(keyword.kwlist) | _JULIA_RESERVED | {"np", "numpy", "sum", "len", "length", "range", "e", "pi", "exp", "log",
                                                     "sqrt", "abs", "max", "min", "math", "i", "j", "k"}


@dataclass
class Code:
    status: str
    target: str | None = None
    form: str | None = None               # explicit | tendency | solved | residual
    args: list[dict] = field(default_factory=list)   # {name, symbol, kind: scalar|array|derivative|function_value}
    python: str | None = None
    julia: str | None = None
    check: str = "skipped"
    error: str | None = None


def ident(name: str) -> str:
    """A symbol name as an identifier valid in Python and Julia."""
    s = re.sub(r"[^0-9A-Za-z_]", "_", name)
    s = re.sub(r"_+", "_", s).strip("_") or "x"
    if s[0].isdigit():
        s = "x" + s
    return s + "_" if s in _RESERVED else s


# ── rewriting the tree ─────────────────────────────────────────────────────────

def _inputs_for_operators(expr):
    """∂h/∂x → symbol dh_dx; Q(t−1) → symbol Q_of_t_1. Returns (expr, {symbol: kind})."""
    import sympy as sp
    from sympy.core.function import AppliedUndef
    kinds, rep = {}, {}
    bound = _bound_indices(expr)
    for node in sp.preorder_traversal(expr):
        if node in rep:
            continue
        if isinstance(node, sp.Derivative):
            f = node.expr
            fname = f.func.__name__ if isinstance(f, AppliedUndef) else str(f)
            parts = []
            for v, cnt in node.variable_count:
                parts.append(f"d{v}{cnt if cnt > 1 else ''}")
            order = sum(c for _, c in node.variable_count)
            name = f"d{order if order > 1 else ''}{fname}_" + "_".join(parts)
            s = sp.Symbol(name)
            rep[node] = s
            kinds[s] = "derivative"
        elif isinstance(node, sp.conjugate):
            a = node.args[0]
            s = sp.Symbol(f"{a.name}_bar" if a.is_Symbol else "mean_of_" + re.sub(r"\W+", "_", str(a)))
            rep[node] = s
            kinds[s] = "scalar"
        elif isinstance(node, AppliedUndef) and node.args and all(a in bound for a in node.args):
            continue                                     # q(j) inside Σ_j is an array element: _arrays
        elif isinstance(node, AppliedUndef):
            s = sp.Symbol(f"{node.func.__name__}_of_" + "_".join(re.sub(r"\W+", "_", str(a)) for a in node.args))
            rep[node] = s
            kinds[s] = "function_value"
    return (expr.xreplace(rep) if rep else expr), kinds


def _split(name: str) -> tuple[str, str]:
    base, _, sub = name.partition("_")
    return base, sub


def _arrays(expr, avoid: frozenset = frozenset()):
    """Symbols indexed by an enclosing sum's index → Indexed(base, index). Returns the new tree,
    the array bases (name → IndexedBase) and the symbols that were turned into arrays."""
    import sympy as sp
    bases: dict = {}
    replaced: dict = {}

    def walk(e, bound: tuple):
        if isinstance(e, (sp.Sum, sp.Product)):
            idx = tuple(str(l[0]) for l in e.limits)
            body = walk(e.function, bound + idx)
            return e.func(body, *e.limits)
        from sympy.core.function import AppliedUndef
        if isinstance(e, AppliedUndef) and e.args and all(str(a) in bound for a in e.args):
            name = e.func.__name__
            label = ident(name) if ident(name) not in avoid else ident(name + "_arr")
            b = bases.setdefault(name, sp.IndexedBase(label))
            replaced[name] = name
            return b[tuple(sp.Symbol(str(a), integer=True) for a in e.args)]
        if e.is_Symbol and bound:
            base, sub = _split(e.name)
            for ix in sorted(bound, key=len, reverse=True):
                if sub == ix or (sub.endswith(ix) and len(sub) > len(ix) + 1 and sub[:-len(ix)].isalpha()):
                    stem = base if sub == ix else f"{base}_{sub[:-len(ix)]}"
                    label = ident(stem) if ident(stem) not in avoid else ident(f"{stem}_{ix}")   # P and P_i
                    b = bases.setdefault(stem, sp.IndexedBase(label))
                    replaced[e.name] = stem
                    return b[sp.Symbol(ix, integer=True)]
                # two indices: x_ij → x[i, j]
                for iy in bound:
                    if iy != ix and sub == ix + iy:
                        label = ident(base) if ident(base) not in avoid else ident(f"{base}_{ix}{iy}")
                        b = bases.setdefault(base, sp.IndexedBase(label))
                        replaced[e.name] = base
                        return b[sp.Symbol(ix, integer=True), sp.Symbol(iy, integer=True)]
            return e
        if e.args:
            return e.func(*[walk(a, bound) for a in e.args])
        return e

    return walk(expr, ()), bases, replaced


def _printers():
    import sympy as sp
    from sympy.printing.julia import JuliaCodePrinter
    from sympy.printing.numpy import NumPyPrinter

    class Py(NumPyPrinter):
        def __init__(self):
            super().__init__({"fully_qualified_modules": True, "allow_unknown_functions": True})

        def _print_Sum(self, expr):
            out = self._print(expr.function)
            for v, lo, hi in reversed(expr.limits):
                out = f"sum({out} for {self._print(v)} in range({self._print(lo)}, {self._print(hi)} + 1))"
            return f"({out})"

        def _print_Product(self, expr):
            out = self._print(expr.function)
            for v, lo, hi in reversed(expr.limits):
                out = f"numpy.prod([{out} for {self._print(v)} in range({self._print(lo)}, {self._print(hi)} + 1)])"
            return out

        def _print_Indexed(self, expr):
            return f"{self._print(expr.base.label)}[{', '.join(self._print(i - 1) for i in expr.indices)}]"

        def _print_Symbol(self, expr):
            return ident(expr.name)

        def _print_Exp1(self, expr):
            return "numpy.e"

        def _print_Pi(self, expr):
            return "numpy.pi"

    class Jl(JuliaCodePrinter):
        def __init__(self):
            super().__init__({"allow_unknown_functions": True})

        def _print_Sum(self, expr):
            out = self._print(expr.function)
            for v, lo, hi in reversed(expr.limits):
                out = f"sum({out} for {self._print(v)} in {self._print(lo)}:{self._print(hi)})"
            return f"({out})"

        def _print_Product(self, expr):
            out = self._print(expr.function)
            for v, lo, hi in reversed(expr.limits):
                out = f"prod({out} for {self._print(v)} in {self._print(lo)}:{self._print(hi)})"
            return f"({out})"

        def _print_Indexed(self, expr):
            return f"{self._print(expr.base.label)}[{', '.join(self._print(i) for i in expr.indices)}]"

        def _print_Symbol(self, expr):
            return ident(expr.name)

    return Py(), Jl()


# ── generation ─────────────────────────────────────────────────────────────────

def _target(eq, lhs_text: str | None):
    """(target symbol, its closed form, form) or (None, None, 'residual')."""
    import sympy as sp
    a, b = eq.lhs, eq.rhs
    sides = [(a, b), (b, a)]
    if lhs_text:                                         # the side the author wrote on the left first
        sides.sort(key=lambda s: sp.sstr(s[0]) != lhs_text)
    for one, other in sides:
        if one.is_Symbol and one not in other.free_symbols:
            form = "tendency" if one.name.startswith("d") and "_d" in one.name else "explicit"
            return one, other, form
    left = sides[0][0]
    for s in sorted(left.free_symbols, key=lambda x: x.name):
        if s in sides[0][1].free_symbols:
            continue
        # solve() mangles Sum objects (it dropped the Σ of NSE): hide them behind symbols
        hidden = {}
        for node in sp.preorder_traversal(sp.Eq(a, b)):
            if isinstance(node, (sp.Sum, sp.Product)) and node not in hidden:
                hidden[node] = sp.Symbol(f"opaque{len(hidden)}_")
        if any(s in n.free_symbols for n in hidden):
            continue                                     # the symbol lives inside a sum
        try:
            with _time_limit(4):
                sol = sp.solve(sp.Eq(a.xreplace(hidden), b.xreplace(hidden)), s)
        except Exception:
            continue
        if len(sol) == 1:
            back = {v: k for k, v in hidden.items()}
            return s, sol[0].xreplace(back), "solved"
    return None, None, "residual"


def generate(srepr: str, lhs_text: str | None = None) -> Code:
    import sympy as sp
    from src.document.formula_algebra import to_expr
    try:
        eq = to_expr(srepr)
    except Exception as exc:
        return Code("failed", error=f"srepr: {exc}")
    if not isinstance(eq, sp.Equality):
        return Code("unsupported", error="not an equation")
    if eq.has(sp.Integral):
        return Code("unsupported", error="integral")
    eq, kinds = _inputs_for_operators(eq)
    target, closed, form = _target(eq, lhs_text)
    resid = eq.lhs - eq.rhs
    py, jl = _printers()

    _, _, first = _arrays(resid)
    avoid = frozenset(ident(x.name) for x in resid.free_symbols if x.name not in first)
    r_expr, r_bases, r_rep = _arrays(resid, avoid)
    arrays = {b.label.name: b for b in r_bases.values()}
    free = {s for s in resid.free_symbols}
    scalars = sorted((s for s in free if s.name not in r_rep and s not in _bound_indices(resid)),
                     key=lambda s: s.name)
    # an upper sum limit used only as a limit becomes the array length
    # (array, axis) indexed by each sum index, and the number of axes of every array
    axis_of, ndim = {}, {}
    for node in sp.preorder_traversal(r_expr):
        if isinstance(node, sp.Indexed):
            label = node.base.label.name
            ndim[label] = max(ndim.get(label, 0), len(node.indices))
            for k, ix in enumerate(node.indices):
                if ix.is_Symbol:
                    axis_of.setdefault(ix.name, (label, k))
    length_of = {}                                       # limit symbol → (array, axis)
    for node in sp.preorder_traversal(r_expr):
        if isinstance(node, (sp.Sum, sp.Product)):
            for ix, lo, hi in node.limits:
                if (hi.is_Symbol and hi in free and not _used_outside_limits(r_expr, hi)
                        and ix.name in axis_of and hi not in length_of):
                    length_of[hi] = axis_of[ix.name]
    limits = {x for node in sp.preorder_traversal(r_expr) if isinstance(node, (sp.Sum, sp.Product))
              for l in node.limits for b in l[1:] for x in b.free_symbols}
    args = ([{"name": n, "symbol": n, "kind": "array", "ndim": ndim.get(n, 1)} for n in sorted(arrays)]
            + [{"name": ident(s.name), "symbol": s.name,
                "kind": "count" if s in limits else kinds.get(s, "scalar")}
               for s in scalars if s not in length_of])
    # Ō next to an array O: the mean of O unless given (the usual convention; documented in the docstring)
    stems = {ident(n): ident(n) for n in arrays}
    for a in args:
        if a["kind"] == "scalar" and a["name"].endswith("_bar") and a["name"][:-4] in stems:
            a["kind"], a["default"] = "mean", a["name"][:-4]
    args.sort(key=lambda a: a["kind"] == "mean")
    names = [a["name"] for a in args]
    defaults = {a["name"]: a["default"] for a in args if a.get("default")}

    def fn_py(name, body_expr, params):
        e, _, _ = _arrays(body_expr, avoid)
        sig = [f"{x}=None" if x in defaults else x for x in params]
        lines = [f"def {name}({', '.join(sig)}):"]
        for x in params:
            if x in defaults:
                lines.append(f"    if {x} is None:\n        {x} = numpy.mean({defaults[x]})")
        for n, (base, k) in length_of.items():
            lines.append(f"    {ident(n.name)} = len({base})" if ndim.get(base, 1) == 1
                         else f"    {ident(n.name)} = numpy.shape({base})[{k}]")
        lines.append(f"    return {py.doprint(e)}")
        return "\n".join(lines)

    def fn_jl(name, body_expr, params):
        e, _, _ = _arrays(body_expr, avoid)
        sig = [f"{x}=nothing" if x in defaults else x for x in params]
        lines = [f"function {name}({', '.join(sig)})"]
        for x in params:
            if x in defaults:
                lines.append(f"    {x} === nothing && ({x} = sum({defaults[x]}) / length({defaults[x]}))")
        for n, (base, k) in length_of.items():
            lines.append(f"    {ident(n.name)} = length({base})" if ndim.get(base, 1) == 1
                         else f"    {ident(n.name)} = size({base}, {k + 1})")
        lines += [f"    return {jl.doprint(e)}", "end"]
        return "\n".join(lines)

    try:
        py_parts, jl_parts = ["import numpy"], []
        status = "residual_only"
        tname = None
        if target is not None and target.name not in r_rep:
            tname = ident(target.name)
            params = [n for n in names if n != tname]
            py_parts.append(fn_py(f"compute_{tname}", closed, params))
            jl_parts.append(fn_jl(f"compute_{tname}", closed, params))
            status = "ok"
        py_parts.append(fn_py("residual", resid, names))
        jl_parts.append(fn_jl("residual", resid, names))
    except Exception as exc:
        return Code("failed", error=f"print: {type(exc).__name__}: {str(exc)[:160]}")
    code = Code(status, tname, form if status == "ok" else "residual", args,
                "\n\n\n".join(py_parts) + "\n", "\n\n".join(jl_parts) + "\n")
    code.check = check(code, resid, target, closed, arrays)
    return code


def _bound_indices(expr) -> set:
    import sympy as sp
    out = set()
    for node in sp.preorder_traversal(expr):
        if isinstance(node, (sp.Sum, sp.Product)):
            out |= {l[0] for l in node.limits}
    return out


def _used_outside_limits(expr, sym) -> bool:
    import sympy as sp
    for node in sp.preorder_traversal(expr):
        if isinstance(node, (sp.Sum, sp.Product)):
            if sym in node.function.free_symbols:
                return True
    stripped = expr.replace(lambda x: isinstance(x, (sp.Sum, sp.Product)), lambda x: sp.Integer(0))
    return sym in stripped.free_symbols


def check(code: Code, resid, target, closed, arrays, trials: int = 5, seed: int = 20261005) -> str:
    """Execute the Python code: residual(compute(x), x) ≈ 0, and compute(x) = SymPy's value when
    there are no arrays."""
    import numpy as np
    import sympy as sp
    ns: dict = {}
    try:
        exec(code.python, ns)                                          # noqa: S102 — our own generated code
    except Exception:
        return "failed"
    rng = random.Random(seed)
    ok = errors = 0
    numeric_limits = [int(b) for node in sp.preorder_traversal(resid) if isinstance(node, (sp.Sum, sp.Product))
                      for l in node.limits for b in l[1:] if b.is_Integer]
    length = max([5] + numeric_limits)
    for _ in range(trials):
        vals = {}
        for a in code.args:
            if a["kind"] == "array":
                shape = (length,) * int(a.get("ndim", 1))
                vals[a["name"]] = np.array([rng.uniform(0.7, 2.3) for _ in range(length ** len(shape))]).reshape(shape)
            elif a["kind"] == "count":
                vals[a["name"]] = 5
            elif a["kind"] != "mean":
                vals[a["name"]] = rng.uniform(0.7, 2.3)
        try:
            with warnings.catch_warnings(), np.errstate(all="ignore"), _time_limit(4):
                warnings.simplefilter("ignore")
                if code.status == "ok":
                    params = {k: v for k, v in vals.items() if k != code.target}
                    t = ns[f"compute_{code.target}"](**params)
                    if not np.isfinite(t) or np.iscomplexobj(t):
                        continue
                    r = ns["residual"](**{**params, code.target: t})
                    scale = 1 + abs(t)
                    if not np.isfinite(r) or abs(r) > 1e-6 * scale:
                        return "failed"
                    if not arrays:
                        subs = {s: vals[ident(s.name)] for s in closed.free_symbols if ident(s.name) in vals}
                        ref = complex(closed.subs(subs).evalf())
                        if abs(ref.imag) < 1e-9 and abs(ref.real - t) > 1e-6 * scale:
                            return "failed"
                else:
                    r = ns["residual"](**vals)
                    if not np.isfinite(r):
                        continue
                    if not arrays:
                        subs = {s: vals[ident(s.name)] for s in resid.free_symbols if ident(s.name) in vals}
                        ref = complex(resid.subs(subs).evalf())
                        if abs(ref.imag) < 1e-9 and abs(ref.real - r) > 1e-6 * (1 + abs(r)):
                            return "failed"
                ok += 1
        except Exception:
            errors += 1
            continue
    if ok >= 2:
        return "passed"
    return "failed" if errors == trials else "skipped"


# ── per equation ───────────────────────────────────────────────────────────────

def annotate(code_row: dict, eq_row: dict, lang: str = "python") -> str:
    """The structure's code with a docstring for one equation: source, formula, arguments
    (meaning, unit, quantity concept) and the warning that it was generated from extraction."""
    from src.document.formula_algebra import param_symbols
    from src.document.formula_parameters import quantity_name
    from src.ontology.quantity_map import resolve
    args = json.loads(code_row["args"]) if isinstance(code_row["args"], str) else code_row["args"]
    params = json.loads(eq_row.get("parameters") or "[]")
    by_symbol = param_symbols(params, {a["symbol"] for a in args} | {code_row.get("target") or ""})
    lines = [f"Equation {eq_row.get('equation_number') or eq_row.get('xml_id')} of {eq_row['paper_id']}"
             f" (page {eq_row.get('page')}); equation_id {eq_row['equation_id']}",
             f"Formula: {code_row['canonical_expression']}",
             f"Generated by formula_code {CODEGEN_VERSION} from the extracted LaTeX; check: {code_row['check']}.",
             "Units are as extracted; the code does not convert them.", "", "Arguments:"]
    target = code_row.get("target")
    for a in [x for x in args if x["name"] != target] + (
            [{"name": target, "symbol": next((x["symbol"] for x in args if x["name"] == target), target),
              "kind": "result"}] if target else []):
        p = by_symbol.get(a["symbol"]) or {}
        q = quantity_name(p.get("description") or "")
        qid = resolve(q).quantity_id if q else None
        desc = p.get("description") or "(no definition found in the paper)"
        unit = f" [{p['unit']}]" if p.get("unit") else ""
        kind = ("" if a["kind"] == "scalar" else f" (default: mean of {a['default']})" if a["kind"] == "mean"
                else f" ({a['kind']})")
        lines.append(f"    {a['name']}{kind}: {desc}{unit}" + (f"  — {qid}" if qid else ""))
    body = code_row["python"] if lang == "python" else code_row["julia"]
    if lang == "python":
        doc = '"""\n' + "\n".join(lines) + '\n"""\n'
        return doc + body
    return "\n".join("# " + l if l else "#" for l in lines) + "\n" + body


# ── corpus ─────────────────────────────────────────────────────────────────────

def build(cache: dict | None = None) -> tuple[pd.DataFrame, dict]:
    cache = load_cache(CACHE) if cache is None else cache
    rows, seen = [], set()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for st in cache.values():
            h = st.get("structural_hash")
            if st.get("status") != "ok" or st.get("kind") != "equation" or not h or h in seen:
                continue
            seen.add(h)
            try:
                c = generate(st["srepr"], st.get("lhs"))
            except Exception as exc:
                c = Code("failed", error=f"{type(exc).__name__}: {str(exc)[:160]}")
            r = asdict(c)
            r["args"] = json.dumps(r["args"], ensure_ascii=False)
            rows.append({"structural_hash": h, "canonical_expression": st.get("canonical_expression"),
                         "codegen_version": CODEGEN_VERSION, **r})
    df = pd.DataFrame(rows)
    stats = {"structures": len(df)}
    if len(df):
        stats["status"] = df["status"].value_counts().to_dict()
        stats["form"] = df["form"].value_counts().to_dict()
        stats["check"] = df["check"].value_counts().to_dict()
    return df, stats


def _one(eq_id: str, lang: str) -> int:
    paper = eq_id.split(":", 1)[0]
    rec = pd.read_parquet(SODB / paper / "equation_records.parquet")
    rec = rec[rec["equation_id"] == eq_id]
    if rec.empty:
        print(f"no equation {eq_id}"); return 1
    eq = {k: (None if isinstance(v, float) and v != v else v) for k, v in rec.iloc[0].to_dict().items()}
    st = load_cache(CACHE).get(eq["formula_hash"])
    if not st or st.get("status") != "ok":
        print(f"no parsed structure for {eq_id} (status {st and st.get('status')})"); return 1
    c = generate(st["srepr"], st.get("lhs"))
    row = {**asdict(c), "canonical_expression": st["canonical_expression"]}
    print(annotate(row, eq, lang))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--eq-id")
    ap.add_argument("--lang", choices=("python", "julia"), default="python")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    if args.eq_id:
        return _one(args.eq_id, args.lang)
    if OUT.exists():
        print(f"{OUT} exists; never overwritten (bump CODEGEN_VERSION)")
        return 1
    df, stats = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(json.dumps(stats, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
