#!/usr/bin/env python3
"""Thesis-by-thesis literature audit of floodstate-eo Paper 3 — now a workbench step.

Runner shim kept for old command lines; it runs

    python -m src.workbench floodstate-eo:paper3 literature <command> [options]

which reads the paper's repository through its ghai.project.yaml and stages the deliverables in
data/workbench/floodstate-eo:paper3/ until ``deliver`` (docs/api/PAPER_WORKFLOW.md)::

    .venv/bin/python3 tools/paper3_literature_audit.py prepare
    .venv/bin/python3 tools/paper3_literature_audit.py retrieve --pass A --limit 2
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.workbench.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["floodstate-eo:paper3", "literature", *sys.argv[1:]]))
