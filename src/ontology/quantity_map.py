"""
quantity_map.py — the surface quantity names of all equation parameters, normalised to the
quantity ontology (src/ontology/quantities.json), and the dimension check of their units.

Output: data/ontology_maps/quantity_map_<ontology version>.parquet
    name, quantity_id, method (exact | stripped | head | embedding | unmatched |
    not_a_quantity), score, qualifiers (JSON), frequency

The graph loader (src/graph/equation_kg_loader.py) reads this file; it never loads the
embedding model itself. Names missing from the map are normalised by rules only.
A map is never overwritten: a new ontology version gives a new file.

Usage:
    python -m src.ontology.quantity_map [--no-embed] [--report FILE]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pandas as pd

from src.document.formula_parameters import quantity_name
from src.ontology.quantities import Match, check_dimension, normalise, normalise_many, ontology

log = logging.getLogger("geohydro.ontology.quantity_map")
ROOT = Path(__file__).resolve().parents[2]
SODB = Path(os.getenv("SODB_DIR", str(ROOT / "data" / "sodb")))
MAP_DIR = ROOT / "data" / "ontology_maps"


def map_path(version: str | None = None) -> Path:
    return MAP_DIR / f"quantity_map_{(version or ontology()['version']).removeprefix('quantities-')}.parquet"


def collect(sodb: Path = SODB) -> tuple[Counter, list[dict]]:
    """Quantity names with their frequency, and every parameter that carries a unit."""
    names: Counter = Counter()
    with_unit: list[dict] = []
    for f in sorted(sodb.glob("*/equation_records.parquet")):
        d = pd.read_parquet(f, columns=["equation_id", "parameters"])
        for eq_id, params in zip(d["equation_id"], d["parameters"]):
            for p in json.loads(params or "[]"):
                q = quantity_name(p.get("description") or "")
                if not q:
                    continue
                names[q] += 1
                if p.get("unit"):
                    with_unit.append({"equation_id": eq_id, "symbol": p.get("symbol"), "name": q,
                                      "unit": p["unit"]})
    return names, with_unit


def build(*, embed: bool = True, sodb: Path = SODB) -> tuple[pd.DataFrame, pd.DataFrame]:
    names, with_unit = collect(sodb)
    matches = normalise_many(list(names)) if embed else {n: normalise(n) for n in names}
    qmap = pd.DataFrame([{"name": n, "quantity_id": m.quantity_id, "method": m.method, "score": m.score,
                          "qualifiers": json.dumps(m.qualifiers, ensure_ascii=False), "frequency": names[n]}
                         for n, m in matches.items()]).sort_values("frequency", ascending=False)
    dims = pd.DataFrame(with_unit)
    if len(dims):
        dims["quantity_id"] = dims["name"].map(lambda n: matches[n].quantity_id)
        dims["dimension_check"] = [check_dimension(q, u) for q, u in zip(dims["quantity_id"], dims["unit"])]
    return qmap, dims


@lru_cache(maxsize=1)
def load_map(path: str | None = None) -> dict[str, Match]:
    """name → Match from the newest map of the current ontology version (empty if none)."""
    p = Path(path) if path else map_path()
    if not p.exists():
        log.warning("no quantity map at %s: names are normalised by rules only", p)
        return {}
    d = pd.read_parquet(p)
    return {r.name: Match(r.quantity_id, r.method, float(r.score), json.loads(r.qualifiers))
            for r in d.itertuples(index=False)}


def resolve(name: str | None) -> Match:
    """The ontology match of one surface name: the map first, rules otherwise."""
    if not name:
        return Match(None, "empty")
    return load_map().get(name) or normalise(name)


def summary(qmap: pd.DataFrame, dims: pd.DataFrame) -> dict:
    occ = qmap.groupby("method")["frequency"].sum()
    total = int(qmap["frequency"].sum())
    quantity_occ = total - int(occ.get("not_a_quantity", 0))
    out = {
        "ontology": ontology()["version"], "names": len(qmap), "occurrences": total,
        "not_a_quantity_occurrences": int(occ.get("not_a_quantity", 0)),
        "coverage_of_quantity_occurrences": {m: round(int(v) / max(quantity_occ, 1), 3)
                                             for m, v in occ.items() if m != "not_a_quantity"},
        "matched_share": round(int(qmap.loc[qmap.quantity_id.notna(), "frequency"].sum()) / max(quantity_occ, 1), 3),
        "concepts_used": int(qmap["quantity_id"].nunique()),
    }
    if len(dims):
        out["parameters_with_unit"] = len(dims)
        out["dimension_check"] = dims["dimension_check"].value_counts().to_dict()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--no-embed", action="store_true", help="rules only (no embedding model)")
    ap.add_argument("--report", type=Path, help="write the dimension check rows (CSV) here")
    ap.add_argument("--out", type=Path, help="write the map here instead (a preview, not read by the loader)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    out = args.out or map_path()
    if out.exists():
        print(f"{out} exists; a map is never overwritten (bump the ontology version)")
        return 1
    qmap, dims = build(embed=not args.no_embed)
    out.parent.mkdir(parents=True, exist_ok=True)
    qmap.to_parquet(out, index=False)
    if args.report is not None and len(dims):
        dims.to_csv(args.report, index=False)
    print(json.dumps(summary(qmap, dims), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
