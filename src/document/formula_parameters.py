"""
formula_parameters.py — the parameters of a formula, from the text around it.

A formula is only usable with its parameters: what each symbol is, its unit and,
for constants, its value. Papers give them in a definition clause right after the
equation ("where \\(P\\) is precipitation (mm d\\({}^{-1}\\)); \\(P_{t}\\) is
throughfall ...; and \\(E_{c}\\) is ...") and sometimes in the sentence before it
("The canopy storage \\(S_{c}\\) (mm) is calculated as"). A run of equations often
shares one clause after the last of them.

Every parameter returned is tied to a symbol that occurs in the equation itself;
a definition of a symbol the equation does not contain is not its parameter.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_INLINE   = re.compile(r"\\\((.+?)\\\)", re.S)
_DISPLAY  = re.compile(r"\\\[.*?\\\]\s*(?:\(\d+[a-z]?\))?", re.S)
_HEADING  = re.compile(r"^\s*#{1,6}\s", re.M)
_YEAR     = re.compile(r"\b(1[89]|20)\d\d[a-z]?\b")
_NUMBER   = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?(?:\s*[×x]\s*10\s*\^?\{?-?\d+\}?)?)\s*(.*)$")
_VERB     = re.compile(r"^\s*(?:is|are|denotes?|represents?|stands for|refers to|is the|=|:)\s+", re.I)
_LEAD     = re.compile(r"^\s*(?:where|in which|with|here|and|in this equation,?|in eq\.?\s*\(\d+\),?)\s*,?\s*", re.I)

MAX_CONTEXT = 1500      # characters of the definition clause considered
_TRAIL = re.compile(r"\s*,?\s*(?:which is |that is |and is |is )?(?:calculated|computed|given|defined|expressed|estimated|obtained|determined)\s+(?:as|by|from)\s*:?$", re.I)
_SET_VALUE = re.compile(r"\b(?:set to|equal to|equals|fixed at|taken as|assumed to be)\s+(-?\d+(?:[.,]\d+)?)\s*((?:[A-Za-z°%µ]{1,5}(?:\^?-?\d+)?\s?){0,3})")
_CITE  = re.compile(r"\s*\([^()]*\b(?:1[89]|20)\d\d[a-z]?\)\s*")


@dataclass
class Parameter:
    symbol:      str            # normalised ("S_c,max")
    symbol_tex:  str            # as written ("S_{\\text{c,max}}")
    description: str
    unit:        str | None
    value:       str | None     # constants: "0.3"
    source:      str            # "after" | "before"


GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "Δ": "Delta", "∆": "Delta",
         "ε": "epsilon", "ϵ": "epsilon", "η": "eta", "θ": "theta", "Θ": "Theta", "κ": "kappa",
         "λ": "lambda", "Λ": "Lambda", "µ": "mu", "μ": "mu", "ν": "nu", "ξ": "xi", "π": "pi",
         "ρ": "rho", "σ": "sigma", "Σ": "Sigma", "τ": "tau", "φ": "phi", "ϕ": "phi", "Φ": "Phi",
         "χ": "chi", "ψ": "psi", "Ψ": "Psi", "ω": "omega", "Ω": "Omega", "ζ": "zeta", "Γ": "Gamma"}


def greek_names(text: str) -> str:
    """'µ' → 'mu', 'σ S' → 'sigma S': one spelling for symbols from LaTeX and from TEI."""
    return "".join(GREEK.get(ch, ch) for ch in text)


def norm_symbol(tex: str) -> str:
    """S_{\\text{c,max}} → S_c,max ; \\overline{u} → u ; \\Delta U → ΔU-ish key."""
    s = re.sub(r"\\(?:text|mathrm|rm|mathit|mathbf|bf|it|operatorname)\s*", "", tex)
    s = re.sub(r"\\(?:overline|bar|hat|tilde|widehat|left|right|langle|rangle|prime|,|;|!|quad)\s*", "", s)
    s = s.replace("{", "").replace("}", "").replace(" ", "").replace("\\", "")
    return greek_names(s.strip(".,;:"))


def _mask_parens(text: str) -> str:
    """Same-length copy with everything inside ( ) or [ ] replaced by "_", so that
    "; see Eq. 3" inside "(mm d^-1; see Eq. 3)" does not end a definition. The math
    delimiters \\( \\) are not parentheses."""
    out, depth, i = [], 0, 0
    while i < len(text):
        two = text[i:i + 2]
        if two in ("\\(", "\\)"):
            out.append(two if depth == 0 else "__")
            i += 2
            continue
        ch = text[i]
        if ch in "([":
            depth += 1
            out.append(ch)
        elif ch in ")]" and depth:
            depth -= 1
            out.append(ch)
        else:
            out.append(ch if depth == 0 else "_")
        i += 1
    return "".join(out)


def _clean(text: str) -> str:
    text = re.sub(r"(?:\{\})?\^\{?\\circ\}?", "°", text)
    text = _INLINE.sub(lambda m: m.group(1), text)
    text = re.sub(r"\{\}\^\{?(-?\d+)\}?", r"^\1", text)
    text = re.sub(r"\\text\{([^}]*)\}|\\mathrm\{([^}]*)\}", lambda m: m.group(1) or m.group(2), text)
    return re.sub(r"\s+", " ", text).strip(" ,;")


UNIT_ATOMS = frozenset("""
m mm cm km µm um nm s ms min h hr hrs d day days wk yr yrs a month months
g kg mg Mg t Gt l L mL ha ac Pa kPa hPa MPa bar mbar atm °C °F K W kW MW GW J kJ MJ GJ
Wh kWh N kN rad sr deg ° % ‰ ppm ppb mol mmol µmol eq meq S dS mS Hz dB
mm/d m/s m/d cm/s km/h mm/h mm/yr m3/s
""".split())
_EXP = re.compile(r"(?:\^?\{?[-−]?\d+(?:\.\d+)?\}?)$")


def _unit_atom(tok: str) -> bool:
    t = tok.strip("()[],;")
    if t in {"-", "–", "·", "*", "/", "×", "x"}:
        return True
    for part in re.split(r"[/·*]", t):
        if not part or part.isdigit():       # "m^-1/3": the denominator of an exponent
            continue
        base = _EXP.sub("", part) if not part.isdigit() else part
        if base not in UNIT_ATOMS:
            return False
    return True


def is_unit(text: str) -> bool:
    """"mm d^-1", "m3 s^-1", "kPa °C^-1", "-", "%" — not "semi", "or local",
    "in the case", "e.g., monthly streamflow" or a bare number."""
    t = _clean(text).replace("−", "-")
    if not t or len(t) > 30:
        return False
    if t in {"-", "–", "dimensionless", "unitless"}:
        return True
    t = re.sub(r"\s*°\s*([CF])", r" °\1", t).strip()       # "kPa°C^-1" → "kPa °C^-1"
    toks = [x for x in re.split(r"\s+", t) if x]
    if not toks or len(toks) > 5:
        return False
    if all(re.fullmatch(r"[-−]?\d+(?:\.\d+)?", x) for x in toks):
        return False                                  # a bare number is a value, not a unit
    return all(_unit_atom(x) for x in toks)


_PAREN = re.compile(r"[\(\[]((?:\\\(.*?\\\)|[^()\[\]])*)[\)\]]")


def _unit_and_desc(desc_tex: str) -> tuple[str, str | None]:
    """Take the first parenthetical that is a unit out of a description."""
    for m in _PAREN.finditer(desc_tex):
        inner = m.group(1).split(";")[0]
        if is_unit(inner):
            rest = (desc_tex[: m.start()] + desc_tex[m.end():]).strip(" ,;")
            return re.sub(r"\s+,", ",", rest), _clean(inner)
    return desc_tex.strip(" ,;"), None


def _segments(clause: str) -> list[tuple[str, str]]:
    """(symbol_tex, rest) for every inline symbol that starts a definition."""
    out = []
    matches = list(_INLINE.finditer(clause))
    for k, m in enumerate(matches):
        after = clause[m.end():]
        sym = m.group(1)
        if re.match(r"\s*(?:\{\})?[_^]", sym):        # "KGE\({}_{r}\)": base written as text
            base = re.search(r"([A-Za-z]{1,8})$", clause[:m.start()])
            if base:
                sym = base.group(1) + sym
        # optional unit right after the symbol: "\(L\) (\(-\)) is ..."
        unit_first = None
        u = re.match(r"\s*\((\\\(.*?\\\)|[^()]{1,25})\)", after)
        if u and is_unit(u.group(1)):
            unit_first = _clean(u.group(1))
            after = after[u.end():]
        v = _VERB.match(after)
        if not v:
            continue
        body = after[v.end():]
        # the definition ends at "; ", at ", and \(" / ", \(" starting the next symbol, or at ". "
        stop = len(body)
        masked = _mask_parens(body)
        for pat in (r";", r",?\s*(?:and\s+)?(?:\w+)?\\\((?=(?:(?!\\\)).)*\\\)\s*(?:\((?:\\\(.*?\\\)|[^()])*\)\s*)?(?:is|are|denotes?|represents?|=)\s)",
                    r"\.\s+(?=[A-Z\\])", r"\.\s*$"):
            mm = re.search(pat, masked)
            if mm:
                stop = min(stop, mm.start())
        out.append((sym, body[:stop], unit_first))
    return out


def parse_clause(clause: str, equation_tex: str | None, source: str) -> list[Parameter]:
    """LaTeX-markdown definitions; equation_tex=None keeps every defined symbol
    (paper glossary), otherwise only symbols of that equation."""
    eq_key = norm_symbol(equation_tex) if equation_tex is not None else None
    params: dict[str, Parameter] = {}
    for sym_tex, body, unit_first in _segments(clause[:MAX_CONTEXT]):
        key = norm_symbol(sym_tex)
        if not key or len(key) > 25 or (eq_key is not None and key not in eq_key):
            continue
        desc_tex, unit = _unit_and_desc(body)
        unit = unit or unit_first
        desc = _TRAIL.sub("", _clean(desc_tex))
        if unit:
            unit = _CITE.sub("", unit).strip(" ,;") or None
        value = None
        n = _NUMBER.match(desc)
        if n and (not n.group(2) or len(_CITE.sub("", n.group(2)).split()) <= 3):
            rest = _CITE.sub("", n.group(2) or "").strip(" ,;")
            value = n.group(1).replace(",", ".")
            unit = unit or (rest if rest and is_unit(rest) else None)
            desc = desc if n.group(2) and len(n.group(2).split()) > 3 else ""
        if value is None:
            sv = _SET_VALUE.search(desc)
            if sv:
                value = sv.group(1).replace(",", ".")
                cand = sv.group(2).strip(" ,;.")
                unit = unit or (cand if cand and is_unit(cand) else None)
        if not desc and value is None:
            continue
        params.setdefault(key, Parameter(key, sym_tex, desc, unit, value, source))
    return list(params.values())


def context_for(markdown: str, block_start: int, block_end: int) -> tuple[str, str]:
    """(before, after) text of an equation block: the sentence leading into it and
    the definition clause after it — after the last equation of a consecutive run."""
    end = block_end
    while True:                                     # skip following equations of the run
        m = _DISPLAY.match(markdown, len(markdown[:end]) + len(markdown[end:]) - len(markdown[end:].lstrip()))
        if not m:
            break
        end = m.end()
    tail = markdown[end:]
    stop = len(tail)
    for pat in (_DISPLAY, _HEADING):
        mm = pat.search(tail)
        if mm:
            stop = min(stop, mm.start())
    # the definition clause is the paragraph right after the equation(s)
    first = tail[:stop].strip().split("\n\n")[0]
    after = _LEAD.sub("", first, count=1)
    head = markdown[:block_start].rstrip()
    sent = re.split(r"(?<=[.!?])\s+(?=[A-Z])", head.split("\n\n")[-1]) if head else [""]
    before = sent[-1] if sent else ""
    return before, after


def formula_parameters(markdown: str, block_start: int, block_end: int,
                       equation_tex: str) -> list[dict]:
    before, after = context_for(markdown, block_start, block_end)
    found = {p.symbol: p for p in parse_clause(after, equation_tex, "after")}
    # "The canopy storage \(S_c\) (mm) is calculated as" — the symbol precedes its verb
    m = re.search(r"([A-Za-z][\w\s\-]{3,80}?)\s*\\\((.+?)\\\)\s*(?:\(([^()]{1,25})\))?\s*"
                  r"(?:is|are|was|were|can be)\s+(?:calculated|computed|given|defined|expressed|estimated|obtained|determined)",
                  before)
    if m:
        key = norm_symbol(m.group(2))
        if key and key in norm_symbol(equation_tex) and key not in found:
            found[key] = Parameter(key, m.group(2), _clean(m.group(1)).split(". ")[-1].removeprefix("The ").removeprefix("the "),
                                   _clean(m.group(3)) if m.group(3) else None, None, "before")
    return [asdict(p) for p in found.values()]


def render_parameters(params: list[dict]) -> str:
    """Plain-text block appended to a formula's text: one parameter per line."""
    lines = []
    for p in params:
        line = f"{p['symbol']}: {p['description']}".rstrip(": ")
        if p.get("value") is not None:
            line += f" = {p['value']}"
        if p.get("unit"):
            line += f" [{p['unit']}]"
        lines.append(line)
    return "where\n" + "\n".join(lines) if lines else ""


