"""run_manifest.json — what was searched, with which rules, over how many papers."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from tools.paper3_audit.config import MANIFEST_JSON, OUT_DIR, REPO_ROOT


def _git() -> dict:
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                                  text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--short"))}


def load(path: Path = MANIFEST_JSON) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def update(path: Path = MANIFEST_JSON, **fields) -> dict:
    """Merge `fields` into the manifest; nested dicts under 'routes'/'steps' are merged one level."""
    manifest = load(path)
    for key, value in fields.items():
        if isinstance(value, dict) and isinstance(manifest.get(key), dict):
            manifest[key].update(value)
        else:
            manifest[key] = value
    manifest.setdefault("git", _git())
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8")
    return manifest
