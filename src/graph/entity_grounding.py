"""Ground Paper→Method/Sensor/Metric edges on the paper's own text (GROBID TEI).

The legacy extractor matched aliases inside words ("iRIC" in "empirical") and its
evidence windows often miss the mention: on 2026-10-02 only 30 % of USES_METHOD
snippets contained the surface form. This step looks for the surface form, or the
entity's display name, as a word in the TEI sentences (abstract, body, captions); see
`term` for the matching rule:

    grounded      True   at least one sentence mentions it
                  False  the full text was searched and nothing mentions it
                  None   no TEI full text (not checked)
    tei_mentions  number of sentences that mention it
    tei_evidence  up to three of those sentences, verbatim (≤ 400 characters each)
    tei_page / tei_section   of the first mention

The result is a file (data/graph_inputs/entity_grounding.parquet + .meta.json) that
the graph loader joins onto the edges, so Neo4j stays a projection of files and the
layer of truth:

    python -m src.graph.entity_grounding --workers 3
    python -m src.graph.build_graph --only entity-edges
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "graph_inputs" / "entity_grounding.parquet"
MAX_EVIDENCE = 3
MAX_CHARS = 400
RELS = {"methods": "USES_METHOD", "sensors": "USES_SENSOR", "metrics": "REPORTS_METRIC"}

log = logging.getLogger("geohydro.graph.grounding")


@dataclass(frozen=True)
class Term:
    pattern: re.Pattern
    short_acronym: bool          # e.g. HAND, GOES, RF: an all-lowercase match ("hand") does not count

    def found_in(self, text: str) -> bool:
        return any(not (self.short_acronym and m.group(0).islower()) for m in self.pattern.finditer(text))


def term(raw: str) -> Term | None:
    """A surface form or display name as a word: no letter or digit either side, case-insensitive,
    '_', '-' and spaces interchangeable ("HYDROLOGICAL_MODEL" = "hydrological model",
    "Two Dimensional …" = "two-dimensional …"). Normalised forms such as SENTINEL or LIDAR
    therefore match "Sentinel-1" and "LiDAR"."""
    words = [w for w in re.split(r"[\s_\-]+", (raw or "").strip()) if w]
    if not words or len("".join(words)) < 2:
        return None
    body = r"[\s\-_]+".join(re.escape(w) for w in words)
    compact = "".join(words)
    short = compact.isupper() and len(compact) <= 4
    return Term(re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE), short)


def sentences(doc) -> list[tuple[str, int | None, str]]:
    """(text, page, section) of every sentence of the abstract, the body and the captions."""
    from src.services import fulltext
    out = []
    for p in fulltext.passages(doc):
        for s in p.sentences:
            out.append((s.text, s.page or p.page, p.section))
    return out


def ground_paper(tei_path: str, paper_id: str, edges: list[dict]) -> list[dict]:
    """edges: [{rel, canonical_id, terms}] → grounding rows for this paper."""
    from src.document.parser import TEIParser
    try:
        doc = TEIParser().parse_file(Path(tei_path), paper_id)
        sents = sentences(doc)
    except Exception as exc:                                  # unreadable TEI: not checked
        return [dict(e, paper_id=paper_id, grounded=None, tei_mentions=0, tei_evidence=[], tei_page=None,
                     tei_section=None, note=f"TEI not parsed: {type(exc).__name__}") for e in edges]
    rows = []
    for e in edges:
        terms = [t for t in (term(x) for x in e["terms"]) if t]
        hits = [(text, page, section) for text, page, section in sents if any(t.found_in(text) for t in terms)]
        rows.append(dict(e, paper_id=paper_id, grounded=bool(hits), tei_mentions=len(hits),
                         tei_evidence=[h[0][:MAX_CHARS] for h in hits[:MAX_EVIDENCE]],
                         tei_page=hits[0][1] if hits else None, tei_section=hits[0][2] if hits else None, note=None))
    return rows


def _job(args):
    return ground_paper(*args)


def collect_edges(limit: int | None = None) -> dict[str, list[dict]]:
    """Entity edges of the graph loader, grouped by paper, with the terms to look for."""
    from src.graph import graph_loader as loader
    pm, ps, pmet, _ai = loader.load_entity_edges_from_enriched(limit=limit, grounding=False)
    names = loader.entity_display_names()
    by_paper: dict[str, list[dict]] = defaultdict(list)
    for rel, rows in (("USES_METHOD", pm), ("USES_SENSOR", ps), ("REPORTS_METRIC", pmet)):
        for r in rows:
            terms = sorted({t for t in (r.get("surface_form"), names.get(r["canonical_id"])) if t})
            by_paper[r["paper_id"]].append({"rel": rel, "canonical_id": r["canonical_id"], "terms": terms})
    return by_paper


def tei_paths(paper_ids: list[str]) -> dict[str, str]:
    from sqlalchemy import select

    from src.db.engine import session_scope
    from src.db.models import PaperFile
    with session_scope() as s:
        rows = s.execute(select(PaperFile.paper_id, PaperFile.path)
                         .where(PaperFile.kind == "tei", PaperFile.status == "ok", PaperFile.paper_id.in_(paper_ids)))
        return {pid: str(ROOT / path) for pid, path in rows}


def run(workers: int = 3, limit: int | None = None, out: Path = OUT) -> dict:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.etl.identity import git_state
    t0 = time.time()
    by_paper = collect_edges(limit)
    paths = tei_paths(sorted(by_paper))
    rows: list[dict] = []
    jobs = []
    for pid, edges in sorted(by_paper.items()):
        if pid in paths:
            jobs.append((paths[pid], pid, edges))
        else:
            rows += [dict(e, paper_id=pid, grounded=None, tei_mentions=0, tei_evidence=[], tei_page=None,
                          tei_section=None, note="no TEI full text") for e in edges]
    log.info("grounding %d edges of %d papers (%d without TEI) with %d workers",
             sum(len(e) for e in by_paper.values()), len(by_paper), len(by_paper) - len(jobs), workers)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i, part in enumerate(pool.map(_job, jobs, chunksize=8), start=1):
            rows += part
            if i % 500 == 0:
                log.info("  %d / %d papers", i, len(jobs))
    for r in rows:
        r["terms"] = list(r["terms"])
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.parquet")
    pq.write_table(pa.Table.from_pylist(rows), tmp)
    tmp.replace(out)
    summary: dict = {"edges": len(rows), "papers": len(by_paper), "papers_without_tei": len(by_paper) - len(jobs)}
    for rel in RELS.values():
        sub = [r for r in rows if r["rel"] == rel]
        summary[rel] = {"edges": len(sub), "grounded": sum(r["grounded"] is True for r in sub),
                        "not_grounded": sum(r["grounded"] is False for r in sub),
                        "not_checked": sum(r["grounded"] is None for r in sub)}
    commit, dirty = git_state()
    meta = {"generated_at": datetime.now(timezone.utc).isoformat(), "git_commit": commit, "git_dirty": dirty,
            "seconds": round(time.time() - t0, 1), "limit": limit, "rule": __doc__.split("\n\n")[1], **summary}
    out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None, help="first N enriched files (dev)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    meta = run(args.workers, args.limit, args.out)
    print(json.dumps({k: v for k, v in meta.items() if k != "rule"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
