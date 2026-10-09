"""Catalog API: read-only cards from one snapshot directory (src/catalog/build.py).

Needs nothing but the snapshot: no Postgres, Chroma, Neo4j or models, so it runs on a small server.

    CATALOG_SNAPSHOT_DIR=data/exports/catalog_20261009_catalog-rules-v1 \\
    uvicorn src.catalog.api:app --host 127.0.0.1 --port 8095

Access: when CATALOG_API_KEY_HASHES is set (comma-separated sha256 hex of the keys), every /v1
request needs a matching X-API-Key header; /health stays open. Without it the API is open, which is
meant for a server reachable only over a private network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

API_VERSION = "1.0"
GUIDE_FILE = Path(__file__).resolve().parents[2] / "docs" / "catalog" / "AGENT_GUIDE.md"
_WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "") if len(w) > 1}


class Snapshot:
    """All cards in memory (≈ 5 600 cards, tens of MB) with a token index for ?q=."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.manifest = json.loads((self.dir / "manifest.json").read_text(encoding="utf-8"))
        cards_file = self.dir / "cards.jsonl"
        digest = hashlib.sha256(cards_file.read_bytes()).hexdigest()
        if digest != self.manifest["files"]["cards.jsonl"]:
            raise ValueError(f"{cards_file}: sha256 does not match manifest.json (damaged or edited snapshot)")
        self.cards: list[dict] = [json.loads(line) for line in cards_file.read_text(encoding="utf-8").splitlines()
                                  if line.strip()]
        self.by_id = {c["paper_id"]: c for c in self.cards}
        self._text = [_tokens(" ".join(self._search_fields(c))) for c in self.cards]

    @staticmethod
    def _search_fields(c: dict) -> list[str]:
        a = c.get("analysis") or {}
        names = [e["name"] for k in ("methods", "sensors", "data", "study_countries") for e in a.get(k) or []]
        return [c["title"], c.get("venue") or "", " ".join(c.get("authors") or []), " ".join(names),
                " ".join(t["name"] for t in c.get("topics") or [])]

    def release(self) -> dict:
        m = self.manifest
        return {"rules_version": m["rules_version"], "built_at": m["built_at"], "git_commit": m.get("git_commit"),
                "counts": m["counts"], "cards_sha256": m["files"]["cards.jsonl"]}


def _has(card: dict, kind: str, value: str) -> bool:
    v = value.lower()
    return any(v in (e.get("id") or "").lower() or v == (e.get("name") or "").lower()
               for e in ((card.get("analysis") or {}).get(kind) or []))


def _matches(card: dict, tokens: set[str], index: set[str], *, method, sensor, data, country, metric,
             year_from, year_to, verified_only, open_access_only) -> bool:
    if tokens and not tokens <= index:
        return False
    a = card.get("analysis") or {}
    if method and not _has(card, "methods", method):
        return False
    if sensor and not _has(card, "sensors", sensor):
        return False
    if data and not _has(card, "data", data):
        return False
    if country and not any(country.lower() == c["name"].lower() for c in a.get("study_countries") or []):
        return False
    if metric and not any(metric.lower() in (r["metric"].lower(), r["metric"].lower().removeprefix("metric."),
                                             r["metric_label"].lower()) for r in a.get("results") or []):
        return False
    y = card.get("year")
    if year_from and (y is None or y < year_from):
        return False
    if year_to and (y is None or y > year_to):
        return False
    if open_access_only and not card["links"].get("open_access"):
        return False
    if verified_only:
        statuses = [card["metadata_status"]] + [x["status"] for k in ("methods", "sensors", "data", "results",
                                                                       "study_countries") for x in a.get(k) or []]
        if "human_verified" not in statuses:
            return False
    return True


def _summary(card: dict) -> dict:
    """The list view: links and counts, without the analysis body."""
    a = card.get("analysis") or {}
    return {k: card[k] for k in ("paper_id", "title", "year", "venue", "authors", "cited_by_count", "links",
                                 "metadata_status", "retracted")} | {
        "methods": [e["name"] for e in a.get("methods") or []][:5],
        "sensors": [e["name"] for e in a.get("sensors") or []][:5],
        "study_countries": [c["name"] for c in a.get("study_countries") or []],
        "results": len(a.get("results") or []),
    }


