"""Minimal BibTeX reading for docs/references.bib of the floodstate-eo repo.

The bib lives in another WSL distro; ``fetch_bib_via_wsl`` copies it once into
``_work/`` so the rest of the audit works from a local, hashed snapshot.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from pathlib import Path

from tools.paper3_audit.config import (FLOODSTATE_BIB, FLOODSTATE_DISTRO, FLOODSTATE_REPO,
                                       FLOODSTATE_TERMINOLOGY_TEST, WORK_DIR)

logger = logging.getLogger(__name__)

BIB_SNAPSHOT = "references.bib"
BIB_JSON = "bib_entries.json"
TERMINOLOGY_TEST_SNAPSHOT = "test_terminology_freeze.py"

_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.MULTILINE)


def wsl_cat(relpath: str, distro: str = FLOODSTATE_DISTRO, repo: str = FLOODSTATE_REPO,
            timeout: int = 60) -> str:
    """Read one file of the sibling repo through wsl.exe (the only route to it)."""
    cmd = ["wsl.exe", "-d", distro, "--cd", repo, "--", "bash", "-c", f"cat {relpath}"]
    out = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"wsl.exe failed for {relpath}: {out.stderr.decode(errors='replace')[:200]}")
    return out.stdout.decode("utf-8", errors="replace").replace("\r", "")


def snapshot_floodstate_files(work_dir: Path = WORK_DIR, reader=wsl_cat) -> dict:
    """Copy references.bib and the terminology test into _work; return their sha256s."""
    work_dir.mkdir(parents=True, exist_ok=True)
    result = {}
    for rel, name in ((FLOODSTATE_BIB, BIB_SNAPSHOT),
                      (FLOODSTATE_TERMINOLOGY_TEST, TERMINOLOGY_TEST_SNAPSHOT)):
        text = reader(rel)
        (work_dir / name).write_text(text, encoding="utf-8")
        result[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    entries = parse_bib((work_dir / BIB_SNAPSHOT).read_text(encoding="utf-8"))
    (work_dir / BIB_JSON).write_text(json.dumps(entries, indent=1, ensure_ascii=False), encoding="utf-8")
    result["n_bib_entries"] = len(entries)
    return result


# Parsing lives in src/services/bibtex.py (shared with the API); re-exported here.
from src.services.bibtex import parse_bib  # noqa: E402,F401
from src.services.bibtex import split_fields as _split_fields  # noqa: E402,F401


def load_bib_entries(work_dir: Path = WORK_DIR) -> dict[str, dict]:
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


def forbidden_phrases_from_test(work_dir: Path = WORK_DIR) -> list[str]:
    """The FORBIDDEN list of tests/test_terminology_freeze.py, read from the snapshot."""
    path = work_dir / TERMINOLOGY_TEST_SNAPSHOT
    if not path.exists():
        return []
    m = re.search(r"FORBIDDEN\s*=\s*\[(.*?)\]", path.read_text(encoding="utf-8"), re.DOTALL)
    if not m:
        return []
    return re.findall(r'"([^"]+)"', m.group(1))
