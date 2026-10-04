#!/usr/bin/env bash
# Process a batch of newly acquired TEI files (scripts/acquire_oa_missing.py grobid) through the
# legacy pipeline and the projections. Usage: scripts/process_oa_batch.sh data/acquisition/oa_<date> [workers]
#
# The pipeline writes Chroma; the API holds the same Chroma directory open, and ChromaDB 1.5.9 must
# have one writer and a fresh reload afterwards, so the API is stopped for the pipeline stage and
# restarted (also on failure). GROBID is stopped first: it needs 5.3 GB. Nougat runs before the
# pipeline (which reads regions.parquet) while the API is still up.
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

step "stop GROBID (5.3 GB RAM; TensorFlow holds most of the GPU)"
docker stop grobid >/dev/null 2>&1 || true

step "Nougat regions (formulas, tables, figure text → data/sodb/<id>/regions.parquet; the API stays up)"
TOKENIZERS_PARALLELISM=false NOUGAT_GPU_FRACTION=1.0 \
  $PY -m src.ingestion.nougat_region_pipeline --pdf-dir data/literature/pdf_oa --workers "${NOUGAT_WORKERS:-3}" \
  || echo "Nougat failed; the pipeline continues on GROBID text alone"
$PY - <<'PY'
import glob, pandas as pd
n = empty = 0
for f in glob.glob("data/sodb/*/regions.parquet"):
    d = pd.read_parquet(f, columns=["source_parser", "nougat_text", "nougat_latex", "created_at"])
    if d.empty or str(d["created_at"].max()) < "2026-10-02":     # zero-row files: papers with no regions
        continue
    n += 1
    empty += int(((d["nougat_text"].fillna("") + d["nougat_latex"].fillna("")).str.len() == 0).all())
print(f"Nougat check: {n} papers with new regions, {empty} with every region empty")
PY

step "equation records (TEI + Nougat pages → equation_records.parquet, one PNG per equation)"
find "$RUN/xml_new" -name '*.tei.xml' -printf '%f\n' | sed 's/\.tei\.xml$//' > "$RUN/equation_papers.txt"
$PY -m src.ingestion.nougat_region_pipeline --equations-only --paper-list "$RUN/equation_papers.txt" \
  || echo "equation records failed; the pipeline continues"

step "stop the API (one Chroma writer)"
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
step "Equation loader (Equation → Parameter → Quantity; needs the Paper nodes above)"
$PY -m src.graph.equation_kg_loader --paper-list "$RUN/equation_papers.txt"
step "done"
