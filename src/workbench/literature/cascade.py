"""The five-stage retrieval cascade, and the structural prefilter.

Order is fixed and deliberate:

    semantic  →  knowledge graph  →  citation  →  full text  →  structured facts

Stages 1–3 *nominate* papers, stage 4 *grounds* them in text that actually contains
the thesis vocabulary, and stage 5 only *decorates* with numbers. Structured facts
come last because `numeric_facts` covers only the tables GROBID recognised, so
leading with them would silently restrict the analysis to well-parsed PDFs.

Stage 4 carries the load-bearing filter. SPECTER2 is a title-and-abstract document
embedder and this corpus is flood-inundation-shaped, so a query for "water surface
slope" returns plausible-looking flood-mapping papers with high confidence. A paper
that hits no key-term family in its own full text is dropped here regardless of how
good its embedding score was.
"""
# Moved from src/paper_3/retrieve.py (P6, 2026-10-02); run paths come from config.py (cfg).
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from src.workbench.literature.corpus_utils import doi_to_slug, load_paper_json, paper_sections
from src.workbench.literature import config as cfg
from src.workbench.literature.theses import Thesis, load_theses

logger = logging.getLogger(__name__)

#: Chunks pulled per query. Wide on purpose: the gate is key terms, not rank.
TOP_K_CHUNKS = 300

#: Cosine distance below which a single chunk is evidence enough to look further.
#: Calibrate with `calibrate_distance()` before trusting a verdict.
DISTANCE_GATE = 0.45

#: …or this many chunks from the same paper, which says the match is not a fluke.
MIN_CHUNKS = 3

MAX_CANDIDATES_PER_THESIS = 40

#: Citation expansion is fixed at one hop. Not a parameter — see
#: `corpus_index.expand_by_citation` for why depth 2 is a manual investigation.
CITATION_DEPTH = 1

#: How a candidate entered the evidence base. Recorded on every row so the
#: finished matrix can say not just *what* was found but *how*, and so a
#: reviewer can see whether a thesis rests on semantic similarity alone.
#: There is deliberately no "seed" origin: a positive control that entered because
#: we named it would prove nothing about retrieval. Every candidate here was found.
RETRIEVAL_ORIGINS = (
    "semantic",        # SPECTER2 chunk similarity
    "references",      # cited by a semantically retrieved paper — ancestry
    "cited_by",        # cites a semantically retrieved paper — descent
    "graph_neighbor",  # shares sensors/methods/study area in the knowledge graph
)

CANDIDATE_COLUMNS = [
    "thesis_id", "paper_id", "doi", "title", "year",
    "stage_found", "retrieval_origin", "citation_depth", "is_positive_control",
    "semantic_best_distance", "n_semantic_chunks", "n_keyterm_families_hit",
    "kg_relation", "citation_relation", "n_seed_links",
    "prefilter_pass", "prefilter_reason", "prefilter_score",
    "contradicts_hint", "analogue_hint", "method_hint",
]


@dataclass
class ThesisCandidate:
    thesis_id: str
    paper_id: str
    doi: str = ""
    title: str = ""
    year: int | None = None
    stage_found: str = "semantic"
    retrieval_origin: str = "semantic"
    citation_depth: int = 0
    #: True when this paper is one of the thesis's hold-out positive controls.
    #: Set after retrieval, never used to help retrieval.
    is_positive_control: bool = False
    semantic_best_distance: float | None = None
    n_semantic_chunks: int = 0
    n_keyterm_families_hit: int = 0
    kg_relation: str | None = None
    citation_relation: str | None = None
    n_seed_links: int = 0
    prefilter_pass: bool = False
    prefilter_reason: str = ""
    prefilter_score: float = 0.0
    contradicts_hint: bool = False
    analogue_hint: bool = False
    method_hint: bool = False


# ── stage 1: semantic ─────────────────────────────────────────────────────────

