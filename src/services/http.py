"""Shared HTTP layer for the registries (Crossref, OpenAlex, DataCite).

* polite headers (contact from OPEN_ALEX_EMAIL), retries that honour Retry-After;
* biblio.http_cache: only a 200 body or a definitive 404/410 is stored. A timeout, 429 or
  5xx raises Upstream and is never stored, so a failed lookup is not remembered as
  "not found". When a refresh fails and an expired entry exists, that entry is returned
  with source="stale_cache";
* secrets (the OpenAlex api_key) go into the request only: never into the stored URL,
  the result, or an error message.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

TTL = {200: timedelta(days=180), 404: timedelta(days=30), 410: timedelta(days=30)}
MAX_RETRY_WAIT = 10.0


class Upstream(Exception):
    """The registry gave no definitive answer (timeout, connection error, 429, 5xx, other 4xx)."""


@dataclass(frozen=True)
class Fetched:
    status: int                 # 200, 404 or 410
    body: dict | None
    fetched_at: datetime
    source: str                 # "network" | "cache" | "stale_cache"
    url: str                    # without secrets


_CLIENT: httpx.Client | None = None


def client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(follow_redirects=True, timeout=15.0)
    return _CLIENT


def user_agent() -> str:
    email = os.getenv("OPEN_ALEX_EMAIL", "")
    return f"GeoHydroAI-KnowledgeAPI/1.0 (mailto:{email})" if email else "GeoHydroAI-KnowledgeAPI/1.0"


# ── cache (biblio.http_cache) ──────────────────────────────────────────────────

def cache_read(service: str, key: str) -> tuple[int, dict | None, datetime, datetime, str] | None:
    from src.db.engine import session_scope
    from src.db.models import HttpCache
    with session_scope() as s:
        row = s.get(HttpCache, (service, key))
        if row is None:
            return None
        return row.status, row.response, row.fetched_at, row.expires_at, row.url


def cache_write(service: str, key: str, url: str, status: int, body: dict | None, fetched_at: datetime) -> None:
    from sqlalchemy.dialects.postgresql import insert

    from src.db.engine import session_scope
    from src.db.models import HttpCache
    values = dict(service=service, key=key, url=url, status=status, response=body, fetched_at=fetched_at,
                  expires_at=fetched_at + TTL[status])
    stmt = insert(HttpCache).values(**values)
    with session_scope() as s:
        s.execute(stmt.on_conflict_do_update(index_elements=["service", "key"],
                                             set_={k: stmt.excluded[k] for k in values if k not in ("service", "key")}))


# ── fetching ───────────────────────────────────────────────────────────────────

def _fetch(url: str, params: dict, secret: dict, timeout: float, retries: int) -> tuple[int, dict | None]:
    last = "no attempt"
    for attempt in range(retries + 1):
        wait = min(2.0 ** attempt, MAX_RETRY_WAIT)
        try:
            r = client().get(url, params={**params, **secret}, timeout=timeout,
                             headers={"User-Agent": user_agent(), "Accept": "application/json"})
        except httpx.TimeoutException:
            last = "timeout"
        except httpx.TransportError as exc:
            last = f"connection error ({type(exc).__name__})"
        else:
            if r.status_code == 200:
                try:
                    return 200, r.json()
                except ValueError:
                    raise Upstream("HTTP 200 with a body that is not JSON") from None
            if r.status_code in (404, 410):
                return r.status_code, None
            if r.status_code != 429 and r.status_code < 500:
                raise Upstream(f"HTTP {r.status_code}")
            last = f"HTTP {r.status_code}"
            retry_after = r.headers.get("Retry-After", "")
            if retry_after.isdigit():
                wait = min(float(retry_after), MAX_RETRY_WAIT)
        if attempt < retries:
            time.sleep(wait)
    raise Upstream(last)


def get_json(service: str, key: str, url: str, *, params: dict | None = None, secret: dict | None = None,
             timeout: float = 15.0, retries: int = 2, refresh: bool = False) -> Fetched:
    """A registry answer for (service, key), from the cache while fresh."""
    params = params or {}
    public_url = f"{url}?{urlencode(params)}" if params else url
    cached = cache_read(service, key)
    now = datetime.now(timezone.utc)
    if cached and not refresh and cached[3] > now:
        return Fetched(cached[0], cached[1], cached[2], "cache", cached[4])
    try:
        status, body = _fetch(url, params, secret or {}, timeout, retries)
    except Upstream:
        if cached:
            return Fetched(cached[0], cached[1], cached[2], "stale_cache", cached[4])
        raise
    cache_write(service, key, public_url, status, body, now)
    return Fetched(status, body, now, "network", public_url)
