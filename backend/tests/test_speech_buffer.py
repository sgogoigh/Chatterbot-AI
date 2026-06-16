"""
tests/test_speech_buffer.py — VAD utterance segmentation (IMPLEMENTATION.md §10, §19).

SpeechBuffer is pure logic (no LiveKit/torch), so onset debounce and
silence-based utterance completion are tested directly by feeding synthetic VAD
probabilities. This is the segmentation that drives barge-in + STT flushing.
"""

from __future__ import annotations

import numpy as np

from config import Settings
from core.agent_worker import SpeechBuffer


def _buf() -> SpeechBuffer:
    """A SpeechBuffer with default thresholds over a fresh Settings()."""
    return SpeechBuffer(Settings())


def _frame() -> np.ndarray:
    """A dummy 512-sample frame (content irrelevant; only the prob drives logic)."""
    return np.zeros(512, dtype=np.float32)


def test_onset_requires_debounce():
    """A single high-prob frame does NOT trigger onset; sustained speech does."""
    buf = _buf()
    buf.update(_frame(), 0.9)
    # one frame is below the min-speech debounce -> not yet in speech
    assert not buf.just_started_speech()
    # feed enough consecutive speech frames to cross the debounce
    started = False
    for _ in range(10):
        buf.update(_frame(), 0.9)
        started = started or buf.just_started_speech()
    assert started


def test_utterance_completes_after_silence():
    """After confirmed speech, sustained silence marks the utterance complete."""
    buf = _buf()
    for _ in range(10):
        buf.update(_frame(), 0.9)          # speech
    assert not buf.utterance_complete()    # still talking
    for _ in range(50):
        buf.update(_frame(), 0.0)          # trailing silence
    assert buf.utterance_complete()
    audio = buf.take()
    assert audio.size > 0                  # captured speech frames


def test_take_resets_buffer():
    """take() returns audio and clears state so the next utterance starts clean."""
    buf = _buf()
    for _ in range(10):
        buf.update(_frame(), 0.9)
    buf.take()
    assert not buf.utterance_complete()
    assert not buf.just_started_speech()
