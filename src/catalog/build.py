"""Build a catalog snapshot: one card per corpus paper, filtered by src/catalog/rules.yaml.

Run on the workstation (the "factory"); the snapshot directory is all the server needs:

    python -m src.catalog.build                       # → data/exports/catalog_<date>_<rules>/
    python -m src.catalog.build --limit 50 --out /tmp/x

Reads only: Postgres (core.paper, verify.current, biblio.http_cache), the OpenAlex SQLite cache,
data/normalized, data/analytics and data/graph_inputs. No HTTP, no Neo4j, no writes to any store.

Snapshot files (a directory is never overwritten):
    cards.jsonl     published cards, sorted by paper_id, deterministic (no timestamps)
    hidden.jsonl    every value the filter hid, with its reason
    excluded.jsonl  papers without a card, with the reason
    manifest.json   rules version, git commit, counts, sha256 of the three files above
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import subprocess
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from src.catalog.quality import HUMAN, PaperInput, build_card, load_rules

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
NORMALIZED_DIR = DATA / "normalized"
FACTS_FILE = DATA / "analytics" / "numeric_facts.parquet"
TOPIC_EDGES_FILE = DATA / "analytics" / "paper_topic_edges.parquet"
TOPICS_FILE = DATA / "analytics" / "topics.parquet"
GROUNDING_FILE = DATA / "graph_inputs" / "entity_grounding.parquet"
OPENALEX_CACHE = DATA / "cache" / "openalex_doi.db"
EXPORTS_DIR = DATA / "exports"


# ── loaders (each reads one source) ───────────────────────────────────────────

def load_identities() -> list[dict]:
    from sqlalchemy import text
    from src.db.engine import get_engine
    with get_engine().connect() as c:
        rows = c.execute(text("SELECT paper_id, doi, title, year, venue, openalex_id, identity_status, duplicate_of "
                              "FROM core.paper ORDER BY paper_id")).mappings().all()
    return [dict(r) for r in rows]


def load_checks() -> dict[str, list[dict]]:
    """paper_id → the person's current judgements (labeler_kind is always 'human' in verify)."""
    from sqlalchemy import text
    from src.db.engine import get_engine
    out: dict[str, list[dict]] = defaultdict(list)
    with get_engine().connect() as c:
        for r in c.execute(text("SELECT target_kind, target_id, field, paper_id, verdict, corrected, created_at "
                                "FROM verify.current WHERE labeler_kind = 'human'")).mappings():
            if r["paper_id"]:
                out[r["paper_id"]].append(dict(r))
    return out


def load_open_access() -> dict[str, dict]:
    """lower(doi) → oa_url, oa_status, is_retracted from cached OpenAlex / Unpaywall responses."""
    from sqlalchemy import text
    from src.db.engine import get_engine
    out: dict[str, dict] = {}
    sql = text("""
        SELECT lower(key) AS doi, service,
               coalesce(response->'open_access'->>'oa_url', response->'best_oa_location'->>'url') AS oa_url,
               coalesce(response->'open_access'->>'oa_status', response->>'oa_status') AS oa_status,
               (response->>'is_retracted')::boolean AS is_retracted
        FROM biblio.http_cache WHERE service IN ('openalex', 'unpaywall') AND status = 200
        ORDER BY service""")  # openalex first; unpaywall only fills gaps
    with get_engine().connect() as c:
        for r in c.execute(sql).mappings():
            cur = out.setdefault(r["doi"], {})
            for k in ("oa_url", "oa_status", "is_retracted"):
                if cur.get(k) is None and r[k] is not None:
                    cur[k] = r[k]
    return out


