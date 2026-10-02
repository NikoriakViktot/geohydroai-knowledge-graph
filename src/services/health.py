"""Dependency health checks for GET /health (short timeouts, run in parallel)."""

from __future__ import annotations

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def _timed(fn) -> dict:
    t0 = time.perf_counter()
    try:
        out = fn() or {}
        out.setdefault("status", "ok")
    except Exception as exc:
        out = {"status": "down", "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}
    out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def check_postgres() -> dict:
    from sqlalchemy import text

    from src.db.engine import get_engine
    with get_engine().connect() as c:
        c.execute(text("SELECT 1"))
    return {}


def check_neo4j() -> dict:
    from neo4j import GraphDatabase

    from src.config import settings
    with GraphDatabase.driver(settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.require_env("NEO4J_PASSWORD")),
                              connection_timeout=3) as d:
        d.verify_connectivity()
    return {}


def check_chroma() -> dict:
    """The collection is still served by a persistent directory (server mode is planned);
    report whether its catalogue is readable without loading the HNSW index."""
    from src.config import CHROMA_DIR
    chroma_dir = Path(os.getenv("CHROMA_DIR", str(CHROMA_DIR)))
    db = chroma_dir / "chroma.sqlite3"
    if not db.exists():
        return {"status": "down", "detail": f"{db} not found"}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    try:
        names = [r[0] for r in con.execute("SELECT name FROM collections")]
    finally:
        con.close()
    return {"detail": f"persistent dir {chroma_dir.name}; collections: {', '.join(names)}"}


def check_http(url: str, ok_detail: str = "") -> dict:
    r = httpx.get(url, timeout=2.0)
    r.raise_for_status()
    return {"detail": ok_detail} if ok_detail else {}


def check_gemini() -> dict:
    configured = bool(os.getenv("GEMINI_API_KEY"))
    return {"status": "ok" if configured else "disabled",
            "detail": "API key configured (no call made)" if configured else "GEMINI_API_KEY not set"}


def health() -> dict:
    checks = {
        "postgres": check_postgres,
        "neo4j": check_neo4j,
        "chroma": check_chroma,
        "grobid": lambda: check_http(os.getenv("GROBID_URL", "http://127.0.0.1:8070") + "/api/isalive"),
        "ollama": lambda: check_http(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434") + "/api/tags"),
        "gemini": check_gemini,
    }
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = {name: pool.submit(_timed, fn) for name, fn in checks.items()}
        deps = {name: f.result() for name, f in futures.items()}
    if deps["grobid"]["status"] == "down":
        deps["grobid"]["detail"] = "not running; ingest jobs start it on demand"
    core = ("postgres",)
    status = "down" if any(deps[c]["status"] == "down" for c in core) else (
        "degraded" if any(d["status"] == "down" for n, d in deps.items() if n != "grobid") else "ok")
    return {"status": status, "dependencies": deps}
