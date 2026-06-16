"""
tests/test_benchmarks.py — Component benchmark scaffolds (IMPLEMENTATION.md §21).

Mirrors the source doc's component benchmarks (VAD latency, intent accuracy,
model footprint). These require the heavy models and are meant to run INSIDE the
container on reference hardware so numbers compare to the doc's tables; they are
skipped automatically when the models aren't available.

Run explicitly:  pytest tests/test_benchmarks.py -m benchmark -s
"""

from __future__ import annotations

import time

import numpy as np
import pytest

pytestmark = pytest.mark.benchmark


def test_vad_latency_post_warmup():
    """100 warmed iterations; assert mean inference < 5 ms (doc VAD target).

    Skips if torch/silero are unavailable. Reports mean/P95 so results line up
    with Table 5.1 in the source document.
    """
    torch = pytest.importorskip("torch", reason="torch/silero not installed")
    from config import get_settings
    from services.vad_service import VADService

    vad = VADService(get_settings())
    vad.warmup()
    frame = np.zeros(get_settings().vad_frame_samples, dtype=np.float32)

    times = []
    for _ in range(100):
        t0 = time.perf_counter()
        vad.probability(frame)
        times.append((time.perf_counter() - t0) * 1000)
    mean = float(np.mean(times))
    p95 = float(np.percentile(times, 95))
    print(f"VAD latency mean={mean:.3f}ms p95={p95:.3f}ms")
    assert mean < 5.0


def test_intent_accuracy_standard_corpus():
    """Accuracy on a small labelled standard corpus should be high (target ≥95%).

    A scaffold over a handful of standard cases; the full benchmark loads the doc's
    68/22/9 standard/adversarial/noisy split from fixtures. Skips without MiniLM.
    """
    pytest.importorskip("sentence_transformers", reason="MiniLM not installed")
    from config import get_settings
    from models import Intent
    from services.embeddings import EmbeddingService
    from services.fast_intent_classifier import FastIntentClassifier

    s = get_settings()
    clf = FastIntentClassifier(EmbeddingService(s.minilm_model, s.models_dir))
    cases = [
        ("next slide", Intent.NEXT),
        ("previous slide", Intent.PREV),
        ("go to slide three", Intent.GOTO),
        ("what is the revenue", Intent.QUESTION),
        ("stop", Intent.STOP),
        ("okay", Intent.IGNORE),
    ]
    correct = sum(1 for text, exp in cases if clf.classify(text)[0] == exp)
    acc = correct / len(cases)
    print(f"intent accuracy (standard sample) = {acc:.1%}")
    assert acc >= 0.95
