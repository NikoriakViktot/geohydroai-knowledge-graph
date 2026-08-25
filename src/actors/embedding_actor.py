import logging
import os
from typing import List

import ray
from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)

# SPECTER2 is an Allen AI model trained on 146M scientific paper citations.
# It outperforms general-purpose models by 10-20pp on scientific similarity.
# Fall back to MiniLM if SPECTER2 is unavailable (e.g. first-run no internet).
_PREFERRED_MODEL = os.getenv("EMBEDDING_MODEL", "allenai/specter2_base")
_FALLBACK_MODEL  = "sentence-transformers/all-MiniLM-L6-v2"
_BATCH_SIZE      = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))


@ray.remote(max_concurrency=4, max_restarts=1)
class EmbeddingActor:
    """
    Singleton Ray actor wrapping a sentence-transformer for scientific text.

    Model: SPECTER2 (allenai/specter2_base) — trained on scientific citations.
    Falls back to all-MiniLM-L6-v2 if preferred model cannot be loaded.
    """

    def __init__(self) -> None:
        try:
            self.model = SentenceTransformer(_PREFERRED_MODEL)
            self.model_name = _PREFERRED_MODEL
        except Exception as exc:
            log.warning("EmbeddingActor: could not load %s (%s) — using fallback",
                        _PREFERRED_MODEL, exc)
            self.model = SentenceTransformer(_FALLBACK_MODEL)
            self.model_name = _FALLBACK_MODEL
        log.info("EmbeddingActor ready: model=%s", self.model_name)

    def encode(self, texts: List[str]) -> List[List[float]]:
        """
        Encode texts in batches.  Returns list[list[float]] (JSON-serialisable).
        Caller in process_paper.py converts back to np.ndarray for cosine_similarity.
        """
        if not texts:
            return []
        try:
            vecs = self.model.encode(
                texts,
                batch_size=_BATCH_SIZE,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            return vecs.tolist()
        except Exception as exc:
            log.error("EmbeddingActor.encode failed: %s", exc)
            raise

    def model_info(self) -> dict:
        """Return model metadata for provenance tracking."""
        return {
            "model_name": self.model_name,
            "embedding_dim": self.model.get_sentence_embedding_dimension(),
        }