"""Auth (X-API-Key → scopes), provenance and service settings for the API."""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from fastapi import Request, Security
from fastapi.security import APIKeyHeader
from starlette.concurrency import run_in_threadpool

from src.api.problems import Problem
from src.contracts.api import Provenance

API_VERSION = "1.0.0-dev"
ROOT = Path(__file__).resolve().parents[2]

#: The collection the API answers vector queries from (switched to v2 once verified).
from src.config import COLLECTION_NAME as COLLECTION  # noqa: E402  (one source of truth)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL_ID", "allenai/specter2_base@3447645e")
RETRIEVAL_RULES_VERSION = "1.2.0"


@dataclass(frozen=True)
class Principal:
    consumer: str
    scopes: frozenset[str]
    key_id: str


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class KeyStore(Protocol):
    def lookup(self, key_hash: str) -> Principal | None: ...


class PostgresKeyStore:
    """ops.api_key lookups with a short cache (revocation takes effect within `ttl` s)."""

    def __init__(self, ttl: float = 60.0):
        self.ttl = ttl
        self._cache: dict[str, tuple[float, Principal | None]] = {}

    def lookup(self, key_hash: str) -> Principal | None:
        now = time.monotonic()
        hit = self._cache.get(key_hash)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        from sqlalchemy import select

        from src.db.engine import session_scope
        from src.db.models import ApiKey

        with session_scope() as s:
            row = s.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None)))
            principal = Principal(row.consumer, frozenset(row.scopes), str(row.key_id)) if row else None
        self._cache[key_hash] = (now, principal)
        return principal


#: Declared in OpenAPI, so Swagger UI (/docs) shows "Authorize" and sends the header.
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False,
                              description="Your consumer key (python -m src.api.keys create …). Never share it.")


def require_scope(scope: str):
    """Dependency: the request must carry an X-API-Key with `scope`."""

    async def dependency(request: Request, api_key: str | None = Security(API_KEY_HEADER)) -> Principal:
        raw = api_key or request.headers.get("x-api-key")
        if not raw:
            raise Problem("UNAUTHENTICATED", "Send your consumer key in the X-API-Key header.")
        store: KeyStore = request.app.state.key_store
        try:
            principal = await run_in_threadpool(store.lookup, hash_key(raw))
        except Exception as exc:  # the key store lives in Postgres
            raise Problem("STORE_UNAVAILABLE", f"key store unreachable: {type(exc).__name__}",
                          headers={"Retry-After": "30"}) from exc
        if principal is None:
            raise Problem("UNAUTHENTICATED", "Unknown or revoked API key.")
        if scope not in principal.scopes:
            raise Problem("FORBIDDEN_SCOPE", f"This endpoint needs scope '{scope}'; "
                                             f"key '{principal.consumer}' has {sorted(principal.scopes)}.")
        request.state.consumer = principal.consumer
        return principal

    return dependency


@lru_cache(maxsize=1)
def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


class ManifestCache:
    """corpus_manifest_id, recomputed when a new identity run appears (checked every 60 s)."""

    def __init__(self, ttl: float = 60.0):
        self.ttl = ttl
        self._at = 0.0
        self._value: dict | None = None

    def get(self) -> dict | None:
        if self._value is not None and time.monotonic() - self._at < self.ttl:
            return self._value
        from src.services import identity_store
        try:
            self._value = identity_store.corpus_manifest(COLLECTION, EMBEDDING_MODEL, RETRIEVAL_RULES_VERSION)
        except Exception:
            return self._value  # keep the last known manifest if Postgres blips
        self._at = time.monotonic()
        return self._value


def provenance(request: Request, **extra) -> Provenance:
    manifest = request.app.state.manifest.get() or {}
    return Provenance(api_version=API_VERSION, git_commit=git_commit(),
                      corpus_manifest_id=manifest.get("corpus_manifest_id"),
                      identity_run_id=manifest.get("identity_run_id"),
                      generated_at=datetime.now(timezone.utc), **extra)
