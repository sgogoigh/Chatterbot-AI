"""
core/knowledge_base/knowledge_base.py — Presentation-aware hybrid RAG (IMPLEMENTATION.md §17).

Facade over the HybridStore that implements the retrieval POLICY:
  * indexing: chunk slides → embed (shared MiniLM) → build + persist FAISS+BM25.
  * retrieval: dense + lexical search → Reciprocal Rank Fusion → current-slide
    boost → top-k → token-budget trim → cited sources.

Resolves the PLAN §16.3 contradiction (FAISS-only vs hybrid) in favour of HYBRID,
the stronger gap-closing claim from the source doc.
"""

from __future__ import annotations

import os

from config import Settings
from core.knowledge_base.chunker import chunk_slides
from core.knowledge_base.store import HybridStore
from models import Chunk, RetrievedContext, SlideContent
from services.embeddings import EmbeddingService


class KnowledgeBase:
    """Indexes presentation content and serves grounded, slide-weighted retrieval."""

    def __init__(self, settings: Settings, embeddings: EmbeddingService):
        """Hold config + the shared embedding model; stores are loaded per job."""
        self.s = settings
        self.emb = embeddings
        self._stores: dict[str, HybridStore] = {}   # job_id -> loaded store

    def _job_dir(self, job_id: str) -> str:
        """Return the on-disk artifact directory for a job's RAG indexes."""
        return os.path.join(self.s.data_dir, "jobs", job_id, "rag")

    # ------------------------------------------------------------- indexing
    def index(self, job_id: str, slides: list[SlideContent]) -> int:
        """Chunk + embed + index a presentation's content and persist it (build step).

        Returns the number of chunks indexed. Embeddings are normalised so the
        store's inner-product FAISS index measures cosine similarity. Called once
        per job during the async build (§7.1).
        """
        chunks = chunk_slides(slides, self.s.rag_chunk_tokens, self.s.rag_chunk_overlap)
        if not chunks:
            return 0
        vecs = self.emb.encode([c.text for c in chunks], normalize=True)
        store = HybridStore(self._job_dir(job_id))
        store.build(chunks, vecs)
        store.save()
        self._stores[job_id] = store
        return len(chunks)

    def evict(self, job_id: str) -> None:
        """Drop a job's in-memory store to free RAM (disk cleanup is the caller's job).

        Called when a session ends/reloads so a finished presentation's index isn't
        held in memory. A later retrieve() would lazily reload from disk if the
        artifacts still exist; if they were also purged, the job is simply gone.
        """
        self._stores.pop(job_id, None)

    def load(self, job_id: str) -> None:
        """Lazily load a job's persisted indexes into memory (§17.2).

        Done at session start (or first question) so the first QUESTION doesn't
        pay disk-load latency. No-op if already loaded.
        """
        if job_id in self._stores:
            return
        store = HybridStore(self._job_dir(job_id))
        store.load()
        self._stores[job_id] = store

    # ------------------------------------------------------------ retrieval
    def retrieve(self, job_id: str, query: str, current_slide: int) -> RetrievedContext:
        """Hybrid-retrieve grounding context for ``query``, biased to the active slide.

        Pipeline (§17.1): dense + lexical candidate lists → Reciprocal Rank Fusion
        (``1/(k+rank)``) → additive boost for chunks on ``current_slide`` → sort →
        top-k → trim to the token budget. Returns the chunks plus the distinct set
        of cited slide indices.
        """
        store = self._stores.get(job_id)
        if store is None:
            self.load(job_id)
            store = self._stores[job_id]

        qvec = self.emb.encode(query, normalize=True)
        dense = store.dense_search(qvec, k=20)
        lexical = store.lexical_search(query, k=20)

        fused = self._rrf(dense, lexical)
        # Current-slide boost: nudge chunks from the on-screen slide upward.
        for cidx in fused:
            if store.chunks[cidx].slide_index == current_slide:
                fused[cidx] += self.s.rag_current_slide_boost

        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[: self.s.rag_top_k]
        picked = self._trim_to_tokens([store.chunks[i] for i, _ in ranked], ranked)
        return RetrievedContext(
            chunks=picked,
            sources=sorted({c.slide_index for c in picked}),
        )

    def _rrf(self, dense: list[tuple[int, float]], lexical: list[tuple[int, float]]) -> dict[int, float]:
        """Fuse two ranked candidate lists via Reciprocal Rank Fusion.

        RRF (``score += 1/(k+rank)``) is rank-based, so it combines the dense and
        lexical signals without needing their raw scores to be comparable. ``k`` is
        ``rag_rrf_k`` from settings. Returns chunk_index -> fused score.
        """
        k = self.s.rag_rrf_k
        out: dict[int, float] = {}
        for rank, (cidx, _score) in enumerate(dense):
            out[cidx] = out.get(cidx, 0.0) + 1.0 / (k + rank)
        for rank, (cidx, _score) in enumerate(lexical):
            out[cidx] = out.get(cidx, 0.0) + 1.0 / (k + rank)
        return out

    def _trim_to_tokens(self, chunks: list[Chunk], ranked: list[tuple[int, float]]) -> list[Chunk]:
        """Drop the lowest-ranked chunks until the context fits the token budget.

        Approximates tokens by word count (consistent with the chunker) and stamps
        each kept chunk with its fused score for downstream inspection/logging.
        """
        budget = self.s.rag_max_context_tokens
        out: list[Chunk] = []
        used = 0
        for chunk, (_, score) in zip(chunks, ranked):
            cost = len(chunk.text.split())
            if used + cost > budget and out:
                break
            chunk.score = float(score)
            out.append(chunk)
            used += cost
        return out
