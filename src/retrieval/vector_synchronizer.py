"""
vector_synchronizer.py — SODB chunks.parquet → ChromaDB synchronizer.

Reads chunks.parquet (which stores embeddings alongside text) and upserts
into the ChromaDB collection.  Idempotent: upsert by chunk_id.

Embeddings are stored in parquet at extraction time so re-embedding is never
needed at sync time.  Chunks without an embedding are skipped (they can be
re-embedded by re-running the chunk stage with an EmbeddingActor).

Usage::

    from src.retrieval.vector_synchronizer import VectorSynchronizer
    sync = VectorSynchronizer()
    stats = sync.sync_paper("2023_Smith", Path("data/sodb"))
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_SODB_ROOT    = Path("data/sodb")
_BATCH_SIZE   = 500          # ChromaDB upsert batch size
_COLLECTION   = "papers"     # ChromaDB collection name


class VectorSynchronizer:
    """
    Upsert chunks from SODB chunks.parquet into ChromaDB.

    Parameters
    ----------
    chroma_path  : path to ChromaDB persistent store (default: data/chroma)
    collection   : ChromaDB collection name
    batch_size   : rows per upsert call
    """

    def __init__(
        self,
        chroma_path: str | Path = "data/chroma",
        collection:  str = _COLLECTION,
        batch_size:  int = _BATCH_SIZE,
    ) -> None:
        self._chroma_path = str(chroma_path)
        self._collection  = collection
        self._batch_size  = batch_size
        self._client      = None   # lazy init

    # ── Public API ────────────────────────────────────────────────────────────

    def sync_paper(
        self,
        paper_id:  str,
        sodb_root: Path = _SODB_ROOT,
    ) -> dict[str, int]:
        """
        Upsert all chunks for paper_id into ChromaDB.

        Returns {"chunks_synced": N, "chunks_skipped": M}.
        Never raises — failures are logged and returned as 0.
        """
        path = Path(sodb_root) / paper_id / "chunks.parquet"
        if not path.exists():
            return {"chunks_synced": 0, "chunks_skipped": 0}

        try:
            import pyarrow.parquet as pq
            rows = pq.read_table(path).to_pylist()
            return self._upsert_rows(rows)
        except Exception as exc:
            log.warning("[VectorSynchronizer] sync_paper failed for %s: %s", paper_id, exc)
            return {"chunks_synced": 0, "chunks_skipped": 0}

    def sync_corpus(
        self,
        sodb_root:    Path = _SODB_ROOT,
        paper_filter: str | None = None,
    ) -> dict[str, int]:
        """Sync all papers. Returns aggregate counters."""
        totals = {"chunks_synced": 0, "chunks_skipped": 0}
        for paper_dir in sorted(Path(sodb_root).iterdir()):
            if not paper_dir.is_dir():
                continue
            if paper_filter and not paper_dir.name.startswith(paper_filter):
                continue
            counts = self.sync_paper(paper_dir.name, sodb_root)
            for k, v in counts.items():
                totals[k] = totals.get(k, 0) + v
        return totals

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_collection(self):
        if self._client is None:
            import chromadb
            self._client = chromadb.PersistentClient(path=self._chroma_path)
        return self._client.get_or_create_collection(self._collection)

    def _upsert_rows(self, rows: list[dict]) -> dict[str, int]:
        with_emb    = [r for r in rows if r.get("embedding")]
        without_emb = [r for r in rows if not r.get("embedding")]

        if without_emb:
            log.debug("[VectorSynchronizer] skipping %d chunks without embeddings",
                      len(without_emb))

        if not with_emb:
            return {"chunks_synced": 0, "chunks_skipped": len(without_emb)}

        col    = self._get_collection()
        synced = 0

        for i in range(0, len(with_emb), self._batch_size):
            batch = with_emb[i : i + self._batch_size]
            col.upsert(
                ids         = [r["chunk_id"] for r in batch],
                embeddings  = [r["embedding"] for r in batch],
                documents   = [r["text"]      for r in batch],
                metadatas   = [
                    {
                        "paper_id":      r["paper_id"],
                        "chunk_index":   r["chunk_index"],
                        "chunk_type":    r["chunk_type"],
                        "section_title": r.get("section_title") or "",
                        "page":          r.get("page") or 0,
                    }
                    for r in batch
                ],
            )
            synced += len(batch)

        log.info("[VectorSynchronizer] upserted %d chunks to ChromaDB", synced)
        return {"chunks_synced": synced, "chunks_skipped": len(without_emb)}
