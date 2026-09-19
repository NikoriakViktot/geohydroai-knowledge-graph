"""Paper 2 literature: corpus scan, precedent references, bounded recursive expansion.

Three questions, answered in one place and kept apart from the Paper 1 harvest
(everything lands under ``data/paper_3_audit/p2/``):

1. **What does the corpus already hold** for the Paper 2 theses (T25–T34)?
   Every normalised paper is tested with ``Thesis.is_on_topic`` on its full text
   — the distinguishing family plus one more — so a flood-mapping paper that
   merely mentions "vegetation" does not count.
2. **What do the precedents cite?** The GROBID bibliography of Didukh 2024 and
   Kuzemko 2025 is parsed; entries without a DOI are resolved through CrossRef
   only when the returned title matches the parsed one (rapidfuzz ≥ 88).
3. **Bounded recursion.** From the seeds (in-corpus on-topic papers + precedents)
   OpenAlex supplies the reference list (``filter=cited_by:W``) and the citing
   works (``filter=cites:W``); each work is screened on title + abstract with the
   same on-topic rule (abstract fallback: three families); on-topic, open-access
   works with a PDF become the download set, and on-topic works seed the next
   hop, up to ``hops`` and ``max_seeds_per_hop``. Off-topic references — most of
   Didukh's willow-physiology list — are recorded and never fetched.

The output frame has the ``harvest_candidates.parquet`` schema so the existing
``harvest_ingest`` steps (download → grobid → pipeline → normalize → chroma) run
unchanged with ``--out data/paper_3_audit/p2``.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import pandas as pd
import requests

from src.paper_3._utils import OUT_DIR
from src.paper_3._utils import doi_to_slug, load_paper_json, normalize_doi, paper_sections
from src.paper_3.harvest_openalex import (_DELAY, _OPENALEX_SEARCH, _known_dois, _known_titles,
                                          _polite_params, fetch_by_doi, make_session, to_record)
from src.paper_3.theses import Thesis, load_theses

logger = logging.getLogger(__name__)

P2_DIR = OUT_DIR / "p2"
PAPER2_THESES = tuple(f"T{i}" for i in range(25, 35))
PRECEDENTS = {
    "didukh2024": "10.32999/ksu1990-553x/2024-20-3-5",
    "kuzemko2025": "10.15407/ukrbotj82.05.488",
}
ABSTRACT_FALLBACK_FAMILIES = 3
CROSSREF_MIN_SCORE = 88
_CROSSREF = "https://api.crossref.org/works"
_UA = "GeoHydroAI/paper_3 (mailto:nikoriakviktor@gmail.com)"
CANDIDATE_COLUMNS = ["doi", "slug", "openalex_id", "title", "abstract", "year", "journal",
                     "cited_by_count", "type", "is_oa", "pdf_url", "referenced_works_count",
                     "matched_thesis_ids", "matched_labels", "n_query_hits", "is_seed",
                     "drop_reason", "score", "selected", "selection_reason"]


def paper2_theses() -> list[Thesis]:
    return [t for t in load_theses() if t.id in PAPER2_THESES]


# ── 1. corpus scan ───────────────────────────────────────────────────────────

def matched_theses(text: str, theses: list[Thesis], abstract_mode: bool = False) -> list[str]:
    """Thesis ids whose on-topic rule the text satisfies (pure)."""
    hits = []
    for t in theses:
        if t.has_negative(text):
            continue
        if t.is_on_topic(text) or (abstract_mode and t.families_hit(text) >= ABSTRACT_FALLBACK_FAMILIES):
            hits.append(t.id)
    return hits


def scan_corpus(theses: list[Thesis], normalized_dir: Path) -> pd.DataFrame:
    """Full-text on-topic test over every normalised paper."""
    rows = []
    files = sorted(normalized_dir.glob("*.json"))
    for i, f in enumerate(files, 1):
        paper = load_paper_json(f.stem)
        if not paper:
            continue
        sections = paper_sections(paper)
        text = " ".join(sections.values()) if isinstance(sections, dict) else str(sections)
        if len(text) < 500:
            continue
        hit = matched_theses(text, theses)
        if hit:
            rows.append({"paper_id": f.stem, "doi": normalize_doi(str(paper.get("doi") or "")),
                         "title": str(paper.get("title") or "")[:200],
                         "year": paper.get("year") or "", "matched_thesis_ids": ";".join(hit),
                         "n_families_max": max(t.families_hit(text) for t in theses if t.id in hit)})
        if i % 1000 == 0:
            logger.info("corpus scan %d/%d — %d on-topic so far", i, len(files), len(rows))
    return pd.DataFrame(rows, columns=["paper_id", "doi", "title", "year", "matched_thesis_ids", "n_families_max"])


# ── 2. precedent references ─────────────────────────────────────────────────

def parse_tei_references(xml_path: Path) -> list[dict]:
    """(surname, year, title, doi) per biblStruct — lxml is allowed only here via tei_io."""
    from src.document.tei_io import parse_file  # facade: the only sanctioned lxml entry point
    root = parse_file(xml_path)
    ns = {"t": "http://www.tei-c.org/ns/1.0"}
    out = []
    for b in root.findall(".//t:listBibl/t:biblStruct", ns):
        def txt(e):
            return " ".join("".join(e.itertext()).split()) if e is not None else ""
        doi = txt(b.find(".//t:idno[@type='DOI']", ns))
        title = txt(b.find(".//t:analytic/t:title", ns)) or txt(b.find(".//t:monogr/t:title", ns))
        date = b.find(".//t:date", ns)
        out.append({"surname": txt(b.find(".//t:author/t:persName/t:surname", ns)),
                    "year": (date.get("when") or "")[:4] if date is not None else "",
                    "title": title, "doi": normalize_doi(doi) if doi else ""})
    return out


def crossref_lookup(title: str, surname: str, year: str, session: requests.Session) -> tuple[str, int]:
    """Best CrossRef match for a DOI-less reference: (doi, score) — '' when below threshold."""
    if len(title) < 15:
        return "", 0
    try:
        from rapidfuzz import fuzz
        r = session.get(_CROSSREF, params={"query.bibliographic": f"{surname} {year} {title}", "rows": 3},
                        headers={"User-Agent": _UA}, timeout=20)
        for item in (r.json().get("message", {}).get("items") or []):
            cand = (item.get("title") or [""])[0]
            score = int(fuzz.token_set_ratio(title.lower(), cand.lower()))
            if score >= CROSSREF_MIN_SCORE:
                return normalize_doi(item.get("DOI", "")), score
    except Exception as exc:
        logger.debug("crossref %r → %s", title[:40], exc)
    return "", 0


def precedent_references(session: requests.Session, xml_dir: Path) -> pd.DataFrame:
    rows = []
    known = _known_dois()
    for key, doi in PRECEDENTS.items():
        xml = xml_dir / f"{doi_to_slug(doi)}.tei.xml"
        if not xml.exists():                       # corpus papers carry their own paper_id
            papers = pd.read_parquet(OUT_DIR.parents[0] / "analytics" / "papers.parquet", columns=["paper_id", "doi"])
            hit = papers[papers.doi.fillna("").str.lower() == doi]
            if len(hit):
                xml = xml_dir / f"{hit.iloc[0].paper_id}.tei.xml"
        if not xml.exists():
            logger.warning("%s: no TEI for %s", key, doi)
            continue
        for ref in parse_tei_references(xml):
            resolved, score = ("", 0)
            if not ref["doi"]:
                resolved, score = crossref_lookup(ref["title"], ref["surname"], ref["year"], session)
                time.sleep(0.3)
            d = ref["doi"] or resolved
            rows.append({"cited_by": key, **ref, "doi_resolved": d, "crossref_score": score,
                         "in_corpus": bool(d and d in known)})
    return pd.DataFrame(rows)


# ── 3. bounded recursive expansion via OpenAlex ─────────────────────────────

def _works_filter(session: requests.Session, flt: str, per_page: int = 100, max_pages: int = 2) -> list[dict]:
    params = _polite_params()
    params.update({"filter": flt, "per-page": per_page})
    out = []
    for page in range(1, max_pages + 1):
        params["page"] = page
        try:
            r = session.get(_OPENALEX_SEARCH, params=params, timeout=30)
        except requests.RequestException as exc:
            logger.warning("OpenAlex %s → %s", flt, exc)
            break
        if r.status_code != 200:
            logger.warning("OpenAlex %s → HTTP %d", flt, r.status_code)
            break
        res = r.json().get("results") or []
        out.extend(res)
        if len(res) < per_page:
            break
        time.sleep(_DELAY)
    time.sleep(_DELAY)
    return out


def neighbours(work_id: str, session: requests.Session) -> tuple[list[dict], list[dict]]:
    """(references, citing works) of one OpenAlex work id."""
    wid = work_id.rsplit("/", 1)[-1]
    refs = _works_filter(session, f"cited_by:{wid}", per_page=100, max_pages=2)
    cites = _works_filter(session, f"cites:{wid}", per_page=50, max_pages=2)
    return refs, cites


def screen_record(rec: dict, theses: list[Thesis]) -> list[str]:
    return matched_theses(f"{rec.get('title', '')} {rec.get('abstract', '')}", theses, abstract_mode=True)


def expand(seed_dois: list[str], theses: list[Thesis], session: requests.Session,
           hops: int = 2, max_seeds_per_hop: int = 40) -> pd.DataFrame:
    known_dois, known_titles = _known_dois(), _known_titles()
    seen: dict[str, dict] = {}
    frontier = list(dict.fromkeys(normalize_doi(d) for d in seed_dois))
    for hop in range(1, hops + 1):
        next_frontier: list[str] = []
        logger.info("hop %d: %d seeds", hop, len(frontier))
        for doi in frontier[:max_seeds_per_hop]:
            work = fetch_by_doi(doi, session)
            time.sleep(_DELAY)
            if not work or not work.get("id"):
                continue
            refs, cites = neighbours(work["id"], session)
            for direction, works in (("reference", refs), ("citing", cites)):
                for w in works:
                    rec = to_record(w)
                    if not rec or rec["doi"] in seen:
                        if rec:
                            seen[rec["doi"]]["n_query_hits"] += 1
                        continue
                    hit = screen_record(rec, theses)
                    rec.update({
                        "matched_thesis_ids": hit, "matched_labels": [f"p2_hop{hop}_{direction}"],
                        "n_query_hits": 1, "is_seed": False,
                        "drop_reason": ("off_topic" if not hit else "doi_in_corpus" if rec["doi"] in known_dois
                                        else "title_in_corpus" if rec["title"].strip().lower() in known_titles
                                        else "not_oa" if not (rec["is_oa"] and rec["pdf_url"]) else ""),
                        "score": float(len(hit) * 10 + min(rec["cited_by_count"], 50) / 10),
                        "selected": False, "selection_reason": "", "via_doi": doi, "hop": hop,
                    })
                    rec["selected"] = rec["drop_reason"] == ""
                    if rec["selected"]:
                        rec["selection_reason"] = f"p2_expansion_hop{hop}_{direction}"
                    seen[rec["doi"]] = rec
                    if hit and rec["doi"] not in frontier:
                        next_frontier.append(rec["doi"])
        frontier = next_frontier
    frame = pd.DataFrame(list(seen.values()))
    for c in CANDIDATE_COLUMNS:
        if c not in frame:
            frame[c] = None
    return frame


# ── report and entry point ───────────────────────────────────────────────────

def render_report(in_corpus: pd.DataFrame, refs: pd.DataFrame, cands: pd.DataFrame) -> str:
    lines = ["# Paper 2 literature — corpus scan, precedent references, bounded expansion", ""]
    lines += [f"## 1. Already in the corpus (full-text on-topic for T25–T34): {len(in_corpus)} papers", ""]
    if len(in_corpus):
        per = in_corpus.matched_thesis_ids.str.split(";").explode().value_counts()
        lines += [f"- {t}: {n}" for t, n in per.items()] + [""]
        for r in in_corpus.sort_values("n_families_max", ascending=False).head(40).itertuples():
            lines.append(f"- [{r.matched_thesis_ids}] {r.title} ({r.year}) doi:{r.doi} — `{r.paper_id}`")
    lines += ["", f"## 2. Precedent references: {len(refs)} entries", ""]
    if len(refs):
        for key, g in refs.groupby("cited_by"):
            lines.append(f"- **{key}**: {len(g)} refs, {int(g.doi.astype(bool).sum())} with DOI, "
                         f"{int((g.doi_resolved.astype(bool) & ~g.doi.astype(bool)).sum())} resolved via CrossRef, "
                         f"{int(g.in_corpus.sum())} already in corpus")
    lines += ["", f"## 3. Expansion: {len(cands)} works seen", ""]
    if len(cands):
        lines += [f"- {k}: {v}" for k, v in cands.drop_reason.replace("", "SELECTED (on-topic, OA, PDF)").value_counts().items()]
        sel = cands[cands.selected.fillna(False)]
        lines += ["", f"### Selected for download ({len(sel)})", ""]
        for r in sel.sort_values("score", ascending=False).itertuples():
            lines.append(f"- [{';'.join(r.matched_thesis_ids)}] {r.title} ({r.year}, {r.journal}) doi:{r.doi} · hop {r.hop} via {r.via_doi}")
        on_topic_paywalled = cands[cands.drop_reason.eq("not_oa")]
        lines += ["", f"### On-topic but not open access ({len(on_topic_paywalled)}) — add by hand if needed", ""]
        for r in on_topic_paywalled.sort_values("cited_by_count", ascending=False).head(40).itertuples():
            lines.append(f"- [{';'.join(r.matched_thesis_ids)}] {r.title} ({r.year}) doi:{r.doi}")
    return "\n".join(lines) + "\n"


def run(out_dir: Path = P2_DIR, hops: int = 2, max_seeds_per_hop: int = 40,
        skip_corpus_scan: bool = False) -> Path:
    from src.paper_3.harvest_ingest import NORMALIZED_DIR, XML_DIR as _XML
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "harvest").mkdir(exist_ok=True)
    theses = paper2_theses()
    session = make_session()

    scan_path = out_dir / "IN_CORPUS.csv"
    if skip_corpus_scan and scan_path.exists():
        in_corpus = pd.read_csv(scan_path, dtype=str, keep_default_na=False)
    else:
        in_corpus = scan_corpus(theses, NORMALIZED_DIR)
        in_corpus.to_csv(scan_path, index=False)
    logger.info("corpus scan: %d on-topic papers", len(in_corpus))

    refs = precedent_references(session, _XML)
    refs.to_csv(out_dir / "PRECEDENT_REFERENCES.csv", index=False)

    seeds = [d for d in in_corpus.doi.tolist() if str(d).startswith("10.")] + list(PRECEDENTS.values())
    on_topic_refs = refs[refs.doi_resolved.astype(bool)] if len(refs) else refs
    seeds += [d for d in on_topic_refs.doi_resolved.tolist() if d] if len(refs) else []
    cands = expand(seeds, theses, session, hops=hops, max_seeds_per_hop=max_seeds_per_hop)
    cands.to_parquet(out_dir / "harvest" / "harvest_candidates.parquet", index=False)
    (out_dir / "P2_LITERATURE_REPORT.md").write_text(render_report(in_corpus, refs, cands), encoding="utf-8")
    json.dump({"hops": hops, "max_seeds_per_hop": max_seeds_per_hop, "n_seeds": len(set(seeds)),
               "n_in_corpus": len(in_corpus), "n_candidates": len(cands),
               "n_selected": int(cands.selected.fillna(False).sum()) if len(cands) else 0},
              open(out_dir / "p2_provenance.json", "w"), indent=1)
    logger.info("p2 literature: %d in corpus, %d candidates, %d selected", len(in_corpus), len(cands),
                int(cands.selected.fillna(False).sum()) if len(cands) else 0)
    return out_dir


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hops", type=int, default=2)
    ap.add_argument("--max-seeds-per-hop", type=int, default=40)
    ap.add_argument("--skip-corpus-scan", action="store_true")
    a = ap.parse_args(argv)
    run(hops=a.hops, max_seeds_per_hop=a.max_seeds_per_hop, skip_corpus_scan=a.skip_corpus_scan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