def stage_semantic(
    thesis: Thesis,
    store,
    embedder,
    top_k: int = TOP_K_CHUNKS,
) -> dict[str, dict]:
    """Nearest chunks for each of the thesis's natural-language queries.

    No `where` clause is passed. `research_query_service._get_paper_ids` caps its
    filter at 500 ids selected by a LIMIT with no ORDER BY, so using it here would
    restrict the search to an arbitrary subset — and would exclude exactly the
    newly harvested papers this analysis exists to find. Filtering happens
    afterwards, in pandas, where it is cheap and inspectable.
    """
    by_paper: dict[str, dict] = {}
    seen_chunks: set[str] = set()

    for query in thesis.search_queries:
        try:
            vector = embedder.embed_query(query)
            hits = store.query(vector, top_k=top_k, where=None)
        except Exception as exc:
            logger.warning("%s: semantic query failed (%s): %s", thesis.id, query, exc)
            continue

        for hit in hits:
            chunk_id = str(hit.get("chunk_id") or "")
            if chunk_id and chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)

            pid = hit.get("paper_id") or hit.get("filename") or ""
            if not pid:
                continue
            distance = float(hit.get("distance", 1.0))
            row = by_paper.setdefault(
                str(pid), {"paper_id": str(pid), "n_chunks": 0, "best_distance": distance})
            row["n_chunks"] += 1
            row["best_distance"] = min(row["best_distance"], distance)

    logger.info("%s: semantic stage → %d papers from %d chunks",
                thesis.id, len(by_paper), len(seen_chunks))
    return by_paper


def semantic_gate(row: dict) -> bool:
    return (row["best_distance"] <= DISTANCE_GATE) or (row["n_chunks"] >= MIN_CHUNKS)


# ── stage 2: knowledge graph ──────────────────────────────────────────────────

_KG_CYPHER = """
MATCH (p:Paper)-[r]->(e)<-[r2]-(q:Paper)
WHERE p.paper_id IN $seeds AND NOT q.paper_id IN $seeds
  AND type(r) IN ['USES_SENSOR','USES_METHOD','HAS_STUDY_AREA','HAS_NUMERIC_FACT']
RETURN q.paper_id AS paper_id,
       type(r) AS via,
       count(DISTINCT e) AS n_shared
ORDER BY n_shared DESC
LIMIT 60
"""


def stage_kg(seed_paper_ids: list[str], min_shared: int = 2,
             cap: int = 20) -> tuple[list[dict], bool]:
    """One-hop expansion over shared entities. Returns (rows, available).

    `available` is False when Neo4j cannot be reached. That fact is propagated all
    the way to the matrix header: a thesis whose only reachable evidence was
    graph-adjacent would otherwise read as UNKNOWN when it is merely unobserved.
    """
    if not seed_paper_ids:
        return [], True
    try:
        from src.dashboard_dash.data_neo4j import _run
        rows = _run(_KG_CYPHER, seeds=list(seed_paper_ids))
    except Exception as exc:
        logger.warning("Neo4j unavailable — KG expansion skipped (%s)", exc)
        return [], False

    kept = [r for r in rows if int(r.get("n_shared", 0)) >= min_shared][:cap]
    return kept, True


# ── stage 3: citation ─────────────────────────────────────────────────────────

def stage_citation(seed_paper_ids: list[str], index, edges, cap: int = 20) -> list[dict]:
    """Papers one citation hop from the seeds, restricted to the corpus."""
    from src.paper_3.corpus_index import expand_by_citation

    out = expand_by_citation(seed_paper_ids, index=index, edges=edges)
    if out.empty:
        return []
    return out.nlargest(cap, "n_seed_links").to_dict("records")


# ── stage 4: full text ────────────────────────────────────────────────────────

