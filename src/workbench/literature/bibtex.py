"""The paper's .bib and terminology test, snapshotted into the work directory.

They live in the paper repository (another WSL distribution); ``snapshot_floodstate_files`` copies
them once so the rest of the run works from a local, hashed snapshot.
"""
# Moved from tools/paper3_audit/bibtex.py (P6, 2026-10-02); run paths come from config.py (cfg).
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from src.workbench.literature import config as cfg

logger = logging.getLogger(__name__)

BIB_SNAPSHOT = "references.bib"
BIB_JSON = "bib_entries.json"
TERMINOLOGY_TEST_SNAPSHOT = "test_terminology_freeze.py"

_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)


def wsl_cat(relpath: str, distro: str = None, repo: str = None, timeout: int = 60) -> str:
    """Read one file of the paper repository (src/workbench/remote.py: wsl.exe, or local)."""
    from src.workbench.remote import LocalRunner, RemoteRepo
    if distro is None:
        distro = cfg.FLOODSTATE_DISTRO
    if repo is None:
        repo = cfg.FLOODSTATE_REPO
    if not repo:
        raise RuntimeError("no paper repository configured")
    remote = RemoteRepo(distro, repo, runner=LocalRunner() if distro == "local" else None)
    data = remote.read(relpath)
    if data is None:
        raise RuntimeError(f"{relpath} not in {distro}:{repo}")
    return data.decode("utf-8", errors="replace").replace("\r", "")


def snapshot_floodstate_files(work_dir: Path = None, reader=wsl_cat) -> dict:
    """Copy references.bib and the terminology test into _work; return their sha256s."""
    if work_dir is None:
        work_dir = cfg.WORK_DIR
    work_dir.mkdir(parents=True, exist_ok=True)
    result = {}
    for rel, name in ((cfg.FLOODSTATE_BIB, BIB_SNAPSHOT),
                      (cfg.FLOODSTATE_TERMINOLOGY_TEST, TERMINOLOGY_TEST_SNAPSHOT)):
        if not rel:
            continue                     # a paper without a terminology test
        try:
            text = reader(rel)
        except RuntimeError:
            if name == BIB_SNAPSHOT:
                raise
            logger.warning("no %s in the paper repository: forbidden phrases come from the review rules", rel)
            continue
        (work_dir / name).write_text(text, encoding="utf-8")
        result[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    entries = parse_bib((work_dir / BIB_SNAPSHOT).read_text(encoding="utf-8"))
    (work_dir / BIB_JSON).write_text(json.dumps(entries, indent=1, ensure_ascii=False), encoding="utf-8")
    result["n_bib_entries"] = len(entries)
    return result


# Parsing lives in src/services/bibtex.py (shared with the API); re-exported here.
from src.services.bibtex import parse_bib  # noqa: E402,F401
from src.services.bibtex import split_fields as _split_fields  # noqa: E402,F401


def load_bib_entries(work_dir: Path = None) -> dict[str, dict]:
    if work_dir is None:
        work_dir = cfg.WORK_DIR
    path = work_dir / BIB_JSON
    if not path.exists():
        return {}
    return {e["key"]: e for e in json.loads(path.read_text(encoding="utf-8"))}


def bib_doi(entry: dict) -> str:
    f = entry.get("fields", {})
    doi = f.get("doi", "")
    if not doi:
        url = f.get("url", "")
        m = re.search(r"doi\.org/(10\.\S+)", url)
        doi = m.group(1) if m else ""
    return doi.strip().rstrip("}")


def forbidden_phrases_from_test(work_dir: Path = None) -> list[str]:
    """The FORBIDDEN list of tests/test_terminology_freeze.py, read from the snapshot."""
    if work_dir is None:
        work_dir = cfg.WORK_DIR
    path = work_dir / TERMINOLOGY_TEST_SNAPSHOT
    if not path.exists():
        return []
    m = re.search(r"FORBIDDEN\s*=\s*\[(.*?)\]", path.read_text(encoding="utf-8"), re.DOTALL)
    if not m:
        return []
    return re.findall(r'"([^"]+)"', m.group(1))