# ── plain text (GROBID TEI) ───────────────────────────────────────────────────
# TEI flattens math: "P t" is P_t, "S c,max" is S_c,max, "mm d -1" is mm d^-1.

_PSYM  = r"(?!(?:where|and|with|in|which|here|the)\b)[A-Za-zα-ωΑ-ΩµΔ∆θ][\w′']{0,5}(?:\s(?!is\b|are\b|and\b)[\w,′']{1,8}){0,2}?"
_PVERB = r"(?:is|are|denotes?|represents?|stands for|refers to)"
_PDEF  = re.compile(
    rf"(?:^|(?<=[;,])\s*(?:and\s+)?|\band\s+|\bwhere\s+|\bwith\s+|\bin which\s+)"
    rf"(?P<sym>{_PSYM})\s*(?:\((?P<u1>[^()]{{1,25}})\)\s*)?{_PVERB}\s+(?P<desc>.+?)"
    rf"(?=;|(?:,\s*|\s+)(?:and\s+)?\b{_PSYM}\s*(?:\([^()]{{1,25}}\)\s*)?{_PVERB}\s|\.\s|\.(?=[A-Z])|\.$|$)",
    re.S)


def plain_key(sym: str) -> str:
    """'P t' → 'P_t', 'S c,max' → 'S_c,max', 'α' → 'α', 'KGE r' → 'KGE_r'."""
    toks = greek_names(sym).strip().split()
    if not toks:
        return ""
    return toks[0] + ("_" + "".join(toks[1:]) if len(toks) > 1 else "")


