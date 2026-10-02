#!/usr/bin/env python3
"""Thesis-by-thesis literature audit of the Kakhovka inundation manuscript (Paper 3).

Runner shim: the code lives in ``tools/paper3_audit/`` and reuses the retrieval,
passage and adjudication machinery of ``src/paper_3``. Run from anywhere::

    .venv/bin/python3 tools/paper3_literature_audit.py prepare
    .venv/bin/python3 tools/paper3_literature_audit.py retrieve --pass A --limit 2
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.paper3_audit.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
