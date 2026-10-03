"""
graph_cleanup_20261003.py — one-off repair of the Neo4j graph after the 2026-10-03 audit.

GraphWriter (src/graph) is MERGE-only by design, so the errors it wrote cannot be
undone by re-running build_graph. This script is the single, explicit place that
deletes, and every deletion is exported first to data/graph/cleanup_20261003/*.jsonl.

Steps (each idempotent; --only <step> runs one):
  nan          float NaN titles/journals → property removed; years outside 1800–2026 removed
  title_key    Paper.title_key (graph_loader.title_key) set on every node
  stubs        reference stubs sharing a title_key merged into one node (a corpus paper
               when it carries that title); incoming CITES repointed, losers deleted
  selfcites    (p)-[:CITES]->(p) deleted
  investigates all Paper→FloodEvent INVESTIGATES edges deleted (country/year heuristic)
  methods      USES_METHOD edges for (paper, method) pairs removed by
               src.orchestration.repair_method_entities deleted; derived
               CO_OCCURS_WITH / COMMONLY_USED_WITH edges deleted (build_graph rebuilds)
  metrics      Metric metric.r_squared folded into metric.r2; display names filled
  facts        every NumericFact deleted — re-created by `python -m src.graph.table_kg_loader`
               with the fixed extractor and the Paper-node filter

Usage:
    python scripts/graph_cleanup_20261003.py --dry-run
    python scripts/graph_cleanup_20261003.py --apply [--only stubs] [--repair-jsonl path]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neo4j import GraphDatabase  # noqa: E402

from src.graph.graph_loader import title_key  # noqa: E402

log = logging.getLogger("graph_cleanup")
OUT = ROOT / "data" / "graph" / "cleanup_20261003"
STEPS = ["nan", "title_key", "stubs", "selfcites", "investigates", "methods", "metrics", "facts"]
BATCH = 2000


class Cleanup:
    def __init__(self, driver, apply: bool):
        self.driver = driver
        self.apply = apply
        OUT.mkdir(parents=True, exist_ok=True)

    # ── helpers ──────────────────────────────────────────────────────────────
    def rows(self, cypher: str, **params) -> list[dict]:
        with self.driver.session() as s:
            return [r.data() for r in s.run(cypher, **params)]

    def write(self, cypher: str, **params) -> dict:
        if not self.apply:
            return {}
        with self.driver.session() as s:
            return s.run(cypher, **params).consume().counters.__dict__

    def write_batches(self, cypher: str, rows: list[dict], label: str) -> None:
        if not self.apply:
            log.info("  [dry-run] %s: %d rows", label, len(rows))
            return
        done = 0
        with self.driver.session() as s:
            for i in range(0, len(rows), BATCH):
                chunk = rows[i:i + BATCH]
                s.execute_write(lambda tx, c=chunk: tx.run(cypher, rows=c).consume())
                done += len(chunk)
        log.info("  %s: %d rows", label, done)

    def export(self, name: str, records: list[dict]) -> None:
        path = OUT / f"{name}.jsonl"
        with open(path, "a") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        log.info("  exported %d records → %s", len(records), path.relative_to(ROOT))

    # ── steps ────────────────────────────────────────────────────────────────
    def step_nan(self) -> None:
        for prop in ("title", "journal"):
            n = self.rows(f"MATCH (p:Paper) WHERE p.{prop} IS NOT NULL AND toString(p.{prop}) = 'NaN' "
                          f"RETURN count(*) AS n")[0]["n"]
            log.info("  Paper.%s = NaN: %d", prop, n)
            self.write(f"MATCH (p:Paper) WHERE p.{prop} IS NOT NULL AND toString(p.{prop}) = 'NaN' REMOVE p.{prop}")
        bad = self.rows("MATCH (p:Paper) WHERE p.year IS NOT NULL AND (p.year < 1800 OR p.year > 2026) "
                        "RETURN elementId(p) AS eid, p.paper_id AS paper_id, p.title AS title, p.year AS year")
        log.info("  Paper.year outside 1800–2026: %d", len(bad))
        self.export("year_removed", bad)
        self.write("MATCH (p:Paper) WHERE p.year IS NOT NULL AND (p.year < 1800 OR p.year > 2026) REMOVE p.year")

    def _papers(self) -> list[dict]:
        return self.rows("MATCH (p:Paper) RETURN elementId(p) AS eid, p.paper_id AS paper_id, p.doi AS doi, "
                         "p.title AS title, p.title_key AS title_key, p.is_reference_stub AS stub, p.year AS year")

    def step_title_key(self) -> None:
        papers = self._papers()
        updates = []
        for p in papers:
            key = title_key(p["title"])
            if key != p["title_key"]:
                updates.append({"eid": p["eid"], "key": key})
        log.info("  Paper nodes: %d, title_key to set: %d", len(papers), len(updates))
        self.write_batches("UNWIND $rows AS r MATCH (p) WHERE elementId(p) = r.eid SET p.title_key = r.key",
                           updates, "title_key")

    def step_stubs(self) -> None:
        papers = self._papers()
        groups: dict[str, list[dict]] = defaultdict(list)
        for p in papers:
            key = p["title_key"] if self.apply else title_key(p["title"])
            if key:
                groups[key].append(p)
        pairs, losers, skipped = [], [], 0
        for key, members in groups.items():
            if len(members) < 2:
                continue
            full = [m for m in members if m["paper_id"]]
            stubs = [m for m in members if not m["paper_id"]]
            if len(full) > 1:
                with_doi = [m for m in full if m["doi"]]
                if len(with_doi) != 1:
                    skipped += 1
                    continue              # several corpus papers share the title: not ours to merge
                survivor = with_doi[0]
            elif full:
                survivor = full[0]
            else:
                dois = {m["doi"] for m in stubs if m["doi"]}
                if len(dois) > 1:
                    skipped += 1
                    continue              # same title, different DOIs: different works
                survivor = next((m for m in stubs if m["doi"]), stubs[0])
            group_doi = next((m["doi"] for m in members if m["doi"]), None)
            for m in stubs:
                if m["eid"] == survivor["eid"]:
                    continue
                pairs.append({"loser": m["eid"], "survivor": survivor["eid"],
                              "doi": group_doi if not survivor["doi"] else None,
                              "year": m["year"]})
                losers.append({**m, "survivor": survivor["eid"], "survivor_paper_id": survivor["paper_id"]})
        log.info("  duplicate title groups: %d, stubs to merge: %d, groups left alone: %d",
                 sum(1 for g in groups.values() if len(g) > 1), len(pairs), skipped)
        self.export("stubs_merged", losers)
        self.write_batches("""
            UNWIND $rows AS r
            MATCH (l) WHERE elementId(l) = r.loser
            MATCH (s) WHERE elementId(s) = r.survivor
            OPTIONAL MATCH (src)-[:CITES]->(l)
            WITH l, s, r, collect(DISTINCT src) AS srcs
            FOREACH (x IN srcs | MERGE (x)-[:CITES]->(s))
            SET s.doi  = coalesce(s.doi, r.doi),
                s.year = coalesce(s.year, r.year)
            DETACH DELETE l
            """, pairs, "stubs merged")

    def step_selfcites(self) -> None:
        rows = self.rows("MATCH (p:Paper)-[:CITES]->(p) RETURN p.paper_id AS paper_id, p.doi AS doi")
        log.info("  self-citations: %d", len(rows))
        self.export("self_cites", rows)
        self.write("MATCH (p:Paper)-[c:CITES]->(p) DELETE c")

    def step_investigates(self) -> None:
        rows = self.rows("MATCH (p:Paper)-[:INVESTIGATES]->(e:FloodEvent) "
                         "RETURN p.paper_id AS paper_id, e.name AS event")
        log.info("  INVESTIGATES edges: %d", len(rows))
        self.export("investigates", rows)
        self.write("MATCH ()-[r:INVESTIGATES]->() DELETE r")

    def step_methods(self, repair_jsonl: Path | None) -> None:
        if not repair_jsonl or not repair_jsonl.exists():
            log.warning("  no repair JSONL (%s): USES_METHOD step skipped", repair_jsonl)
            return
        # canonical ids: from the repair file, else from the graph's own surface forms
        surface_to_cid: dict[str, str] = {}
        for r in self.rows("MATCH ()-[e:USES_METHOD]->(m:Method) WHERE e.surface_form IS NOT NULL "
                           "RETURN DISTINCT toLower(e.surface_form) AS s, m.canonical_id AS cid"):
            surface_to_cid.setdefault(r["s"], r["cid"])
        pairs, unresolved = [], set()
        for line in open(repair_jsonl):
            rep = json.loads(line)
            if rep.get("status") != "repaired":
                continue
            ids = {rep["stem"], rep.get("paper_id")} - {None}
            for rm in rep["removed"]:
                cid = rm.get("canonical_id") or surface_to_cid.get(str(rm["name"]).lower())
                if not cid:
                    unresolved.add(rm["name"])
                    continue
                for pid in ids:
                    pairs.append({"paper_id": pid, "cid": cid, "surface": rm["name"]})
        existing = self.rows("""
            UNWIND $rows AS r
            MATCH (p:Paper {paper_id: r.paper_id})-[e:USES_METHOD]->(m:Method {canonical_id: r.cid})
            RETURN r.paper_id AS paper_id, r.cid AS canonical_id, r.surface AS surface_form,
                   e.evidence AS evidence""", rows=pairs)
        log.info("  removed (paper, method) pairs: %d; USES_METHOD edges found: %d; unresolved names: %s",
                 len(pairs), len(existing), sorted(unresolved)[:20])
        self.export("uses_method_removed", existing)
        self.write_batches("""
            UNWIND $rows AS r
            MATCH (p:Paper {paper_id: r.paper_id})-[e:USES_METHOD]->(m:Method {canonical_id: r.cid})
            DELETE e""", pairs, "USES_METHOD deleted")
        for rel in ("CO_OCCURS_WITH", "COMMONLY_USED_WITH"):
            n = self.rows(f"MATCH ()-[r:{rel}]->() RETURN count(*) AS n")[0]["n"]
            log.info("  derived %s edges to drop (build_graph recreates them): %d", rel, n)
            self.write(f"MATCH ()-[r:{rel}]->() DELETE r")
        self.write("MATCH (m:Method) SET m.paper_count = size([(m)<-[:USES_METHOD]-() | 1])")

    def step_metrics(self) -> None:
        from src.extraction.table_extractor import _canonical_metric
        old = self.rows("MATCH (p)-[r:REPORTS_METRIC]->(m:Metric {canonical_id:'metric.r_squared'}) "
                        "RETURN p.paper_id AS paper_id")
        log.info("  REPORTS_METRIC → metric.r_squared: %d (folded into metric.r2)", len(old))
        self.write("""
            MATCH (old:Metric {canonical_id:'metric.r_squared'})
            MERGE (new:Metric {canonical_id:'metric.r2'})
            WITH old, new
            OPTIONAL MATCH (p)-[r:REPORTS_METRIC]->(old)
            FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
                MERGE (p)-[r2:REPORTS_METRIC]->(new) SET r2 += properties(r))
            WITH DISTINCT old
            DETACH DELETE old""")
        names = [{"cid": r["cid"], "name": _canonical_metric(r["cid"])}
                 for r in self.rows("MATCH (m:Metric) WHERE m.display_name IS NULL RETURN m.canonical_id AS cid")]
        self.write_batches("UNWIND $rows AS r MATCH (m:Metric {canonical_id: r.cid}) SET m.display_name = r.name",
                           names, "Metric display names")

    def step_facts(self) -> None:
        n = self.rows("MATCH (n:NumericFact) RETURN count(*) AS n")[0]["n"]
        log.info("  NumericFact nodes to delete (re-created by table_kg_loader): %d", n)
        if not self.apply:
            return
        while True:
            c = self.rows("MATCH (n:NumericFact) WITH n LIMIT 10000 DETACH DELETE n RETURN count(*) AS c")[0]["c"]
            if not c:
                break
        log.info("  NumericFact: 0 left")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--only", choices=STEPS, action="append")
    ap.add_argument("--repair-jsonl", type=Path,
                    default=ROOT / "data" / "repair" / f"method_entities_{date.today():%Y%m%d}.jsonl")
    ap.add_argument("--uri", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    ap.add_argument("--user", default=os.getenv("NEO4J_USER", "neo4j"))
    ap.add_argument("--password", default=os.getenv("NEO4J_PASSWORD"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    if not args.password:
        ap.error("NEO4J_PASSWORD is not set")

    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    c = Cleanup(driver, apply=args.apply)
    for step in (args.only or STEPS):
        log.info("== %s %s", "APPLY" if args.apply else "DRY-RUN", step)
        if step == "methods":
            c.step_methods(args.repair_jsonl)
        else:
            getattr(c, f"step_{step}")()
    driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