def _plain_has(formula_plain: str, sym: str) -> bool:
    f = " " + re.sub(r"[^\w,′']+", " ", greek_names(formula_plain)) + " "
    return (" " + " ".join(greek_names(sym).split()) + " ") in f


def parse_plain_clause(clause: str, formula_plain: str | None, source: str) -> list[Parameter]:
    """Definitions in GROBID text. With formula_plain, only symbols in that formula."""
    clause = re.sub(r"(\w)\s+([-−]\d)", r"\1^\2", clause)          # "d -1" → "d^-1"
    clause = re.sub(r"\b(m|km|cm|mm|dm)\s+([23])\b", r"\1^\2", clause)   # "m 3" → "m^3"
    masked = _mask_parens(clause)
    out: dict[str, Parameter] = {}
    for m in _PDEF.finditer(masked):
        sym = clause[m.start("sym"):m.end("sym")].strip()
        desc_tex = clause[m.start("desc"):m.end("desc")]
        u1 = clause[m.start("u1"):m.end("u1")] if m.group("u1") else None
        if formula_plain is not None and not _plain_has(formula_plain, sym):
            continue
        key = plain_key(sym)
        if not key or key.lower() in {"it", "this", "that", "there", "which", "the", "a", "an"}:
            continue
        desc_tex, unit = _unit_and_desc(desc_tex)
        unit = unit or (_clean(u1) if u1 and is_unit(u1) else None)
        desc = _TRAIL.sub("", _clean(desc_tex))
        value = None
        n = _NUMBER.match(desc)
        if n and len(_CITE.sub("", n.group(2) or "").split()) <= 3:
            rest = _CITE.sub("", n.group(2) or "").strip(" ,;")
            value, unit, desc = n.group(1), unit or (rest if rest and is_unit(rest) else None), ""
        else:
            sv = _SET_VALUE.search(desc)
            if sv:
                value = sv.group(1)
                cand = sv.group(2).strip(" ,;.")
                unit = unit or (cand if cand and is_unit(cand) else None)
        if not desc and value is None:
            continue
        out.setdefault(key, Parameter(key, sym, desc, unit, value, source))
    return list(out.values())


