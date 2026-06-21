"""
tests/harness.py — Tier-1 E2E test harness (E2E_TESTING.md §3 Tier 1, §6).

Lets the REAL ``ChatterbotAgentWorker`` orchestration run with NO LiveKit, NO
ML models, and NO network — by substituting:

  * FakeAudioSource  — records capture_frame / clear_queue instead of sending audio
  * FakeAudioStream  — async-iterates synthetic int16 frames into the input loop
  * StubVAD          — amplitude-thresholded "speech vs silence" (deterministic)
  * StubSTT          — returns scripted transcripts (no audio decoding)
  * StubTTS          — emits N silent float32 chunks (no Piper)
  * StubLLM          — returns a canned grounded answer (no Groq/Ollama)
  * StubIntent       — returns scripted (intent, slide) so routing is deterministic
  * StubKB           — returns a canned RetrievedContext (no FAISS index needed)

This isolates the highest-risk code (barge-in, transition lock, deterministic
resume, intent routing) and tests it fast + deterministically. Real STT/TTS/
intent/RAG are covered by their own component tests + validate_ml.py (Tier 2).
"""

from __future__ import annotations

import numpy as np

from core.agent_worker import ChatterbotAgentWorker
from core.playback_tracker import PlaybackTracker
from core.session_state import SessionState
from models import (
    Intent,
    PacingPlan,
    Persona,
    RetrievedContext,
    ScriptSentence,
    SlideScript,
    Track,
)

SR = 22050  # stub TTS native rate


# ---------------------------------------------------------------- fake audio
class FakeAudioStream:
    """Async-iterable inbound transport: yields float32 mono frames (16 kHz).

    Matches the transport contract (audio_in yields float32 arrays). Finite, so the
    input loop terminates in tests; ``delay`` trickles frames so the listener and
    speaker loops interleave realistically.
    """

    def __init__(self, frames: list[np.ndarray], delay: float = 0.0):
        """Store the float32 frame list; ``delay`` (s) paces iteration."""
        self._frames = frames
        self._delay = delay

    def __aiter__(self):
        """Return a fresh async iterator over the frames."""
        self._i = 0
        return self

    async def __anext__(self) -> np.ndarray:
        """Yield the next float32 frame (after an optional delay) or stop when drained."""
        if self._i >= len(self._frames):
            raise StopAsyncIteration
        if self._delay:
            import asyncio

            await asyncio.sleep(self._delay)
        frame = self._frames[self._i]
        self._i += 1
        return frame


class FakeAudioSource:
    """Outbound sink that records activity instead of playing audio.

    Implements the sink contract (``play``/``stop``). Counts pushed frames/samples
    and stop() calls so tests can assert "audio produced" and "playback aborted on
    barge-in" without real devices. Counter names (frames/clear_calls) are kept
    stable for the existing assertions.
    """

    def __init__(self):
        """Initialise counters for played frames/samples and stop (barge-in) calls."""
        self.frames = 0
        self.samples = 0
        self.clear_calls = 0

    async def play(self, samples: np.ndarray, sample_rate: int) -> None:
        """Record one played chunk (counts the chunk + its sample length)."""
        self.frames += 1
        self.samples += len(samples)

    async def stop(self) -> None:
        """Record an interrupt-driven playback flush (barge-in)."""
        self.clear_calls += 1


# ------------------------------------------------------------------- stubs
class StubVAD:
    """Deterministic VAD: high probability for loud frames, ~0 for quiet ones."""

    def __init__(self, threshold_amp: float = 0.1):
        """``threshold_amp`` is the mean-abs amplitude above which a frame is 'speech'."""
        self.threshold_amp = threshold_amp

    def probability(self, frame: np.ndarray) -> float:
        """Return 0.95 if the frame is 'loud' (speech), else 0.0 (silence)."""
        return 0.95 if float(np.abs(frame).mean()) > self.threshold_amp else 0.0

    def reset(self):
        """No-op (stateless stub)."""

    def warmup(self):
        """No-op."""


class StubSTT:
    """Returns scripted transcripts in order, ignoring the audio bytes."""

    def __init__(self, transcripts: list[str] | None = None):
        """``transcripts`` is consumed FIFO; an exhausted/empty queue yields ''."""
        self.queue = list(transcripts or [])
        self.calls = 0

    async def transcribe(self, audio: np.ndarray) -> str:
        """Pop and return the next scripted transcript (or '' if none left)."""
        self.calls += 1
        return self.queue.pop(0) if self.queue else ""


class StubTTS:
    """Emits a fixed number of small silent chunks per synthesis (no Piper)."""

    def __init__(self, chunks: int = 3, chunk_samples: int = 256):
        """Configure how many chunks and how many samples each synthesis yields."""
        self.native_sr = SR
        self._chunks = chunks
        self._chunk_samples = chunk_samples
        self.calls = 0

    def synthesize_stream(self, text: str):
        """Yield ``chunks`` float32 silent arrays so speaking advances deterministically."""
        self.calls += 1
        for _ in range(self._chunks):
            yield np.zeros(self._chunk_samples, dtype=np.float32)

    async def synthesize(self, text: str) -> np.ndarray:
        """Concatenate the streamed chunks into one array (non-streaming helper)."""
        return np.concatenate(list(self.synthesize_stream(text)))

    def warmup(self):
        """No-op."""


