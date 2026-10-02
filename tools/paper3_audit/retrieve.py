"""Candidate retrieval per atomic claim: exact refs → semantic chunks → citation
neighbours (OpenAlex referenced_works) → knowledge-graph neighbours → full-text
density gate. Everything found is recorded with its route; nothing is dropped
silently. Reuses the gates and the prefilter of ``src.paper_3.retrieve``.
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.paper_3 import retrieve as base
from src.paper_3.retrieve import (DISTANCE_GATE, MAX_CANDIDATES_PER_THESIS, MIN_CHUNKS,
                                  TOP_K_CHUNKS, ThesisCandidate, prefilter, semantic_gate,
                                  stage_fulltext, stage_kg)
from tools.paper3_audit import corpus as corpus_mod
from tools.paper3_audit.bibtex import bib_doi, load_bib_entries
from tools.paper3_audit.claims import AtomicClaim
from tools.paper3_audit.config import SEARCH_LOG, WORK_DIR

logger = logging.getLogger(__name__)

CANDIDATE_PARTS = "candidates_parts"
CANDIDATES_FILE = "candidates.parquet"
SEMANTIC_HITS_FILE = "semantic_hits.parquet"
BEST_HITS_PER_PAPER = 3
CITATION_CAP = 20
KG_CAP = 20

CANDIDATE_COLUMNS = list(base.CANDIDATE_COLUMNS) + [
    "atomic_claim_id", "priority", "category", "routes", "exact_ref_key",
    "exact_ref_relation", "exact_ref_status", "best_chunk_id", "best_chunk_page",
    "best_chunk_section", "best_chunk_distance", "from_counter_query", "duplicate_of",
    "contrast_cue", "method_heavy",
]


def rules() -> dict:
    return {"DISTANCE_GATE": DISTANCE_GATE, "MIN_CHUNKS": MIN_CHUNKS, "TOP_K_CHUNKS": TOP_K_CHUNKS,
            "MAX_CANDIDATES_PER_THESIS": MAX_CANDIDATES_PER_THESIS,
            "CITATION_CAP": CITATION_CAP, "KG_CAP": KG_CAP, "source": "src.paper_3.retrieve"}


# ── routes ────────────────────────────────────────────────────────────────────

def route_exact(claim: AtomicClaim, resolver: corpus_mod.PaperResolver,
                bib: dict[str, dict]) -> tuple[dict[str, dict], list[dict]]:
    """The references theses.csv already names: bib key → DOI/title → corpus paper."""
    found: dict[str, dict] = {}
    log = []
    for ref in (claim.thesis.refs if claim.thesis else ()):
        if ref.placeholder:
            log.append({"key": ref.key, "resolved": None, "reason": "placeholder (NEEDS_SOURCE)"})
            continue
        entry = bib.get(ref.key)
        if entry is None:
            log.append({"key": ref.key, "resolved": None, "reason": "key not in references.bib"})
            continue
        doi = bib_doi(entry)
        pid = resolver.from_doi(doi) if doi else None
        how = "exact_doi" if pid else ""
        if pid is None:
            title = entry["fields"].get("title", "")
            pid = resolver.from_title(title) if title else None
            how = "exact_title" if pid else ""
        log.append({"key": ref.key, "doi": doi, "resolved": pid,
                    "reason": how or "not in corpus"})
        if pid:
            found[pid] = {"key": ref.key, "relation": ref.relation, "status": ref.status, "how": how}
    return found, log


def route_semantic(claim: AtomicClaim, store, embedder, resolver: corpus_mod.PaperResolver,
                   top_k: int = TOP_K_CHUNKS) -> tuple[dict[str, dict], list[dict], dict]:
    """Nearest chunks per query, aggregated per canonical paper; chunk ids kept."""
    by_paper: dict[str, dict] = {}
    hits_out: list[dict] = []
    seen_chunks: set[str] = set()
    n_hits = 0
    for query, kind in claim.queries:
        try:
            vec = embedder.embed_query(query)
            hits = store.query(vec, top_k=top_k, where=None)
        except Exception as exc:
            logger.warning("%s: semantic query failed (%s): %s", claim.atomic_id, query[:60], exc)
            continue
        for rank, hit in enumerate(hits, 1):
            n_hits += 1
            chunk_id = str(hit.get("chunk_id") or "")
            pid = resolver.from_hit(str(hit.get("paper_id") or hit.get("filename") or ""))
            distance = float(hit.get("distance", 1.0))
            hits_out.append({
                "atomic_claim_id": claim.atomic_id, "query_kind": kind, "query": query,
                "rank": rank, "chunk_id": chunk_id, "paper_id": pid or "",
                "raw_paper_id": str(hit.get("paper_id") or hit.get("filename") or ""),
                "page": hit.get("page"), "section_title": hit.get("section_title", ""),
                "chunk_type": hit.get("chunk_type", ""), "distance": distance,
                "text_head": (hit.get("text") or "")[:300],
            })
            if pid is None or (chunk_id and chunk_id in seen_chunks):
                continue
            seen_chunks.add(chunk_id)
            row = by_paper.setdefault(pid, {"paper_id": pid, "n_chunks": 0, "best_distance": 1.0,
                                            "best": [], "from_counter": False})
            row["n_chunks"] += 1
            row["best_distance"] = min(row["best_distance"], distance)
            row["from_counter"] = row["from_counter"] or kind == "counter"
            row["best"].append((distance, chunk_id, hit.get("page"), hit.get("section_title", "")))
    for row in by_paper.values():
        row["best"] = sorted(row["best"], key=lambda t: t[0])[:BEST_HITS_PER_PAPER]
    stats = {"top_k": top_k, "n_queries": len(claim.queries), "n_hits": n_hits,
             "n_unique_chunks": len(seen_chunks), "n_papers": len(by_paper),
             "n_gated": sum(1 for r in by_paper.values() if semantic_gate(r))}
    return by_paper, hits_out, stats


class CitationGraph:
    """OpenAlex referenced_works of the corpus, both directions, corpus-restricted."""

    def __init__(self, oa_index: pd.DataFrame, edges: pd.DataFrame):
        self.oa_to_pid = dict(zip(oa_index["openalex_id"], oa_index["paper_id"]))
        self.pid_to_oa = {v: k for k, v in self.oa_to_pid.items()}
        self.forward: dict[str, set[str]] = defaultdict(set)   # paper → referenced W-ids
        self.reverse: dict[str, set[str]] = defaultdict(set)   # W-id → papers citing it
        for r in edges.itertuples():
            self.forward[r.paper_id].add(r.referenced_openalex_id)
            self.reverse[r.referenced_openalex_id].add(r.paper_id)

    def expand(self, seeds: list[str], cap: int = CITATION_CAP) -> tuple[list[dict], dict]:
        links: dict[str, dict] = {}
        seeds_set = set(seeds)
        n_refs = n_cited = 0
        for s in seeds:
            for w in self.forward.get(s, ()):
                pid = self.oa_to_pid.get(w)
                if pid and pid not in seeds_set:
                    n_refs += 1
                    d = links.setdefault(pid, {"paper_id": pid, "n_seed_links": 0, "relation": "references"})
                    d["n_seed_links"] += 1
            w_seed = self.pid_to_oa.get(s)
            if w_seed:
                for pid in self.reverse.get(w_seed, ()):
                    if pid not in seeds_set:
                        n_cited += 1
                        d = links.setdefault(pid, {"paper_id": pid, "n_seed_links": 0, "relation": "cited_by"})
                        d["n_seed_links"] += 1
        rows = sorted(links.values(), key=lambda d: -d["n_seed_links"])[:cap]
        return rows, {"n_seeds": len(seeds), "n_seeds_with_openalex": sum(1 for s in seeds if s in self.pid_to_oa),
                      "n_reference_links": n_refs, "n_cited_by_links": n_cited, "n_kept": len(rows),
                      "source": "data/enriched openalex.referenced_works"}


def probe_neo4j(runner=None) -> bool:
    try:
        if runner is None:
            from src.dashboard_dash.data_neo4j import _run as runner
        rows = runner("RETURN 1 AS ok")
        return bool(rows)
    except Exception as exc:
        logger.warning("Neo4j probe failed: %s", exc)
        return False


def route_kg(seeds: list[str], available: bool, resolver: corpus_mod.PaperResolver,
             runner=None) -> tuple[list[dict], dict]:
    if not available:
        return [], {"status": "route_unavailable", "reason": "Neo4j not reachable"}
    try:
        if runner is not None:
            rows = runner(base._KG_CYPHER, seeds=list(seeds))
            rows = [r for r in rows if int(r.get("n_shared", 0)) >= 2][:KG_CAP]
        else:
            rows, ok = stage_kg(seeds, min_shared=2, cap=KG_CAP)
            if not ok:
                return [], {"status": "route_unavailable", "reason": "stage_kg reported unavailable"}
    except Exception as exc:
        return [], {"status": "route_unavailable", "reason": str(exc)[:120]}
    out = []
    for r in rows:
        pid = resolver.from_hit(str(r.get("paper_id", "")))
        if pid:
            out.append({"paper_id": pid, "via": r.get("via", ""), "n_shared": int(r.get("n_shared", 0))})
    return out, {"status": "ok", "n_seeds": len(seeds), "n_kept": len(out)}


# ── per-claim assembly ────────────────────────────────────────────────────────

def assemble(claim: AtomicClaim, exact: dict, semantic: dict, citation: list[dict],
             kg: list[dict], index_by_pid: dict, fulltext=stage_fulltext) -> list[dict]:
    thesis = claim.to_thesis()
    origins: dict[str, dict] = {}
    for pid, meta in exact.items():
        origins.setdefault(pid, {"routes": set(), "stage": "exact"})["routes"].add("exact")
    for pid, row in semantic.items():
        d = origins.setdefault(pid, {"routes": set(), "stage": "semantic"})
        d["routes"].add("semantic")
    for r in citation:
        d = origins.setdefault(r["paper_id"], {"routes": set(), "stage": "citation"})
        d["routes"].add(r["relation"])
    for r in kg:
        d = origins.setdefault(r["paper_id"], {"routes": set(), "stage": "kg"})
        d["routes"].add("graph_neighbor")

    cit_by = {r["paper_id"]: r for r in citation}
    kg_by = {r["paper_id"]: r for r in kg}
    rows = []
    for pid, o in origins.items():
        meta = index_by_pid.get(pid, {})
        sem = semantic.get(pid)
        families, _sections, contrast, method_heavy = fulltext(thesis, pid)
        gated = bool(sem and semantic_gate(sem))
        stage = o["stage"]
        if stage == "semantic" and not gated:
            stage_found = "semantic_below_gate"
        else:
            stage_found = stage
        cand = ThesisCandidate(
            thesis_id=claim.atomic_id, paper_id=pid, doi=meta.get("doi_clean", ""),
            title=meta.get("title", ""), year=_year(meta.get("year")),
            stage_found=stage_found if stage_found != "exact" else "exact",
            retrieval_origin=("semantic" if "semantic" in o["routes"] else
                              "references" if "references" in o["routes"] else
                              "cited_by" if "cited_by" in o["routes"] else
                              "graph_neighbor" if "graph_neighbor" in o["routes"] else "exact"),
            citation_depth=1 if pid in cit_by else 0,
            semantic_best_distance=(sem["best_distance"] if sem else None),
            n_semantic_chunks=(sem["n_chunks"] if sem else 0),
            n_keyterm_families_hit=families,
            kg_relation=(kg_by[pid]["via"] if pid in kg_by else None),
            citation_relation=(cit_by[pid]["relation"] if pid in cit_by else None),
            n_seed_links=(cit_by[pid]["n_seed_links"] if pid in cit_by else 0),
            contradicts_hint=contrast, method_hint=method_heavy,
        )
        if pid in exact:
            passed, reason, score = True, "named in theses.csv refs", 99.0
            cand.stage_found = "exact"
        elif stage_found == "semantic_below_gate":
            passed, reason, score = False, "semantic hit below gate", 0.0
        else:
            passed, reason, score = prefilter(cand)
        cand.prefilter_pass, cand.prefilter_reason, cand.prefilter_score = passed, reason, score
        best = (sem["best"][0] if sem and sem["best"] else (None, "", None, ""))
        rows.append({
            **cand.__dict__,
            "atomic_claim_id": claim.atomic_id, "priority": claim.priority,
            "category": claim.thesis.category if claim.thesis else "",
            "routes": "+".join(sorted(o["routes"])),
            "exact_ref_key": exact.get(pid, {}).get("key", ""),
            "exact_ref_relation": exact.get(pid, {}).get("relation", ""),
            "exact_ref_status": exact.get(pid, {}).get("status", ""),
            "best_chunk_id": best[1], "best_chunk_page": best[2], "best_chunk_section": best[3],
            "best_chunk_distance": best[0],
            "from_counter_query": bool(sem and sem.get("from_counter")),
            "duplicate_of": meta.get("duplicate_of", ""),
            "contrast_cue": contrast, "method_heavy": method_heavy,
        })
    rows.sort(key=lambda r: (-int(r["prefilter_pass"]), -r["prefilter_score"]))
    # Cap what goes to screening; everything is still recorded.
    n_pass = 0
    for r in rows:
        if r["prefilter_pass"]:
            n_pass += 1
            if n_pass > MAX_CANDIDATES_PER_THESIS:
                r["prefilter_pass"] = False
                r["prefilter_reason"] = f"beyond cap of {MAX_CANDIDATES_PER_THESIS} (score {r['prefilter_score']})"
    return rows


def _year(v):
    try:
        return int(str(v)[:4]) if v not in (None, "") else None
    except ValueError:
        return None


# ── the step ──────────────────────────────────────────────────────────────────

def done_claims(log_path: Path = SEARCH_LOG) -> set[str]:
    if not log_path.exists():
        return set()
    out = set()
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            out.add(json.loads(line)["atomic_claim_id"])
        except Exception:
            continue
    return out


def run(claims: list[AtomicClaim], store, embedder, work_dir: Path = WORK_DIR,
        log_path: Path = SEARCH_LOG, queries_sha256: str = "", n_chroma: int | None = None,
        limit: int | None = None, force: bool = False, kg_available: bool | None = None,
        kg_runner=None, fulltext=stage_fulltext, top_k: int = TOP_K_CHUNKS) -> Path:
    index = corpus_mod.load_runtime_index(work_dir)
    index_by_pid = {r["paper_id"]: r for r in index.to_dict("records")}
    resolver = corpus_mod.PaperResolver(index)
    oa_index, edges = corpus_mod.load_openalex(work_dir)
    graph = CitationGraph(oa_index, edges)
    bib = load_bib_entries(work_dir)
    if kg_available is None:
        kg_available = probe_neo4j(kg_runner)
    logger.info("KG route: %s", "available" if kg_available else "route_unavailable")

    parts_dir = work_dir / CANDIDATE_PARTS
    parts_dir.mkdir(parents=True, exist_ok=True)
    already = set() if force else done_claims(log_path)
    todo = [c for c in claims if c.atomic_id not in already and not c.not_needed]
    # Pass A (high) first, then B, then C — the order the brief requires; stable within a pass.
    todo.sort(key=lambda c: {"A": 0, "B": 1, "C": 2}.get(c.pass_id, 3))
    if limit:
        todo = todo[:limit]
    logger.info("retrieve: %d claims to do (%d already logged)", len(todo), len(already))

    hits_path = work_dir / SEMANTIC_HITS_FILE
    with log_path.open("a", encoding="utf-8") as log:
        for i, claim in enumerate(todo, 1):
            t0 = time.time()
            exact, exact_log = route_exact(claim, resolver, bib)
            semantic, hits, sem_stats = route_semantic(claim, store, embedder, resolver, top_k=top_k)
            seeds = sorted(set(exact) | {p for p, r in semantic.items() if semantic_gate(r)})
            citation, cit_stats = graph.expand(seeds)
            kg, kg_stats = route_kg(seeds, kg_available, resolver, runner=kg_runner)
            rows = assemble(claim, exact, semantic, citation, kg, index_by_pid, fulltext=fulltext)

            frame = pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)
            frame.to_parquet(parts_dir / f"{claim.atomic_id}.parquet", index=False)
            if hits:
                hframe = pd.DataFrame(hits)
                if hits_path.exists():
                    hframe = pd.concat([pd.read_parquet(hits_path), hframe], ignore_index=True)
                hframe.to_parquet(hits_path, index=False)

            log.write(json.dumps({
                "atomic_claim_id": claim.atomic_id, "thesis_id": claim.thesis_id,
                "pass": claim.pass_id, "priority": claim.priority,
                "queries": [{"text": q, "kind": k} for q, k in claim.queries],
                "queries_sha256": queries_sha256, "n_chroma": n_chroma,
                "retriever": {"semantic": sem_stats, "exact": exact_log, "citation": cit_stats,
                              "kg": kg_stats},
                "candidate_ids": [r["paper_id"] for r in rows],
                "prefilter_pass": [r["paper_id"] for r in rows if r["prefilter_pass"]],
                "prefilter_rejected": [{"paper_id": r["paper_id"], "reason": r["prefilter_reason"]}
                                       for r in rows if not r["prefilter_pass"]],
                "unmapped_hit_ids": sorted(set(resolver.unmapped))[:20],
                "elapsed_s": round(time.time() - t0, 1),
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }, ensure_ascii=False) + "\n")
            log.flush()
            resolver.unmapped.clear()
            logger.info("  [%d/%d] %s: %d candidates, %d pass (%.0fs)", i, len(todo), claim.atomic_id,
                        len(rows), sum(r["prefilter_pass"] for r in rows), time.time() - t0)
    return consolidate(work_dir)


def consolidate(work_dir: Path = WORK_DIR) -> Path:
    parts = sorted((work_dir / CANDIDATE_PARTS).glob("*.parquet"))
    out = work_dir / CANDIDATES_FILE
    if not parts:
        return out
    frame = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    frame.to_parquet(out, index=False)
    logger.info("candidates.parquet: %d rows, %d prefilter_pass, %d claims",
                len(frame), int(frame["prefilter_pass"].sum()), frame["atomic_claim_id"].nunique())
    return out


# ── selection for screening ───────────────────────────────────────────────────

#: Pairs to screen per atomic claim, by thesis priority. Exact-route papers are
#: always included; counter-query papers get a reserved share so a claim is not
#: screened only against papers that look like it.
SCREEN_QUOTA = {"high": 12, "medium": 8, "low": 5}
COUNTER_RESERVE = 2


def primary_density(thesis, text: str) -> float:
    """Occurrences of the primary family per 10k characters of full text."""
    if not text:
        return 0.0
    low = text.lower()
    n = sum(low.count(t.lower()) for t in thesis.primary_family)
    return 1e4 * n / max(1, len(low))


def select_for_screening(candidates: pd.DataFrame, claims: list[AtomicClaim],
                         quota: dict = SCREEN_QUOTA, loader=None) -> pd.DataFrame:
    """Rank prefilter-passing candidates by topical density and closeness; mark the
    per-claim quota as ``screen_selected``. Everything else stays recorded."""
    from src.paper_3._utils import load_paper_json, paper_sections
    loader = loader or (lambda pid: " ".join(paper_sections(load_paper_json(pid) or {}).values()))
    by_id = {c.atomic_id: c for c in claims}
    frame = candidates.copy()
    frame["primary_density"] = 0.0
    frame["screen_score"] = 0.0
    frame["screen_selected"] = False
    frame["screen_rank"] = 0
    text_cache: dict[str, str] = {}
    for cid, grp in frame[frame["prefilter_pass"].fillna(False)].groupby("atomic_claim_id"):
        claim = by_id.get(cid)
        if claim is None:
            continue
        thesis = claim.to_thesis()
        dens, scores = {}, {}
        for r in grp.itertuples():
            if r.paper_id not in text_cache:
                text_cache[r.paper_id] = loader(r.paper_id)
            d = primary_density(thesis, text_cache[r.paper_id])
            dens[r.Index] = d
        dmax = max(dens.values()) if dens else 1.0
        for r in grp.itertuples():
            dist = r.semantic_best_distance if r.semantic_best_distance is not None and not pd.isna(r.semantic_best_distance) else 0.13
            closeness = min(max((0.13 - float(dist)) / 0.09, 0.0), 1.0)
            structural = 0.3 if (r.citation_relation or r.kg_relation) else 0.0
            scores[r.Index] = (dens[r.Index] / dmax if dmax else 0.0) + closeness + structural + 0.1 * min(int(r.n_keyterm_families_hit), 4)
        order = sorted(scores, key=lambda i: -scores[i])
        n = quota.get(claim.priority, 5)
        chosen = [i for i in order if frame.at[i, "routes"] and "exact" in str(frame.at[i, "routes"])]
        counters = [i for i in order if bool(frame.at[i, "from_counter_query"]) and i not in chosen][:COUNTER_RESERVE]
        rest = [i for i in order if i not in chosen and i not in counters]
        chosen = chosen + counters + rest[: max(0, n - len(chosen) - len(counters))]
        for rank, i in enumerate(order, 1):
            frame.at[i, "primary_density"] = round(dens[i], 3)
            frame.at[i, "screen_score"] = round(scores[i], 3)
            frame.at[i, "screen_rank"] = rank
        for i in chosen:
            frame.at[i, "screen_selected"] = True
    return frame


def run_select(work_dir: Path = WORK_DIR, claims: list[AtomicClaim] | None = None) -> Path:
    path = work_dir / CANDIDATES_FILE
    frame = select_for_screening(pd.read_parquet(path), claims or [])
    frame.to_parquet(path, index=False)
    sel = frame[frame["screen_selected"]]
    logger.info("screen selection: %d pairs (%s) over %d claims, %d unique papers",
                len(sel), sel.groupby("priority").size().to_dict(), sel["atomic_claim_id"].nunique(), sel["paper_id"].nunique())
    return path


# ── expert-named pairs ────────────────────────────────────────────────────────

SUPPLEMENTARY_FILE = "supplementary_pairs.csv"


def apply_supplementary(work_dir: Path = WORK_DIR, out_dir: Path | None = None,
                        index_by_pid: dict | None = None) -> int:
    """Pairs a person named after reading theses.csv (placeholder sources that exist
    in the corpus, Kakhovka papers for comparator claims). Recorded with route
    ``expert_named`` so they never count as retrieval recall; they are screened like
    any other pair and the quote gate applies."""
    from tools.paper3_audit.config import OUT_DIR
    path = (out_dir or OUT_DIR) / SUPPLEMENTARY_FILE
    if not path.exists():
        return 0
    sup = pd.read_csv(path, dtype=str).fillna("")
    cpath = work_dir / CANDIDATES_FILE
    frame = pd.read_parquet(cpath)
    if index_by_pid is None:
        index_by_pid = {r["paper_id"]: r for r in corpus_mod.load_runtime_index(work_dir).to_dict("records")}
    for col, default in (("screen_selected", False), ("screen_rank", 0), ("primary_density", 0.0), ("screen_score", 0.0)):
        if col not in frame:
            frame[col] = default
    added = marked = 0
    for r in sup.itertuples():
        pid = r.paper_id
        if pid not in index_by_pid:
            logger.warning("supplementary pair %s/%s: paper not in corpus index", r.atomic_claim_id, pid)
            continue
        m = (frame["atomic_claim_id"] == r.atomic_claim_id) & (frame["paper_id"] == pid)
        if m.any():
            frame.loc[m, "screen_selected"] = True
            frame.loc[m, "routes"] = frame.loc[m, "routes"].apply(lambda v: "+".join(sorted(set(str(v).split("+")) | {"expert_named"})))
            marked += 1
        else:
            meta = index_by_pid[pid]
            row = {c: None for c in frame.columns}
            row.update({"thesis_id": r.atomic_claim_id, "atomic_claim_id": r.atomic_claim_id, "paper_id": pid,
                        "doi": meta.get("doi_clean", ""), "title": meta.get("title", ""), "year": _year(meta.get("year")),
                        "stage_found": "expert_named", "retrieval_origin": "exact", "citation_depth": 0,
                        "is_positive_control": False, "n_semantic_chunks": 0, "n_keyterm_families_hit": 0,
                        "n_seed_links": 0, "prefilter_pass": True, "prefilter_reason": f"expert-named: {r.reason}",
                        "prefilter_score": 50.0, "contradicts_hint": False, "analogue_hint": False, "method_hint": False,
                        "priority": r.priority if "priority" in sup.columns and r.priority else "",
                        "category": "", "routes": "expert_named", "exact_ref_key": r.ref_key if "ref_key" in sup.columns else "",
                        "exact_ref_relation": "", "exact_ref_status": "", "best_chunk_id": "", "best_chunk_page": None,
                        "best_chunk_section": "", "best_chunk_distance": None, "from_counter_query": False,
                        "duplicate_of": meta.get("duplicate_of", ""), "contrast_cue": False, "method_heavy": False,
                        "screen_selected": True, "screen_rank": 0, "primary_density": 0.0, "screen_score": 0.0})
            frame = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
            added += 1
    frame.to_parquet(cpath, index=False)
    logger.info("supplementary pairs: %d added, %d already present and marked", added, marked)
    return added + marked
