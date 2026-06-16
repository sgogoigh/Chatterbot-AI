"""
deps.py — ServiceRegistry: process-wide singletons (IMPLEMENTATION.md §6).

One place that owns every heavy model handle and shared service. Loaded once in
the FastAPI lifespan (§6) and shared by both the REST routes (via FastAPI
``Depends``) and the in-process LiveKit agent worker. Centralising this keeps a
single source of truth for model handles and makes the 23 MB footprint claim
honest (MiniLM is loaded once and shared, §3).
"""

from __future__ import annotations

import asyncio

from config import Settings
from utils.logging import get_logger

log = get_logger(component="registry")


class ServiceRegistry:
    """Holds and lifecycle-manages all shared services / model singletons."""

    def __init__(self) -> None:
        """Create an empty registry; call the ``load_*`` coroutines to populate it."""
        self.settings: Settings | None = None
        self.embeddings = None      # EmbeddingService (shared MiniLM)
        self.vad = None             # VADService
        self.stt = None             # STTService
        self.tts = None             # TTSService
        self.intent = None          # FastIntentClassifier
        self.llm = None             # GroqLLMService
        self.kb = None              # KnowledgeBase
        self.slides = None          # SlideProcessor
        self._ready = False

    # --------------------------------------------------------------- loaders
    # Each loader wraps blocking model construction in ``asyncio.to_thread`` so
    # the lifespan can load them concurrently without blocking the event loop.
    async def load_embeddings(self, s: Settings) -> None:
        """Load the shared MiniLM embedding model (used by intent + RAG)."""
        from services.embeddings import EmbeddingService

        self.embeddings = await asyncio.to_thread(
            EmbeddingService, s.minilm_model, s.models_dir
        )

    async def load_vad(self, s: Settings) -> None:
        """Load the Silero VAD model."""
        from services.vad_service import VADService

        self.vad = await asyncio.to_thread(VADService, s)

    async def load_stt(self, s: Settings) -> None:
        """Load the Faster-Whisper STT model."""
        from services.stt_service import STTService

        self.stt = await asyncio.to_thread(STTService, s)

    async def load_tts(self, s: Settings) -> None:
        """Load the Piper TTS voice."""
        from services.tts_service import TTSService

        self.tts = await asyncio.to_thread(TTSService, s)

    async def load_all(self, s: Settings) -> None:
        """Load every heavy model concurrently, then wire the light dependents.

        Embeddings/VAD/STT/TTS load in parallel (they are independent). The
        intent classifier, KB, LLM and SlideProcessor depend on those and are
        constructed afterward. Mirrors the lifespan flow in §6.
        """
        self.settings = s
        await asyncio.gather(
            self.load_embeddings(s),
            self.load_vad(s),
            self.load_stt(s),
            self.load_tts(s),
        )
        from core.knowledge_base import KnowledgeBase
        from services.fast_intent_classifier import FastIntentClassifier
        from services.groq_llm_service import GroqLLMService
        from services.slide_processor import SlideProcessor

        self.intent = FastIntentClassifier(self.embeddings)
        self.llm = GroqLLMService(s)
        self.kb = KnowledgeBase(s, self.embeddings)
        self.slides = SlideProcessor(s, self.llm)

    async def warmup(self) -> None:
        """Run one dummy inference per model so live/benchmark latency is post-warmup.

        Critical for the VAD <5 ms target (ONNX/CT2 first call is slow). Best-effort
        per model; failures are logged, not fatal.
        """
        for name, obj in (("vad", self.vad), ("embeddings", self.embeddings),
                          ("intent", self.intent), ("stt", self.stt), ("tts", self.tts)):
            try:
                if hasattr(obj, "warmup"):
                    await asyncio.to_thread(obj.warmup)
            except Exception as e:  # noqa: BLE001 - warmup is best-effort
                log.warning(f"warmup failed for {name}: {e}")
        self._ready = True

    def is_ready(self) -> bool:
        """Return True once all models are loaded and warmed (drives /readyz, §6)."""
        return self._ready

    async def aclose(self) -> None:
        """Release external resources on shutdown (HTTP clients, etc.)."""
        if self.llm is not None:
            await self.llm.aclose()
