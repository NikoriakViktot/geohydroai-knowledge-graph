"""
gold_equation_set.py — sample and freeze the gold set of the equation-centric KG
(docs_v2/EQUATION_KG_PLAN.md, "Еталонний набір").

Writes data/gold/equation_kg_<version>/:
    equations.jsonl       300 equation occurrences, stratified by publisher, formula source
                          (Nougat LaTeX / GROBID text) and whether parameters were found
    parameters.jsonl      the parameters of those equations (capped at 800)
    quantity_names.jsonl  200 surface quantity names: 100 most frequent + 100 from the tail
    equation_pairs.jsonl  150 pairs: 50 likely equivalent, 50 random, 50 hard negatives
    law_links.jsonl       150 equation–law pairs: 60 accepted links, 60 candidates, 30 equations whose
                          text names a law without a link (recall); the annotator sees neither score
                          nor category (both are in the manifest)
    metric_facts.jsonl    150 NumericFacts, stratified by metric
    qa_items.jsonl        empty template: questions are written by a person
    MANIFEST.json         seed, source snapshot, sha256 of every file

Labels are added only by a person through the verification layer (verify.human_check,
target kinds equation / parameter / quantity_name / equation_pair / metric_fact / qa_item).
The pair category (why a pair was sampled) is kept in the manifest, not in the item the
annotator sees. A version directory is never overwritten: a new sample is a new version.

    python -m src.evaluation.gold_equation_set --version v1 [--dry-run]
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SODB = ROOT / "data" / "sodb"
OUT = ROOT / "data" / "gold"
SEED = 20261005

N_EQUATIONS, N_PARAMS, N_QNAMES, N_FACTS = 300, 800, 200, 150
N_PAIRS = {"likely_equivalent": 50, "random": 50, "hard_negative": 50}
N_LAW_LINKS = {"accepted": 60, "candidate": 60, "unlinked_cue": 30}
FAMILIES = {   # keyword families for hard negatives: near formulas of different laws
    "flow_resistance": r"manning|chezy|ch[ée]zy|darcy|weisbach|roughness|friction",
    "infiltration":    r"green.?ampt|horton|philip|infiltration",
    "efficiency":      r"\bnse\b|nash|kge|kling|rmse|root mean|nrmse|pbias|\br2\b|determination",
    "evaporation":     r"penman|priestley|hargreaves|evapotranspiration",
    "routing":         r"muskingum|saint.?venant|kinematic|diffusive",
    "runoff":          r"scs|curve number|rational method|runoff coefficient",
}


def publisher(paper_id: str) -> str:
    m = re.match(r"(10\.\d{4,5})_", paper_id)
    return {"10.1016": "elsevier", "10.1029": "agu_wiley", "10.1002": "wiley", "10.5194": "copernicus",
            "10.3390": "mdpi", "10.1007": "springer", "10.1080": "taylor_francis",
            "10.2166": "iwa"}.get(m.group(1) if m else "", "other" if m else "no_doi")


def load_equations() -> pd.DataFrame:
    rows = [pd.read_parquet(f) for f in glob.glob(str(SODB / "*" / "equation_records.parquet"))]
    d = pd.concat(rows, ignore_index=True)
    # publisher from the DOI in the identity layer: half of the paper_ids are file names
    try:
        from src.graph.graph_loader import load_identity_from_postgres
        ident = load_identity_from_postgres()
        doi_of = {pid: (r.get("doi") or "") for pid, r in ident.items()}
    except Exception:
        doi_of = {}
    d["publisher"] = [publisher((doi_of.get(p) or "").replace("/", "_") + "_" if doi_of.get(p) else p)
                      for p in d.paper_id]
    d["source"] = d.latex.notna().map({True: "nougat_latex", False: "grobid_text"})
    d["has_params"] = d.n_parameters > 0
    return d


def stratified(d: pd.DataFrame, n: int, keys: list[str], rng: random.Random) -> pd.DataFrame:
    """Proportional allocation over strata with at least one item per stratum."""
    groups = list(d.groupby(keys))
    total = len(d)
    alloc = {k: max(1, round(n * len(g) / total)) for k, g in groups}
    while sum(alloc.values()) > n:                       # trim the largest strata
        k = max(alloc, key=alloc.get); alloc[k] -= 1
    picked = []
    for k, g in groups:
        idx = list(g.index); rng.shuffle(idx)
        picked += idx[: alloc[k]]
    return d.loc[picked]


def _s(v):
    return v if isinstance(v, str) else None


def snapshot(r) -> dict:
    """What the annotator sees of an equation, frozen with the gold set."""
    return {"equation_id": r.equation_id, "paper_id": r.paper_id, "equation_number": _s(r.equation_number),
            "page": None if pd.isna(r.page) else int(r.page), "latex": _s(r.latex),
            "text_grobid": _s(r.text_grobid), "image_path": _s(r.image_path), "formula_hash": r.formula_hash,
            "lead_in": _s(r.lead_in), "clause": _s(r.clause), "section": _s(getattr(r, "section", None))}


def item_equation(r) -> dict:
    return {"target_kind": "equation", "target_id": r.equation_id, "paper_id": r.paper_id,
            "equation": snapshot(r),
            "system": {"lhs_symbol": _s(r.lhs_symbol), "purpose": _s(r.purpose),
                       "purpose_source": _s(getattr(r, "purpose_source", None))}}


def family_of(text: str) -> str | None:
    t = (text or "").lower()
    for fam, pat in FAMILIES.items():
        if re.search(pat, t):
            return fam
    return None


def sample_pairs(d: pd.DataFrame, rng: random.Random) -> list[dict]:
    pairs: list[dict] = []
    seen: set[tuple] = set()

    def add(a, b, cat):
        key = tuple(sorted((a.equation_id, b.equation_id)))
        if a.paper_id == b.paper_id or key in seen:
            return False
        seen.add(key)
        pairs.append({"a": a.equation_id, "b": b.equation_id, "category": cat})
        return True

    ctx = (d.lead_in.fillna("") + " " + d.clause.fillna("") + " " + d.latex.fillna("") + " " + d.text_grobid.fillna(""))
    d = d.assign(family=ctx.map(family_of))
    # likely equivalent: same formula hash in two papers, then same family + same LHS symbol
    by_hash = [g for _, g in d[d.latex.notna()].groupby("formula_hash") if g.paper_id.nunique() > 1]
    rng.shuffle(by_hash)
    for g in by_hash:
        if sum(p["category"] == "likely_equivalent" for p in pairs) >= N_PAIRS["likely_equivalent"] // 2:
            break
        a, b = g.sample(2, random_state=rng.randint(0, 10**6)).itertuples()
        add(a, b, "likely_equivalent")
    fam = d[d.family.notna() & d.lhs_symbol.notna()]
    for _, g in fam.groupby(["family", "lhs_symbol"]):
        if len(g) < 2 or g.paper_id.nunique() < 2:
            continue
        if sum(p["category"] == "likely_equivalent" for p in pairs) >= N_PAIRS["likely_equivalent"]:
            break
        a, b = g.sample(2, random_state=rng.randint(0, 10**6)).itertuples()
        add(a, b, "likely_equivalent")
    # hard negatives: same family, different LHS symbol or different formula
    fams = [g for _, g in d[d.family.notna()].groupby("family") if g.paper_id.nunique() > 1]
    tries = 0
    while sum(p["category"] == "hard_negative" for p in pairs) < N_PAIRS["hard_negative"] and tries < 5000:
        tries += 1
        g = rng.choice(fams)
        a, b = g.sample(2, random_state=rng.randint(0, 10**6)).itertuples()
        if a.formula_hash != b.formula_hash and a.lhs_symbol != b.lhs_symbol:
            add(a, b, "hard_negative")
    # random pairs
    while sum(p["category"] == "random" for p in pairs) < N_PAIRS["random"]:
        a, b = d.sample(2, random_state=rng.randint(0, 10**6)).itertuples()
        add(a, b, "random")
    return pairs


def _round_robin(groups: dict[str, list], n: int, rng: random.Random) -> list:
    """Up to n items taking one from each group in turn (so one frequent law cannot fill the stratum)."""
    pools = {k: v[:] for k, v in groups.items()}
    for v in pools.values():
        rng.shuffle(v)
    out, keys = [], sorted(pools)
    while len(out) < n and any(pools.values()):
        for k in keys:
            if pools[k] and len(out) < n:
                out.append(pools[k].pop())
    return out


def sample_law_links(d: pd.DataFrame, rng: random.Random) -> tuple[list[dict], dict]:
    """Equation–law pairs for calibrating the law score (src/ontology/laws.py), and the size of each
    stratum's population (the evaluator weights by it to estimate corpus-wide precision and recall)."""
    from src.ontology.laws import out_path, registry, s_text
    if not out_path().exists():
        print(f"law links skipped: {out_path()} not built", file=sys.stderr)
        return [], {}
    links = pd.read_parquet(out_path())
    eq_ids = set(d.equation_id)
    links = links[links.eq_id.isin(eq_ids)]
    picked = []
    for status in ("accepted", "candidate"):
        groups: dict[str, list] = collections.defaultdict(list)
        for r in links[links.status == status].itertuples():
            groups[r.law_id].append((r.eq_id, r.law_id))
        picked += [(e, l, status) for e, l in _round_robin(groups, N_LAW_LINKS[status], rng)]
    linked = set(zip(links.eq_id, links.law_id))
    groups = collections.defaultdict(list)
    cols = ["equation_id", "lead_in", "purpose", "section", "mentions", "clause"]
    for r in d[cols].itertuples(index=False):
        row = {k: (v if isinstance(v, str) else None) for k, v in zip(cols, r)}
        for law in registry()["laws"]:
            if (row["equation_id"], law["id"]) not in linked and s_text(law["id"], row) >= 0.6:
                groups[law["id"]].append((row["equation_id"], law["id"]))
    picked += [(e, l, "unlinked_cue") for e, l in _round_robin(groups, N_LAW_LINKS["unlinked_cue"], rng)]
    population = {"accepted": int((links.status == "accepted").sum()), "candidate": int((links.status == "candidate").sum()),
                  "unlinked_cue": int(sum(len(v) for v in groups.values()))}
    return [{"eq": e, "law": l, "category": c} for e, l, c in picked], population


