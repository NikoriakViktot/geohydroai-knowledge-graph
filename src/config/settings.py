import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Ollama ────────────────────────────────────────────────────────────────────
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral-nemo:12b")
OLLAMA_URL   = os.getenv("OLLAMA_URL",   "http://localhost:11434")

# ── Auth ──────────────────────────────────────────────────────────────────────
HF_TOKEN = os.getenv("HF_TOKEN", "")


def require_env(name: str) -> str:
    """Return a required env-backed setting or fail fast with a clear message.

    Used for credentials that must never have an in-code default
    (NEO4J_PASSWORD, GEONAMES_USER, ...). See .env.example.
    """
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(
            f"Required environment variable {name} is not set. "
            f"Add it to your .env file (see .env.example)."
        )
    return value


# ── Neo4j ─────────────────────────────────────────────────────────────────────
# No in-code password default: consumers call require_env("NEO4J_PASSWORD")
# when no explicit password is provided.
NEO4J_URI  = os.getenv("NEO4J_URI",  "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")

# ── PostgreSQL layer of truth (API_PLAN_v1/11_POSTGRES_TRUTH_LAYER.md) ─────────
# Password only via require_env("GHAI_PG_PASSWORD"), like Neo4j.
GHAI_PG_HOST = os.getenv("GHAI_PG_HOST", "127.0.0.1")
GHAI_PG_PORT = int(os.getenv("GHAI_PG_PORT", "5433"))
GHAI_PG_DB   = os.getenv("GHAI_PG_DB",   "ghai")
GHAI_PG_USER = os.getenv("GHAI_PG_USER", "ghai")

# ── Model names ───────────────────────────────────────────────────────────────
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "allenai/specter2_base",  # 768-dim; matches active collection flood_papers_768d
)
SPACY_MODEL = os.getenv("SPACY_MODEL", "en_core_web_sm")

# ── HF cache ─────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
HF_CACHE_DIR  = os.getenv("HF_HOME", str(_PROJECT_ROOT / ".hf_cache"))

# ── Ingestion paths ───────────────────────────────────────────────────────────
_DEFAULT_LIT_DIR = _PROJECT_ROOT / "data" / "literature"
_DEFAULT_NORM_LIT_DIR = _PROJECT_ROOT / "data" / "normalized"

XML_DIR = Path(os.getenv("XML_DIR", str(_DEFAULT_LIT_DIR / "grobid_xml")))
OUT_DIR = Path(os.getenv("OUT_DIR", str(_DEFAULT_LIT_DIR / "paper_json")))
NORMALIZED_DIR = Path(os.getenv("NORMALIZED_DIR",str(_DEFAULT_NORM_LIT_DIR)))

# ── Geonames ─────────────────────────────────────────────────────────────────
GEONAMES_USER  = os.getenv("GEONAMES_USER",  "")   # required for geo lookups; no personal default
GEONAMES_DELAY = float(os.getenv("GEONAMES_DELAY", "1.0"))

# ── Scoring thresholds ────────────────────────────────────────────────────────
ENTITY_SCORE_THRESHOLD          = float(os.getenv("ENTITY_SCORE_THRESHOLD",          "0.3"))
GEO_CONFIDENCE_THRESHOLD        = float(os.getenv("GEO_CONFIDENCE_THRESHOLD",        "0.75"))
STUDY_TYPE_CONFIDENCE_THRESHOLD = float(os.getenv("STUDY_TYPE_CONFIDENCE_THRESHOLD", "0.85"))
JUDGE_TASK_CONFIDENCE_THRESHOLD = float(os.getenv("JUDGE_TASK_CONFIDENCE_THRESHOLD", "0.85"))

# ── Ray ───────────────────────────────────────────────────────────────────────
RAY_MAX_CONCURRENT = int(os.getenv("RAY_MAX_CONCURRENT", "4"))


OPEN_ALEX_API = os.getenv(
    "OPEN_ALEX_API"
)

OPEN_ALEX_EMAIL = os.getenv("OPEN_ALEX_EMAIL", "")  # polite-pool contact; no personal default

# ── Stage 2 enrichment paths ──────────────────────────────────────────────────
ENRICHED_DIR           = Path(os.getenv("ENRICHED_DIR",           str(_PROJECT_ROOT / "data" / "enriched")))
CACHE_DIR              = Path(os.getenv("CACHE_DIR",              str(_PROJECT_ROOT / "data" / "cache")))
REFERENCE_ENRICHED_DIR = Path(os.getenv("REFERENCE_ENRICHED_DIR", str(_PROJECT_ROOT / "data" / "reference_enriched")))

# ── Stage 5 analytics layer ───────────────────────────────────────────────────
PARQUET_DIR = Path(os.getenv("PARQUET_DIR", str(_PROJECT_ROOT / "data" / "parquet")))

# ── Input PDFs ───────────────────────────────────────────────────────────────
PDF_DIR = Path(os.getenv("PDF_DIR", str(_DEFAULT_LIT_DIR / "pdf")))

# ── Stage 0 — raw immutable artifacts ────────────────────────────────────────
RAW_DIR = Path(os.getenv("RAW_DIR", str(_PROJECT_ROOT / "data" / "raw")))

# ── Stage 1 — structural parsed artifacts ────────────────────────────────────
PARSED_DIR = Path(os.getenv("PARSED_DIR", str(_PROJECT_ROOT / "data" / "parsed")))

# ── Stage 2 / SODB layer ─────────────────────────────────────────────────────
SODB_DIR = Path(os.getenv("SODB_DIR", str(_PROJECT_ROOT / "data" / "sodb")))

# ── Pipeline registry (DuckDB state store) ────────────────────────────────────
REGISTRY_DIR     = Path(os.getenv("REGISTRY_DIR", str(_PROJECT_ROOT / "data" / "registry")))
PIPELINE_VERSION = os.getenv("PIPELINE_VERSION", "2.0.0")

# ── Parser routing ────────────────────────────────────────────────────────────
# Strategies: auto | grobid_only | nougat_only | hybrid | grobid_with_nougat_fallback
PARSER_STRATEGY = os.getenv("PARSER_STRATEGY", "grobid_with_nougat_fallback")

# ── Nougat visual parser ──────────────────────────────────────────────────────
NOUGAT_ENABLED        = os.getenv("NOUGAT_ENABLED",        "true").lower() not in {"0", "false", "no"}
NOUGAT_MODEL          = os.getenv("NOUGAT_MODEL",          "facebook/nougat-base")
NOUGAT_MAX_PAGES      = int(os.getenv("NOUGAT_MAX_PAGES",      "20"))
NOUGAT_MAX_NEW_TOKENS = int(os.getenv("NOUGAT_MAX_NEW_TOKENS",  "4096"))
NOUGAT_DEVICE         = os.getenv("NOUGAT_DEVICE",         "auto")   # cuda | cpu | auto

# ── Hybrid merge tuning ───────────────────────────────────────────────────────
HYBRID_AUGMENT_FORMULAS    = os.getenv("HYBRID_AUGMENT_FORMULAS",    "true").lower() != "false"
HYBRID_AUGMENT_TABLES      = os.getenv("HYBRID_AUGMENT_TABLES",      "true").lower() != "false"
HYBRID_AUGMENT_VISUAL_TEXT = os.getenv("HYBRID_AUGMENT_VISUAL_TEXT", "true").lower() != "false"

# ── Gemini AI ─────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")