# ── paper glossary ────────────────────────────────────────────────────────────

_NOMENCLATURE_HEAD = re.compile(r"nomenclature|notation|list of (?:symbols|variables|notations)|symbols", re.I)
_SYMBOL_COL = re.compile(r"^(?:symbol|notation|variable|parameter|abbreviation)s?$", re.I)
_DESC_COL   = re.compile(r"^(?:description|definition|meaning|name|explanation)s?$", re.I)
_UNIT_COL   = re.compile(r"^(?:units?|dimensions?)$", re.I)


def glossary_from_table(rows: list[list[str]], source: str) -> list[Parameter]:
    """A nomenclature table: a header row with a symbol and a description column."""
    if not rows:
        return []
    head = [(c or "").strip() for c in rows[0]]
    si = next((i for i, c in enumerate(head) if _SYMBOL_COL.match(c)), None)
    di = next((i for i, c in enumerate(head) if _DESC_COL.match(c)), None)
    ui = next((i for i, c in enumerate(head) if _UNIT_COL.match(c)), None)
    if si is None or di is None:
        return []
    out = []
    for r in rows[1:]:
        if len(r) <= max(si, di):
            continue
        sym, desc = (r[si] or "").strip(), (r[di] or "").strip()
        if not sym or not desc or len(sym) > 25:
            continue
        unit = (r[ui] or "").strip() if ui is not None and ui < len(r) else None
        key = norm_symbol(sym) if "\\" in sym or "_" in sym else plain_key(sym)
        out.append(Parameter(key, sym, _clean(desc), _clean(unit) if unit and is_unit(unit) else None,
                             None, source))
    return out