def law_item(eq_snapshot: dict, law_id: str) -> dict:
    from src.ontology.laws import registry
    law = next(x for x in registry()["laws"] if x["id"] == law_id)
    return {"target_kind": "law_link", "target_id": f"{eq_snapshot['equation_id']}||{law_id}",
            "paper_id": eq_snapshot["paper_id"], "equation": eq_snapshot,
            "law": {"law_id": law_id, "name": law["name"], "kind": law.get("kind"), "reference": law.get("reference"),
                    "forms": [{"latex": f["latex"], "variant": f.get("variant")} for f in law["forms"]]}}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    out = OUT / f"equation_kg_{args.version}"
    if out.exists() and not args.dry_run:
        print(f"{out} exists: a gold set version is never overwritten", file=sys.stderr)
        return 2
    rng = random.Random(SEED)

    d = load_equations()
    # at most 3 equations per paper, so that one long paper cannot dominate a stratum
    capped = d.sample(frac=1, random_state=SEED).groupby("paper_id").head(3)
    eq = stratified(capped, N_EQUATIONS, ["publisher", "source", "has_params"], rng)
    equations = [item_equation(r) for r in eq.itertuples()]

    params = []
    for r in eq.itertuples():
        for p in json.loads(r.parameters or "[]"):
            params.append({"target_kind": "parameter", "target_id": f"{r.equation_id}:{p['symbol']}",
                           "paper_id": r.paper_id, "equation": snapshot(r), "symbol": p["symbol"],
                           "symbol_tex": p.get("symbol_tex"),
                           "system": {k: p.get(k) for k in ("description", "unit", "value", "source")}})
    rng.shuffle(params); params = params[:N_PARAMS]

    names = collections.Counter()
    for ps in d.parameters.dropna():
        for p in json.loads(ps):
            if p.get("quantity"):
                names[p["quantity"]] += 1
    top = [n for n, _ in names.most_common(N_QNAMES // 2)]
    tail = [n for n in names if n not in set(top)]
    rng.shuffle(tail)
    qnames = [{"target_kind": "quantity_name", "target_id": n, "frequency": names[n]}
              for n in top + tail[: N_QNAMES - len(top)]]

    pairs = sample_pairs(d, rng)
    by_id = d.drop_duplicates("equation_id").set_index("equation_id", drop=False)
    pair_items = [{"target_kind": "equation_pair", "target_id": f"{p['a']}||{p['b']}",
                   "a": snapshot(by_id.loc[p["a"]]), "b": snapshot(by_id.loc[p["b"]])} for p in pairs]
    rng.shuffle(pair_items)

    law_links, law_population = sample_law_links(d, rng)
    law_items = [law_item(snapshot(by_id.loc[x["eq"]]), x["law"]) for x in law_links]
    rng.shuffle(law_items)

    facts = []
    try:
        from neo4j import GraphDatabase
        drv = GraphDatabase.driver(os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                                   auth=(os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]))
        with drv.session() as s:
            allf = [r.data() for r in s.run(
                "MATCH (n:NumericFact) RETURN n.fact_id AS fact_id, n.paper_id AS paper_id, "
                "n.canonical_id AS metric, n.value AS value, n.raw_cell AS raw_cell, n.table_id AS table_id, "
                "n.page AS page, n.col_header AS col_header")]
        drv.close()
        fd = pd.DataFrame(allf)
        fs = stratified(fd, N_FACTS, ["metric"], rng)
        facts = [{"target_kind": "metric_fact", "target_id": r.fact_id, "paper_id": r.paper_id,
                  "system": {"metric": r.metric, "value": r.value, "raw_cell": r.raw_cell,
                             "table_id": r.table_id, "page": r.page, "col_header": r.col_header}}
                 for r in fs.itertuples()]
    except Exception as exc:
        print(f"metric facts skipped: {exc}", file=sys.stderr)

    summary = {
        "equations": len(equations), "parameters": len(params), "quantity_names": len(qnames),
        "equation_pairs": dict(collections.Counter(p["category"] for p in pairs)), "metric_facts": len(facts),
        "law_links": dict(collections.Counter(x["category"] for x in law_links)),
        "equation_strata": eq.groupby(["publisher", "source", "has_params"]).size().to_dict(),
    }
    print(json.dumps({k: (v if k != "equation_strata" else {"|".join(map(str, kk)): vv for kk, vv in v.items()})
                      for k, v in summary.items()}, indent=1, ensure_ascii=False))
    if args.dry_run:
        return 0

    out.mkdir(parents=True)
    # PNGs are copied: later runs may rewrite data/nougat_regions/*/equations
    import shutil
    (out / "images").mkdir()
    def freeze_png(snap: dict) -> None:
        src = snap.get("image_path")
        if src and (ROOT / src).exists():
            dst = out / "images" / Path(src).name
            if not dst.exists():
                shutil.copy2(ROOT / src, dst)
            snap["image_path"] = str(dst.relative_to(ROOT))
            snap["image_sha256"] = sha256(dst)
    for it in equations + params:
        freeze_png(it["equation"])
    for it in pair_items:
        freeze_png(it["a"]); freeze_png(it["b"])
    for it in law_items:
        freeze_png(it["equation"])
    files = {"equations.jsonl": equations, "parameters.jsonl": params, "quantity_names.jsonl": qnames,
             "equation_pairs.jsonl": pair_items, "metric_facts.jsonl": facts, "law_links.jsonl": law_items,
             "qa_items.jsonl": []}
    for name, rows in files.items():
        with open(out / name, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    manifest = {
        "version": args.version, "seed": SEED, "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit, "source": {"equations_total": len(d), "papers": int(d.paper_id.nunique())},
        "pair_categories": {f"{p['a']}||{p['b']}": p["category"] for p in pairs},
        "law_link_categories": {f"{x['eq']}||{x['law']}": x["category"] for x in law_links},
        "law_link_population": law_population,
        "files": {name: sha256(out / name) for name in files},
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    print(f"frozen → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
