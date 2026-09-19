import logging
from typing import List

import numpy as np
import ray

log = logging.getLogger(__name__)


# Opening the persistent ChromaDB collection loads the HNSW index into the
# calling process — measured at ~4.5 GB for flood_papers_768d (2026-09-18).
# Doing that inside every process_paper worker is what capped the pipeline at
# --workers 1.  This actor is the single writer: one PersistentClient per
# pipeline, workers ship (chunks, vectors) here instead of opening Chroma.
# max_concurrency=1 keeps writes serialised (ChromaDB is single-writer safe).
@ray.remote(max_concurrency=1, max_restarts=1)
class VectorStoreActor:
    """Singleton Ray actor owning the one ChromaDB PersistentClient."""

    def __init__(self) -> None:
        from src.vectorstore.chroma_store import VectorStore
        self._vs = VectorStore()
        log.info("VectorStoreActor ready: existing_docs=%d", self.count())

    def upsert_document_chunks(self, chunks: list, embeddings: List[List[float]]) -> int:
        """Upsert DocumentChunks; returns the number of chunks written."""
        if not chunks:
            return 0
        self._vs.upsert_document_chunks(
            chunks, np.asarray(embeddings, dtype=np.float32),
        )
        return len(chunks)

    def count(self) -> int:
        return self._vs._collection.count()
