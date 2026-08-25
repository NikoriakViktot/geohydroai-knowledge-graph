"""
Vector store layer — ChromaDB (persistent, local).

Uses ChromaDB ≥ 0.4 API (PersistentClient).
Embeddings are supplied externally so the collection uses no_embedding function.

Two upsert paths:
  upsert()               — accepts old TextChunk (PDF page-window chunker)
  upsert_document_chunks() — accepts new DocumentChunk (LayoutAwareChunker)

Both are idempotent on chunk_id.  The DocumentChunk path is the canonical
production path; TextChunk is kept for backward compatibility.

query() and get_by_filename() return a unified hit dict that includes all
DocumentChunk coordinate fields when present, falling back to TextChunk
legacy fields when absent.
"""
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import chromadb
import numpy as np
from chromadb.config import Settings

from src.config import CHROMA_DIR, COLLECTION_NAME
from src.processing.chunker import TextChunk

if TYPE_CHECKING:
    from src.document.chunker import DocumentChunk

logger = logging.getLogger(__name__)

# ChromaDB stores metadata values as str | int | float | bool only
_META_FIELDS = ("filename", "page_start", "page_end", "chunk_index")


class VectorStore:
    """Thin wrapper around a ChromaDB persistent collection."""

    def __init__(
        self,
        persist_dir: Path = CHROMA_DIR,
        collection_name: str = COLLECTION_NAME,
    ) -> None:
        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        live_count = self._collection.count()
        logger.info(
            "VectorStore ready: collection=%s  existing_docs=%d",
            collection_name, live_count,
        )

        # Detect HNSW reload corruption (ChromaDB 1.5.9 bug)
        sentinel = persist_dir / ".index_verified"
        if sentinel.exists():
            try:
                verified = json.loads(sentinel.read_text(encoding="utf-8"))
                if live_count != verified.get("count"):
                    logger.warning(
                        "HNSW mismatch: live=%d verified=%s (as of %s) — "
                        "collection may be corrupt; consider re-running reindex_chromadb",
                        live_count, verified.get("count"), verified.get("verified_at"),
                    )
            except Exception:
                pass

    # ── write ────────────────────────────────────────────────────────────────

    def upsert(
        self,
        chunks: list[TextChunk],
        embeddings: np.ndarray,
        batch_size: int = 512,
    ) -> None:
        """
        Upsert chunks + embeddings.
        Idempotent: existing chunk_ids are overwritten.
        """
        n = len(chunks)
        assert embeddings.shape[0] == n, "chunks / embeddings length mismatch"

        logger.info("Upserting %d chunks into ChromaDB …", n)
        for start in range(0, n, batch_size):
            end = min(start + batch_size, n)
            batch_chunks = chunks[start:end]
            batch_vecs   = embeddings[start:end].tolist()

            self._collection.upsert(
                ids        = [c.chunk_id for c in batch_chunks],
                embeddings = batch_vecs,
                documents  = [c.text for c in batch_chunks],
                metadatas  = [_meta(c) for c in batch_chunks],
            )
            logger.debug("  Upserted batch [%d:%d]", start, end)

        logger.info("Upsert complete. Total in collection: %d", self._collection.count())

    def upsert_document_chunks(
        self,
        chunks: "list[DocumentChunk]",
        embeddings: np.ndarray,
        batch_size: int = 512,
    ) -> None:
        """
        Upsert layout-aware DocumentChunks with their embeddings.

        DocumentChunk carries section provenance, bounding boxes, figure
        grounding, and citation links — a strict superset of TextChunk metadata.
        Idempotent on chunk_id (SHA-1 of paper_id:key).
        """
        n = len(chunks)
        if n == 0:
            return
        assert embeddings.shape[0] == n, "chunks / embeddings length mismatch"

        logger.info("Upserting %d DocumentChunks into ChromaDB …", n)
        for start in range(0, n, batch_size):
            end  = min(start + batch_size, n)
            batch = chunks[start:end]
            vecs  = embeddings[start:end].tolist()

            self._collection.upsert(
                ids        = [c.chunk_id for c in batch],
                embeddings = vecs,
                documents  = [c.text for c in batch],
                metadatas  = [_document_chunk_meta(c) for c in batch],
            )
            logger.debug("  Upserted DocumentChunk batch [%d:%d]", start, end)

        logger.info("DocumentChunk upsert complete. Total: %d", self._collection.count())

    def clear(self) -> None:
        """Delete all documents from the collection."""
        self._client.delete_collection(self._collection.name)
        self._collection = self._client.get_or_create_collection(
            name=self._collection.name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.warning("Collection cleared.")

    # ── read ─────────────────────────────────────────────────────────────────

    def query(
        self,
        query_embedding: np.ndarray,
        top_k: int = 10,
        where: dict | None = None,
    ) -> list[dict]:
        """
        Return top-k nearest chunks as list of dicts.

        Core fields (always present):
            text, chunk_id, filename, page_start, page_end, distance

        DocumentChunk coordinate fields (present when indexed via
        upsert_document_chunks — absent / defaulted for legacy TextChunks):
            paper_id, chunk_type, section_title, section_level, section_n,
            page, bbox, near_figure, figure_id, near_table, table_id,
            citation_count
        """
        kwargs: dict = dict(
            query_embeddings=[query_embedding.tolist()],
            n_results=min(top_k, self._collection.count() or 1),
            include=["documents", "metadatas", "distances"],
        )
        if where:
            kwargs["where"] = where

        results = self._collection.query(**kwargs)

        hits = []
        for doc, meta, dist, cid in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
            results["ids"][0],
        ):
            hits.append(_hit_from_meta(doc, cid, dist, meta))
        return hits

    def count(self) -> int:
        return self._collection.count()

    def get_by_filename(self, filename: str) -> list[dict]:
        """
        Return ALL stored chunks for *filename*, sorted by page.
        Used by SectionExtractor to reconstruct full document text.
        """
        results = self._collection.get(
            where={"filename": filename},
            include=["documents", "metadatas"],
        )
        hits = []
        for doc, meta, cid in zip(
            results.get("documents") or [],
            results.get("metadatas") or [],
            results.get("ids")       or [],
        ):
            hits.append(_hit_from_meta(doc, cid, 0.0, meta))
        hits.sort(key=lambda h: (h["page_start"], h["chunk_id"]))
        return hits

    def list_filenames(self) -> list[str]:
        """Return sorted list of unique PDF filenames in the collection."""
        results = self._collection.get(include=["metadatas"])
        seen: set[str] = set()
        names: list[str] = []
        for meta in results.get("metadatas") or []:
            fn = meta.get("filename", "")
            if fn and fn not in seen:
                seen.add(fn)
                names.append(fn)
        return sorted(names)


