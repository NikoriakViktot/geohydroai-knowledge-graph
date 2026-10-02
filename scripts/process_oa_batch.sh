#!/usr/bin/env bash
# Process a batch of newly acquired TEI files (scripts/acquire_oa_missing.py grobid) through the
# legacy pipeline and the projections. Usage: scripts/process_oa_batch.sh data/acquisition/oa_<date> [workers]
#
# The pipeline writes Chroma; the API holds the same Chroma directory open, and ChromaDB 1.5.9 must
# have one writer and a fresh reload afterwards, so the API is stopped for the pipeline stage and
# restarted (also on failure). GROBID is stopped first: it needs 5.3 GB.
set -euo pipefail
cd "$(dirname "$0")/.."
RUN=${1:?run directory, e.g. data/acquisition/oa_20261002}
WORKERS=${2:-2}
PY=.venv/bin/python
export TOKENIZERS_PARALLELISM=false RAYON_NUM_THREADS=1 OMP_NUM_THREADS=2
export OLLAMA_MODEL=${OLLAMA_MODEL:-mistral-nemo:12b} OLLAMA_URL=${OLLAMA_URL:-http://localhost:11434}
export SPACY_MODEL=${SPACY_MODEL:-en_core_web_sm}
set -a; [ -f .env ] && . ./.env; set +a
step() { echo; echo "== $(date '+%F %T') $*"; }

n=$(find "$RUN/xml_new" -name '*.tei.xml' | wc -l)
step "batch $RUN: $n TEI files"
[ "$n" -gt 0 ] || { echo "nothing to process"; exit 0; }

step "stop GROBID and the API"
docker stop grobid >/dev/null 2>&1 || true
systemctl --user stop ghai-api
trap 'systemctl --user start ghai-api; echo "API restarted"' EXIT

step "pipeline_runner (workers $WORKERS)"
$PY -m src.orchestration.pipeline_runner --xml-dir "$RUN/xml_new" --workers "$WORKERS"

step "Chroma check after reload"
$PY - <<'PY'
import chromadb
from chromadb.config import Settings
from src.services.search import chroma_dir, collection_name
c = chromadb.PersistentClient(path=str(chroma_dir()), settings=Settings(anonymized_telemetry=False))
print(collection_name(), "count", c.get_collection(collection_name()).count())
PY

systemctl --user start ghai-api
trap - EXIT

step "normalization"
$PY -m src.orchestration.normalization_runner --input-dir data/literature/paper_json --output-dir data/normalized
step "OpenAlex enrichment"
$PY -m src.orchestration.enrichment_runner
step "identity (Postgres)"
$PY -m src.etl.identity
step "parquet layer"
$PY -m src.enrichment.build_parquet_layer
step "Neo4j graph (MERGE, no wipe)"
$PY -m src.graph.build_graph --uri bolt://localhost:7687 --user neo4j --password "${NEO4J_PASSWORD:-python2024}" --identity postgres
step "NumericFact loader"
$PY -m src.graph.table_kg_loader
step "done"
