"""Paper identity: scan the file store, classify, load core.paper / paper_alias / paper_file.

Usage
-----
    python -m src.etl.identity --dry-run          # scan + classify, print the report only
    python -m src.etl.identity                    # also load into Postgres (one transaction)
    python -m src.etl.identity --manifest data/backups/manifest_20261002/SHA256SUMS.data

v1 decision (API_PLAN_v1/11 §2.6): paper_id is the file stem already used across
data/, Chroma and Neo4j; DOIs, slugs, OpenAlex ids and PDF hashes become aliases.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import logging
import re
import sqlite3
import subprocess
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.contracts.identity import CohortMember, PaperAlias, PaperFile, PaperIdentity
from src.services.identity import normalize_doi

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]

#: (kind, directory relative to ROOT, filename suffix)
FILE_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("pdf", "data/literature/pdf", ".pdf"),
    ("pdf", "data/literature/pdf_missing", ".pdf"),
    ("pdf", "data/literature/pdf_oa", ".pdf"),          # open-access acquisitions (scripts/acquire_oa_missing.py)
    ("tei", "data/literature/grobid_xml", ".tei.xml"),
    ("paper_json", "data/literature/paper_json", ".tei.paper.json"),
    ("normalized", "data/normalized", ".json"),
    ("enriched", "data/enriched", ".json"),
)
#: (kind, directory whose sub-directories are named by paper_id)
DIR_SOURCES: tuple[tuple[str, str], ...] = (
    ("sodb", "data/sodb"),
    ("nougat_regions", "data/nougat_regions"),
)

#: Documents in the corpus that are not scholarly works (API_PLAN_v1 О-40).
NOT_A_PAPER: frozenset[str] = frozenset({"EXHIBIT A #3013EURIZON_Prof. Osypov"})

#: GROBID title vs the OpenAlex title of the header DOI below this ratio → mismatch.
TITLE_MATCH_MIN = 0.6
#: Title-only duplicate detection needs a long, specific title.
TITLE_DUP_MIN_LEN = 40

_PUNCT = re.compile(r"[\W_]+")  # keeps letters of any script (Cyrillic titles are common here)


def norm_title(title: str | None) -> str:
    return _PUNCT.sub(" ", (title or "").casefold()).strip()


def is_latin(title: str | None) -> bool:
    """True when most letters are Latin — only then can the title be compared with
    OpenAlex, which usually stores the English title of Ukrainian articles."""
    letters = [ch for ch in (title or "") if ch.isalpha()]
    if len(letters) < 10:
        return False
    return sum(ch.isascii() for ch in letters) / len(letters) >= 0.8


def doi_slug(doi: str) -> str:
    return doi.replace("/", "_")


def doi_slug_colon(doi: str) -> str:
    return doi.replace("/", "_").replace(":", "_")


_DOI_STEM = re.compile(r"^10\.\d{4,9}_")


def stem_doi(paper_id: str) -> str | None:
    """DOI encoded in a DOI-named file stem ("10.1029_2025gl120832" → "10.1029/2025gl120832").

    Files named this way were saved under the DOI they were fetched for, which is more
    reliable than the DOI GROBID reads from the PDF header (it sometimes picks up the DOI
    of a cited or companion article). The registrant prefix never contains "_", so the
    first "_" is the "/" of the DOI.
    """
    if not _DOI_STEM.match(paper_id):
        return None
    return normalize_doi(paper_id.replace("_", "/", 1))


def repair_header_doi(doi: str | None, paper_id: str) -> tuple[str | None, str | None]:
    """Repair the two header-DOI defects GROBID produces, or drop the DOI.

    * cut at a line-break hyphen: "10.1146/annurev-fluid-030121-" — when the file stem
      continues the suffix ("annurev-fluid-030121-113138") the DOI is rebuilt from it;
    * text glued after it: "10.30501/jree.2021.257941.1162).2423-7469/" — cut at the
      first unbalanced ")".
    Returns (doi, note); note is None when nothing was changed.
    """
    if not doi:
        return doi, None
    prefix, _, suffix = doi.partition("/")
    close = suffix.find(")")
    if close != -1 and "(" not in suffix[:close]:
        repaired = normalize_doi(f"{prefix}/{suffix[:close]}")
        return repaired, f"header DOI {doi} carried trailing text; cut to {repaired}"
    if doi.endswith(("-", ".", "_", "/")):
        stem = paper_id.lower()
        if suffix and stem.startswith(suffix) and len(stem) > len(suffix):
            repaired = normalize_doi(f"{prefix}/{stem}")
            return repaired, f"header DOI {doi} was cut at a line break; rebuilt from the file name as {repaired}"
        return None, f"header DOI {doi} looks truncated; dropped"
    return doi, None


def openalex_short(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).rstrip("/").rsplit("/", 1)[-1] or None


# ── scan ───────────────────────────────────────────────────────────────────────

@dataclass
class ScannedPaper:
    paper_id: str
    files: list[PaperFile] = field(default_factory=list)
    title: str | None = None
    doi_raw: str | None = None
    doi_source: str | None = None
    year: int | None = None
    venue: str | None = None
    openalex_id: str | None = None
    truncated_json: bool = False


def _year(value) -> int | None:
    try:
        y = int(str(value)[:4])
    except (TypeError, ValueError):
        return None
    return y if 1500 <= y <= 2100 else None


def load_manifest(path: Path | None) -> dict[str, str]:
    """`sha256sum` output (``<hash>  <relative path>``) → {relative path: hash}."""
    if not path or not path.exists():
        return {}
    out: dict[str, str] = {}
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            digest, _, rel = line.rstrip("\n").partition("  ")
            if len(digest) == 64 and rel:
                out[rel.lstrip("./")] = digest
    return out


def scan(root: Path = ROOT, manifest: dict[str, str] | None = None) -> dict[str, ScannedPaper]:
    """Walk the file store and read identity fields from paper.json / enriched JSON."""
    manifest = manifest or {}
    papers: dict[str, ScannedPaper] = {}

    def get(pid: str) -> ScannedPaper:
        return papers.setdefault(pid, ScannedPaper(paper_id=pid))

    for kind, rel_dir, suffix in FILE_SOURCES:
        d = root / rel_dir
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if not f.is_file() or not f.name.endswith(suffix):
                continue
            pid = f.name[: -len(suffix)]
            rel = str(f.relative_to(root))
            get(pid).files.append(PaperFile(kind=kind, path=rel, sha256=manifest.get(rel),
                                            size_bytes=f.stat().st_size))

    for kind, rel_dir in DIR_SOURCES:
        d = root / rel_dir
        if not d.is_dir():
            continue
        for sub in sorted(d.iterdir()):
            if sub.is_dir():
                get(sub.name).files.append(PaperFile(kind=kind, path=str(sub.relative_to(root))))

    for sp in papers.values():
        _read_metadata(root, sp)
        _mark_duplicate_pdf_copies(sp)
    return papers


def _read_metadata(root: Path, sp: ScannedPaper) -> None:
    by_kind = defaultdict(list)
    for f in sp.files:
        by_kind[f.kind].append(f)

    meta: dict = {}
    for pf in by_kind.get("paper_json", []):
        try:
            meta = (json.loads((root / pf.path).read_text(encoding="utf-8")) or {}).get("metadata") or {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            sp.truncated_json = True
            sp.files[sp.files.index(pf)] = pf.model_copy(update={"status": "truncated"})

    enriched: dict = {}
    for pf in by_kind.get("enriched", []):
        try:
            enriched = json.loads((root / pf.path).read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            enriched = {}
    e_meta = ((enriched.get("paper") or {}).get("metadata")) or {}
    oa = enriched.get("openalex") or {}
    em = enriched.get("enrichment_meta") or {}

    sp.title = (meta.get("title") or e_meta.get("title") or None)
    for value, source in ((meta.get("doi"), "paper_json.metadata.doi"),
                          (e_meta.get("doi"), "enriched.paper.metadata.doi"),
                          (em.get("doi"), "enriched.enrichment_meta.doi")):
        if value:
            sp.doi_raw, sp.doi_source = value, source
            break
    sp.year = _year(meta.get("year")) or _year(e_meta.get("year")) or _year(oa.get("publication_year"))
    sp.venue = meta.get("journal") or e_meta.get("journal") or None
    sp.openalex_id = openalex_short(oa.get("openalex_id"))


def _mark_duplicate_pdf_copies(sp: ScannedPaper) -> None:
    """A pdf_missing/ copy byte-identical to the pdf/ file is marked duplicate_copy."""
    main = {f.sha256 for f in sp.files if f.kind == "pdf" and "/pdf/" in f"/{f.path}" and f.sha256}
    for i, f in enumerate(sp.files):
        if f.kind == "pdf" and "pdf_missing" in f.path and f.sha256 and f.sha256 in main:
            sp.files[i] = f.model_copy(update={"status": "duplicate_copy"})


# ── classify ───────────────────────────────────────────────────────────────────

@dataclass
class Classified:
    identities: list[PaperIdentity]
    counts: dict
    notes: dict[str, str]


def classify(
    scanned: dict[str, ScannedPaper],
    openalex_titles: dict[str, str] | None = None,
    not_a_paper: frozenset[str] = NOT_A_PAPER,
) -> Classified:
    openalex_titles = openalex_titles or {}
    status: dict[str, str] = {}
    doi: dict[str, str | None] = {}
    notes: dict[str, str] = {}
    duplicate_of: dict[str, str] = {}

    for pid, sp in scanned.items():
        d, repair_note = repair_header_doi(normalize_doi(sp.doi_raw), pid)
        if repair_note:
            notes[pid] = repair_note
        d_stem = stem_doi(pid)
        if d_stem:
            if d and d != d_stem:
                notes[pid] = f"header DOI {d} ({sp.doi_source}) differs from the file-name DOI {d_stem}; file name kept"
            d = d_stem
        if pid in not_a_paper:
            status[pid], doi[pid] = "not_a_paper", None
            continue
        if sp.truncated_json:
            status[pid] = "truncated_json"
        oa_title = openalex_titles.get(d) if d else None
        if d and oa_title and is_latin(sp.title) and is_latin(oa_title):
            ratio = difflib.SequenceMatcher(None, norm_title(sp.title), norm_title(oa_title)).ratio()
            if ratio < TITLE_MATCH_MIN:
                status.setdefault(pid, "title_doi_mismatch")
                origin = "file-name DOI" if d == d_stem else f"header DOI ({sp.doi_source})"
                notes[pid] = (f"{origin} {d} resolves to {oa_title!r}; title similarity {ratio:.2f}"
                              + (f"; {notes[pid]}" if pid in notes else ""))
                d = None  # a DOI that names another work must not become this paper's identity
        doi[pid] = d

    # Duplicates by DOI
    by_doi: dict[str, list[str]] = defaultdict(list)
    for pid, d in doi.items():
        if d and status.get(pid) not in ("not_a_paper",):
            by_doi[d].append(pid)
    for d, pids in by_doi.items():
        if len(pids) > 1:
            canon = _pick_canonical(d, pids, scanned, status)
            for pid in pids:
                if pid != canon:
                    duplicate_of[pid] = canon

    # Duplicates by (title, year) for long titles, against any non-duplicate paper
    by_title: dict[tuple[str, int | None], list[str]] = defaultdict(list)
    for pid, sp in scanned.items():
        t = norm_title(sp.title)
        if len(t) >= TITLE_DUP_MIN_LEN and pid not in duplicate_of and status.get(pid) != "not_a_paper":
            by_title[(t, sp.year)].append(pid)
    for _, pids in by_title.items():
        if len(pids) > 1:
            canon = _pick_canonical(None, pids, scanned, status, doi)
            for pid in pids:
                if pid != canon and pid not in duplicate_of:
                    duplicate_of[pid] = canon

    identities: list[PaperIdentity] = []
    for pid, sp in sorted(scanned.items()):
        if pid in duplicate_of:
            st = "duplicate"
        else:
            st = status.get(pid) or ("ok" if doi.get(pid) else "no_doi")
        identities.append(PaperIdentity(
            paper_id=pid, doi=doi.get(pid), title=sp.title, year=sp.year, venue=sp.venue,
            openalex_id=sp.openalex_id, identity_status=st, duplicate_of=duplicate_of.get(pid),
            files=sp.files,
        ))
    identities = _attach_aliases(identities, scanned)
    counts = {
        "papers": len(identities),
        "by_status": dict(Counter(i.identity_status for i in identities)),
        "files_by_kind": dict(Counter(f.kind for i in identities for f in i.files)),
        "truncated_paper_json": sum(1 for sp in scanned.values() if sp.truncated_json),
        "duplicate_pdf_copies": sum(1 for i in identities for f in i.files if f.status == "duplicate_copy"),
    }
    return Classified(identities=identities, counts=counts, notes=notes)


def _pick_canonical(d, pids, scanned, status, doi=None) -> str:
    """Prefer a usable paper named after its DOI, then the one with the most files."""
    def score(pid: str):
        sp = scanned[pid]
        bad = status.get(pid) in ("truncated_json", "not_a_paper")
        named = bool(d) and pid in (doi_slug(d), doi_slug_colon(d))
        has_doi = bool(doi and doi.get(pid))
        kinds = len({f.kind for f in sp.files})
        return (not bad, named, has_doi, kinds, -len(pid), pid)
    return max(pids, key=score)


def _attach_aliases(identities: list[PaperIdentity], scanned: dict[str, ScannedPaper]) -> list[PaperIdentity]:
    """Aliases are unique per (type, alias); on conflict the canonical paper wins."""
    owner: dict[tuple[str, str], str] = {}
    source: dict[tuple[str, str], str] = {}
    canonical = {i.paper_id for i in identities if i.identity_status != "duplicate"}

    def claim(alias_type: str, alias: str | None, pid: str, src: str) -> None:
        if not alias:
            return
        key = (alias_type, alias)
        current = owner.get(key)
        if current is None or (current not in canonical and pid in canonical):
            owner[key], source[key] = pid, src

    for i in identities:
        claim("file_stem", i.paper_id, i.paper_id, "scan:file_name")
        target = i.duplicate_of or i.paper_id
        if i.doi:
            claim("doi", i.doi, target, f"scan:{scanned[i.paper_id].doi_source}")
            claim("doi_slug", doi_slug(i.doi), target, "scan:derived_from_doi")
            if doi_slug_colon(i.doi) != doi_slug(i.doi):
                claim("doi_slug_colon", doi_slug_colon(i.doi), target, "scan:derived_from_doi")
        claim("openalex_id", i.openalex_id, target, "scan:enriched.openalex.openalex_id")
        for f in i.files:
            if f.kind == "pdf" and f.sha256:
                claim("pdf_sha256", f.sha256, target, f"scan:{f.path}")

    aliases: dict[str, list[PaperAlias]] = defaultdict(list)
    for (alias_type, alias), pid in owner.items():
        aliases[pid].append(PaperAlias(alias_type=alias_type, alias=alias, source=source[(alias_type, alias)]))
    return [i.model_copy(update={"aliases": sorted(aliases.get(i.paper_id, []),
                                                   key=lambda a: (a.alias_type, a.alias))})
            for i in identities]


def resolve_cohort(rows: list[dict], identities: list[PaperIdentity], cohort: str, source: str) -> tuple[list[CohortMember], list[dict]]:
    """Map cohort rows (paper_id and/or doi) onto canonical paper ids."""
    by_alias = {(a.alias_type, a.alias): i.paper_id for i in identities for a in i.aliases}
    members, unresolved = {}, []
    for row in rows:
        pid = by_alias.get(("file_stem", (row.get("paper_id") or "").strip()))
        d = normalize_doi(row.get("doi"))
        if pid is None and d:
            pid = by_alias.get(("doi", d))
        if pid is None:
            unresolved.append(row)
            continue
        canon = next((i.duplicate_of or i.paper_id for i in identities if i.paper_id == pid), pid)
        members[canon] = CohortMember(cohort=cohort, paper_id=canon, source=source)
    return list(members.values()), unresolved


# ── external lookups (read-only) ──────────────────────────────────────────────

def openalex_titles_from_cache(db_path: Path) -> dict[str, str]:
    """DOI → OpenAlex title from data/cache/openalex_doi.db (doi_cache table)."""
    if not db_path.exists():
        return {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    out: dict[str, str] = {}
    try:
        for doi_value, response in con.execute("SELECT doi, response FROM doi_cache"):
            try:
                title = (json.loads(response) or {}).get("title")
            except json.JSONDecodeError:
                continue
            d = normalize_doi(doi_value)
            if d and title:
                out[d] = title
    finally:
        con.close()
    return out


def git_state(root: Path = ROOT) -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                                    capture_output=True, text=True, check=True).stdout.strip())
        return commit, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, None


# ── load ───────────────────────────────────────────────────────────────────────

def load(classified: Classified, cohorts: list[CohortMember], params: dict) -> uuid.UUID:
    """Write one identity snapshot in one transaction; returns the ops.run id.

    Rows created by earlier scans (source 'scan:…') are replaced; aliases or files
    added later by the API keep their own source and are left alone.
    """
    from sqlalchemy import delete, update
    from sqlalchemy.dialects.postgresql import insert

    from src.db.engine import session_scope
    from src.db.models import CohortMember as CohortRow
    from src.db.models import Paper, PaperAlias as AliasRow, PaperFile as FileRow, Run

    commit, dirty = git_state()
    run_id = uuid.uuid4()
    with session_scope() as s:
        s.add(Run(run_id=run_id, kind="etl", name="identity", status="running",
                  git_commit=commit, git_dirty=dirty, params=params))
        s.flush()

        rows = [dict(paper_id=i.paper_id, doi=i.doi, title=i.title, year=i.year, venue=i.venue,
                     openalex_id=i.openalex_id, identity_status=i.identity_status,
                     duplicate_of=i.duplicate_of, notes=classified.notes.get(i.paper_id), run_id=run_id)
                for i in classified.identities]
        # Canonical rows first: duplicates reference them through a foreign key.
        rows.sort(key=lambda r: r["duplicate_of"] is not None)
        scanned_ids = [r["paper_id"] for r in rows]
        # Re-runs: which copy is canonical for a DOI may change. The partial unique index
        # on doi is checked per statement, so reset the rows being rewritten first.
        for chunk in _chunks(scanned_ids, 2000):
            s.execute(update(Paper).where(Paper.paper_id.in_(chunk))
                      .values(doi=None, duplicate_of=None, identity_status="no_doi"))
        for chunk in _chunks(rows, 1000):
            stmt = insert(Paper).values(chunk)
            s.execute(stmt.on_conflict_do_update(
                index_elements=[Paper.paper_id],
                set_={c: stmt.excluded[c] for c in
                      ("doi", "title", "year", "venue", "openalex_id", "identity_status",
                       "duplicate_of", "notes", "run_id")}))

        s.execute(delete(AliasRow).where(AliasRow.source.like("scan:%")))
        alias_rows = [dict(alias_type=a.alias_type, alias=a.alias, paper_id=i.paper_id,
                           source=a.source, run_id=run_id)
                      for i in classified.identities for a in i.aliases]
        for chunk in _chunks(alias_rows, 2000):
            s.execute(insert(AliasRow).values(chunk).on_conflict_do_nothing())

        for chunk in _chunks(scanned_ids, 2000):
            s.execute(delete(FileRow).where(FileRow.paper_id.in_(chunk)))
        file_rows = [dict(paper_id=i.paper_id, kind=f.kind, path=f.path, sha256=f.sha256,
                          size_bytes=f.size_bytes, status=f.status, run_id=run_id)
                     for i in classified.identities for f in i.files]
        for chunk in _chunks(file_rows, 2000):
            s.execute(insert(FileRow).values(chunk))

        if cohorts:
            for name in {c.cohort for c in cohorts}:
                s.execute(delete(CohortRow).where(CohortRow.cohort == name))
            s.execute(insert(CohortRow).values(
                [dict(cohort=c.cohort, paper_id=c.paper_id, source=c.source, run_id=run_id) for c in cohorts]))

        counts = dict(classified.counts, aliases=len(alias_rows), files=len(file_rows), cohort_members=len(cohorts))
        s.execute(update(Run).where(Run.run_id == run_id).values(
            status="succeeded", finished_at=datetime.now(timezone.utc), counts=counts))
    return run_id


def _chunks(rows: list, n: int):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


# ── CLI ────────────────────────────────────────────────────────────────────────

def _report(classified: Classified, cohort_unresolved: list[dict]) -> str:
    c = classified.counts
    lines = [f"papers: {c['papers']}", f"by status: {c['by_status']}",
             f"files by kind: {c['files_by_kind']}",
             f"truncated paper.json: {c['truncated_paper_json']}",
             f"pdf_missing copies identical to pdf/: {c['duplicate_pdf_copies']}",
             f"cohort rows not in corpus: {len(cohort_unresolved)}"]
    mism = [i for i in classified.identities if i.identity_status == "title_doi_mismatch"]
    if mism:
        lines.append("title/DOI mismatches (first 10):")
        lines += [f"  {i.paper_id}: {classified.notes.get(i.paper_id)}" for i in mism[:10]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="scan and classify only; write nothing")
    ap.add_argument("--manifest", type=Path,
                    default=ROOT / "data/backups/manifest_20261002/SHA256SUMS.data",
                    help="sha256sum output used for file hashes (missing → hashes left empty)")
    ap.add_argument("--openalex-cache", type=Path, default=ROOT / "data/cache/openalex_doi.db")
    ap.add_argument("--cohort-csv", type=Path, default=ROOT / "data/paper_3_audit/cohort_paper_3.csv")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    manifest = load_manifest(args.manifest)
    log.info("manifest entries: %d", len(manifest))
    scanned = scan(ROOT, manifest)
    titles = openalex_titles_from_cache(args.openalex_cache)
    log.info("scanned %d paper ids; OpenAlex titles for %d DOIs", len(scanned), len(titles))
    classified = classify(scanned, titles)

    cohort_rows = list(csv.DictReader(args.cohort_csv.open(encoding="utf-8"))) if args.cohort_csv.exists() else []
    cohort_name = (cohort_rows[0].get("cohort") or "paper_3") if cohort_rows else "paper_3"
    members, unresolved = resolve_cohort(cohort_rows, classified.identities, cohort_name,
                                         f"scan:{args.cohort_csv.relative_to(ROOT)}" if cohort_rows else "")
    print(_report(classified, unresolved))
    print(f"cohort {cohort_name}: {len(members)} members in corpus")
    if args.dry_run:
        return 0
    params = {"manifest": str(args.manifest), "openalex_cache": str(args.openalex_cache),
              "cohort_csv": str(args.cohort_csv), "manifest_entries": len(manifest)}
    run_id = load(classified, members, params)
    print(f"loaded into Postgres, run_id={run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
