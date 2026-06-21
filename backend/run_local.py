"""
run_local.py — Run the full Chatterbot-AI pipeline locally on Windows (WINDOWS_TESTING.md W2).

Two modes:

  python run_local.py --selftest
      Headless round-trip (no microphone): synthesizes a spoken question with
      edge-TTS, runs it through the REAL pipeline — Faster-Whisper STT → hybrid
      intent → FAISS+BM25 RAG → Groq LLM → edge-TTS answer — and prints a report.
      Use this to verify every cloud/ML stage works on Windows.

  python run_local.py
      Live mode: narrates a demo deck through your speakers and listens on your
      microphone. Talk to interrupt; ask "what was the revenue?"; say "next slide",
      "stop". Uses LocalAudioTransport (sounddevice). Ctrl+C to quit.

Everything runs on Windows — no Docker, no LiveKit, no Piper. Requires the Groq
key in .env (LLM + grounded answers).
"""

from __future__ import annotations

import argparse
import asyncio

import numpy as np

from config import get_settings
from core.playback_tracker import PlaybackTracker
from core.session_state import SessionState
from deps import ServiceRegistry
from models import (
    PacingPlan,
    Persona,
    ScriptSentence,
    SlideContent,
    SlideScript,
    Track,
)
from utils.audio import resample
from utils.logging import get_logger, setup_logging

log = get_logger(component="run-local")

# A tiny self-contained demo deck (avoids needing a .pptx or Groq script-gen).
_DEMO_SLIDES = [
    SlideContent(
        index=0, title="Quarterly Results",
        body_text="Revenue grew by twenty five percent to four million dollars this quarter. "
                  "Operating costs fell after our cloud migration.",
        notes="Emphasize the revenue growth and the cost reduction.",
    ),
    SlideContent(
        index=1, title="Outlook",
        body_text="We project continued growth into the next fiscal year, "
                  "driven by new product lines and improved retention.",
        notes="Keep this forward looking and concise.",
    ),
]


def _demo_plan() -> PacingPlan:
    """Build an inline 3-track PacingPlan from the demo slides (no LLM needed).

    Each slide's body is split into sentences for STANDARD; SUMMARY/TURBO reuse the
    same sentences here (the demo focuses on the live loop, not script generation).
    """
    from utils.text import split_sentences, word_count

    def scripts(track: Track) -> list[SlideScript]:
        out = []
        for s in _DEMO_SLIDES:
            sents = [ScriptSentence(text=t, word_count=word_count(t)) for t in split_sentences(s.body_text)]
            out.append(SlideScript(slide_index=s.index, track=track, sentences=sents))
        return out

    return PacingPlan(
        job_id="demo", persona=Persona.GENERAL, target_seconds=120.0,
        per_slide_seconds={0: 60.0, 1: 60.0}, priorities={},
        scripts={t: scripts(t) for t in (Track.STANDARD, Track.SUMMARY, Track.TURBO)},
    )


async def _load_registry() -> ServiceRegistry:
    """Load + warm all real services and index the demo deck for RAG."""
    s = get_settings()
    reg = ServiceRegistry()
    await reg.load_all(s)
    await reg.warmup()
    await asyncio.to_thread(reg.kb.index, "demo", _DEMO_SLIDES)
    return reg


async def selftest() -> int:
    """Headless: synth a question, run the real pipeline, print each stage. Returns exit code."""
    s = get_settings()
    reg = await _load_registry()

    question = "What was the revenue this quarter?"
    print(f"\n[1] Synthesizing spoken question via edge-TTS: {question!r}")
    q_audio_24k = await reg.tts.synthesize(question)              # float32 @ tts.native_sr
    q_audio_16k = resample(q_audio_24k, reg.tts.native_sr, s.sample_rate)
    print(f"    got {len(q_audio_16k)/s.sample_rate:.2f}s of audio @ {s.sample_rate}Hz")

    print("[2] Transcribing with Faster-Whisper...")
    text = await reg.stt.transcribe(q_audio_16k)
    print(f"    STT  -> {text!r}")

    intent, slide_no = reg.intent.classify(text)
    print(f"[3] Intent -> {intent}  (slide={slide_no})")

    print("[4] Retrieving grounding context (FAISS + BM25)...")
    ctx = reg.kb.retrieve("demo", text, current_slide=0)
    print(f"    sources -> slides {ctx.sources}")

    print("[5] Asking Groq LLM for a grounded answer...")
    answer = await reg.llm.answer(text, ctx, _DEMO_SLIDES[0].body_text)
    print(f"    ANSWER -> {answer!r}")

    print("[6] Synthesizing the answer via edge-TTS...")
    ans_audio = await reg.tts.synthesize(answer)
    print(f"    got {len(ans_audio)/reg.tts.native_sr:.2f}s of answer audio")

    await reg.aclose()
    ok = bool(text.strip()) and bool(answer.strip()) and len(ans_audio) > 1000
    print(f"\nSELFTEST {'PASS' if ok else 'FAIL'} — full Windows pipeline "
          f"(edge-TTS -> Whisper -> intent -> RAG -> Groq -> edge-TTS)\n")
    return 0 if ok else 1


async def live() -> int:
    """Live mic/speaker session over the demo deck. Ctrl+C to stop."""
    from core.agent_worker import ChatterbotAgentWorker
    from core.transport.local_audio import LocalAudioTransport

    s = get_settings()
    reg = await _load_registry()

    plan = _demo_plan()
    first = plan.scripts[Track.STANDARD][0].sentences
    tracker = PlaybackTracker(first, out_sr=reg.tts.native_sr, wpm=150)
    state = SessionState(session_id="local", job_id="demo", plan=plan, tracker=tracker)

    transport = LocalAudioTransport(in_sr=s.sample_rate, out_sr=reg.tts.native_sr)
    await transport.start()
    worker = ChatterbotAgentWorker(ctx=None, registry=reg, settings=s, state=state)
    worker.audio_in = transport
    worker.audio_out = transport

    print("\n=== Chatterbot-AI live (demo deck) ===")
    print("Listening on your mic. Try: interrupt while it talks, ask "
          "'what was the revenue?', say 'next slide' or 'stop'. Ctrl+C to quit.\n")
    try:
        await worker.run()
    except KeyboardInterrupt:
        pass
    finally:
        await transport.aclose()
        await reg.aclose()
    return 0


def main() -> int:
    """Parse args and dispatch to selftest or live mode."""
    setup_logging(get_settings().log_level)
    ap = argparse.ArgumentParser(description="Run Chatterbot-AI locally on Windows")
    ap.add_argument("--selftest", action="store_true", help="headless pipeline check (no mic)")
    args = ap.parse_args()
    return asyncio.run(selftest() if args.selftest else live())


if __name__ == "__main__":
    raise SystemExit(main())
