#!/usr/bin/env bash
# The paper workbench, from anywhere: `scripts/workbench.sh <project_id> <step> [options]`.
# From a paper repository in the other distribution, the launcher ~/.local/bin/ghai-workbench there
# calls this script through wsl.exe, so the build always runs here (docs/api/PAPER_WORKFLOW.md §11).
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
export TOKENIZERS_PARALLELISM=false
exec .venv/bin/python -m src.workbench "$@"
