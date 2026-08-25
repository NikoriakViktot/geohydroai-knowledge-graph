"""
Flood-Paper RAG Pipeline — CLI entry point.

Usage
-----
  python main.py --ingest                     # index all PDFs in data/literature/
  python main.py --ingest --force             # re-index from scratch
  python main.py --query                      # extract to outputs/
  python main.py --ingest --query             # full run in one command
  python main.py --query --extractor ollama   # use local LLM (requires Ollama)
  python main.py --query --top-k 20           # widen retrieval
  python main.py --status                     # show store stats
"""
import argparse
import sys
from pathlib import Path

from src.config import DATA_DIR, LOG_FILE, LOG_LEVEL, OUTPUT_CSV
from src.utils.logging_config import configure_logging


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Local RAG pipeline for flood-mapping literature analysis.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    actions = p.add_argument_group("Actions (at least one required)")
    actions.add_argument("--ingest",  action="store_true", help="Index PDFs into ChromaDB")
    actions.add_argument("--query",   action="store_true", help="Run extraction + save CSV")
    actions.add_argument("--status",  action="store_true", help="Show vector store statistics")

    opts = p.add_argument_group("Options")
    opts.add_argument(
        "--folder",
        type=Path,
        default=DATA_DIR,
        help=f"PDF input folder (default: {DATA_DIR})",
    )
    opts.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_CSV,
        help=f"CSV output path (default: {OUTPUT_CSV})",
    )
    opts.add_argument(
        "--extractor",
        choices=["regex", "ollama", "section"],
        default="regex",
        help="Extraction backend (default: regex)",
    )
    opts.add_argument(
        "--top-k",
        type=int,
        default=12,
        dest="top_k",
        help="Chunks retrieved per query (default: 12)",
    )
    opts.add_argument(
        "--force",
        action="store_true",
        help="Clear existing vector store before ingesting",
    )
    opts.add_argument(
        "--log-level",
        default=LOG_LEVEL,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args   = parser.parse_args(argv)

    if not (args.ingest or args.query or args.status):
        parser.print_help()
        return 1

    configure_logging(level=args.log_level, log_file=LOG_FILE)

    import logging
    log = logging.getLogger("main")

    # lazy import to keep startup fast when just printing --help
    from src.pipeline.rag_pipeline import RAGPipeline

    pipeline = RAGPipeline(
        extractor_type=args.extractor,
        data_dir=args.folder,
        output_csv=args.output,
    )

    exit_code = 0

    if args.status:
        from src.vectorstore.chroma_store import VectorStore
        store = VectorStore()
        log.info("Vector store: %d chunks indexed", store.count())

    if args.ingest:
        n = pipeline.ingest(folder=args.folder, force=args.force)
        if n == 0:
            log.error("Ingest produced 0 chunks — check your PDF folder.")
            exit_code = 1

    if args.query:
        try:
            df = pipeline.query(top_k=args.top_k, save_csv=True)
            print("\n" + "=" * 70)
            print(f"Extracted {len(df)} papers  →  {args.output}")
            print("=" * 70)
            existing = [c for c in _PREVIEW_COLS if c in df.columns]
            print(df[existing].to_string(index=False, max_rows=25))
        except RuntimeError as exc:
            log.error(str(exc))
            exit_code = 1

    return exit_code


# ── Preview columns shown after --query ───────────────────────────────────────

_PREVIEW_COLS = [
    "Authors", "Year", "Study_Type", "Satellite_Names", "Sensor_Type",
    "Country", "Methods", "Near_Real_Time", "OA", "F1", "Confidence",
]


if __name__ == "__main__":
    sys.exit(main())
