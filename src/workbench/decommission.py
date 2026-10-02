"""P6: freeze the per-paper folders, record where every file goes, gate their deletion.

    python -m src.workbench.decommission freeze      # byte copy into data/frozen/paper_folders_<date>/
    python -m src.workbench.decommission inventory   # (re)classify every frozen file -> INVENTORY.csv
    python -m src.workbench.decommission verify      # re-hash the freeze; report drift of the originals
    python -m src.workbench.decommission gate        # exit 0 only when every file is accounted for

The freeze is immutable: read-only files, SHA256SUMS in ``sha256sum -c`` format and a README.
INVENTORY.csv is its one mutable file. It is the ledger of where each file goes (disposition,
project, destination inside the consumer repository) and whether that has been verified
(status). The originals may be deleted only when ``gate`` passes (API_PLAN_v1/11 §5, P6).

Destinations follow the author's decisions of 2026-10-02: a paper's own-data analysis goes to
its repository; deliveries are written by the workbench and committed by the human;
kakhovka-report:v1 is archived only; Article 1 lives in floodstate-eo under
``articles/flood_mapping_methods_review/``. floodstate-eo and SWOT-DNIPRO are public, so
docx/pdf builds only ever land in ignored ``private/`` folders (disposition ``private``).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FROZEN_DIR = ROOT / "data" / "frozen"
DEFAULT_NAME = "paper_folders_20261002"

# Everything P6 touches: the per-paper folders, the per-paper code and the stray outputs.
# Tracked code is frozen too: the ETL re-imports src/paper_3/*.yaml from the freeze, and the
# working tree holds uncommitted edits that exist nowhere else.
FREEZE_ROOTS = (
    "paper_unet-case-kakhovka",
    "paper_terrain-case-kakhovka",
    "paper_3_audit",
    "paper_my",
    "data/paper_1_audit",
    "data/paper_3_audit",
    "data/paper_audit",
    "outputs/paper_1",
    "src/paper_audit",
    "src/paper_3",
    "tools/paper3_audit",
    "tools/paper3_literature_audit.py",
    "scripts/paper1_*",
    "scripts/revise_paper*.py",
    "references_validated.bib",
    "references_validation.csv",
)
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = (".pyc",)

SUMS = "SHA256SUMS"
INVENTORY = "INVENTORY.csv"
README = "README.md"
COLUMNS = ("path", "kind", "bytes", "sha256", "git", "imported", "disposition", "project",
           "destination", "rule", "status", "checked_at", "decision", "note")

DISPOSITIONS = {
    "deliver": "copied byte-identical into the consumer repository, tracked there",
    "private": "copied into an ignored path of the consumer (docx/pdf for the human, never committed)",
    "mirror": "the consumer already holds the authoritative copy; ours is only archived",
    "code": "code moved into the consumer with a '# Provenance:' header",
    "here": "generic machinery that stays in this repository under a new module",
    "archive": "kept only in the freeze",
    "retire": "code removed; the freeze and git history keep it",
    "unclassified": "no rule matched: blocks the gate until a rule is written",
}
# disposition -> statuses that let the original be deleted
DONE = {
    "deliver": {"present", "delivered"},
    "private": {"present", "delivered"},
    "code": {"present", "delivered"},
    "mirror": {"present", "accepted"},
    "here": {"moved"},
    "archive": {"frozen"},
    "retire": {"frozen"},
    "unclassified": set(),
}

FS3, KT2, P1, KR1, ART1 = ("floodstate-eo:paper3", "kakhovka-terrain:paper2", "swot-dnipro:paper1",
                          "kakhovka-report:v1", "article1")
REPOS = {FS3: "floodstate-eo", KT2: "kakhovka-terrain", P1: "SWOT-DNIPRO", ART1: "floodstate-eo"}

LA = "paper_unet-case-kakhovka/literature_audit_paper3"
PUB = "paper_unet-case-kakhovka/publication"
P2 = "paper_terrain-case-kakhovka/publication"
D3 = "data/paper_3_audit"
R3 = "paper_3_audit"
FS3_LA = "case_studies/kakhovka_2023/literature_audit/"
FS3_PUB = "case_studies/kakhovka_2023/publication/"
KT2_PUB = "case_studies/kakhovka/publication/"
P1_OUT = "outputs/paper/"
P1_KR = "outputs/paper/knowledge_repo/"          # Paper 1 as built here (kakhovka-report:v1 era)
KT2_KR = KT2_PUB + "knowledge_repo/"             # Paper 2 as built here
ART1_DIR = "articles/flood_mapping_methods_review/"


@dataclass(frozen=True)
class Rule:
    pattern: str                   # glob on the repo-relative path: * stays in one segment, ** crosses
    disposition: str
    project: str | None = None
    dest: str | None = None        # consumer path; a trailing "/" maps the path below the fixed prefix
    note: str = ""

    def destination(self, path: str) -> str:
        if not self.dest:
            return ""
        if not self.dest.endswith("/"):
            return self.dest
        fixed = self.pattern.split("*", 1)[0]
        base = fixed[: fixed.rfind("/") + 1]
        return self.dest + path[len(base):]


R = Rule
RULES: tuple[Rule, ...] = (
    R("**/*:Zone.Identifier", "archive", note="Windows download marker"),

    # U-Net bundle -> floodstate-eo:paper3. The audit is run here and copied there, so ours is newer.
    R(f"{LA}/_work/**", "archive", FS3, note="caches and intermediates; ignored in floodstate-eo"),
    R(f"{LA}/article/figures/**", "archive", FS3, note="byte copies of publication/figures made by final_article.py"),
    R(f"{LA}/article/tables/**", "archive", FS3, note="byte copies of publication/tables made by final_article.py"),
    R(f"{LA}/article/*.docx", "private", FS3, FS3_LA + "article/", note="ignored there (article/*.docx)"),
    R(f"{LA}/**", "deliver", FS3, FS3_LA),
    R(f"{PUB}/**", "mirror", FS3, FS3_PUB, note="older mirror; floodstate-eo publication/ is the source"),
    R("paper_unet-case-kakhovka/paper_verif_swot/*.docx", "private", P1, P1_OUT,
      note="Paper 1 v4 build; outputs/paper/*.docx is ignored in SWOT-DNIPRO"),
    R("paper_unet-case-kakhovka/paper_research/*.docx", "private", ART1, ART1_DIR + "private/history/",
      note="Article 1 V-4 and appendices A-C"),

    # Terrain bundle: kakhovka-terrain is the source; ours is a mirror.
    R(f"{P2}/**", "mirror", KT2, KT2_PUB, note="mirror of the kakhovka-terrain bundle"),

    # paper_3_audit: the published copy of data/paper_3_audit (kakhovka-report:v1, tracked in git).
    R(f"{R3}/PASSPORTS/paper1.md", "deliver", P1, P1_OUT + "PASSPORT.md"),
    R(f"{R3}/PASSPORTS/paper2.md", "deliver", KT2, KT2_PUB + "PASSPORT.md"),
    R(f"{R3}/PASSPORTS/paper3.md", "deliver", FS3, FS3_PUB + "PASSPORT.md"),
    R(f"{R3}/PASSPORTS/paper4.md", "deliver", KT2, KT2_PUB + "PASSPORT_paper4.md",
      note="Paper 4 (revegetation and roughness) belongs with kakhovka-terrain"),
    R(f"{R3}/PASSPORTS/paper5.md", "archive", KR1, note="Paper 5 (hydraulic modelling) has no repository yet"),
    R(f"{R3}/insitu_2023/**", "archive", KR1, note="DECIDE: SWOT-DNIPRO data/insitu_2023/ (2023 yearbook gauges)"),
    R(f"{R3}/**", "archive", KR1, note="publish copy (src/paper_3/publish.py); git history keeps it"),

    # data/paper_3_audit: Paper 1 -> SWOT-DNIPRO, Paper 2 -> kakhovka-terrain, the rest is the archived report.
    R(f"{D3}/paper1_*.docx", "private", P1, P1_KR + "private/"),
    R(f"{D3}/paper1_*", "deliver", P1, P1_KR),
    R(f"{D3}/PAPER1_OPEN_ITEMS.md", "deliver", P1, P1_KR),
    R(f"{D3}/response_to_reviewer2_R1.md", "deliver", P1, P1_KR + "reviews/"),
    R(f"{D3}/reviewer2_*.md", "deliver", P1, P1_KR + "reviews/"),
    R(f"{D3}/OWN_EVIDENCE.csv", "deliver", P1, P1_KR + "evidence/",
      note="multi-paper (column 'paper'); Paper 1 holds the binding script"),
    R(f"{D3}/ARTICLE_THESES.csv", "deliver", P1, P1_KR + "evidence/"),
    R(f"{D3}/SECTION_7_8_STUB.md", "deliver", P1, P1_KR),
    R(f"{D3}/REFERENCES.*", "deliver", P1, P1_KR + "references/",
      note="report reference registry; CiteKey/BibVerification rows are in Postgres (kakhovka-report:v1)"),
    R(f"{D3}/derived/**", "deliver", P1, P1_KR + "derived/"),
    R(f"{D3}/figures/P2-F*", "deliver", KT2, KT2_KR + "figures/"),
    R(f"{D3}/figures/**", "deliver", P1, P1_KR + "figures/"),
    R(f"{D3}/paper2_*.docx", "private", KT2, KT2_KR + "private/"),
    R(f"{D3}/paper2_*", "deliver", KT2, KT2_KR),
    R(f"{D3}/PAPER2_OPEN_ITEMS.md", "deliver", KT2, KT2_KR),
    R(f"{D3}/p2/*.md", "deliver", KT2, KT2_KR + "literature_scan/"),
    R(f"{D3}/p2/*.csv", "deliver", KT2, KT2_KR + "literature_scan/"),
    R(f"{D3}/p2/**", "archive", KT2, note="harvest parquets, caches and run manifests of the Paper 2 scan"),
    R(f"{D3}/briefs/**", "archive", KR1, note="geodesy mini-corpus (59 PDFs); never into any git"),
    R(f"{D3}/swot_dnipro_snapshot/**", "archive", KR1, note="copy of SWOT-DNIPRO/icesat2 tables; the sources hold them"),
    R(f"{D3}/missing_paper_23-09-2026/**", "archive", KR1, note="literature PDFs: corpus-ingestion candidates, never a consumer git"),
    R(f"{D3}/insitu/**", "archive", KR1, note="hydromet yearbook PDFs, source of paper_3_audit/insitu_2023"),
    R(f"{D3}/**", "archive", KR1, note="kakhovka-report:v1 machinery output; labels and references are in Postgres"),

    # Paper 1 analyses: inputs and bulk results stay archived; their figures go to SWOT-DNIPRO.
    R("data/paper_1_audit/**", "archive", P1, note="946 MB inputs/results of scripts/paper1_*; MANIFEST.csv goes to SWOT-DNIPRO"),
    R("outputs/paper_1/**", "deliver", P1, P1_KR + "analysis/"),
    R("scripts/paper1_*", "code", P1, "scripts/paper1_audit/"),
    R("scripts/revise_paper*.py", "retire", ART1, note="writes into shared data/analytics; hard-coded Neo4j credentials"),
    R("references_validated.bib", "archive", ART1, note="output of scripts/revise_paper.py"),
    R("references_validation.csv", "archive", ART1, note="output of scripts/revise_paper.py"),

    # Article 1 data -> floodstate-eo articles/flood_mapping_methods_review/
    R("data/paper_audit/frozen_*/**", "archive", ART1, note="earlier freezes of the appendices"),
    R("data/paper_audit/backup_*/**", "archive", ART1),
    R("data/paper_audit/removed_*/**", "archive", ART1, note="rows removed as paywalled (Taylor & Francis)"),
    R("data/paper_audit/*.backup_*", "archive", ART1),
    R("data/paper_audit/missing_ingest_xml/**", "archive", ART1, note="symlink into the corpus"),
    R("data/paper_audit/verification/*_raw.json", "archive", ART1, note="raw model output with long quotes"),
    R("data/paper_audit/**", "deliver", ART1, ART1_DIR + "data/"),

    # Article 1 code and the final package (gitignored here: the freeze is the only other copy).
    R("src/paper_audit/article_1_final/figures/fig_1/v1_archive/**", "archive", ART1),
    R("src/paper_audit/article_1_final/**/*.tiff*", "private", ART1, ART1_DIR + "private/publication/"),
    R("src/paper_audit/article_1_final/**/*.pdf", "private", ART1, ART1_DIR + "private/publication/"),
    R("src/paper_audit/article_1_final/**/*.docx", "private", ART1, ART1_DIR + "private/publication/"),
    R("src/paper_audit/article_1_final/**/*.py", "code", ART1, ART1_DIR + "publication/"),
    R("src/paper_audit/article_1_final/**", "deliver", ART1, ART1_DIR + "publication/"),
    R("src/paper_audit/*.md", "deliver", ART1, ART1_DIR + "manuscript/"),
    R("src/paper_audit/*.py", "code", ART1, ART1_DIR + "workflows/paper_audit/"),
    R("src/paper_audit/**", "deliver", ART1, ART1_DIR + "workflows/paper_audit/"),

    # paper_my: the Article 1 workspace before the final package.
    R("paper_my/theses_v4.json", "deliver", ART1, ART1_DIR + "literature/theses_v4.json",
      note="imported as the article1 theses"),
    R("paper_my/theses*.json", "deliver", ART1, ART1_DIR + "literature/history/"),
    R("paper_my/cache/**", "archive", ART1, note="OpenAlex verification cache"),
    R("paper_my/.circuit_breaker/**", "archive", ART1),
    R("paper_my/app/**", "archive", ART1, note="Streamlit evidence app, superseded by apps/mvp and the workbench"),
    R("paper_my/*.py", "archive", ART1, note="generation pipelines on repository internals, superseded by the API"),
    R("paper_my/*.pdf", "archive", ART1),
    R("paper_my/*.txt", "archive", ART1, note="prompts"),
    R("paper_my/*.json", "archive", ART1, note="logs, caches and evidence dumps"),
    R("paper_my/**", "deliver", ART1, ART1_DIR + "history/paper_my/"),

    # Per-paper code in src/paper_3 (tracked): generic parts move into the workbench, the rest is archived.
    R("src/paper_3/evidence.py", "here", None, "src/services/evidence_text.py"),
    R("src/paper_3/snapshot.py", "here", None, "src/workbench/remote.py"),
    R("src/paper_3/v2/assemble.py", "here", None, "src/workbench/assemble/"),
    R("src/paper_3/v2/markers.py", "here", None, "src/workbench/assemble/markers.py"),
    R("src/paper_3/v2/claim_map.py", "here", None, "src/workbench/assemble/"),
    R("src/paper_3/v2/translate*", "here", None, "src/workbench/steps/"),
    R("src/paper_3/derived.py", "code", P1, "scripts/paper1_audit/"),
    R("src/paper_3/figures.py", "code", P1, "scripts/paper1_audit/"),
    R("src/paper_3/own_evidence.py", "code", P1, "scripts/paper1_audit/"),
    R("src/paper_3/publish.py", "retire", KR1),
    R("src/paper_3/own_claims.yaml", "code", P1, P1_KR + "evidence/own_claims.yaml",
      note="multi-paper (papers 1, 2, 4)"),
    R("src/paper_3/v2/templates/paper1/**", "code", P1, P1_OUT + "templates/"),
    R("src/paper_3/v2/templates/paper2/**", "code", KT2, KT2_PUB + "templates/"),
    R("src/paper_3/v2/templates/paper4/**", "code", KT2, KT2_PUB + "templates_paper4/"),
    R("src/paper_3/v2/reference_registry.py", "retire", KR1, note="superseded by /bib/* and biblio.http_cache"),
    R("src/paper_3/v2/scientific_claim_status.yaml", "retire", KR1, note="written, never read"),
    R("src/paper_3/v2/scientific_status.py", "retire", KR1),
    R("src/paper_3/insitu/**", "archive", KR1, note="DECIDE with paper_3_audit/insitu_2023"),
    R("src/paper_3/**", "archive", KR1, note="kakhovka-report:v1 machinery; git history keeps it"),

    # The Paper 3 literature audit tool becomes the workbench literature and review steps.
    R("tools/paper3_audit/**", "here", None, "src/workbench/literature/"),
    R("tools/paper3_literature_audit.py", "here", None, "src/workbench/__main__.py"),
)


# ── glob matching and classification ───────────────────────────────────────────

def glob_regex(pattern: str) -> re.Pattern:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


_COMPILED: dict[str, re.Pattern] = {}


def classify(path: str, rules: tuple[Rule, ...] = RULES) -> tuple[int, Rule | None]:
    """First matching rule (index, rule); (-1, None) when nothing matches."""
    for i, rule in enumerate(rules):
        rx = _COMPILED.get(rule.pattern) or _COMPILED.setdefault(rule.pattern, glob_regex(rule.pattern))
        if rx.match(path):
            return i, rule
    return -1, None


# ── freeze ─────────────────────────────────────────────────────────────────────

def _roots(root: Path, roots: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for spec in roots:
        if any(c in spec for c in "*?["):
            found += sorted(p.relative_to(root).as_posix() for p in root.glob(spec))
        elif (root / spec).exists() or (root / spec).is_symlink():
            found.append(spec)
    return found


def walk(root: Path, roots: tuple[str, ...]) -> list[tuple[str, str]]:
    """(relative path, kind) for every file and symlink under the roots, caches excluded."""
    entries: list[tuple[str, str]] = []
    for top in _roots(root, roots):
        start = root / top
        if start.is_symlink() or start.is_file():
            entries.append((top, "symlink" if start.is_symlink() else "file"))
            continue
        for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
            here = Path(dirpath)
            keep = []
            for d in sorted(dirnames):
                if d in SKIP_DIRS:
                    continue
                if (here / d).is_symlink():
                    entries.append(((here / d).relative_to(root).as_posix(), "symlink"))
                else:
                    keep.append(d)
            dirnames[:] = keep
            for f in sorted(filenames):
                if f.endswith(SKIP_SUFFIXES):
                    continue
                p = here / f
                entries.append((p.relative_to(root).as_posix(), "symlink" if p.is_symlink() else "file"))
    for rel, _ in entries:
        if "\n" in rel or "\\" in rel:
            raise ValueError(f"path cannot be written to {SUMS}: {rel!r}")
    return sorted(set(entries))


def _copy_hash(src: Path, dst: Path) -> tuple[int, str]:
    h, n = hashlib.sha256(), 0
    with src.open("rb") as fi, dst.open("xb") as fo:
        for block in iter(lambda: fi.read(1 << 20), b""):
            h.update(block)
            fo.write(block)
            n += len(block)
    shutil.copystat(src, dst, follow_symlinks=False)
    return n, h.hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True)
    return proc.stdout.decode("utf-8", errors="surrogateescape") if proc.returncode == 0 else ""


def git_commit(root: Path) -> tuple[str, int]:
    head = _git(root, "rev-parse", "HEAD").strip()
    dirty = len([l for l in _git(root, "status", "--porcelain").splitlines() if l.strip()])
    return head, dirty


def freeze(dest: Path, root: Path = ROOT, roots: tuple[str, ...] = FREEZE_ROOTS) -> dict:
    """Copy every file under the roots into ``dest`` (repo-relative layout), hash while copying."""
    if dest.exists():
        raise FileExistsError(f"{dest} exists; a freeze is never rewritten (pick a new name)")
    entries = walk(root, roots)
    tmp = dest.with_name(dest.name + ".partial")
    if tmp.exists():
        _make_writable(tmp)
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    sums: list[tuple[str, str]] = []
    links: list[tuple[str, str]] = []
    total = 0
    for rel, kind in entries:
        src, dst = root / rel, tmp / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if kind == "symlink":
            target = os.readlink(src)
            os.symlink(target, dst)
            links.append((rel, target))
            continue
        n, digest = _copy_hash(src, dst)
        total += n
        sums.append((digest, rel))
    (tmp / SUMS).write_text("".join(f"{d}  ./{rel}\n" for d, rel in sorted(sums, key=lambda x: x[1])),
                            encoding="utf-8")
    commit, dirty = git_commit(root)
    info = {"name": dest.name, "frozen_at": _now(),
            "git_commit": commit, "git_dirty_paths": dirty, "files": len(sums), "symlinks": len(links),
            "bytes": total, "roots": _roots(root, roots)}
    (tmp / README).write_text(_readme(info, links), encoding="utf-8")
    tmp.rename(dest)
    return info


def _readme(info: dict, links: list[tuple[str, str]]) -> str:
    roots = "\n".join(f"- `{r}`" for r in info["roots"])
    link_lines = "\n".join(f"- `{rel}` → `{target}`" for rel, target in links) or "- none"
    disp = "\n".join(f"- `{k}`: {v}" for k, v in DISPOSITIONS.items())
    return f"""# Frozen per-paper folders ({info['frozen_at'][:10]})

