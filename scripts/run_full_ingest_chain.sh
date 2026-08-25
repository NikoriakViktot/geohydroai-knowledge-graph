#!/usr/bin/env bash
# Full ingest chain: wait for download → GROBID → Nougat → Pipeline
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON=".venv/bin/python3"
LOG_DIR="data/ingestion_logs"
mkdir -p "$LOG_DIR"
CHAIN_LOG="$LOG_DIR/ingest_chain_$(date +%Y%m%d_%H%M%S).log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$CHAIN_LOG"; }

# ── 1. Wait for download_missing_papers.py to finish ────────────────────────
log "Waiting for download_missing_papers.py to finish..."
until ! pgrep -f "download_missing_papers" > /dev/null 2>&1; do
    COUNT=$(ls data/literature/pdf_missing/*.pdf 2>/dev/null | wc -l)
    log "  download still running... PDFs so far: $COUNT"
    sleep 30
done

TOTAL_PDF=$(ls data/literature/pdf_missing/*.pdf 2>/dev/null | wc -l)
log "Download finished. Total PDFs in pdf_missing/: $TOTAL_PDF"

# ── 2. GROBID on all new PDFs (skips existing XMLs) ─────────────────────────
log "============================================================"
log "STEP 1/3 — GROBID"
log "============================================================"
$PYTHON scripts/ingest_missing_pdfs.py --grobid-only 2>&1 | tee -a "$CHAIN_LOG"

TOTAL_XML=$(ls data/literature/grobid_xml/*.tei.xml 2>/dev/null | wc -l)
log "GROBID done. Total XMLs: $TOTAL_XML"

# ── 3. Nougat Region Pipeline on new PDFs ──────────────────────────────────
log "============================================================"
log "STEP 2/3 — Nougat Region Pipeline"
log "============================================================"
TOKENIZERS_PARALLELISM=false NOUGAT_GPU_FRACTION=1.0 \
    $PYTHON -m src.ingestion.nougat_region_pipeline --workers 3 \
    2>&1 | tee -a "$CHAIN_LOG"

log "Nougat done."

# ── 4. Ray Pipeline (entity extraction, embeddings, LLM judge) ──────────────
log "============================================================"
log "STEP 3/3 — Ray Pipeline (entity extraction + embeddings)"
log "============================================================"
TOKENIZERS_PARALLELISM=false \
RAYON_NUM_THREADS=1 \
OMP_NUM_THREADS=2 \
OLLAMA_MODEL=mistral-nemo:12b \
OLLAMA_URL=http://localhost:11434 \
SPACY_MODEL=en_core_web_sm \
    $PYTHON -m src.orchestration.pipeline_runner --workers 3 \
    2>&1 | tee -a "$CHAIN_LOG"

log "============================================================"
log "CHAIN COMPLETE"
log "============================================================"
TOTAL_JSON=$(ls data/literature/paper_json/*.paper.json 2>/dev/null | wc -l)
log "  paper_json count: $TOTAL_JSON"
log "  Full log: $CHAIN_LOG"
