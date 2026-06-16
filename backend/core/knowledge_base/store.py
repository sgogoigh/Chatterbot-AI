"""
core/knowledge_base/store.py — FAISS + BM25 persistence (IMPLEMENTATION.md §17).

Owns the two indexes that back hybrid retrieval and their on-disk persistence per
job (under ``data/jobs/<job_id>/``):
  * FAISS flat inner-product index over normalised MiniLM embeddings (= cosine).
  * BM25 lexical index (rank_bm25) over tokenised chunk text.
  * chunks.json — the chunk payloads, index-aligned to both indexes.

The store knows nothing about fusion/boosting; that lives in the KnowledgeBase
facade (§17.1). This separation keeps persistence and retrieval policy testable
independently.
"""

from __future__ import annotations

import json
import os
import pickle

import numpy as np

from models import Chunk


class HybridStore:
    """Persistable pair of (FAISS dense index, BM25 lexical index) over chunks."""

    def __init__(self, job_dir: str):
        """Bind the store to a job's artifact directory (created on save)."""
        self.job_dir = job_dir
        self.chunks: list[Chunk] = []
        self._faiss = None
        self._bm25 = None

    # ----------------------------------------------------------------- build
    def build(self, chunks: list[Chunk], embeddings: np.ndarray) -> None:
        """Build both indexes in memory from chunks + their (normalised) embeddings.

        ``embeddings`` must be L2-normalised so FAISS inner product equals cosine
        similarity. BM25 is built over simple whitespace tokenisation of each
        chunk. Call :meth:`save` to persist.
        """
        import faiss
        from rank_bm25 import BM25Okapi

        self.chunks = chunks
        dim = embeddings.shape[1]
        index = faiss.IndexFlatIP(dim)
        index.add(embeddings.astype(np.float32))
        self._faiss = index
        self._bm25 = BM25Okapi([c.text.lower().split() for c in chunks])

    # ------------------------------------------------------------- retrieval
    def dense_search(self, query_vec: np.ndarray, k: int) -> list[tuple[int, float]]:
        """Return top-``k`` (chunk_index, score) by dense cosine similarity."""
        if self._faiss is None or not self.chunks:
            return []
        scores, idxs = self._faiss.search(query_vec.astype(np.float32)[None], min(k, len(self.chunks)))
        return [(int(i), float(s)) for i, s in zip(idxs[0], scores[0]) if i >= 0]

    def lexical_search(self, query: str, k: int) -> list[tuple[int, float]]:
        """Return top-``k`` (chunk_index, score) by BM25 lexical relevance."""
        if self._bm25 is None or not self.chunks:
            return []
        scores = self._bm25.get_scores(query.lower().split())
        ranked = np.argsort(scores)[::-1][:k]
        return [(int(i), float(scores[i])) for i in ranked]

    # ----------------------------------------------------------- persistence
    def save(self) -> None:
        """Persist FAISS index, BM25 object, and chunk payloads to ``job_dir``.

        Called once at the end of indexing (build endpoint). BM25 + chunks are
        pickled/JSON-dumped; FAISS uses its own writer.
        """
        import faiss

        os.makedirs(self.job_dir, exist_ok=True)
        faiss.write_index(self._faiss, os.path.join(self.job_dir, "faiss.index"))
        with open(os.path.join(self.job_dir, "bm25.pkl"), "wb") as f:
            pickle.dump(self._bm25, f)
        with open(os.path.join(self.job_dir, "chunks.json"), "w", encoding="utf-8") as f:
            json.dump([c.model_dump() for c in self.chunks], f)

    def load(self) -> None:
        """Load FAISS index, BM25 object, and chunks from ``job_dir`` into memory.

        Used lazily when a session needs to answer questions (§17.2). Raises if
        the artifacts are missing (build hasn't run for this job).
        """
        import faiss

        self._faiss = faiss.read_index(os.path.join(self.job_dir, "faiss.index"))
        with open(os.path.join(self.job_dir, "bm25.pkl"), "rb") as f:
            self._bm25 = pickle.load(f)
        with open(os.path.join(self.job_dir, "chunks.json"), encoding="utf-8") as f:
            self.chunks = [Chunk(**d) for d in json.load(f)]