Byte copy of every per-paper folder, per-paper script and stray paper output of this repository,
made before they are moved to the consumer repositories and removed (API_PLAN_v1/11 §5, P6).
The layout is the repository layout. Written by `python -m src.workbench.decommission freeze`.

- frozen at {info['frozen_at']}, repository commit `{info['git_commit']}` with {info['git_dirty_paths']} dirty paths
  (the copy is of the working tree, uncommitted edits included)
- {info['files']} files, {info['bytes'] / 1e6:,.1f} MB, plus {info['symlinks']} symlinks (kept as symlinks)
- `__pycache__/` and `*.pyc` are not copied

Roots:
{roots}

Symlinks (they point into the corpus, which stays):
{link_lines}

Verify: `cd {FROZEN_DIR.relative_to(ROOT).as_posix()}/{info['name']} && sha256sum -c --quiet {SUMS}`.
Do not modify this directory: files and folders are read-only.

`{INVENTORY}` is the one mutable file: the ledger of where every file goes and whether that was verified.
Columns: {", ".join(f"`{c}`" for c in COLUMNS)}.

Dispositions:
{disp}

The originals may be deleted only when `python -m src.workbench.decommission gate` passes.
"""


def _make_writable(path: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(path):
        os.chmod(dirpath, os.stat(dirpath).st_mode | stat.S_IWUSR)
        for f in filenames:
            p = os.path.join(dirpath, f)
            if not os.path.islink(p):
                os.chmod(p, os.stat(p).st_mode | stat.S_IWUSR)


def make_readonly(dest: Path) -> None:
    """Strip write bits from everything but INVENTORY.csv (dirs last, bottom-up)."""
    for dirpath, dirnames, filenames in os.walk(dest, topdown=False):
        for f in filenames:
            p = os.path.join(dirpath, f)
            if os.path.islink(p) or (dirpath == str(dest) and f == INVENTORY):
                continue
            os.chmod(p, os.stat(p).st_mode & ~0o222)
        os.chmod(dirpath, os.stat(dirpath).st_mode & ~0o222)


def read_sums(dest: Path) -> dict[str, str]:
    out = {}
    for line in (dest / SUMS).read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        out[rel.removeprefix("./")] = digest
    return out


def frozen_links(dest: Path) -> dict[str, str]:
    links = {}
    for dirpath, dirnames, filenames in os.walk(dest, followlinks=False):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            if p.is_symlink():
                links[p.relative_to(dest).as_posix()] = os.readlink(p)
    return links


# ── inventory ──────────────────────────────────────────────────────────────────

def git_states(root: Path, paths: list[str]) -> dict[str, str]:
    """tracked | modified | staged | untracked | ignored for every path (by git's view of the original)."""
    tops = sorted({p.split("/")[0] if p.count("/") == 0 else "/".join(p.split("/")[:2]) for p in paths})

    def ls(*args: str) -> set[str]:
        return {p for p in _git(root, *args, "-z", "--", *tops).split("\0") if p}

    tracked = ls("ls-files")
    modified = ls("ls-files", "-m")
    staged = {p for p in _git(root, "diff", "--cached", "--name-only", "-z", "--", *tops).split("\0") if p}
    untracked = ls("ls-files", "-o", "--exclude-standard")
    ignored = ls("ls-files", "-o", "-i", "--exclude-standard")
    out = {}
    for p in paths:
        out[p] = ("modified" if p in modified else "staged" if p in staged else "tracked" if p in tracked
                  else "untracked" if p in untracked else "ignored" if p in ignored else "")
    return out


def etl_sources() -> set[str]:
    """Files the layer of truth is imported from (src/etl/paper_folders.py, plus the cohort feed)."""
    from src.etl import paper_folders as pf
    return {b.source for b in pf.collect()} | {f"{D3}/cohort_paper_3.csv"}


def read_inventory(dest: Path) -> list[dict]:
    path = dest / INVENTORY
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_inventory(dest: Path, rows: list[dict]) -> None:
    with (dest / INVENTORY).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def build_inventory(dest: Path, root: Path = ROOT, imported: set[str] | None = None,
                    rules: tuple[Rule, ...] = RULES) -> list[dict]:
    """Classify every frozen file; keep status/decision of rows whose bytes did not change."""
    sums = read_sums(dest)
    links = frozen_links(dest)
    paths = sorted(set(sums) | set(links))
    prev = {r["path"]: r for r in read_inventory(dest)}
    states = git_states(root, paths)
    imported = etl_sources() if imported is None else imported
    rows = []
    for p in paths:
        idx, rule = classify(p, rules)
        kind = "symlink" if p in links else "file"
        disposition = rule.disposition if rule else "unclassified"
        row = {
            "path": p, "kind": kind,
            "bytes": 0 if kind == "symlink" else (dest / p).stat().st_size,
            "sha256": sums.get(p, ""),
            "git": states.get(p, ""),
            "imported": "yes" if p in imported else "",
            "disposition": disposition,
            "project": (rule.project or "") if rule else "",
            "destination": rule.destination(p) if rule else "",
            "rule": str(idx) if rule else "",
            "status": "frozen", "checked_at": "", "decision": "",
            "note": (f"-> {links[p]}; " if kind == "symlink" else "") + (rule.note if rule else ""),
        }
        old = prev.get(p)
        if old and old.get("sha256") == row["sha256"] and old.get("disposition") == disposition \
                and old.get("destination") == row["destination"]:
            row.update(status=old["status"], checked_at=old["checked_at"], decision=old.get("decision", ""))
        if disposition == "here" and row["status"] != "moved" and (root / row["destination"]).exists():
            row.update(status="moved", checked_at=_now())
        rows.append(row)
    return rows


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── verify and gate ────────────────────────────────────────────────────────────

def verify(dest: Path, root: Path = ROOT, roots: tuple[str, ...] = FREEZE_ROOTS) -> dict:
    """Re-hash the freeze, then compare the originals against it (drift = edited after the freeze)."""
    sums = read_sums(dest)
    corrupt = [p for p, d in sums.items() if not (dest / p).is_file() or sha256_file(dest / p) != d]
    current = dict(walk(root, roots))
    changed, deleted = [], []
    for p, d in sums.items():
        src = root / p
        if not src.exists():
            deleted.append(p)
        elif src.is_file() and sha256_file(src) != d:
            changed.append(p)
    links = frozen_links(dest)
    new = sorted(p for p in current if p not in sums and p not in links)
    return {"files": len(sums), "corrupt": corrupt, "changed": sorted(changed),
            "new": new, "deleted": sorted(deleted)}


def gate(rows: list[dict]) -> list[dict]:
    """Rows that still block deleting the originals."""
    return [r for r in rows if r["status"] not in DONE.get(r["disposition"], set())]


def summary(rows: list[dict]) -> str:
    by = Counter((r["disposition"], r["project"]) for r in rows)
    size = Counter()
    for r in rows:
        size[(r["disposition"], r["project"])] += int(r["bytes"] or 0)
    lines = [f"{'disposition':<13} {'project':<26} {'files':>6} {'MB':>9}"]
    for (d, p), n in sorted(by.items()):
        lines.append(f"{d:<13} {p or '-':<26} {n:>6} {size[(d, p)] / 1e6:>9.1f}")
    lines.append(f"{'total':<40} {len(rows):>6} {sum(size.values()) / 1e6:>9.1f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["freeze", "inventory", "verify", "gate"])
    ap.add_argument("--name", default=DEFAULT_NAME, help=f"freeze directory under data/frozen (default {DEFAULT_NAME})")
    args = ap.parse_args(argv)
    dest = FROZEN_DIR / args.name

    if args.command == "freeze":
        info = freeze(dest)
        print(f"frozen {info['files']} files ({info['bytes'] / 1e6:,.1f} MB) and {info['symlinks']} symlinks "
              f"into {dest.relative_to(ROOT)}")
        write_inventory(dest, build_inventory(dest))
        make_readonly(dest)
        print(summary(read_inventory(dest)))
        return 0
    if args.command == "inventory":
        rows = build_inventory(dest)
        write_inventory(dest, rows)
        print(summary(rows))
        unclassified = [r["path"] for r in rows if r["disposition"] == "unclassified"]
        for p in unclassified[:20]:
            print(f"unclassified: {p}")
        return 1 if unclassified else 0
    if args.command == "verify":
        rep = verify(dest)
        print(f"{rep['files']} frozen files; corrupt {len(rep['corrupt'])}; originals changed since the freeze "
              f"{len(rep['changed'])}, new {len(rep['new'])}, deleted {len(rep['deleted'])}")
        for key in ("corrupt", "changed", "new"):
            for p in rep[key][:30]:
                print(f"  {key}: {p}")
        return 1 if rep["corrupt"] else 0
    blockers = gate(read_inventory(dest))
    by = Counter((r["disposition"], r["status"]) for r in blockers)
    for (d, s), n in sorted(by.items()):
        print(f"blocked: {n:>5} {d} with status {s}")
    print("gate: PASS" if not blockers else f"gate: FAIL ({len(blockers)} files not accounted for)")
    return 0 if not blockers else 1


if __name__ == "__main__":
    sys.exit(main())
