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


def norm_symbol(tex: str) -> str:
    """S_{\\text{c,max}} → S_c,max ; \\overline{u} → u ; \\Delta U → ΔU-ish key."""
    s = re.sub(r"\\(?:text|mathrm|rm|mathit|mathbf|bf|it|operatorname)\s*", "", tex)
    s = re.sub(r"\\(?:overline|bar|hat|tilde|widehat|left|right|langle|rangle|prime|,|;|!|quad)\s*", "", s)
    s = s.replace("{", "").replace("}", "").replace(" ", "").replace("\\", "")
    return s.strip(".,;:")


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
        if not part:
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


def parse_clause(clause: str, equation_tex: str, source: str) -> list[Parameter]:
    eq_key = norm_symbol(equation_tex)
    params: dict[str, Parameter] = {}
    for sym_tex, body, unit_first in _segments(clause[:MAX_CONTEXT]):
        key = norm_symbol(sym_tex)
        if not key or len(key) > 25 or key not in eq_key:
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
            value, unit = n.group(1).replace(",", "."), unit or (rest or None)
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
