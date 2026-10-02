"""Manuscript assembly, dialect ``floodstate_fill``: every number is a cell of a publication table.

From floodstate-eo workflows/paper/fill_manuscript.py (2026-09-25), unchanged in behaviour:

    {{TID|col=value,col=value|column|agg|fmt}}   agg: first (default), sum, mean, min, max, count;
                                                 fmt like .0f / .2f / +.1f; prefix `neg` negates
Filters are separated by ',' or, when a value contains a comma, by ';'. An unresolvable
placeholder becomes ``[[MISSING: …]]``, so the text can never silently show a wrong number.
"""

from __future__ import annotations

import io
import re
from typing import Callable

import pandas as pd

PAT = re.compile(r"\{\{([A-Za-z0-9]+)\|([^|]*)\|([^|]+)\|([^|]*)\|([^}]*)\}\}")
MISSING = re.compile(r"\[\[MISSING: [^\]]*\]\]")


def make_resolver(get: Callable[[str], pd.DataFrame]):
    def resolve(m: re.Match) -> str:
        tid, filt, col, agg, fmt = m.groups()
        agg = agg or "first"
        try:
            df = get(tid)
            for f in [x for x in (filt.split(";") if ";" in filt else filt.split(",")) if x.strip()]:
                k, v = f.split("=", 1)
                s = df[k].astype(str)
                df = df[s == v]
            if len(df) == 0 or col not in df.columns:
                return f"[[MISSING: {m.group(0)}]]"
            x = df[col]
            val = {"first": lambda: x.iloc[0], "sum": lambda: x.sum(), "mean": lambda: x.mean(),
                   "min": lambda: x.min(), "max": lambda: x.max(), "count": lambda: len(x)}[agg]()
            if fmt.startswith("neg"):
                val, fmt = -val, fmt[3:]
            return format(val, fmt) if fmt else str(val)
        except Exception as e:  # noqa: BLE001 — every failure becomes a visible MISSING marker
            return f"[[MISSING: {m.group(0)} ({e})]]"
    return resolve


def fill(template: str, table_csv: Callable[[str], str | None]) -> tuple[str, list[str], int]:
    """(text, missing markers, placeholders). Tables are read once each from their CSV text."""
    cache: dict[str, pd.DataFrame] = {}

    def get(tid: str) -> pd.DataFrame:
        if tid not in cache:
            text = table_csv(tid)
            if text is None:
                raise FileNotFoundError(f"tables/{tid}.csv")
            cache[tid] = pd.read_csv(io.StringIO(text))
        return cache[tid]
    out = PAT.sub(make_resolver(get), template)
    missing = MISSING.findall(out)
    if missing:
        out += "\n\n<!-- UNRESOLVED PLACEHOLDERS -->\n" + "\n".join(missing)
    return out, missing, len(PAT.findall(template))
