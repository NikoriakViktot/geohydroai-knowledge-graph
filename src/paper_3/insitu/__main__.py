"""CLI: python -m src.paper_3.insitu [--pdf-dir DIR] [--out DIR] [--table NAME …] [--force]

First run copies the two PDFs into data/paper_3_audit/insitu/source/ and freezes
their sha256 in the manifest; later runs default to those copies, so the
extraction does not depend on the F: drive being mounted.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from src.paper_3.insitu import manifest as manifest_mod
from src.paper_3.insitu import publish
from src.paper_3.insitu.pages import PageCache
from src.paper_3.insitu.sources import DEFAULT_OUT, SOURCE_DIR, copy_sources, find_pdfs
from src.paper_3.insitu.tables import (Extracted, daily_levels, descriptions,
                                       events, frequency, gaps, narrative,
                                       sea_levels, stations)

logger = logging.getLogger(__name__)

#: Order matters for `narrative` (checks against the daily table) and `gaps`
#: (reads the others' footnotes).
EXTRACTORS = ("stations", "descriptions", "daily_levels", "sea_levels",
              "frequency", "events", "narrative", "gaps")


def run(pdf_dir: Path | None = None, out_dir: Path | None = None,
        tables: tuple[str, ...] = EXTRACTORS, force: bool = False,
        source_dir: Path | None = None) -> dict:
    out = Path(out_dir) if out_dir else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    src_dir = Path(source_dir) if source_dir else SOURCE_DIR

    if pdf_dir:
        pdfs = copy_sources(find_pdfs(Path(pdf_dir)), src_dir)
    else:
        pdfs = find_pdfs(src_dir)
    sources = manifest_mod.source_records(pdfs)
    manifest_mod.assert_sources_unchanged(out, sources, force=force)

    pc = PageCache(pdfs)
    results: dict[str, Extracted] = {}
    footnotes: list[dict] = []
    for name in EXTRACTORS:
        if name not in tables:
            continue
        logger.info("=== insitu: %s ===", name)
        if name == "gaps":
            got = gaps.run(pc, footnotes=footnotes,
                           station_rows=results.get("stations", Extracted("x")).rows)
        elif name == "narrative":
            got = narrative.run(
                pc, daily_rows=results.get("daily_levels_2023", Extracted("x")).rows)
        else:
            module = {"stations": stations, "descriptions": descriptions,
                      "daily_levels": daily_levels, "sea_levels": sea_levels,
                      "frequency": frequency, "events": events}[name]
            got = module.run(pc)
        for out_name, ex in got.items():
            results[out_name] = ex
            footnotes.extend(ex.footnotes)
            for a in ex.anomalies:
                logger.warning("  anomaly: %s", a)
            for cname, c in ex.checks.items():
                (logger.info if c["passed"] else logger.error)(
                    "  [%s] %s — %s", "PASS" if c["passed"] else "FAIL", cname, c["detail"])

    # The station list prints no codes; the descriptions do. Carry code and
    # type onto the registry so one file answers «which station is 80805?».
    if "stations" in results and "station_descriptions" in results:
        by_post = {}
        for d in results["station_descriptions"].rows:
            by_post.setdefault((d["post_name"], d["source_volume"]), d)
            by_post.setdefault((d["post_name"], "*"), d)
        for st in results["stations"].rows:
            d = by_post.get((st["post_name"], st["source_volume"])) or \
                by_post.get((st["post_name"], "*"))
            st["code"] = d["code"] if d else ""
            st["post_type"] = st.get("post_type") or (d["post_type"] if d else "")

    written: dict[str, int] = {}
    for name, ex in results.items():
        frame = pd.DataFrame(ex.rows)
        frame.to_csv(out / f"{name}.csv", index=False)
        written[name] = len(frame)
        logger.info("  %-28s %5d rows → %s.csv", name, len(frame), name)

    manifest = manifest_mod.build(out, sources, results, written)
    manifest_mod.write(out, manifest)
    publish.write(out, manifest, results)
    logger.info("Manifest: %d checks, %d failed → %s", len(manifest["checks"]),
                manifest["n_checks_failed"], out / manifest_mod.MANIFEST)
    return manifest


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description="Extract 2023 estuary gauge data from the "
                                             "hydromet yearbooks")
    ap.add_argument("--pdf-dir", default=None,
                    help="directory holding the two Інв_№229/№230 PDFs (copied in on first run)")
    ap.add_argument("--out", default=None, help=f"output dir (default {DEFAULT_OUT})")
    ap.add_argument("--table", action="append", choices=EXTRACTORS,
                    help="run only these extractors")
    ap.add_argument("--force", action="store_true",
                    help="overwrite outputs even if the source PDFs changed")
    args = ap.parse_args(argv)
    try:
        manifest = run(Path(args.pdf_dir) if args.pdf_dir else None,
                       Path(args.out) if args.out else None,
                       tuple(args.table) if args.table else EXTRACTORS,
                       force=args.force)
    except (FileNotFoundError, RuntimeError) as exc:
        logger.error("%s", exc)
        return 1
    return 0 if manifest["n_checks_failed"] == 0 else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
