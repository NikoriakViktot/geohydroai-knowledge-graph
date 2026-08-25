"""Shared logging setup — call configure_logging() once at startup."""
import logging
import sys
from pathlib import Path


def configure_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
    ]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        datefmt=datefmt,
        handlers=handlers,
        force=True,
    )

    # quiet noisy third-party libs
    for noisy in ("sentence_transformers", "transformers", "chromadb", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
