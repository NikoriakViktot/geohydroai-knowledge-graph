#!/usr/bin/env bash
# Start the MVP app on 127.0.0.1:8502 (needs the API on 127.0.0.1:8090: scripts/ghai_api.sh).
# The API key is read from GHAI_API_KEY or ~/.config/ghai/env on the server side.
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/streamlit run apps/mvp/app.py --server.address 127.0.0.1 --server.port "${GHAI_MVP_PORT:-8502}" \
     --server.headless true --browser.gatherUsageStats false "$@"