class InterruptingTTS(StubTTS):
    """A StubTTS that arms the session's interrupt event as synthesis is pulled.

    Used to deterministically simulate a barge-in landing *during* a sentence:
    ``_speak_sentence`` clears the event, then pulls the generator (which sets the
    event here), so the very first chunk-check trips the interrupt branch — exactly
    the mid-sentence barge-in path, without real-time racing.
    """

    def __init__(self, state, chunks: int = 3, chunk_samples: int = 256):
        """Bind to the SessionState whose ``interrupt_event`` will be set on synth."""
        super().__init__(chunks=chunks, chunk_samples=chunk_samples)
        self._state = state

    def synthesize_stream(self, text: str):
        """Arm the interrupt, then yield chunks as usual."""
        self._state.interrupt_event.set()
        yield from super().synthesize_stream(text)


class StubLLM:
    """Returns a canned answer; can be told to bump turn_id to simulate supersession."""

    def __init__(self, answer: str = "The revenue grew by 25 percent.", state=None, bump_turn=False):
        """If ``bump_turn`` is set, the answer call increments ``state.turn_id`` to
        emulate a navigation arriving while the answer is being generated (R11)."""
        self._answer = answer
        self._state = state
        self._bump = bump_turn
        self.calls = 0

    async def answer(self, question: str, ctx, slide_text: str) -> str:
        """Return the canned answer (optionally simulating a superseding turn)."""
        self.calls += 1
        if self._bump and self._state is not None:
            self._state.turn_id += 1
        return self._answer

    async def generate_script(self, *a, **k) -> str:
        """Canned narration for build-time use (unused in most Tier-1 tests)."""
        return "This is a sentence. Here is another one."

    async def aclose(self):
        """No-op."""


class StubIntent:
    """Returns scripted (intent, slide_index) pairs in order."""

    def __init__(self, script: list[tuple[Intent, int | None]] | None = None):
        """``script`` is consumed FIFO; exhausted → IGNORE."""
        self.queue = list(script or [])

    def classify(self, text: str):
        """Pop and return the next scripted (intent, slide) decision."""
        return self.queue.pop(0) if self.queue else (Intent.IGNORE, None)


class StubKB:
    """Returns a canned retrieved context (no FAISS/BM25 index required)."""

    def retrieve(self, job_id: str, query: str, current_slide: int) -> RetrievedContext:
        """Return a single canned chunk citing the current slide."""
        from models import Chunk

        return RetrievedContext(
            chunks=[Chunk(chunk_id="c0", slide_index=current_slide, text="canned context")],
            sources=[current_slide],
        )


class FakeRegistry:
    """Holds the stub services in the same shape as the real ServiceRegistry (§6)."""

    def __init__(self, **overrides):
        """Build a registry of stubs; pass overrides (e.g. ``intent=StubIntent([...])``)."""
        self.vad = overrides.get("vad", StubVAD())
        self.stt = overrides.get("stt", StubSTT())
        self.tts = overrides.get("tts", StubTTS())
        self.intent = overrides.get("intent", StubIntent())
        self.llm = overrides.get("llm", StubLLM())
        self.kb = overrides.get("kb", StubKB())


# --------------------------------------------------------- builders / helpers
def make_plan(n_slides: int = 2, sentences_per_slide: int = 3,
              target_seconds: float = 60.0) -> PacingPlan:
    """Build a PacingPlan with identical scripts on all three tracks for testing.

    Each slide gets ``sentences_per_slide`` short sentences. per_slide_seconds is
    split evenly so drift math is predictable.
    """
    def slide_scripts(track: Track) -> list[SlideScript]:
        out = []
        for si in range(n_slides):
            sents = [
                ScriptSentence(text=f"Slide {si} sentence {sj}.", word_count=4)
                for sj in range(sentences_per_slide)
            ]
            out.append(SlideScript(slide_index=si, track=track, sentences=sents))
        return out

    per_slide = {i: target_seconds / n_slides for i in range(n_slides)}
    return PacingPlan(
        job_id="test-job", persona=Persona.GENERAL, target_seconds=target_seconds,
        per_slide_seconds=per_slide, priorities={},
        scripts={t: slide_scripts(t) for t in (Track.STANDARD, Track.SUMMARY, Track.TURBO)},
    )


def make_worker(registry: FakeRegistry | None = None, plan: PacingPlan | None = None):
    """Construct a ChatterbotAgentWorker wired to fakes, ready for direct-method tests.

    Returns ``(worker, state, fake_source)``. ``audio_out`` is a FakeAudioSource;
    ``audio_in`` is left None (set it to a FakeAudioStream for full-loop tests).
    Bypasses ``setup_audio``/``run`` (which need LiveKit) — tests call the inner
    methods or the loops directly.
    """
    from config import get_settings

    reg = registry or FakeRegistry()
    plan = plan or make_plan()
    first = plan.scripts[Track.STANDARD][0].sentences
    tracker = PlaybackTracker(first, out_sr=reg.tts.native_sr,
                              wpm=150)
    state = SessionState(session_id="s1", job_id="test-job", plan=plan, tracker=tracker)
    worker = ChatterbotAgentWorker(ctx=None, registry=reg, settings=get_settings(), state=state)
    fake_source = FakeAudioSource()
    worker.audio_out = fake_source
    return worker, state, fake_source


def loud_frame(samples: int) -> np.ndarray:
    """A 'speech' frame: amplitude above the StubVAD threshold."""
    return np.full(samples, 0.5, dtype=np.float32)


def quiet_frame(samples: int) -> np.ndarray:
    """A 'silence' frame: near-zero amplitude."""
    return np.zeros(samples, dtype=np.float32)