def glossary_from_lines(lines: list[str], source: str) -> list[Parameter]:
    """Nomenclature written as lines: "Q discharge (m3 s-1)", "Q: discharge", "Q – discharge"."""
    out = []
    for line in lines:
        line = re.sub(r"(\w)\s+([-−]\d)", r"\1^\2", line.strip())
        m = re.match(rf"^(?P<sym>{_PSYM})\s*(?:[:=–—-]\s*|\s{{2,}}|\s)(?P<desc>[A-Za-z].{{2,}})$", line)
        if not m:
            continue
        desc, unit = _unit_and_desc(m.group("desc"))
        out.append(Parameter(plain_key(m.group("sym")), m.group("sym"), _clean(desc), unit, None, source))
    return out


def merge_glossary(*groups: list[Parameter]) -> dict[str, Parameter]:
    """First definition of a key wins; groups are given in priority order.

    A symbol that running text defines as two different quantities ("a" is an
    exponent in one section and an area in another) is ambiguous and left out, unless
    a nomenclature section or table defines it."""
    g: dict[str, Parameter] = {}
    seen: dict[str, set] = {}
    for grp in groups:
        for p in grp:
            if not p.symbol or not (p.description or p.value):
                continue
            if not p.source.startswith("nomenclature"):
                seen.setdefault(p.symbol, set()).add(quantity_name(p.description) or p.value)
            g.setdefault(p.symbol, p)
    for k, names in seen.items():
        if len(names - {None}) > 1 and not g[k].source.startswith("nomenclature"):
            del g[k]
    return g


_TEX_SYMBOL = re.compile(r"(\\[A-Za-z]+|[A-Za-z])(?:_\{(?:[^{}]|\{[^{}]*\})*\}|_[A-Za-z0-9])?")
_TEX_SKIP = frozenset("""frac sum prod int sqrt left right cdot times text mathrm rm mathit mathbf bf
begin end cases array min max exp log ln sin cos tan partial mbox quad qquad leq geq le ge neq
approx sim infty limits overline bar hat tilde widehat langle rangle prime circ mathcal operatorname
d e if otherwise and for""".split())


def equation_symbols(latex: str) -> set[str]:
    """Normalised symbols of a LaTeX equation (S_c,max, P_t, alpha, ...)."""
    body = re.sub(r"\\(?:begin|end)\{[^}]*\}", " ", latex)       # environment names
    body = re.sub(r"\\(?:text|mathrm|mbox)\{(?:otherwise|if|for|and|else)\}", " ", body)
    # a word set in \text{} outside a subscript is one symbol ("ICU"), not three letters
    body = re.sub(r"(?<![_{])\\(?:text|mathrm|rm)\s*\{([A-Za-z]{2,})\}", r"\\\1", body)
    # differentials: d/dt, \text{d}t, {\rm d}t — "t" here is not a parameter
    body = re.sub(r"(?:\\(?:text|mathrm)\{d\}|\{\\rm\s*d\}|(?<![A-Za-z\\])d)\s*t(?![A-Za-z_])", " ", body)
    out = set()
    for m in _TEX_SYMBOL.finditer(body):
        head = m.group(1).lstrip("\\")
        if head in _TEX_SKIP:
            continue
        key = norm_symbol(m.group(0))
        if key and key not in _TEX_SKIP:
            out.add(key)
    return out


_QUANTITY_CUT = re.compile(
    r",|;|\(|\s(?:calculated|computed|derived|estimated|obtained|given|defined|determined|which|that|"
    r"based on|according to|as a function|in the case|from eq|see|i\.e\.|e\.g\.)\b", re.I)


def quantity_name(description: str) -> str | None:
    """Short, comparable name of what a parameter is ("throughfall, the fraction of P
    that reaches the soil" → "throughfall"); used to find formulas by quantity."""
    d = _QUANTITY_CUT.split(description or "", maxsplit=1)[0]
    d = re.sub(r"^(?:the|a|an)\s+", "", d.strip(), flags=re.I).lower()
    d = re.sub(r"\s+", " ", d).strip(" .")
    words = d.split()
    if not words or len(words) > 7 or not re.search(r"[a-z]{3}", d):
        return None
    return d
