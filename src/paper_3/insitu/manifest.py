"""INSITU_MANIFEST.json — what was read, from which bytes, and what was checked."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.paper_3.insitu.sources import VOLUMES, git_short, sha256
from src.paper_3.insitu.tables import Extracted

MANIFEST = "INSITU_MANIFEST.json"


def load(out_dir: Path) -> dict:
    path = Path(out_dir) / MANIFEST
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def source_records(pdfs: dict[str, Path]) -> dict[str, dict]:
    return {vol_id: {"file": Path(p).name, "sha256": sha256(p),
                     "label": VOLUMES[vol_id].label,
                     "printed_title": VOLUMES[vol_id].printed_title}
            for vol_id, p in pdfs.items()}


def assert_sources_unchanged(out_dir: Path, current: dict[str, dict],
                             force: bool = False) -> None:
    """Refuse to overwrite outputs made from different bytes unless forced."""
    previous = load(out_dir).get("sources", {})
    if not previous or force:
        return
    changed = [v for v in current if previous.get(v, {}).get("sha256")
               and previous[v]["sha256"] != current[v]["sha256"]]
    if changed:
        raise RuntimeError(
            f"Source PDF changed since the last extraction: {changed}. The outputs "
            f"in {out_dir} were made from other bytes; pass --force to replace them "
            f"knowingly (the previous sources are kept in the manifest history).")


def build(out_dir: Path, sources: dict[str, dict], results: dict[str, Extracted],
          written: dict[str, int]) -> dict:
    previous = load(out_dir)
    history = previous.get("history", [])
    if previous and previous.get("sources") != sources:
        history = [*history, {k: v for k, v in previous.items() if k != "history"}]

    tables = {}
    checks = {}
    anomalies = {}
    for name, ex in results.items():
        tables[name] = {
            "csv": f"{name}.csv", "rows": written.get(name, len(ex.rows)),
            "pages": ex.pages, "rows_skipped_other_posts": ex.skipped_other_posts,
            "n_anomalies": len(ex.anomalies),
        }
        if name in ("daily_levels_2023", "sea_levels_2023"):
            tables[name].update({
                "n_flagged": sum(1 for r in ex.rows if r.get("flags")),
                "n_doubtful": sum(1 for r in ex.rows if "?" in (r.get("flags") or "")),
                "n_dam_breach_flagged": sum(1 for r in ex.rows if "/" in (r.get("flags") or "")),
                "n_undocumented_flags": sum(1 for r in ex.rows if r.get("undocumented_flags")),
                "posts": sorted({r["post_name"] for r in ex.rows}),
            })
        for cname, c in ex.checks.items():
            checks[f"{name}.{cname}"] = c
        if ex.anomalies:
            anomalies[name] = ex.anomalies

    return {
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git_short(),
        "year": 2023,
        "sources": sources,
        "tables": tables,
        "checks": checks,
        "n_checks_failed": sum(1 for c in checks.values() if not c["passed"]),
        "anomalies": anomalies,
        "history": history,
    }


def write(out_dir: Path, manifest: dict) -> Path:
    path = Path(out_dir) / MANIFEST
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8")
    return path