_CONTRAST_CUES = (
    "however", "in contrast", "by contrast", "does not", "do not", "did not",
    "we find no", "no significant", "unlike", "contrary to", "fails to",
    "cannot", "but we", "whereas", "disagree", "inconsistent with",
)
_METHOD_SECTIONS = ("method", "methods", "methodology", "data", "materials",
                    "study area", "approach")


def stage_fulltext(thesis: Thesis, paper_id: str) -> tuple[int, dict, bool, bool]:
    """Read the paper's own text. Returns (families_hit, sections, contrast, method).

    `families_hit` is reported as 0 unless the paper is on topic by
    `Thesis.is_on_topic` — the distinguishing concept plus one further family.
    Reporting the raw count would let a paper qualify on two generic families
    ("satellite", "difference"), which most of this corpus satisfies.

    `sections` is returned so the passages step does not have to load the file a
    second time.
    """
    paper = load_paper_json(paper_id)
    if not paper:
        return 0, {}, False, False
    sections = paper_sections(paper)
    if not sections:
        return 0, {}, False, False

    text = " ".join(sections.values())
    families = thesis.families_hit(text) if thesis.is_on_topic(text) else 0
    if families == 0:
        return 0, sections, False, False

    low = text.lower()
    contrast = False
    for fam in thesis.key_terms:
        for term in fam:
            pos = low.find(term.lower())
            while pos != -1 and not contrast:
                window = low[max(0, pos - 220): pos + 220]
                if any(cue in window for cue in _CONTRAST_CUES):
                    contrast = True
                    break
                pos = low.find(term.lower(), pos + 1)
            if contrast:
                break
        if contrast:
            break

    method_hits = sum(
        thesis.families_hit(body)
        for name, body in sections.items()
        if any(marker in name.lower() for marker in _METHOD_SECTIONS)
    )
    method_heavy = method_hits >= max(1, families // 2) and method_hits > 0

    return families, sections, contrast, method_heavy


# ── stage 5: structured facts ─────────────────────────────────────────────────

def stage_facts(paper_ids: list[str], thesis: Thesis) -> pd.DataFrame:
    """Numeric facts for these papers whose own context mentions a thesis term.

    Decoration only: nothing here admits or rejects a candidate. A paper with no
    machine-readable table is not a paper without numbers — it is a paper whose
    numbers GROBID could not reach, and the extraction table says exactly that.
    """
    if not paper_ids:
        return pd.DataFrame()
    from src.workbench.literature.corpus_utils import get_con

    try:
        con = get_con()
        placeholders = ", ".join("?" for _ in paper_ids)
        facts = con.execute(
            f"SELECT * FROM numeric_facts WHERE paper_id IN ({placeholders})",
            paper_ids,
        ).fetchdf()
    except Exception as exc:
        logger.warning("numeric_facts unavailable: %s", exc)
        return pd.DataFrame()

    if facts.empty:
        return facts

    context_cols = [c for c in ("row_context", "col_header", "table_caption", "context")
                    if c in facts.columns]
    if not context_cols:
        return facts.iloc[0:0]

    context = facts[context_cols].fillna("").agg(" ".join, axis=1)
    keep = context.map(lambda s: thesis.families_hit(s) >= 1)
    return facts[keep]


# ── prefilter ─────────────────────────────────────────────────────────────────

def prefilter(candidate: ThesisCandidate) -> tuple[bool, str, float]:
    """Decide whether a candidate is worth an adjudication call.

    Two independent conditions, both required:

      * the paper's own full text hits at least two key-term families — topical
        presence, not topical neighbourhood;
      * the paper was found convincingly — a close chunk, several chunks, or a
        structural link through the graph or citation network.

    Rejected candidates are still recorded, with the reason, so every number in
    the matrix can be traced back to a decision rather than a disappearance.
    """
    if candidate.n_keyterm_families_hit < 2:
        return False, (f"only {candidate.n_keyterm_families_hit} key-term "
                       f"family/families in full text"), 0.0

    close = (candidate.semantic_best_distance is not None
             and candidate.semantic_best_distance <= DISTANCE_GATE)
    many = candidate.n_semantic_chunks >= MIN_CHUNKS
    structural = candidate.stage_found in ("kg", "citation")

    if not (close or many or structural):
        return False, (f"weak semantic match (best distance "
                       f"{candidate.semantic_best_distance}, "
                       f"{candidate.n_semantic_chunks} chunks) and no structural link"), 0.0

    score = (
        2.0 * candidate.n_keyterm_families_hit
        + (1.0 if close else 0.0)
        + min(candidate.n_semantic_chunks, 10) * 0.2
        + (1.0 if structural else 0.0)
    )
    reasons = []
    if close:
        reasons.append(f"distance {candidate.semantic_best_distance:.3f}")
    if many:
        reasons.append(f"{candidate.n_semantic_chunks} chunks")
    if structural:
        reasons.append(f"found via {candidate.stage_found}")
    return True, "; ".join(reasons), round(score, 3)


# ── seeds and the positive control ────────────────────────────────────────────

def resolve_seeds(theses: list[Thesis],
                  index) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    """Map each thesis's seed DOIs to corpus paper_ids.

    Returns (found, missing). A missing seed is not fatal — it usually means the
    harvest has not run yet — but it is logged, because a thesis whose seed is
    absent has no positive control and its UNKNOWN cannot be trusted.
    """
    by_doi = {d: p for d, p in zip(index["doi"], index["paper_id"]) if d}
    found: dict[str, set[str]] = {}
    missing: dict[str, list[str]] = {}
    for t in theses:
        hits, gone = set(), []
        for doi in t.seed_dois:
            pid = by_doi.get(doi)
            (hits.add(pid) if pid else gone.append(doi))
        found[t.id] = hits
        if gone:
            missing[t.id] = gone
    return found, missing


#: Which retrieval origins satisfy each declared expectation.
_STAGE_ORIGINS = {
    "semantic_retrieval": ("semantic",),
    "citation_expansion": ("references", "cited_by"),
    "kg_expansion": ("graph_neighbor",),
}


def _stage_matches(expected_stage: str, found_via: str) -> bool:
    return found_via in _STAGE_ORIGINS.get(expected_stage, ())


def positive_control(theses: list[Thesis], index, candidates) -> "pd.DataFrame":
    """Did every hold-out control survive retrieval, and if not, which stage lost it?

    This is the recall half of calibration, and it only means anything because
    controls are held out: they are ingested into the corpus and then retrieval
    has to find them unaided.

    `failure_stage` names the one thing to fix:

      * `not_in_corpus`      — the harvest has not brought it in yet
      * `primary_family`     — the discriminating key term misses the paper's own
                               text; the family is worded too narrowly
      * `second_family`      — primary hits but nothing else does
      * `semantic_retrieval` — on topic, but no query brought it back
      * `ranking_cutoff`     — retrieved, but ranked outside the candidate cap
      * `prefilter`          — a candidate, but the prefilter rejected it
      * `stage_mismatch`     — recovered, but only through a stage the control was
                               not expected to need (e.g. citation rather than
                               semantic), which is a weaker result than it looks
    """
    resolved, _missing = resolve_seeds(theses, index)
    doi_by_pid = dict(zip(index["paper_id"], index["doi"])) if len(index) else {}
    # A control is "in corpus" when its text is there, not when its DOI is.
    # Older reports carry no DOI a parser can find - the 1989 USGS roughness
    # guide is the case that exposed this - so a DOI-only match reported a
    # fully ingested paper as absent and sent the reader off to fetch it again.
    # The slug is derived from the DOI by a deterministic rule, so it is a
    # sound second key.
    pid_by_slug = {}
    if len(index):
        for pid_, slug_ in zip(index["paper_id"], index.get("slug", index["paper_id"])):
            if slug_:
                pid_by_slug.setdefault(str(slug_), str(pid_))
        for pid_ in index["paper_id"]:          # paper_id is itself the slug on disk
            pid_by_slug.setdefault(str(pid_), str(pid_))

    rows = []
    for t in theses:
        for control in t.positive_controls:
            pid = next((p for p in resolved[t.id]
                        if doi_by_pid.get(p) == control.doi), None)
            if pid is None:
                want = doi_to_slug(control.doi)
                pid = next((p for p in resolved[t.id] if str(p) == want), None) \
                    or (pid_by_slug.get(want) if want in pid_by_slug else None)
            base = {
                "thesis_id": t.id, "is_critical": t.is_critical,
                "seed_doi": control.doi, "role": control.role,
                "relevance": control.relevance, "is_direct": control.is_direct,
                "difficulty": control.difficulty,
                "expected_stage": control.expected_stage,
                "source_of_seed": control.source_of_seed,
                "manually_checked": control.manually_checked,
                "holdout_consumed": control.holdout_consumed,
            }
            if pid is None:
                rows.append({**base, "paper_id": "", "in_corpus": False,
                             "primary_hit": False, "on_topic": False,
                             "retrieved": False, "prefilter_pass": False,
                             "families_hit": 0, "found_via": "",
                             "recovered": False, "recovered_at_expected_stage": False,
                             "recovered_semantically": False,
                             "failure_stage": "not_in_corpus",
                             "status": "not in corpus"})
                continue

            paper = load_paper_json(pid)
            text = " ".join(paper_sections(paper).values()) if paper else ""
            primary = t.primary_hit(text)
            families = t.families_hit(text)
            on_topic = t.is_on_topic(text)

            row = candidates[(candidates["thesis_id"] == t.id)
                             & (candidates["paper_id"] == pid)]
            retrieved = not row.empty
            passed = bool(row["prefilter_pass"].any()) if retrieved else False
            found_via = (str(row["retrieval_origin"].iloc[0])
                         if retrieved and "retrieval_origin" in row else "")

            if not primary:
                stage, status = "primary_family", "FAIL: primary family too narrow — control misses it"
            elif not on_topic:
                stage, status = "second_family", "FAIL: primary hits but no second family"
            elif not retrieved:
                stage, status = "semantic_retrieval", "FAIL: on topic but retrieval missed it"
            elif not passed:
                stage, status = "prefilter", "FAIL: retrieved but prefiltered out"
            elif (control.expected_stage == "semantic_retrieval"
                  and found_via not in ("semantic", "")):
                stage, status = "stage_mismatch", (
                    f"WEAK: recovered only via {found_via}, not semantic retrieval")
            else:
                stage, status = "", "ok"

            # Three different questions, three different answers:
            #   recovered                  — did it end up in the candidate set at all
            #   recovered_at_expected_stage— did the stage we predicted deliver it
            #   recovered_semantically     — did embedding search alone find it
            # A paper that only arrived through citation expansion is a weaker
            # result than "recall passed" suggests, and is reported as such.
            in_candidates = retrieved and passed
            rows.append({**base, "paper_id": pid, "in_corpus": True,
                         "primary_hit": primary, "on_topic": on_topic,
                         "retrieved": retrieved, "prefilter_pass": passed,
                         "families_hit": families, "found_via": found_via,
                         "recovered": in_candidates,
                         "recovered_at_expected_stage": bool(
                             in_candidates and _stage_matches(
                                 control.expected_stage, found_via)),
                         "recovered_semantically": bool(
                             in_candidates and found_via == "semantic"),
                         "failure_stage": stage,
                         "status": status})

    import pandas as pd
    return pd.DataFrame(rows, columns=[
        "thesis_id", "is_critical", "seed_doi", "paper_id", "role", "relevance",
        "is_direct", "difficulty", "expected_stage", "source_of_seed",
        "manually_checked", "holdout_consumed",
        "in_corpus", "primary_hit", "on_topic", "retrieved", "prefilter_pass",
        "families_hit", "found_via", "recovered",
        "recovered_at_expected_stage", "recovered_semantically",
        "failure_stage", "status"])


# ── orchestration ─────────────────────────────────────────────────────────────

def run(
    theses: list[Thesis] | None = None,
    out_dir: Path | None = None,
    top_k: int = TOP_K_CHUNKS,
    max_candidates: int = MAX_CANDIDATES_PER_THESIS,
    store=None,
    embedder=None,
    hold_out_controls: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """Run the cascade for every thesis. Returns (candidates, diagnostics).

    `hold_out_controls` is True and should stay True. Setting it False would let
    positive controls enter the candidate set without being retrieved, which
    turns the recall benchmark into a tautology.
    """
    from src.config import COLLECTION_NAME, EMBEDDING_MODEL
    from src.paper_3.corpus_index import load_citation_edges, load_index

    theses = theses if theses is not None else load_theses()
    target = Path(out_dir) if out_dir else cfg.WORK_DIR
    target.mkdir(parents=True, exist_ok=True)

    if store is None:
        from src.vectorstore.chroma_store import VectorStore
        store = VectorStore(collection_name=COLLECTION_NAME)
    if embedder is None:
        from src.embedding.embedder import Embedder
        embedder = Embedder(model_name=EMBEDDING_MODEL)

    index = load_index(target)
    edges = load_citation_edges()
    meta = {r["paper_id"]: r for r in index.to_dict("records")}
    seed_paper_ids, missing_seeds = resolve_seeds(theses, index)
    for thesis_id, dois in missing_seeds.items():
        logger.warning("%s: seed DOI(s) not in corpus — %s. Retrieval for this "
                       "thesis cannot be positive-controlled.",
                       thesis_id, ", ".join(dois))

    kg_available = True
    semantic_stats: dict[str, dict] = {}
    keyterm_counts: dict[str, int] = {}
    candidates: list[ThesisCandidate] = []

    for thesis in theses:
        semantic = stage_semantic(thesis, store, embedder, top_k=top_k)
        semantic_stats[thesis.id] = {
            "n_chunks": sum(r["n_chunks"] for r in semantic.values()),
            "best_distance": min((r["best_distance"] for r in semantic.values()),
                                 default=None),
        }

        seeds = [pid for pid, row in semantic.items() if semantic_gate(row)]
        seeds = sorted(seeds, key=lambda p: semantic[p]["best_distance"])[:max_candidates]

        control_pids = seed_paper_ids.get(thesis.id, set())

        found: dict[str, ThesisCandidate] = {}
        for pid in seeds:
            row = semantic[pid]
            found[pid] = ThesisCandidate(
                thesis_id=thesis.id, paper_id=pid, stage_found="semantic",
                retrieval_origin="semantic", citation_depth=0,
                is_positive_control=pid in control_pids,
                semantic_best_distance=round(row["best_distance"], 4),
                n_semantic_chunks=row["n_chunks"],
            )

        # Positive controls are NOT injected here. They are in the corpus like any
        # other paper and retrieval has to find them unaided — a recall figure
        # measured against a search that was handed the answer measures nothing.
        if hold_out_controls:
            missed = control_pids - set(found)
            if missed:
                logger.info("%s: %d positive control(s) not recovered by semantic "
                            "retrieval — recorded as a recall failure, not injected",
                            thesis.id, len(missed))

        kg_rows, available = stage_kg(seeds)
        kg_available = kg_available and available
        for row in kg_rows:
            pid = str(row["paper_id"])
            if pid in found:
                found[pid].kg_relation = row.get("via")
                continue
            found[pid] = ThesisCandidate(
                thesis_id=thesis.id, paper_id=pid, stage_found="kg",
                retrieval_origin="graph_neighbor", citation_depth=0,
                is_positive_control=pid in control_pids,
                kg_relation=row.get("via"))

        for row in stage_citation(seeds, index, edges):
            pid = str(row["paper_id"])
            relation = row.get("relation") or ""
            if pid in found:
                found[pid].citation_relation = relation
                found[pid].n_seed_links = int(row.get("n_seed_links", 0))
                continue
            # "references+cited_by" means both directions reached it; record the
            # first named direction as the origin rather than inventing a label.
            origin = relation.split("+")[0] if relation else "references"
            found[pid] = ThesisCandidate(
                thesis_id=thesis.id, paper_id=pid, stage_found="citation",
                retrieval_origin=origin if origin in RETRIEVAL_ORIGINS else "references",
                citation_depth=CITATION_DEPTH,
                is_positive_control=pid in control_pids,
                citation_relation=relation,
                n_seed_links=int(row.get("n_seed_links", 0)))

        n_keyterm = 0
        for pid, cand in found.items():
            families, _sections, contrast, method_heavy = stage_fulltext(thesis, pid)
            cand.n_keyterm_families_hit = families
            cand.contradicts_hint = contrast
            cand.method_hint = method_heavy
            info = meta.get(pid, {})
            cand.doi = info.get("doi", "")
            cand.title = info.get("title", "")
            cand.year = info.get("year")
            if families >= 2:
                n_keyterm += 1
            cand.prefilter_pass, cand.prefilter_reason, cand.prefilter_score = \
                prefilter(cand)
        keyterm_counts[thesis.id] = n_keyterm

        kept = sorted(found.values(),
                      key=lambda c: (-c.prefilter_score, c.paper_id))[:max_candidates]
        candidates.extend(kept)
        logger.info("%s: %d candidates, %d pass prefilter",
                    thesis.id, len(kept), sum(1 for c in kept if c.prefilter_pass))

    frame = pd.DataFrame([asdict(c) for c in candidates], columns=CANDIDATE_COLUMNS)
    frame.to_parquet(target / "thesis_candidates.parquet", index=False)

    control = positive_control(theses, index, frame)
    control.to_csv(target / "POSITIVE_CONTROL.csv", index=False)
    failures = control[control["status"] != "ok"]
    if not failures.empty:
        logger.warning("Positive control: %d of %d seeds did not survive retrieval",
                       len(failures), len(control))
        for r in failures.itertuples():
            logger.warning("  %s %s — %s", r.thesis_id, r.seed_doi, r.status)

    origins = (frame["retrieval_origin"].value_counts().to_dict()
               if not frame.empty else {})
    diagnostics = {
        "kg_expansion_available": kg_available,
        "citation_depth": CITATION_DEPTH,
        "semantic_stats": semantic_stats,
        "keyterm_counts": keyterm_counts,
        "retrieval_origins": origins,
        "n_candidates": len(frame),
        "n_prefilter_pass": int(frame["prefilter_pass"].sum()) if not frame.empty else 0,
        "positive_control_failures": int(len(failures)),
        "positive_control_total": int(len(control)),
    }
    return frame, diagnostics


def calibrate_distance(store, embedder, known_paper_ids: list[str],
                       query: str, top_k: int = TOP_K_CHUNKS) -> dict:
    """Report where known-good papers rank for a query, to set DISTANCE_GATE.

    Run this against a SWOT paper, an ICESat-2 paper and a Kakhovka paper before
    trusting any verdict: a gate tuned on nothing is a gate that decides the
    results by accident.
    """
    vector = embedder.embed_query(query)
    hits = store.query(vector, top_k=top_k, where=None)
    ranked: dict[str, dict] = {}
    for rank, hit in enumerate(hits):
        pid = str(hit.get("paper_id") or hit.get("filename") or "")
        if pid and pid not in ranked:
            ranked[pid] = {"rank": rank, "distance": float(hit.get("distance", 1.0))}
    return {pid: ranked.get(pid, {"rank": None, "distance": None})
            for pid in known_paper_ids}
