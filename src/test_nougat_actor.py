# src/test_nougat_actor.py

from __future__ import annotations

import json
import time
from pathlib import Path

import ray

from src.actors.nougat_actor import NougatActor


PDF_DIR = Path("/home/niko/projects/knoweledg_graf/data/literature/pdf")

TEST_PDFS = [
    "1-s2.0-S0022169421010738-main.pdf",
    "032003_1.pdf",
]

OUTPUT_DIR = Path("debug/nougat_tests")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def print_summary(result: dict):
    print("\n" + "=" * 80)

    if result.get("error"):
        print(f"ERROR: {result['error']}")
        return

    summary = result["summary"]

    print(f"Paper ID:       {summary.get('paper_id')}")
    print(f"Parser:         {result.get('parser_kind')}")
    print(f"Sections:       {len(result.get('sections', []))}")
    print(f"Formulas:       {len(result.get('formulas', []))}")
    print(f"Tables:         {len(result.get('tables', []))}")

    md = result.get("markdown_text") or ""
    visual = result.get("visual_text") or ""

    print(f"Markdown chars: {len(md):,}")
    print(f"Visual chars:   {len(visual):,}")

    print("=" * 80)


def save_debug_outputs(result: dict, paper_id: str):
    out_dir = OUTPUT_DIR / paper_id
    out_dir.mkdir(exist_ok=True)

    with open(out_dir / "result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    md = result.get("markdown_text")
    if md:
        with open(out_dir / "nougat.md", "w", encoding="utf-8") as f:
            f.write(md)

    visual = result.get("visual_text")
    if visual:
        with open(out_dir / "visual.txt", "w", encoding="utf-8") as f:
            f.write(visual)

    formulas = result.get("formulas", [])
    if formulas:
        with open(out_dir / "formulas.json", "w", encoding="utf-8") as f:
            json.dump(formulas, f, indent=2, ensure_ascii=False)

    tables = result.get("tables", [])
    if tables:
        with open(out_dir / "tables.json", "w", encoding="utf-8") as f:
            json.dump(tables, f, indent=2, ensure_ascii=False)


def main():
    ray.init(ignore_reinit_error=True)

    print("\n[INIT] Starting NougatActor...\n")

    actor = NougatActor.remote()

    info = ray.get(actor.model_info.remote())

    print(json.dumps(info, indent=2, ensure_ascii=False))

    total_start = time.time()

    for pdf_name in TEST_PDFS:
        pdf_path = PDF_DIR / pdf_name

        if not pdf_path.exists():
            print(f"[SKIP] Missing: {pdf_path}")
            continue

        paper_id = pdf_path.stem

        print(f"\n[PARSE] {paper_id}")

        start = time.time()

        result = ray.get(
            actor.parse_pdf.remote(
                str(pdf_path),
                paper_id
            )
        )

        elapsed = time.time() - start

        print_summary(result)

        print(f"Elapsed: {elapsed:.2f}s")

        save_debug_outputs(result, paper_id)

    total_elapsed = time.time() - total_start

    print("\n" + "=" * 80)
    print(f"TOTAL ELAPSED: {total_elapsed:.2f}s")
    print("=" * 80)


if __name__ == "__main__":
    main()