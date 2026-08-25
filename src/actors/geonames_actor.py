"""
geonames_actor.py — Ray actor wrapping GeoNames HTTP lookups.

Runs GeoNames lookups asynchronously alongside NER and embedding so they
no longer block the per-paper extraction loop.  Results are memoised for
the actor's lifetime (process-local LRU cache).

Usage (in a Ray task)::

    geo_actor  = GeoNamesActor.remote()
    geo_future = geo_actor.lookup_batch.remote(["Yangtze River", "China"])
    ...
    geo_results = ray.get(geo_future)   # list[dict | None]
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Optional

import ray
import requests

log = logging.getLogger(__name__)

_API_URL      = "http://api.geonames.org/searchJSON"
_REQUEST_TIMEOUT = 5       # seconds per request
_RATE_DELAY      = 1.0     # minimum seconds between API calls
_LRU_SIZE        = 4096    # names cached per actor lifetime


@ray.remote(max_restarts=1)
class GeoNamesActor:
    """
    Ray actor for rate-limited, cached GeoNames lookups.

    Rate limiting is actor-local (enforced via ``_last_call``).
    Each Ray worker runs its own actor instance, so the per-actor rate limit
    applies independently — callers should ensure they do not submit more
    actors than the GeoNames API rate allows.
    """

    def __init__(self) -> None:
        from src.config import settings
        self._username: str = settings.require_env("GEONAMES_USER")
        self._last_call: float = 0.0

    # ── Public methods ────────────────────────────────────────────────────────

    def lookup(self, name: str) -> Optional[dict]:
        """Look up a single place name. Returns None if not found or on error."""
        return self._lookup_cached(name)

    def lookup_batch(self, names: list[str]) -> list[Optional[dict]]:
        """Look up a list of place names. Preserves order."""
        return [self._lookup_cached(n) for n in names]

    # ── Internal ──────────────────────────────────────────────────────────────

    def _lookup_cached(self, name: str) -> Optional[dict]:
        if not name or not name.strip():
            return None
        return _cached_lookup(name.strip(), self)

    def _rate_limited_get(self, name: str) -> Optional[dict]:
        elapsed = time.monotonic() - self._last_call
        if elapsed < _RATE_DELAY:
            time.sleep(_RATE_DELAY - elapsed)
        self._last_call = time.monotonic()

        params = {"q": name, "maxRows": 5, "username": self._username}
        try:
            r = requests.get(_API_URL, params=params, timeout=_REQUEST_TIMEOUT)
            if r.status_code != 200:
                log.debug("[GeoNamesActor] HTTP %d for %r", r.status_code, name)
                return None
            geonames = r.json().get("geonames", [])
            if not geonames:
                return None
            best = next(
                (g for g in geonames if g.get("fcode", "").startswith(("P", "A", "H", "T"))),
                geonames[0],
            )
            lat, lon = best.get("lat"), best.get("lng")
            if lat is None or lon is None:
                return None
            return {
                "name":    best.get("name"),
                "lat":     float(lat),
                "lon":     float(lon),
                "type":    _fcode_type(best.get("fcode", "")),
                "country": best.get("countryName"),
                "feature": best.get("fcodeName"),
                "raw":     best,
            }
        except requests.exceptions.Timeout:
            log.debug("[GeoNamesActor] timeout for %r", name)
            return None
        except Exception as exc:
            log.debug("[GeoNamesActor] lookup error for %r: %s", name, exc)
            return None


# Module-level LRU so the cache survives across calls within the actor process.
# We can't put @lru_cache on a method, so we use a module-level wrapper.
@lru_cache(maxsize=_LRU_SIZE)
def _cached_lookup(name: str, actor: "GeoNamesActor") -> Optional[dict]:
    return actor._rate_limited_get(name)


def _fcode_type(fcode: str) -> str:
    """Map GeoNames feature code prefix to a simple type string."""
    mapping = {
        "A": "administrative",
        "H": "hydrological",
        "L": "area",
        "P": "populated_place",
        "R": "road",
        "S": "spot",
        "T": "terrain",
        "U": "undersea",
        "V": "vegetation",
    }
    return mapping.get(fcode[:1].upper(), "unknown") if fcode else "unknown"
