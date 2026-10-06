"""Phase 6 machinery on synthetic data: gold law-link sampling, predictions, evaluator, QA harness.
No live stores, no model calls; labels are synthetic and never written anywhere."""
import json
import random

import httpx
import pandas as pd
import pytest

from src.evaluation import equation_kg_eval as ev
from src.evaluation import equation_kg_predictions as pr
from src.evaluation import gold_equation_set as gs
from src.evaluation import qa_experiment as qa


def lab(kind, tid, field, verdict="correct", corrected=None, problem=None):
    return {(kind, tid, field): {"target_kind": kind, "target_id": tid, "field": field, "verdict": verdict,
                                 "corrected": corrected, "problem": problem}}


def test_wilson_and_prf():
    assert ev.wilson(0, 0)["rate"] is None
    w = ev.wilson(8, 10)
    assert w["rate"] == 0.8 and 0.45 < w["ci"][0] < 0.5
    p = ev.prf([(True, True), (True, False), (False, True), (False, False)])
    assert (p["tp"], p["fp"], p["fn"], p["f1"]) == (1, 1, 1, 0.5)


def test_normalization_metrics():
    labels = {}
    labels |= lab("quantity_name", "water depth", "canonical", corrected={"quantity_id": "quantity.water_depth"})
    labels |= lab("quantity_name", "flow", "canonical", corrected={"quantity_id": "quantity.discharge"})
    labels |= lab("quantity_name", "the", "canonical", "incorrect")
    labels |= lab("quantity_name", "stomatal conductance", "canonical", corrected={"quantity": "stomatal conductance"})
    preds = {("quantity_name", "water depth"): {"quantity_id": "quantity.water_depth", "method": "exact"},
             ("quantity_name", "flow"): {"quantity_id": "quantity.velocity", "method": "embedding"},
             ("quantity_name", "the"): {"quantity_id": None, "method": "not_a_quantity"},
             ("quantity_name", "stomatal conductance"): {"quantity_id": None, "method": "unmatched"}}
    n = ev.normalization(labels, preds)
    assert n["accuracy"]["rate"] == 0.5 and n["accuracy"]["n"] == 2
    assert n["by_method"]["exact"]["rate"] == 1.0 and n["by_method"]["embedding"]["rate"] == 0.0
    assert n["not_a_quantity_detection"]["tp"] == 1 and n["concepts_missing_from_ontology"] == 1


def test_reasoning_and_categories():
    labels, preds = {}, {}
    for tid, gold, dep, chk in (("a||b", "ALGEBRAIC", "ALGEBRAIC", "ALGEBRAIC"), ("c||d", "DIFFERENT", "DIFFERENT", "DIFFERENT"),
                                ("e||f", "EXACT", "DIFFERENT", "EXACT"), ("g||h", "UNCERTAIN", "DIFFERENT", None)):
        labels |= lab("equation_pair", tid, "relation", corrected={"relation": gold})
        preds[("equation_pair", tid)] = {"deployed": dep, "checker": chk, "parsed": [True, True]}
    r = ev.reasoning(labels, preds, {"a||b": "likely_equivalent", "c||d": "hard_negative"})
    assert r["labelled"] == 3                                        # UNCERTAIN is not scored
    assert r["equivalence_deployed"]["fn"] == 1 and r["equivalence_checker_on_parsed_pairs"]["tp"] == 2
    assert r["confusion"]["EXACT->DIFFERENT"] == 1


def test_law_links_weight_strata_by_population():
    labels, preds = {}, {}
    rows = [("e1||law.nse", "accepted", "yes"), ("e2||law.nse", "accepted", "no"),
            ("e3||law.nse", "candidate", "yes"), ("e4||law.nse", "candidate", "no"),
            ("e5||law.nse", "unlinked_cue", "yes"), ("e6||law.nse", "unlinked_cue", "no")]
    cats = {}
    for tid, stratum, inst in rows:
        labels |= lab("law_link", tid, "instance", corrected={"instance": inst})
        preds[("law_link", tid)] = {"status": stratum if stratum != "unlinked_cue" else "none",
                                    "score": {"accepted": 0.7, "candidate": 0.4}.get(stratum, 0.0)}
        cats[tid] = stratum
    out = ev.law_links(labels, preds, cats, {"accepted": 100, "candidate": 300, "unlinked_cue": 600})
    assert out["by_stratum"]["accepted"]["rate"] == 0.5
    # true positives: 50 accepted, 150 candidate, 300 unlinked → recall of accepted = 50 / 500
    assert out["corpus_estimate"]["recall_accepted"] == 0.1
    assert out["refit"]["status"] == "too_few_labels"


def test_refit_proposes_weights_with_enough_labels():
    rng = random.Random(1)
    rows = []
    for _ in range(80):
        m, t = rng.random(), rng.random()
        rows.append({"y": m + 0.3 * t > 0.7, "x": [rng.random() * 0.2, m, t, 0.0]})
    f = ev.refit(rows)
    assert f["status"] == "proposed" and f["weights"]["math"] > f["weights"]["quantity"]


