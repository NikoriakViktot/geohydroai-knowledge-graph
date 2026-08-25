"""
Retrieval layer.
Executes multiple semantic queries and merges results,
deduplicating by chunk_id and grouping by source file.

Ranking: combined score = semantic_score * 0.85 + citation_boost * 0.15
  citation_boost = min(0.15, 0.15 * log1p(cited_by_count) / log1p(500))
"""
import json
import logging
import math
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from src.config import RETRIEVAL_QUERIES, RETRIEVAL_TOP_K
from src.embedding.embedder import Embedder
from src.vectorstore.chroma_store import VectorStore

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PAPER_JSON_DIR = _PROJECT_ROOT / "data" / "literature" / "paper_json"

# Citation scaling constant: a paper with 500 citations gets the full boost.
_CITATION_SCALE = 500.0
# Weight split: 85 % semantic distance, 15 % citation reputation.
_SEMANTIC_WEIGHT  = 0.85
_CITATION_WEIGHT  = 0.15


@lru_cache(maxsize=1)
def _citation_map() -> dict[str, int]:
    """Return {filename_stem: cited_by_count} from all paper_json files."""
    result: dict[str, int] = {}
    for f in _PAPER_JSON_DIR.glob("*.paper.json"):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            count = doc.get("metadata", {}).get("cited_by_count")
            if count is not None:
                result[f.stem] = int(count)
        except Exception:
            pass
    logger.debug("Citation map loaded: %d entries", len(result))
    return result


def _citation_boost(filename: str) -> float:
    """
    Log-normalised citation boost in [0, _CITATION_WEIGHT].

    Scales from 0 (uncited) to _CITATION_WEIGHT (≥ _CITATION_SCALE citations).
    filename is the chunk's source filename, matched by stem.
    """
    stem = Path(filename).stem
    # paper_json stems look like "abc.tei.paper" → strip further suffixes
    for suffix in (".tei.paper", ".paper", ".tei"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    cited = _citation_map().get(stem, 0)
    if not cited:
        return 0.0
    return min(_CITATION_WEIGHT,
               _CITATION_WEIGHT * math.log1p(cited) / math.log1p(_CITATION_SCALE))


def _rank_score(distance: float, filename: str) -> float:
    """Combined ranking score (higher = better)."""
    semantic_score = 1.0 - distance   # distance is cosine distance in [0, 2]
    return _SEMANTIC_WEIGHT * semantic_score + _citation_boost(filename)


class CandidatePaper(NamedTuple):
    """A ranked candidate paper from the retrieval layer."""
    filename:       str
    score:          float
    hits:           list[dict]
    evidence_count: int


class Retriever:
    def __init__(self, store: VectorStore, embedder: Embedder) -> None:
        self._store   = store
        self._embedder = embedder

    def retrieve_for_queries(
        self,
        queries: list[str] = RETRIEVAL_QUERIES,
        top_k: int = RETRIEVAL_TOP_K,
    ) -> dict[str, list[dict]]:
        """
        Run every query and return a mapping:
            filename → [deduplicated hit dicts]
        Hits are sorted by descending combined score (semantic + citation).
        """
        seen_ids: set[str] = set()
        by_file: dict[str, list[dict]] = defaultdict(list)

        for query in queries:
            logger.info("Querying: '%s'", query[:60])
            vec  = self._embedder.embed_query(query)
            hits = self._store.query(vec, top_k=top_k)

            for hit in hits:
                if hit["chunk_id"] in seen_ids:
                    continue
                seen_ids.add(hit["chunk_id"])
                hit["rank_score"] = _rank_score(hit["distance"], hit["filename"])
                by_file[hit["filename"]].append(hit)

        # sort each file's hits by descending combined score (best first)
        for fname in by_file:
            by_file[fname].sort(key=lambda h: h["rank_score"], reverse=True)

        total = sum(len(v) for v in by_file.values())
        logger.info(
            "Retrieved %d unique chunks across %d files",
            total, len(by_file),
        )
        return dict(by_file)

    def fetch_full_context(self) -> dict[str, list[dict]]:
        """
        Return ALL stored chunks for every file, each list sorted by page order.
        Used by SectionExtractor to reconstruct full document text per paper.
        """
        filenames = self._store.list_filenames()
        logger.info("Fetching full context for %d files …", len(filenames))
        by_file: dict[str, list[dict]] = {}
        for fn in filenames:
            chunks = self._store.get_by_filename(fn)
            if chunks:
                by_file[fn] = chunks
        total = sum(len(v) for v in by_file.values())
        logger.info("Full context: %d chunks across %d files", total, len(by_file))
        return by_file

    def retrieve_many_ranked(
        self,
        queries: list[str] = RETRIEVAL_QUERIES,
        top_k: int = RETRIEVAL_TOP_K,
        max_candidates: int = 50,
    ) -> list[CandidatePaper]:
        """
        Run all queries and return candidate papers sorted by relevance score.

        Score = 1 / (1 + best_distance) + 0.1 * evidence_count

        Citation boost is intentionally omitted here (the retriever has no
        access to the storage layer).  The pipeline adds it after PaperStore.load()
        returns the paper metadata.

        Parameters
        ----------
        queries        : semantic query strings
        top_k          : chunks per query
        max_candidates : cap on returned candidate papers

        Returns
        -------
        List of CandidatePaper, sorted descending by score (best first).
        """
        by_file = self.retrieve_for_queries(queries=queries, top_k=top_k)
        candidates = []
        for filename, hits in by_file.items():
            best_dist = min(h["distance"] for h in hits)
            score     = 1.0 / (1.0 + best_dist) + 0.1 * len(hits)
            candidates.append(CandidatePaper(
                filename       = filename,
                score          = round(score, 6),
                hits           = hits,
                evidence_count = len(hits),
            ))
        ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
        logger.info(
            "retrieve_many_ranked: %d candidates (top score=%.4f, max=%d)",
            len(ranked),
            ranked[0].score if ranked else 0.0,
            max_candidates,
        )
        return ranked[:max_candidates]

    def retrieve_for_file(
        self,
        filename: str,
        queries: list[str] = RETRIEVAL_QUERIES,
        top_k: int = RETRIEVAL_TOP_K,
    ) -> list[dict]:
        """Retrieve chunks restricted to a single source file."""
        seen_ids: set[str] = set()
        hits: list[dict] = []

        for query in queries:
            vec  = self._embedder.embed_query(query)
            raw  = self._store.query(
                vec, top_k=top_k,
                where={"filename": filename},
            )
            for h in raw:
                if h["chunk_id"] not in seen_ids:
                    seen_ids.add(h["chunk_id"])
                    h["rank_score"] = _rank_score(h["distance"], h["filename"])
                    hits.append(h)

        hits.sort(key=lambda h: h["rank_score"], reverse=True)
        return hits
