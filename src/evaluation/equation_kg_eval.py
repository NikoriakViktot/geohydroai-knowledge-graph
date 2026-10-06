"""
equation_kg_eval.py — evaluate the equation-centric KG on its human-labelled gold set
(docs_v2/EQUATION_KG_PLAN.md, phase 6).

Labels: the current human judgements in verify.current (people only, R-ACC-7).
Predictions: a frozen file from src/evaluation/equation_kg_predictions.py (component versions inside).

Tasks
    1 extraction     equation LaTeX vs image; parameter definition and unit (precision; by source)
    2 linking        what the equation computes (lhs/purpose)
    3 normalization  surface quantity name → concept: accuracy, coverage, macro-F1, by match method
    4 reasoning      pair relation: deployed system and the algebra checker; binary equivalence
                     (EXACT ∪ ALGEBRAIC) and same-law identity; confusion matrices
    5 law links      precision / recall of `accepted`, estimated for the whole corpus by weighting each
                     sampled stratum with its population; a logistic re-fit of the four channel weights
                     (proposed, never applied: a new weighting is a new laws version)
    6 end-to-end QA  per system: correct, citations correct, evidence complete; paired differences
    metric facts     precision of table facts
Every rate carries a 95 % interval (Wilson for proportions, percentile bootstrap, 2,000 resamples,
seed 20261005, for differences and F1). An empty label set reports n = 0, never a rate.

Writes data/gold/equation_kg_<version>/eval/<UTC stamp>.{json,md}; never overwrites.

Usage:
    python -m src.evaluation.equation_kg_eval --gold v1 [--predictions <stamp>]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "data" / "gold"
SEED = 20261005
B = 2000
EQUIVALENT = {"EXACT", "ALGEBRAIC"}
RELATIONS = ("EXACT", "ALGEBRAIC", "SAME_LAW", "RELATED", "DIFFERENT", "UNCERTAIN")


# ── statistics ─────────────────────────────────────────────────────────────────

def wilson(k: float, n: int, z: float = 1.96) -> dict:
    if n == 0:
        return {"n": 0, "rate": None, "ci": None}
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return {"n": n, "k": k, "rate": round(p, 4), "ci": [round(max(0, mid - half), 4), round(min(1, mid + half), 4)]}


def bootstrap(values: list, stat, seed: int = SEED, b: int = B) -> list | None:
    if not values:
        return None
    rng = random.Random(seed)
    out = []
    for _ in range(b):
        sample = [values[rng.randrange(len(values))] for _ in values]
        v = stat(sample)
        if v is not None:
            out.append(v)
    if not out:
        return None
    out.sort()
    return [round(out[int(0.025 * len(out))], 4), round(out[int(0.975 * len(out)) - 1], 4)]


def prf(pairs: list[tuple[bool, bool]]) -> dict:
    """pairs of (predicted positive, truly positive)."""
    tp = sum(p and t for p, t in pairs)
    fp = sum(p and not t for p, t in pairs)
    fn = sum((not p) and t for p, t in pairs)
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if prec == 0 or rec == 0 else None)

    def f1_of(s):
        t_p = sum(p and t for p, t in s); f_p = sum(p and not t for p, t in s); f_n = sum((not p) and t for p, t in s)
        return 2 * t_p / (2 * t_p + f_p + f_n) if (2 * t_p + f_p + f_n) else None
    return {"n": len(pairs), "tp": tp, "fp": fp, "fn": fn,
            "precision": wilson(tp, tp + fp), "recall": wilson(tp, tp + fn),
            "f1": None if f1 is None else round(f1, 4), "f1_ci": bootstrap(pairs, f1_of)}


def macro_f1(gold: list[str], pred: list[str | None]) -> float | None:
    classes = set(gold)
    if not classes:
        return None
    scores = []
    for c in classes:
        tp = sum(g == c and p == c for g, p in zip(gold, pred))
        fp = sum(g != c and p == c for g, p in zip(gold, pred))
        fn = sum(g == c and p != c for g, p in zip(gold, pred))
        scores.append(2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0)
    return round(sum(scores) / len(scores), 4)


def score_of(verdict: str) -> float | None:
    return {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}.get(verdict)


def rate_of(labels: list[dict]) -> dict:
    """Share of correct (partial counts half) with its interval, and the verdict counts."""
    vals = [score_of(l["verdict"]) for l in labels if score_of(l["verdict"]) is not None]
    out = wilson(sum(vals), len(vals))
    out["verdicts"] = dict(Counter(l["verdict"] for l in labels))
    return out


# ── tasks ──────────────────────────────────────────────────────────────────────

def extraction(labels: dict, items: dict) -> dict:
    eq = [l for (k, t, f), l in labels.items() if k == "equation" and f == "latex"]
    by_src = defaultdict(list)
    for l in eq:
        it = items.get(("equation", l["target_id"])) or {}
        by_src["nougat_latex" if (it.get("equation") or {}).get("latex") else "grobid_text"].append(l)
    params = defaultdict(list)
    by_psrc = defaultdict(list)
    for (k, t, f), l in labels.items():
        if k == "parameter" and f in ("definition", "unit"):
            params[f].append(l)
            if f == "definition":
                src = ((items.get(("parameter", t)) or {}).get("system") or {}).get("source") or "?"
                by_psrc[src].append(l)
    return {"equation_formula": rate_of(eq), "equation_formula_by_source": {k: rate_of(v) for k, v in by_src.items()},
            "formula_problems": dict(Counter(l.get("problem") for l in eq if l.get("problem"))),
            "parameter_definition": rate_of(params["definition"]), "parameter_unit": rate_of(params["unit"]),
            "parameter_definition_by_source": {k: rate_of(v) for k, v in by_psrc.items()},
            "parameter_problems": dict(Counter(l.get("problem") for l in params["definition"] if l.get("problem")))}


def linking(labels: dict) -> dict:
    comp = [l for (k, t, f), l in labels.items() if k == "equation" and f == "computes"]
    roles = Counter((l.get("corrected") or {}).get("computation_role") for l in comp)
    return {"computes": rate_of(comp), "computation_roles": dict(roles)}


def normalization(labels: dict, preds: dict) -> dict:
    rows = []
    for (k, t, f), l in labels.items():
        if k != "quantity_name" or f != "canonical":
            continue
        p = preds.get(("quantity_name", t)) or {}
        c = l.get("corrected") or {}
        is_q = l["verdict"] != "incorrect"
        rows.append({"name": t, "is_quantity": is_q, "gold": c.get("quantity_id"), "free": c.get("quantity"),
                     "pred": p.get("quantity_id"), "method": p.get("method")})
    junk = [(r["method"] == "not_a_quantity", not r["is_quantity"]) for r in rows]
    q = [r for r in rows if r["is_quantity"] and r["gold"]]
    missing = [r for r in rows if r["is_quantity"] and not r["gold"]]
    acc = wilson(sum(r["pred"] == r["gold"] for r in q), len(q))
    predicted = [r for r in q if r["pred"]]
    by_method = defaultdict(list)
    for r in q:
        by_method[r["method"]].append(r["pred"] == r["gold"])
    return {"labelled": len(rows), "not_a_quantity_detection": prf(junk),
            "accuracy": acc, "coverage": wilson(len(predicted), len(q)),
            "precision_when_predicted": wilson(sum(r["pred"] == r["gold"] for r in predicted), len(predicted)),
            "macro_f1": macro_f1([r["gold"] for r in q], [r["pred"] for r in q]),
            "by_method": {m: wilson(sum(v), len(v)) for m, v in by_method.items()},
            "concepts_missing_from_ontology": len(missing),
            "missing_examples": [r["free"] or r["name"] for r in missing[:30]]}


def reasoning(labels: dict, preds: dict, categories: dict) -> dict:
    rows = []
    for (k, t, f), l in labels.items():
        if k != "equation_pair" or f != "relation":
            continue
        rel = (l.get("corrected") or {}).get("relation")
        if rel not in RELATIONS or rel == "UNCERTAIN" or l["verdict"] == "unsure":
            continue
        p = preds.get(("equation_pair", t)) or {}
        rows.append({"gold": rel, "deployed": p.get("deployed"), "checker": p.get("checker"),
                     "parsed": all(p.get("parsed") or [False]), "category": categories.get(t)})
    conf = Counter((r["gold"], r["deployed"]) for r in rows)
    eq_dep = prf([(r["deployed"] in EQUIVALENT, r["gold"] in EQUIVALENT) for r in rows])
    parsed = [r for r in rows if r["parsed"] and r["checker"] not in (None, "UNKNOWN")]
    eq_chk = prf([(r["checker"] in EQUIVALENT, r["gold"] in EQUIVALENT) for r in parsed])
    same_law = prf([(r["deployed"] in EQUIVALENT | {"SAME_LAW"}, r["gold"] in EQUIVALENT | {"SAME_LAW"}) for r in rows])
    return {"labelled": len(rows), "relation_accuracy": wilson(sum(r["gold"] == r["deployed"] for r in rows), len(rows)),
            "confusion": {f"{g}->{p}": n for (g, p), n in sorted(conf.items())},
            "equivalence_deployed": eq_dep, "equivalence_checker_on_parsed_pairs": eq_chk,
            "same_law_or_equivalent": same_law,
            "by_sampling_category": {c: wilson(sum(r["gold"] == r["deployed"] for r in rows if r["category"] == c),
                                               sum(r["category"] == c for r in rows))
                                     for c in sorted({r["category"] for r in rows if r["category"]})}}


def law_links(labels: dict, preds: dict, categories: dict, population: dict) -> dict:
    rows = []
    for (k, t, f), l in labels.items():
        if k != "law_link" or f != "instance":
            continue
        inst = (l.get("corrected") or {}).get("instance")
        if inst not in ("yes", "no"):
            continue
        p = preds.get(("law_link", t)) or {}
        rows.append({"y": inst == "yes", "status": p.get("status", "none"), "score": p.get("score") or 0.0,
                     "x": [p.get(c) or 0.0 for c in ("s_quantity", "s_math", "s_text", "s_concept")],
                     "stratum": categories.get(t) or ("accepted" if p.get("status") == "accepted" else
                                                      "candidate" if p.get("status") == "candidate" else "unlinked_cue"),
                     "variant_gold": (l.get("corrected") or {}).get("variant"), "variant_pred": p.get("variant")})
    out = {"labelled": len(rows), "by_stratum": {}}
    strata = defaultdict(list)
    for r in rows:
        strata[r["stratum"]].append(r)
    for s, rs in strata.items():
        out["by_stratum"][s] = wilson(sum(r["y"] for r in rs), len(rs))
    # corpus-wide estimate: each stratum's positive rate × its population
    est = {s: (population.get(s, 0) * (sum(r["y"] for r in rs) / len(rs))) for s, rs in strata.items() if rs}
    if est.get("accepted") is not None:
        tp = est["accepted"]
        positives = sum(est.values())
        out["corpus_estimate"] = {"precision_accepted": out["by_stratum"]["accepted"],
                                  "recall_accepted": round(tp / positives, 4) if positives else None,
                                  "population": population,
                                  "note": "recall counts only true instances among linked or text-cued pairs"}
    acc = [r for r in rows if r["variant_gold"] and r["variant_pred"]]
    out["variant_accuracy"] = wilson(sum(r["variant_gold"] == r["variant_pred"] for r in acc), len(acc))
    out["threshold_curve"] = [{"threshold": th, **{k: v for k, v in prf([(r["score"] >= th, r["y"]) for r in rows]).items()
                                                  if k in ("tp", "fp", "fn", "f1")}}
                              for th in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)] if rows else []
    out["refit"] = refit(rows)
    return out


def refit(rows: list[dict]) -> dict:
    """Logistic regression on the four channels (5-fold AUC), weights rescaled to sum to 1. A proposal only."""
    ys = [r["y"] for r in rows]
    if len(rows) < 30 or len(set(ys)) < 2 or min(Counter(ys).values()) < 5:
        return {"status": "too_few_labels", "n": len(rows)}
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    X, y = np.array([r["x"] for r in rows]), np.array(ys, dtype=int)
    model = LogisticRegression(class_weight="balanced", max_iter=1000)
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    prob = cross_val_predict(model, X, y, cv=folds, method="predict_proba")[:, 1]
    current = X @ np.array([0.3, 0.4, 0.2, 0.1])
    model.fit(X, y)
    w = np.clip(model.coef_[0], 0, None)
    return {"status": "proposed", "n": len(rows), "auc_cv_refit": round(float(roc_auc_score(y, prob)), 4),
            "auc_current_weights": round(float(roc_auc_score(y, current)), 4),
            "weights": dict(zip(("quantity", "math", "text", "concept"),
                                [round(float(v), 4) for v in (w / w.sum() if w.sum() else w)])),
            "note": "never applied automatically; adopting it means a new laws version"}


def metric_facts(labels: dict) -> dict:
    return rate_of([l for (k, t, f), l in labels.items() if k == "metric_fact" and f == "value"])


def qa(labels: dict, runs: list[dict]) -> dict:
    """Answers judged blind; the run manifest maps each answer id to its system."""
    system_of = {}
    for run in runs:
        system_of.update(run.get("answer_system", {}))
    per = defaultdict(lambda: defaultdict(list))
    by_q = defaultdict(dict)
    val = {"yes": 1.0, "partly": 0.5, "no": 0.0}
    for (k, t, f), l in labels.items():
        if k != "qa_answer" or f != "answer" or t not in system_of:
            continue
        sysname, qid = system_of[t]
        c = l.get("corrected") or {}
        for m in ("correct", "citations_correct", "evidence_complete"):
            if c.get(m) in val:
                per[sysname][m].append(val[c[m]])
        if c.get("correct") in val:
            by_q[qid][sysname] = val[c["correct"]]
    out = {"systems": {s: {m: {"n": len(v), "mean": round(sum(v) / len(v), 4) if v else None,
                               "ci": bootstrap(v, lambda x: sum(x) / len(x))} for m, v in ms.items()}
                       for s, ms in per.items()}, "paired": {}}
    names = sorted(per)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            diffs = [d[a] - d[b] for d in by_q.values() if a in d and b in d]
            out["paired"][f"{a} - {b}"] = {"n": len(diffs), "mean": round(sum(diffs) / len(diffs), 4) if diffs else None,
                                           "ci": bootstrap(diffs, lambda x: sum(x) / len(x))}
    return out


# ── loading ────────────────────────────────────────────────────────────────────

def load_items(gold_dir: Path) -> dict:
    items = {}
    for f in gold_dir.glob("*.jsonl"):
        for line in f.open():
            it = json.loads(line)
            items[(it["target_kind"], it["target_id"])] = it
    return items


def load_predictions(gold_dir: Path, stamp: str | None) -> tuple[dict, dict]:
    files = sorted((gold_dir / "predictions").glob("*.jsonl"))
    if stamp:
        files = [f for f in files if f.stem == stamp]
    if not files:
        return {}, {}
    f = files[-1]
    preds = {(r["target_kind"], r["target_id"]): r["prediction"] for r in map(json.loads, f.open())}
    meta = json.loads(f.with_suffix(".json").read_text()) if f.with_suffix(".json").exists() else {}
    return preds, {**meta, "file": f.name}


KINDS = ("equation", "parameter", "quantity_name", "equation_pair", "law_link", "metric_fact", "qa_answer")


def load_labels(items: dict, runs: list[dict]) -> dict:
    """(kind, target_id, field) → current human judgement, for the targets of this gold set."""
    from src.services import verify
    out = {}
    for kind in KINDS:
        ids = [t for (k, t) in items if k == kind] if kind != "qa_answer" else \
            [a for r in runs for a in r.get("answer_system", {})]
        for i in range(0, len(ids), 500):
            for r in verify.listing(current=True, target_kind=kind, target_ids=ids[i:i + 500], limit=5000):
                out[(kind, r["target_id"], r.get("field"))] = r
    return out


def evaluate(items: dict, labels: dict, preds: dict, manifest: dict, runs: list[dict]) -> dict:
    return {
        "labels": dict(Counter(k for (k, _, _) in labels)),
        "1_extraction": extraction(labels, items),
        "2_linking": linking(labels),
        "3_normalization": normalization(labels, preds),
        "4_reasoning": reasoning(labels, preds, manifest.get("pair_categories", {})),
        "5_law_links": law_links(labels, preds, manifest.get("law_link_categories", {}),
                                 manifest.get("law_link_population", {})),
        "6_qa": qa(labels, runs),
        "metric_facts": metric_facts(labels),
    }


def markdown(report: dict) -> str:
    def r(x):
        if not isinstance(x, dict) or "n" not in x:
            return "—"
        if not x.get("n"):
            return "n = 0"
        ci = x.get("ci")
        return f"{x['rate']:.2f} [{ci[0]:.2f}–{ci[1]:.2f}], n = {x['n']}" if ci else f"n = {x['n']}"
    e, n, rs, ll = report["1_extraction"], report["3_normalization"], report["4_reasoning"], report["5_law_links"]
    lines = [f"# Equation KG evaluation — gold {report['gold']}, predictions {report['predictions'].get('file')}", "",
             "Human labels only (verify.current). Rates are shares of correct (partial = ½) with 95 % intervals.", "",
             "| Task | Measure | Value |", "|---|---|---|",
             f"| Extraction | formula matches image | {r(e['equation_formula'])} |",
             f"| Extraction | parameter definition | {r(e['parameter_definition'])} |",
             f"| Extraction | parameter unit | {r(e['parameter_unit'])} |",
             f"| Linking | what the equation computes | {r(report['2_linking']['computes'])} |",
             f"| Normalization | name → concept accuracy | {r(n['accuracy'])} |",
             f"| Normalization | coverage | {r(n['coverage'])} |",
             f"| Reasoning | relation accuracy (deployed) | {r(rs['relation_accuracy'])} |",
             f"| Reasoning | equivalence precision (deployed) | {r(rs['equivalence_deployed']['precision'])} |",
             f"| Reasoning | equivalence recall (deployed) | {r(rs['equivalence_deployed']['recall'])} |",
             f"| Law links | precision of accepted | {r(ll['by_stratum'].get('accepted'))} |",
             f"| Metric facts | value correct | {r(report['metric_facts'])} |", ""]
    if ll.get("corpus_estimate"):
        lines.append(f"Law links, corpus-wide recall estimate: {ll['corpus_estimate']['recall_accepted']}.")
    if ll.get("refit", {}).get("status") == "proposed":
        f = ll["refit"]
        lines.append(f"Law score re-fit (proposal, not applied): weights {f['weights']}, CV AUC {f['auc_cv_refit']} "
                     f"vs current {f['auc_current_weights']}.")
    for s, ms in report["6_qa"]["systems"].items():
        lines.append(f"QA {s}: " + ", ".join(f"{m} {v['mean']} (n = {v['n']})" for m, v in ms.items()))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--gold", required=True)
    ap.add_argument("--predictions", help="stamp of a predictions file (default: newest)")
    args = ap.parse_args(argv)
    gold_dir = GOLD / f"equation_kg_{args.gold}"
    manifest = json.loads((gold_dir / "MANIFEST.json").read_text())
    items = load_items(gold_dir)
    preds, pmeta = load_predictions(gold_dir, args.predictions)
    if not preds:
        print("no predictions: run python -m src.evaluation.equation_kg_predictions first", file=sys.stderr)
        return 2
    runs = [json.loads(p.read_text()) for p in sorted((gold_dir / "qa_runs").glob("*/RUN.json"))]
    labels = load_labels(items, runs)
    report = {"gold": args.gold, "predictions": pmeta, **evaluate(items, labels, preds, manifest, runs)}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = gold_dir / "eval"
    out.mkdir(exist_ok=True)
    (out / f"{stamp}.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    (out / f"{stamp}.md").write_text(markdown(report))
    print(markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
