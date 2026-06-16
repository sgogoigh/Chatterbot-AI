"""
services/stt_service.py — Faster-Whisper speech-to-text (IMPLEMENTATION.md §11).

Transcribes a buffered user utterance to text for intent classification + RAG.
  * Model: faster-whisper small.en, CTranslate2, INT8 (CPU). ~1.5 s for ~2 s audio.
  * Greedy decoding (beam_size=1, best_of=1) for lowest latency, per the doc.
  * Runs inside ``asyncio.to_thread`` so the heavy, blocking transcription never
    stalls the event loop (the doc's async requirement).

⚠️ R3: the doc's "≤2 s audio" is a benchmark window, not a hard limit. Real
questions are longer; the SpeechBuffer caps utterances at ``stt_max_utterance_ms``
and lets trailing silence (not a 2 s window) decide the boundary.
"""

from __future__ import annotations

import asyncio

import numpy as np

from config import Settings


class STTService:
    """Faster-Whisper transcription service (async wrapper over a blocking model)."""

    def __init__(self, settings: Settings):
        """Load the CTranslate2 INT8 Whisper model onto CPU.

        Import is local so torch/faster-whisper aren't required to import the rest
        of the app. Model assets auto-download into ``models_dir`` on first use.
        """
        from faster_whisper import WhisperModel

        self.settings = settings
        self.model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            download_root=settings.models_dir,
        )

    async def transcribe(self, audio_f32_16k: np.ndarray) -> str:
        """Transcribe a float32 mono 16 kHz utterance to text (off the event loop).

        Delegates the blocking decode to a worker thread via ``asyncio.to_thread``
        so the agent's audio loops keep running. Input is already at 16 kHz (the
        inbound LiveKit AudioStream produced that rate), so no resampling here.
        """
        return await asyncio.to_thread(self._transcribe_sync, audio_f32_16k)

    def _transcribe_sync(self, audio: np.ndarray) -> str:
        """Blocking greedy transcription; joins all segment texts into one string.

        ``condition_on_previous_text=False`` keeps each utterance independent
        (no cross-turn drift), and disabling the internal VAD filter avoids
        double-gating since our Silero VAD already segmented the audio.
        """
        segments, _info = self.model.transcribe(
            audio,
            language="en",
            beam_size=1,
            best_of=1,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        return " ".join(seg.text for seg in segments).strip()

    def warmup(self) -> None:
        """Decode a short silent buffer so the first real transcription isn't cold (§6)."""
        self._transcribe_sync(np.zeros(self.settings.sample_rate, dtype=np.float32))
