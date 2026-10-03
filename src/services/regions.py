"""Visual regions of a paper (NougatRegionPipeline, data/sodb/<paper_id>/regions.parquet) and
browser links to a paper's PDF and to the PNG crops of its regions.

Links are signed /v1/files/<token> URLs (src/services/filelinks.py): a browser cannot send the
API key, so a person opens them directly; agents must not fetch them (AGENT_RULES R-DATA-3).
"""

from __future__ import annotations

import math
from pathlib import Path

from src.services import filelinks

ROOT = Path(__file__).resolve().parents[2]
SODB = ROOT / "data" / "sodb"
PDF_DIRS = tuple(ROOT / "data" / "literature" / d for d in ("pdf", "pdf_missing", "pdf_oa"))


def regions_path(paper_id: str) -> Path | None:
    if "/" in paper_id or paper_id.startswith("."):
        return None
    p = SODB / paper_id / "regions.parquet"
    return p if p.is_file() else None


def _clean(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def file_url(base: str, path: str | Path | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    p = p if p.is_absolute() else ROOT / p
    if not p.is_file():
        return None
    return f"{base}/v1/files/{filelinks.sign(filelinks.relative(p))}"


def regions(paper_id: str, base: str) -> list[dict] | None:
    """None when the paper has no regions.parquet (Nougat has not run on it)."""
    path = regions_path(paper_id)
    if path is None:
        return None
    import pandas as pd
    df = pd.read_parquet(path)
    out = []
    for r in df.to_dict("records"):
        bbox = [_clean(r.get(k)) for k in ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1")]
        out.append({"region_id": r["region_id"], "page": _clean(r.get("page")), "region_type": r.get("region_type") or "",
                    "bbox": None if any(b is None for b in bbox) else [float(b) for b in bbox],
                    "text": _clean(r.get("nougat_text")) or None, "latex": _clean(r.get("nougat_latex")) or None,
                    "source_parser": _clean(r.get("source_parser")),
                    "crop_url": file_url(base, _clean(r.get("crop_path")))})
    return out


def pdf_paths(paper_ids: list[str]) -> dict[str, str]:
    """paper_id → the PDF a person should open (canonical copy first, an existing file only)."""
    from src.services.locate import corpus_files
    out: dict[str, str] = {}
    for f in corpus_files(paper_ids):
        if f["kind"] == "pdf" and f["exists"] and f["paper_id"] not in out:
            out[f["paper_id"]] = f["path"]
    for pid in paper_ids:                       # not yet in core.paper_file (identity ETL not re-run)
        if pid not in out and "/" not in pid:
            hit = next((d / f"{pid}.pdf" for d in PDF_DIRS if (d / f"{pid}.pdf").is_file()), None)
            if hit:
                out[pid] = str(hit)
    return out
