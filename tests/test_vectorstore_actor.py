"""
test_vectorstore_actor.py — VectorStoreActor is the single ChromaDB writer.

Background (2026-09-18): opening the persistent collection inside every
process_paper worker cost ~4.5 GB RSS per task and capped the pipeline at
--workers 1.  The actor owns the one PersistentClient; workers ship
(chunks, vectors) to it.

No live ChromaDB here — VectorStore is stubbed (see
feedback: no live stores in unit tests).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np


def _plain_actor_class():
    from src.actors.vectorstore_actor import VectorStoreActor
    # unwrap @ray.remote so the class runs in-process without a cluster
    return VectorStoreActor.__ray_metadata__.modified_class


def test_actor_opens_one_store_and_forwards_upsert():
    stub = MagicMock()
    stub._collection.count.return_value = 7
    with patch("src.vectorstore.chroma_store.VectorStore", return_value=stub) as ctor:
        actor = _plain_actor_class()()
        chunks = [MagicMock(chunk_id="a"), MagicMock(chunk_id="b")]
        n = actor.upsert_document_chunks(chunks, [[0.1, 0.2], [0.3, 0.4]])

    ctor.assert_called_once()
    assert n == 2
    passed_chunks, passed_vecs = stub.upsert_document_chunks.call_args.args
    assert passed_chunks is chunks
    assert isinstance(passed_vecs, np.ndarray) and passed_vecs.dtype == np.float32
    assert passed_vecs.shape == (2, 2)
    assert actor.count() == 7


def test_actor_empty_upsert_is_noop():
    stub = MagicMock()
    with patch("src.vectorstore.chroma_store.VectorStore", return_value=stub):
        actor = _plain_actor_class()()
        assert actor.upsert_document_chunks([], []) == 0
    stub.upsert_document_chunks.assert_not_called()


def test_process_paper_signature_accepts_vectorstore_actor():
    """Runner passes vectorstore_actor=...; the task must accept it and
    default to None so legacy callers keep working."""
    import inspect
    from src.orchestration.process_paper import process_paper
    params = inspect.signature(process_paper._function).parameters
    assert "vectorstore_actor" in params
    assert params["vectorstore_actor"].default is None
