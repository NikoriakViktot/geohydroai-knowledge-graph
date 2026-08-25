"""
Embedding layer.
Wraps sentence-transformers with batching and progress reporting.
"""
import logging
from typing import Sequence

import numpy as np
from sentence_transformers import SentenceTransformer

from src.config import EMBEDDING_BATCH_SIZE, EMBEDDING_DEVICE, EMBEDDING_MODEL
from src.processing.chunker import TextChunk

logger = logging.getLogger(__name__)


class Embedder:
    """Encodes text chunks using a local sentence-transformer model."""

    def __init__(
        self,
        model_name: str = EMBEDDING_MODEL,
        device: str = EMBEDDING_DEVICE,
    ) -> None:
        logger.info("Loading embedding model: %s (device=%s)", model_name, device)
        self.model = SentenceTransformer(model_name, device=device)
        self.model_name = model_name
        get_dim = getattr(
            self.model,
            "get_embedding_dimension",
            self.model.get_sentence_embedding_dimension,
        )
        self.dimension = get_dim()
        logger.info("Embedding dimension: %d", self.dimension)

    def embed_chunks(
        self,
        chunks: list[TextChunk],
        batch_size: int = EMBEDDING_BATCH_SIZE,
        show_progress: bool = True,
    ) -> np.ndarray:
        """
        Embed a list of TextChunks in batches.
        Returns float32 array of shape (N, D).
        """
        texts = [c.text for c in chunks]
        logger.info("Embedding %d chunks (batch=%d) …", len(texts), batch_size)

        vectors = self.model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=show_progress,
            normalize_embeddings=True,   # cosine-ready
            convert_to_numpy=True,
        )

        assert vectors.shape == (len(chunks), self.dimension), \
            f"Unexpected shape: {vectors.shape}"
        logger.info("Embedding complete. Shape: %s", vectors.shape)
        return vectors.astype(np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        """Embed a single query string. Returns 1-D float32 array."""
        vec = self.model.encode(
            [query],
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return vec[0].astype(np.float32)
