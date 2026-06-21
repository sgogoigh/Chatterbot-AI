"""
services/embeddings.py — Shared MiniLM embedding model (IMPLEMENTATION.md §3, §13, §17).

Design decision (§3): ONE all-MiniLM-L6-v2 instance is shared by BOTH the intent
classifier (§13) and the RAG knowledge base (§17). Loading it once keeps the
~22 MB footprint honest (the doc's 23 MB full-duplex claim depends on not loading
MiniLM twice) and avoids redundant warmup.

This module exposes a small wrapper so callers depend on a stable ``encode`` API
rather than on sentence-transformers internals.
"""

from __future__ import annotations

import numpy as np


class EmbeddingService:
    """Thin singleton-style wrapper around a SentenceTransformer (all-MiniLM-L6-v2)."""

    def __init__(self, model_name: str, cache_dir: str | None = None):
        """Load the MiniLM model.

        Heavy work (model download/load) happens here, so construction is done
        once at startup off the event loop (see ServiceRegistry / lifespan §6).
        Import is local so the rest of the app can be imported without pulling in
        torch when models aren't needed (e.g. pure-logic unit tests).
        """
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name, cache_folder=cache_dir)
        self.dim = self.model.get_sentence_embedding_dimension()

    def encode(self, texts: list[str] | str, normalize: bool = True) -> np.ndarray:
        """Embed one or many texts, returning a float32 array.

        ``normalize=True`` returns unit vectors so a dot product equals cosine
        similarity — relied on by both the intent prototypes (§13) and the FAISS
        inner-product index (§17).
        """
        single = isinstance(texts, str)
        arr = self.model.encode(
            [texts] if single else texts,
            normalize_embeddings=normalize,
            convert_to_numpy=True,
        ).astype(np.float32)
        return arr[0] if single else arr

    def warmup(self) -> None:
        """Run one dummy embedding so the first real call isn't cold (§6)."""
        self.encode("warmup", normalize=True)