def test_qa_paired_comparison():
    labels = {}
    runs = [{"answer_system": {"a1": ["rag", "q1"], "a2": ["kg_rag", "q1"], "a3": ["rag", "q2"], "a4": ["kg_rag", "q2"]}}]
    for aid, c in (("a1", "no"), ("a2", "yes"), ("a3", "partly"), ("a4", "yes")):
        labels |= lab("qa_answer", aid, "answer", corrected={"correct": c, "citations_correct": "yes", "evidence_complete": c})
    out = ev.qa(labels, runs)
    assert out["systems"]["kg_rag"]["correct"]["mean"] == 1.0 and out["systems"]["rag"]["correct"]["mean"] == 0.25
    assert out["paired"]["kg_rag - rag"]["mean"] == 0.75


def test_predict_pair_and_law_link():
    cache = {"h1": {"structural_hash": "s1", "status": "ok", "kind": "equation"},
             "h2": {"structural_hash": "s2", "status": "ok", "kind": "equation"}}
    ctx = pr.Context(cache=cache, algebra=pd.DataFrame([{"structural_hash_a": "s2", "structural_hash_b": "s1",
                                                          "verdict": "ALGEBRAIC"}]),
                     law_links=pd.DataFrame([{"eq_id": "p1:f0", "law_id": "law.manning", "status": "accepted", "score": 0.7,
                                              "variant": "velocity", "s_quantity": 0.5, "s_math": 1.0, "s_text": 1.0,
                                              "s_concept": 0.0, "math_method": "exact", "capped": None}]),
                     code=pd.DataFrame())
    item = {"target_kind": "equation_pair", "target_id": "x",
            "a": {"equation_id": "p1:f0", "formula_hash": "h1"}, "b": {"equation_id": "p2:f0", "formula_hash": "h2"}}
    p = pr.predict_pair(item, ctx)
    assert p["deployed"] == "ALGEBRAIC"
    assert pr.predict_law_link({"target_id": "p1:f0||law.manning"}, ctx)["status"] == "accepted"
    assert pr.predict_law_link({"target_id": "p9:f0||law.manning"}, ctx) == {"status": "none", "score": 0.0}


def test_gold_law_link_sampling(monkeypatch, tmp_path):
    from src.ontology import laws
    links = pd.DataFrame([{"eq_id": f"p{i}:f0", "law_id": "law.manning" if i % 2 else "law.nse",
                           "status": "accepted" if i < 10 else "candidate"} for i in range(20)])
    path = tmp_path / "links.parquet"
    links.to_parquet(path)
    monkeypatch.setattr(laws, "out_path", lambda: path)
    d = pd.DataFrame([{"equation_id": f"p{i}:f0", "lead_in": "using Manning's equation" if i >= 20 else None,
                       "purpose": None, "section": None, "mentions": None, "clause": None} for i in range(25)])
    picked, pop = gs.sample_law_links(d, random.Random(0))
    cats = [x["category"] for x in picked]
    assert cats.count("accepted") == 10 and cats.count("candidate") == 10 and cats.count("unlinked_cue") == 5
    assert pop == {"accepted": 10, "candidate": 10, "unlinked_cue": 5}
    assert {x["law"] for x in picked if x["category"] == "accepted"} == {"law.manning", "law.nse"}


def test_qa_run_is_blind_and_checks_citations():
    def handler(request):
        p = request.url.path
        if p.endswith("/search/chunks"):
            return httpx.Response(200, json={"hits": [{"chunk_id": "c1", "paper": {"paper_id": "P1"}, "page": 3,
                                                       "text": "NSE was 0.81"}]})
        if p.endswith("/laws/law.nse"):
            return httpx.Response(200, json={"law": {"name": "NSE", "reference": "N&S 1970"}, "forms": [], "instances": []})
        if p.endswith("/metrics/facts"):
            return httpx.Response(200, json={"items": []})
        if p.endswith("/equations/search"):
            return httpx.Response(200, json={"items": []})
        return httpx.Response(404, json={})
    api = qa.Api("http://x/v1", "k", transport=httpx.MockTransport(handler))
    llm = lambda prompt: "The NSE was 0.81 [C:c1] [C:zz]." if "[C:c1]" in prompt else "The evidence provided does not answer this."
    answers, meta = qa.run([{"qid": "q1", "question": "What NSE did they get?", "answer": "0.81", "evidence": ["P1, p. 3"]}],
                           api, llm, "20261006T000000Z")
    assert len(answers) == 3 and all("system" not in a for a in answers)
    assert sorted(s for s, _ in meta["answer_system"].values()) == ["kg", "kg_rag", "rag"]
    rag_id = next(a for a, (s, _) in meta["answer_system"].items() if s == "rag")
    assert meta["auto"][rag_id]["cited_unknown"] == ["C:zz"] and meta["auto"][rag_id]["reference_paper_recall"] == 1.0
    kg_id = next(a for a, (s, _) in meta["answer_system"].items() if s == "kg")
    assert meta["auto"][kg_id]["abstained"] is True


def test_qa_api_fails_loudly():
    api = qa.Api("http://x", "k", transport=httpx.MockTransport(lambda r: httpx.Response(503, json={})))
    with pytest.raises(RuntimeError):
        qa.rag(api, "a question")