def create_app(snapshot: Snapshot | None = None, key_hashes: set[str] | None = None) -> FastAPI:
    snap = snapshot or Snapshot(Path(os.environ["CATALOG_SNAPSHOT_DIR"]))
    if key_hashes is None:
        key_hashes = {h.strip().lower() for h in os.environ.get("CATALOG_API_KEY_HASHES", "").split(",") if h.strip()}

    def require_key(request: Request) -> None:
        if not key_hashes:
            return
        given = hashlib.sha256(request.headers.get("X-API-Key", "").encode()).hexdigest()
        if not any(hmac.compare_digest(given, h) for h in key_hashes):
            raise HTTPException(401, "missing or invalid X-API-Key")

    app = FastAPI(title="GeoHydroAI paper catalog", version=API_VERSION,
                  description="Links to papers and a short, quality-filtered analysis of each. "
                              "Every value carries a status: human_verified, model or source.")
    v1 = [Depends(require_key)]

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "cards": len(snap.cards), "rules_version": snap.manifest["rules_version"]}

    @app.get("/agent-guide", response_class=PlainTextResponse)
    def agent_guide() -> PlainTextResponse:
        """How an AI agent connects to and cites this API (docs/catalog/AGENT_GUIDE.md); open, like /health."""
        if not GUIDE_FILE.exists():
            raise HTTPException(404, "agent guide not installed")
        return PlainTextResponse(GUIDE_FILE.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")

    @app.get("/llms.txt", response_class=PlainTextResponse)
    def llms_txt() -> str:
        return ("# GeoHydroAI paper catalog\n\n> Links to papers and a short, quality-filtered analysis of each.\n\n"
                "- [Agent guide](/agent-guide): authentication, endpoints, card fields, citation rules\n"
                "- [OpenAPI](/openapi.json)\n")

    @app.get("/v1/release", dependencies=v1)
    def release() -> dict:
        """Which snapshot is served: rules version, build time, commit, counts, checksum."""
        return snap.release()

    @app.get("/v1/cards", dependencies=v1)
    def list_cards(
        q: Annotated[str | None, Query(description="words that must all occur in title, authors, venue, entities")] = None,
        method: str | None = None, sensor: str | None = None, data: str | None = None,
        country: str | None = None, metric: Annotated[str | None, Query(description="e.g. metric.nse or NSE")] = None,
        year_from: int | None = None, year_to: int | None = None,
        verified_only: Annotated[bool, Query(description="only cards with at least one human-verified value")] = False,
        open_access_only: bool = False,
        sort: Annotated[str, Query(pattern="^(cited|year|title)$")] = "cited",
        limit: Annotated[int, Query(ge=1, le=200)] = 20, offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict:
        tokens = _tokens(q or "")
        hits = [c for c, idx in zip(snap.cards, snap._text)
                if _matches(c, tokens, idx, method=method, sensor=sensor, data=data, country=country, metric=metric,
                            year_from=year_from, year_to=year_to, verified_only=verified_only,
                            open_access_only=open_access_only)]
        key = {"cited": lambda c: (-(c.get("cited_by_count") or 0), c["paper_id"]),
               "year": lambda c: (-(c.get("year") or 0), c["paper_id"]),
               "title": lambda c: (c["title"].lower(), c["paper_id"])}[sort]
        hits.sort(key=key)
        return {"total": len(hits), "limit": limit, "offset": offset,
                "items": [_summary(c) for c in hits[offset: offset + limit]]}

    @app.get("/v1/cards/{paper_id:path}", dependencies=v1)
    def get_card(paper_id: str) -> dict:
        card = snap.by_id.get(paper_id)
        if card is None:
            raise HTTPException(404, f"no card for {paper_id!r}")
        return card

    @app.get("/v1/facets", dependencies=v1)
    def facets(top: Annotated[int, Query(ge=1, le=500)] = 50) -> dict:
        """The most frequent values to filter by, with card counts."""
        out = {k: Counter() for k in ("methods", "sensors", "data", "study_countries", "metrics", "years")}
        for c in snap.cards:
            a = c.get("analysis") or {}
            for k in ("methods", "sensors", "data"):
                out[k].update({e["id"] for e in a.get(k) or []})
            out["study_countries"].update({x["name"] for x in a.get("study_countries") or []})
            out["metrics"].update({r["metric"] for r in a.get("results") or []})
            if c.get("year"):
                out["years"][c["year"]] += 1
        return {k: [{"value": v, "cards": n} for v, n in cnt.most_common(top)] for k, cnt in out.items()}

    return app


def __getattr__(name: str):
    """`uvicorn src.catalog.api:app` builds the app on first access (importing the module stays cheap)."""
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