def load_openalex_works(path: Path = OPENALEX_CACHE) -> dict[str, dict]:
    """lower(doi) → authors, cited_by_count, title, year from the pipeline's OpenAlex cache (read-only)."""
    if not path.exists():
        return {}
    out = {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for doi, resp in con.execute("SELECT doi, response FROM doi_cache"):
            try:
                w = json.loads(resp)
            except ValueError:
                continue
            if not isinstance(w, dict):
                continue
            out[str(doi).lower()] = {"authors": [a.get("name") for a in w.get("authors") or [] if a.get("name")],
                                     "cited_by_count": w.get("cited_by_count"), "title": w.get("title"),
                                     "year": w.get("publication_year")}
    finally:
        con.close()
    return out


def load_grounding(path: Path = GROUNDING_FILE) -> dict[str, dict]:
    import pyarrow.parquet as pq
    out: dict[str, dict] = defaultdict(dict)
    if not path.exists():
        return out
    cols = ["rel", "canonical_id", "terms", "paper_id", "grounded", "tei_mentions", "tei_evidence"]
    for r in pq.read_table(path, columns=cols).to_pylist():
        out[r["paper_id"]][(r["rel"], r["canonical_id"])] = r
    return out


def load_facts(path: Path = FACTS_FILE) -> dict[str, list[dict]]:
    import pyarrow.parquet as pq
    out: dict[str, list[dict]] = defaultdict(list)
    if not path.exists():
        return out
    cols = ["fact_id", "paper_id", "table_id", "table_label", "page", "row_context", "canonical_id", "value",
            "unit", "confidence"]
    for r in pq.read_table(path, columns=cols).to_pylist():
        out[r["paper_id"]].append(r)
    return out


def load_topics(edges: Path = TOPIC_EDGES_FILE, names: Path = TOPICS_FILE) -> dict[str, list[tuple[str, float]]]:
    import pyarrow.parquet as pq
    out: dict[str, list] = defaultdict(list)
    if not edges.exists() or not names.exists():
        return out
    name = {r["topic_id"]: r["topic_name"] for r in pq.read_table(names, columns=["topic_id", "topic_name"]).to_pylist()}
    for r in pq.read_table(edges).to_pylist():
        if r["topic_id"] in name:
            out[r["paper_id"]].append((name[r["topic_id"]], r["score"]))
    return out


def load_normalized(paper_id: str, root: Path = NORMALIZED_DIR) -> dict | None:
    p = root / f"{paper_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _tei_authors(norm: dict | None) -> list[str]:
    authors = ((norm or {}).get("metadata") or {}).get("authors") or []
    return [a.get("full_name") for a in authors if isinstance(a, dict) and a.get("full_name")]


# ── assembly ──────────────────────────────────────────────────────────────────

def assemble_inputs(identities, *, works, open_access, checks, grounding, facts, topics, normalized) -> list[PaperInput]:
    """normalized: callable paper_id → dict | None (lazy: 5 000 JSON files)."""
    out = []
    for ident in identities:
        pid = ident["paper_id"]
        doi = (ident.get("doi") or "").lower()
        norm = normalized(pid)
        w, oa = works.get(doi, {}), open_access.get(doi, {})
        bib = {"authors": w.get("authors") or _tei_authors(norm), "cited_by_count": w.get("cited_by_count"),
               "title": w.get("title"), "year": w.get("year"),
               "oa_url": oa.get("oa_url"), "oa_status": oa.get("oa_status"), "is_retracted": oa.get("is_retracted")}
        out.append(PaperInput(identity=ident, bib=bib, normalized=norm, grounding=grounding.get(pid, {}),
                              facts=facts.get(pid, []), topics=topics.get(pid, []), checks=checks.get(pid, [])))
    return out


def _jsonl(path: Path, rows) -> str:
    h = hashlib.sha256()
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            line = json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) + "\n"
            f.write(line)
            h.update(line.encode("utf-8"))
    return h.hexdigest()


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def write_snapshot(inputs: list[PaperInput], out_dir: Path, rules: dict | None = None) -> dict:
    """Filter every paper and write the snapshot; returns the manifest."""
    rules = rules or load_rules()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"{out_dir} exists and is not empty: snapshots are never overwritten")
    out_dir.mkdir(parents=True, exist_ok=True)

    cards, hidden, excluded = [], [], []
    for inp in sorted(inputs, key=lambda i: i.identity["paper_id"]):
        card, why, hid = build_card(inp, rules)
        hidden.extend(asdict(h) for h in hid)
        if card is None:
            excluded.append({"paper_id": inp.identity["paper_id"], "reason": why})
        else:
            cards.append(card)

    status = Counter()
    for c in cards:
        a = c["analysis"] or {}
        for key in ("methods", "sensors", "data", "results", "study_countries"):
            for item in a.get(key) or []:
                status[f"{key}:{item['status']}"] += 1
    manifest = {
        "rules_version": rules["version"],
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "counts": {
            "papers": len(inputs),
            "cards": len(cards),
            "excluded": len(excluded),
            "with_analysis": sum(1 for c in cards if c["analysis"] is not None),
            "with_open_access_link": sum(1 for c in cards if c["links"]["open_access"]),
            "human_verified_metadata": sum(1 for c in cards if c["metadata_status"] == HUMAN),
            "retracted": sum(1 for c in cards if c["retracted"]),
        },
        "excluded_reasons": dict(sorted(Counter(e["reason"] for e in excluded).items())),
        "hidden_reasons": dict(sorted(Counter(f"{h['field']}:{h['reason']}" for h in hidden).items())),
        "shown_status": dict(sorted(status.items())),
        "files": {
            "cards.jsonl": _jsonl(out_dir / "cards.jsonl", cards),
            "hidden.jsonl": _jsonl(out_dir / "hidden.jsonl", hidden),
            "excluded.jsonl": _jsonl(out_dir / "excluded.jsonl", excluded),
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, help="snapshot directory (default data/exports/catalog_<date>_<rules>)")
    ap.add_argument("--limit", type=int, help="only the first N papers (a preview)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    rules = load_rules()
    out = args.out or EXPORTS_DIR / f"catalog_{date.today():%Y%m%d}_{rules['version']}"
    identities = load_identities()
    if args.limit:
        identities = identities[: args.limit]
    log.info("papers: %d", len(identities))
    inputs = assemble_inputs(identities, works=load_openalex_works(), open_access=load_open_access(),
                             checks=load_checks(), grounding=load_grounding(), facts=load_facts(),
                             topics=load_topics(), normalized=load_normalized)
    m = write_snapshot(inputs, out, rules)
    log.info("snapshot %s\n%s", out, json.dumps({k: m[k] for k in ("counts", "excluded_reasons")}, indent=2))


if __name__ == "__main__":
    main()
