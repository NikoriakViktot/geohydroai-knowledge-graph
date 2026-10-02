"""Record what each derived store currently holds (core.projection_state).

    python -m src.etl.projections neo4j
    python -m src.etl.projections parquet data/parquet
    python -m src.etl.projections parquet data/analytics
    python -m src.etl.projections chroma .chromadb_v2 flood_papers_768d_v2

Counts are read without loading heavy indexes: Neo4j through Cypher counts, parquet
from file metadata, Chroma from its SQLite catalogue (read-only).
"""

from __future__ import annotations

import argparse
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def neo4j_counts() -> dict:
    from neo4j import GraphDatabase

    from src.config import settings
    with GraphDatabase.driver(settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.require_env("NEO4J_PASSWORD"))) as d, \
            d.session() as s:
        labels = {r["label"]: r["n"] for r in s.run(
            "CALL db.labels() YIELD label CALL (label) { MATCH (n) WHERE label IN labels(n) RETURN count(n) AS n } "
            "RETURN label, n")}
        rels = {r["t"]: r["n"] for r in s.run(
            "CALL db.relationshipTypes() YIELD relationshipType AS t "
            "CALL (t) { MATCH ()-[r]->() WHERE type(r) = t RETURN count(r) AS n } RETURN t, n")}
        corpus = s.run("MATCH (p:Paper) WHERE p.is_reference_stub IS NULL RETURN count(p) AS n").single()["n"]
    return {"item_count": sum(labels.values()), "details": {"labels": labels, "relationships": rels, "corpus_papers": corpus}}


def parquet_counts(directory: Path) -> dict:
    import pyarrow.parquet as pq
    tables = {f.stem: pq.ParquetFile(f).metadata.num_rows for f in sorted(directory.glob("*.parquet"))}
    return {"item_count": tables.get("papers"), "details": {"tables": tables}}


def chroma_counts(directory: Path, collection: str) -> dict:
    con = sqlite3.connect(f"file:{directory / 'chroma.sqlite3'}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT id, dimension FROM collections WHERE name = ?", (collection,)).fetchone()
        if row is None:
            raise SystemExit(f"collection {collection!r} not found in {directory}")
        cid, dim = row
        n = con.execute("SELECT count(*) FROM embeddings e JOIN segments s ON e.segment_id = s.id "
                        "WHERE s.collection = ? AND s.scope = 'METADATA'", (cid,)).fetchone()[0]
        queue = con.execute("SELECT count(*) FROM embeddings_queue").fetchone()[0]
    finally:
        con.close()
    return {"item_count": n, "details": {"collection_id": cid, "dimension": dim, "directory": str(directory),
                                         "embeddings_queue": queue}}


def record(store: str, name: str, item_count: int | None, details: dict, notes: str | None = None) -> uuid.UUID:
    from sqlalchemy.dialects.postgresql import insert

    from src.db.engine import session_scope
    from src.db.models import ProjectionState, Run
    from src.etl.identity import git_state

    commit, dirty = git_state()
    run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    kind = {"neo4j": "graph_build", "chroma": "reindex"}.get(store, "etl")
    with session_scope() as s:
        s.add(Run(run_id=run_id, kind=kind, name=f"projection:{store}:{name}", status="succeeded",
                  git_commit=commit, git_dirty=dirty, finished_at=now,
                  params={"store": store, "name": name}, counts={"item_count": item_count}))
        s.flush()
        values = dict(store=store, name=name, run_id=run_id, item_count=item_count, built_at=now,
                      details=details, notes=notes)
        stmt = insert(ProjectionState).values(**values)
        s.execute(stmt.on_conflict_do_update(index_elements=["store", "name"],
                                             set_={k: stmt.excluded[k] for k in values if k not in ("store", "name")}))
    return run_id


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--notes")
    sub = ap.add_subparsers(dest="store", required=True)
    sub.add_parser("neo4j", parents=[common])
    pq_ = sub.add_parser("parquet", parents=[common])
    pq_.add_argument("directory", type=Path)
    ch = sub.add_parser("chroma", parents=[common])
    ch.add_argument("directory", type=Path)
    ch.add_argument("collection")
    args = ap.parse_args(argv)
    if args.store == "neo4j":
        name, data = "neo4j", neo4j_counts()
    elif args.store == "parquet":
        d = (ROOT / args.directory) if not args.directory.is_absolute() else args.directory
        name, data = str(args.directory), parquet_counts(d)
    else:
        d = (ROOT / args.directory) if not args.directory.is_absolute() else args.directory
        name, data = args.collection, chroma_counts(d, args.collection)
    run_id = record(args.store, name, data["item_count"], data["details"], args.notes)
    print(f"{args.store}:{name} item_count={data['item_count']} run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
