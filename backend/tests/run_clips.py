"""
tests/run_clips.py — Run real human voice clips through the pipeline (S14b, manual).

Decodes the .m4a clips in ../test_assets/clips with PyAV (AAC), resamples to 16 kHz
mono, and runs each through the REAL pipeline:
    Faster-Whisper STT -> hybrid intent -> (for QUESTION) FAISS+BM25 RAG -> Groq LLM
then grades the result against the TerraGrid deck. This verifies STT + intent + Q&A
on genuine human speech — the thing edge-TTS self-tests can't cover.

Run:
  CB_MODELS_DIR=../models ../.venv/Scripts/python tests/run_clips.py
"""

from __future__ import annotations

import asyncio
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_settings  # noqa: E402
from deps import ServiceRegistry  # noqa: E402
from models import Intent  # noqa: E402

CLIPS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "test_assets", "clips")
PPTX = os.path.join(os.path.dirname(__file__), "..", "..", "test_assets", "terragrid_q3.pptx")

# Expected result per clip stem. For questions, `keys` = keywords the answer should
# contain (any-of, case-insensitive); intent is what the classifier should return.
EXPECTED: dict[str, dict] = {
    "next": {"intent": Intent.NEXT},
    "previous": {"intent": Intent.PREV},
    "goto": {"intent": Intent.GOTO, "slide": 3},          # "slide four" -> 0-based 3
    "stop": {"intent": Intent.STOP},
    "move_on": {"intent": Intent.NEXT},
    "ignore": {"intent": Intent.IGNORE},
    "q_revenue": {"intent": Intent.QUESTION, "keys": ["12.4", "million", "18"]},
    "q_revenue_fast": {"intent": Intent.QUESTION, "keys": ["12.4", "million", "18"]},
    "q_revenue_far": {"intent": Intent.QUESTION, "keys": ["12.4", "million", "18"]},
    "q_customers": {"intent": Intent.QUESTION, "keys": ["1,200", "1200", "twelve hundred"]},
    "q_regions": {"intent": Intent.QUESTION, "keys": ["europe", "japan", "brazil"]},
    "q_product": {"intent": Intent.QUESTION, "keys": ["atlas", "mobile", "dashboard"]},
    "q_long": {"intent": Intent.QUESTION, "keys": ["18", "percent", "subscription", "million"]},
    "q_outofdeck": {"intent": Intent.QUESTION, "keys": ["not", "don't", "isn't", "no ", "slides"]},
}


def load_clip(path: str, target_sr: int = 16000) -> np.ndarray:
    """Decode an audio file to float32 mono at ``target_sr`` using PyAV (handles AAC/m4a)."""
    import av

    container = av.open(path)
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=target_sr)
    out: list[np.ndarray] = []
    for frame in container.decode(audio=0):
        for r in resampler.resample(frame):
            out.append(r.to_ndarray().reshape(-1))
    for r in resampler.resample(None):  # flush
        out.append(r.to_ndarray().reshape(-1))
    container.close()
    pcm = np.concatenate(out).astype(np.int16) if out else np.zeros(1, np.int16)
    return pcm.astype(np.float32) / 32768.0


async def main() -> int:
    """Decode + run every clip, print a graded report, return non-zero on failures."""
    s = get_settings()
    print("[loading models]")
    reg = ServiceRegistry()
    await reg.load_all(s)
    await reg.warmup()

    # Index the real deck for grounded Q&A.
    job_dir = os.path.join(s.data_dir, "jobs", "clips")
    os.makedirs(job_dir, exist_ok=True)
    slides = await asyncio.to_thread(reg.slides.extract, os.path.abspath(PPTX), job_dir)
    await asyncio.to_thread(reg.kb.index, "clips", slides)
    print(f"[indexed {len(slides)} slides]\n")

    files = sorted(glob.glob(os.path.join(CLIPS_DIR, "*.m4a")) + glob.glob(os.path.join(CLIPS_DIR, "*.wav")))
    if not files:
        print("no clips found in test_assets/clips/")
        return 1

    rows, fails = [], 0
    for path in files:
        stem = os.path.splitext(os.path.basename(path))[0]
        exp = EXPECTED.get(stem, {})
        audio = load_clip(path, s.sample_rate)
        dur = len(audio) / s.sample_rate
        text = await reg.stt.transcribe(audio)
        intent, slide_no = reg.intent.classify(text)

        answer = ""
        if intent == Intent.QUESTION:
            ctx = reg.kb.retrieve("clips", text, current_slide=0)
            answer = await reg.llm.answer(text, ctx, slides[0].body_text if slides else "")

        # ---- grade ----
        ok = True
        notes = []
        if "intent" in exp:
            if intent == exp["intent"]:
                notes.append("intent ok")
            else:
                ok = False
                notes.append(f"intent X(got {intent.value}, want {exp['intent'].value})")
        if "slide" in exp:
            if slide_no == exp["slide"]:
                notes.append("slide ok")
            else:
                ok = False
                notes.append(f"slide X(got {slide_no})")
        if "keys" in exp:
            low = answer.lower()
            hit = [k for k in exp["keys"] if k.lower() in low]
            if hit:
                notes.append(f"answer ok({hit[0]!r})")
            else:
                ok = False
                notes.append("answer X(no expected keyword)")
        if not exp:
            notes.append("(ungraded)")
        if not ok:
            fails += 1

        rows.append((stem, dur, text, intent.value, slide_no, answer, "PASS" if ok else "FAIL", " ".join(notes)))

    # ---- report ----
    print("=" * 100)
    for stem, dur, text, intent, slide_no, answer, verdict, notes in rows:
        print(f"\n[{verdict}] {stem}  ({dur:.1f}s)   {notes}")
        print(f"    STT    : {text!r}")
        print(f"    intent : {intent}" + (f"  slide={slide_no}" if slide_no is not None else ""))
        if answer:
            print(f"    answer : {answer!r}")
    print("\n" + "=" * 100)
    graded = sum(1 for r in rows if r[7] and "ungraded" not in r[7])
    print(f"SUMMARY: {len(rows)} clips, {graded - fails}/{graded} graded checks passed, {fails} failed.")

    await reg.aclose()
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
