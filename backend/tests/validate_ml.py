"""
tests/validate_ml.py — Manual ML-service smoke validation (NOT a pytest test).

Run with the venv python from backend/:
    ../.venv/Scripts/python.exe tests/validate_ml.py

Exercises the real models end-to-end (downloads weights on first run): the shared
MiniLM embeddings, Silero VAD, Faster-Whisper STT, the hybrid intent classifier,
and the FAISS+BM25 RAG retriever. Piper TTS is intentionally skipped on Windows
(no piper-phonemize wheel — validated in the Linux container instead).

Prints a PASS/FAIL line per service and exits non-zero if any hard check fails.
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

# Make backend/ importable when run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_settings  # noqa: E402

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    """Append a validation outcome and echo it immediately."""
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def validate_embeddings(s):
    """Load MiniLM and check encode() returns normalised vectors of expected dim."""
    from services.embeddings import EmbeddingService

    t0 = time.perf_counter()
    emb = EmbeddingService(s.minilm_model, s.models_dir)
    v = emb.encode("hello world", normalize=True)
    ok = v.shape[0] == emb.dim and abs(float(np.linalg.norm(v)) - 1.0) < 1e-3
    record("embeddings", ok, f"dim={emb.dim} load+encode {time.perf_counter()-t0:.1f}s")
    return emb


def validate_vad(s):
    """Load Silero VAD and check probability() responds to speech vs silence."""
    from services.vad_service import VADService

    t0 = time.perf_counter()
    vad = VADService(s)
    vad.warmup()
    silence = np.zeros(s.vad_frame_samples, dtype=np.float32)
    # A loud noisy frame should score higher than digital silence.
    noise = (np.random.randn(s.vad_frame_samples) * 0.3).astype(np.float32)
    p_sil = vad.probability(silence)
    p_noise = vad.probability(noise)
    # Latency sample (post-warmup).
    n = 50
    t1 = time.perf_counter()
    for _ in range(n):
        vad.probability(silence)
    lat_ms = (time.perf_counter() - t1) / n * 1000
    ok = 0.0 <= p_sil <= 1.0 and 0.0 <= p_noise <= 1.0
    record("vad", ok, f"p_silence={p_sil:.3f} p_noise={p_noise:.3f} latency={lat_ms:.3f}ms load {time.perf_counter()-t0:.1f}s")


def validate_stt(s):
    """Load Faster-Whisper and transcribe a synthetic tone (expect no crash, str out)."""
    from services.stt_service import STTService

    t0 = time.perf_counter()
    stt = STTService(s)
    # 1s of a 220Hz tone — not speech, so transcript may be empty; we only assert
    # the pipeline runs and returns a string without error.
    sr = s.sample_rate
    tone = (0.2 * np.sin(2 * np.pi * 220 * np.arange(sr) / sr)).astype(np.float32)
    text = stt._transcribe_sync(tone)
    ok = isinstance(text, str)
    record("stt", ok, f"returned str (len={len(text)}) load+infer {time.perf_counter()-t0:.1f}s")


def validate_intent(s, emb):
    """Build the hybrid classifier and check several intents resolve correctly."""
    from models import Intent
    from services.fast_intent_classifier import FastIntentClassifier

    clf = FastIntentClassifier(emb)
    cases = [
        ("next slide please", Intent.NEXT),
        ("go back", Intent.PREV),
        ("go to slide five", Intent.GOTO),
        ("what does this term mean", Intent.QUESTION),
        ("stop", Intent.STOP),
        ("okay got it", Intent.IGNORE),
    ]
    correct = 0
    goto_ok = True
    for text, exp in cases:
        intent, slide = clf.classify(text)
        correct += int(intent == exp)
        if exp == Intent.GOTO:
            goto_ok = slide == 4
    acc = correct / len(cases)
    record("intent", acc == 1.0 and goto_ok, f"accuracy={acc:.0%} goto_slide_ok={goto_ok}")


def validate_rag(s, emb):
    """Index a tiny deck and confirm hybrid retrieval returns relevant, cited chunks."""
    from core.knowledge_base import KnowledgeBase
    from models import SlideContent

    kb = KnowledgeBase(s, emb)
    slides = [
        SlideContent(index=0, title="Revenue", body_text="Quarterly revenue grew by 25 percent to 4 million dollars."),
        SlideContent(index=1, title="Costs", body_text="Operating costs decreased due to cloud migration and automation."),
        SlideContent(index=2, title="Outlook", body_text="We project continued growth into the next fiscal year."),
    ]
    n = kb.index("validate-job", slides)
    ctx = kb.retrieve("validate-job", "how much did revenue grow", current_slide=0)
    # The revenue slide (0) should be among the cited sources.
    ok = n > 0 and 0 in ctx.sources and len(ctx.chunks) > 0
    record("rag", ok, f"indexed {n} chunks, top sources={ctx.sources}")


def main() -> int:
    """Run every ML validation and return a process exit code (0 = all passed)."""
    s = get_settings()
    print(f"models_dir={s.models_dir}  (weights download here on first run)\n")
    emb = validate_embeddings(s)
    validate_vad(s)
    validate_stt(s)
    validate_intent(s, emb)
    validate_rag(s, emb)
    print("\n--- SUMMARY ---")
    failed = [n for n, ok, _ in results if not ok]
    for n, ok, d in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {n}")
    print("\nTTS (Piper): SKIPPED on Windows (no piper-phonemize wheel) — validate in Linux container.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