# ── helpers ───────────────────────────────────────────────────────────────────

def _meta(chunk: TextChunk) -> dict:
    return {
        "filename":    chunk.filename,
        "page_start":  chunk.page_start,
        "page_end":    chunk.page_end,
        "chunk_index": chunk.chunk_index,
    }


def _document_chunk_meta(chunk: "DocumentChunk") -> dict:
    """Flatten DocumentChunk fields for ChromaDB (str | int | float | bool only)."""
    if chunk.bbox:
        bx, by, bw, bh = chunk.bbox
    else:
        bx = by = bw = bh = -1.0
    return {
        # backward-compat so query()'s filename/page_start/page_end always work
        "filename":       chunk.paper_id,
        "page_start":     chunk.page,
        "page_end":       chunk.page,
        # DocumentChunk fields
        "paper_id":       chunk.paper_id,
        "chunk_type":     chunk.chunk_type,
        "section_title":  chunk.section_title,
        "section_level":  chunk.section_level,
        "section_n":      chunk.section_n,
        "page":           chunk.page,
        "bbox_x":         bx,
        "bbox_y":         by,
        "bbox_w":         bw,
        "bbox_h":         bh,
        "near_figure":    chunk.near_figure,
        "figure_id":      chunk.figure_id or "",
        "near_table":     chunk.near_table,
        "table_id":       chunk.table_id or "",
        "citation_count": len(chunk.citations),
    }


def _bbox_from_meta(meta: dict) -> list[float] | None:
    """Reconstruct [x, y, w, h] from stored bbox_* fields; None if absent."""
    bx = meta.get("bbox_x", -1.0)
    if bx == -1.0 or bx is None:
        return None
    return [
        float(bx),
        float(meta.get("bbox_y", 0.0)),
        float(meta.get("bbox_w", 0.0)),
        float(meta.get("bbox_h", 0.0)),
    ]


def _hit_from_meta(text: str, chunk_id: str, distance: float, meta: dict) -> dict:
    """Build a unified hit dict that works for both TextChunk and DocumentChunk metadata."""
    page = meta.get("page", meta.get("page_start", 0))
    return {
        # core (always present)
        "text":          text,
        "chunk_id":      chunk_id,
        "filename":      meta.get("filename", ""),
        "page_start":    meta.get("page_start", page),
        "page_end":      meta.get("page_end",   page),
        "distance":      distance,
        # DocumentChunk coordinate fields (defaulted when absent)
        "paper_id":      meta.get("paper_id", meta.get("filename", "")),
        "chunk_type":    meta.get("chunk_type", ""),
        "section_title": meta.get("section_title", ""),
        "section_level": meta.get("section_level", 0),
        "section_n":     meta.get("section_n", ""),
        "page":          page,
        "bbox":          _bbox_from_meta(meta),
        "near_figure":   bool(meta.get("near_figure", False)),
        "figure_id":     meta.get("figure_id", "") or None,
        "near_table":    bool(meta.get("near_table", False)),
        "table_id":      meta.get("table_id", "") or None,
        "citation_count": meta.get("citation_count", 0),
    }
