#!/usr/bin/env bash
# Start the GeoHydroAI Knowledge API on 127.0.0.1:8090 (reachable from the sibling WSL distro).
# Swagger: http://127.0.0.1:8090/docs · rules for agents: http://127.0.0.1:8090/v1/agent-rules
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d postgres >/dev/null
exec .venv/bin/uvicorn src.api.app:app --host 127.0.0.1 --port "${GHAI_API_PORT:-8090}" "$@"
