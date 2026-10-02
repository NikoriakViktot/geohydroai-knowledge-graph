"""
Re-exports all constants from the legacy flat src/config.py module.

The src/config/ package shadows src/config.py in Python's import system, so
any `from src.config import FOO` must be satisfied here.  All values are
sourced from the same env-var keys as the flat module so existing overrides
continue to work.
"""
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ── Paths ────────────────────────────────────────────────────────────────────
DATA_DIR    = _PROJECT_ROOT / "data" / "literature"
OUTPUTS_DIR = _PROJECT_ROOT / "outputs"
CHROMA_DIR  = Path(os.getenv("CHROMA_DIR", str(_PROJECT_ROOT / ".chromadb_v2")))  # v1 .chromadb kept for pinned audits

# ── Chunking ─────────────────────────────────────────────────────────────────
CHUNK_SIZE    = int(os.getenv("CHUNK_SIZE",    "2000"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "300"))
CHUNK_MIN     = int(os.getenv("CHUNK_MIN",     "120"))

# ── Embeddings ────────────────────────────────────────────────────────────────
EMBEDDING_MODEL      = os.getenv("EMBEDDING_MODEL", "allenai/specter2_base")  # 768-dim; matches flood_papers_768d_v2
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))
EMBEDDING_DEVICE     = os.getenv("EMBEDDING_DEVICE", "cpu")

# ── ChromaDB ─────────────────────────────────────────────────────────────────
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "flood_papers_768d_v2")  # 2026-10-02: unique chunk ids, fixed TEI text

# ── Retrieval ────────────────────────────────────────────────────────────────
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "12"))
RETRIEVAL_QUERIES = [
    "flood mapping satellite remote sensing sensor study area method",
    "SAR Sentinel-1 flood detection thresholding change detection backscatter",
    "Sentinel-2 Landsat optical flood mapping NDWI MNDWI water index",
    "near-real-time operational flood monitoring latency revisit time",
    "U-Net CNN deep learning flood segmentation classification",
    "hydrological hydrodynamic hydraulic model flood simulation HEC-RAS LISFLOOD",
    "flood extent mapping accuracy validation OA F1 IoU Kappa",
    "flood event study area country region river basin city",
]

# ── LLM (Ollama) ──────────────────────────────────────────────────────────────
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL",    "mistral-nemo:12b")
OLLAMA_TIMEOUT  = int(os.getenv("OLLAMA_TIMEOUT", "60"))

# ── Output ────────────────────────────────────────────────────────────────────
OUTPUT_CSV = OUTPUTS_DIR / "flood_papers_extracted.csv"

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE  = _PROJECT_ROOT / "rag_pipeline.log"
