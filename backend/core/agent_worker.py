"""
core/agent_worker.py — ChatterbotAgentWorker: the orchestration core (IMPLEMENTATION.md §9, §19).

This is the heart of the system. It owns a :class:`SessionState` and runs two
concurrent asyncio tasks over a LiveKit room:

  * _input_loop()     — always-on listener: VAD on the mic stream, captures
                        utterances, and signals barge-in (never takes the lock).
  * _narration_loop() — speaker: synthesizes + pushes the script sentence-by-
                        sentence, yielding immediately on interrupt.

Coordination (§19):
  * transition_lock (asyncio.Lock) serialises every audio producer → the doc's
    "Serialized Q&A Transition Locking" (no audio collision).
  * interrupt_event (asyncio.Event) is set by the listener on confirmed barge-in
    and polled by the speaker between frame pushes.
  * turn_id guards against a late answer being spoken after the user moved on
    (Context Integrity Verification, R11).

The pure VAD framing/segmentation logic lives in :class:`SpeechBuffer` so it can
be unit tested without LiveKit or audio hardware.
"""

from __future__ import annotations

import time

import numpy as np

from config import Settings
from core.pacing import PERSONA_PROFILES, PacingService
from core.playback_tracker import PlaybackTracker
from core.session_state import ResumePoint, SessionState
from deps import ServiceRegistry
from models import Intent, SessionPhase, Track
from utils.audio import Reframer, float32_to_int16, int16_to_float32
from utils.logging import get_logger


class SpeechBuffer:
    """Pure VAD-driven utterance segmenter (no LiveKit dependency, §10/§19).

    Fed one VAD probability + frame at a time, it tracks speech onset (with a
    min-speech debounce to reject coughs) and utterance end (trailing silence),
    and accumulates the speech audio for STT. Kept dependency-free so the onset /
    silence logic is directly unit-testable.
    """

    def __init__(self, settings: Settings):
        """Configure debounce/silence thresholds (in frames) from settings."""
        self.s = settings
        frame_ms = 1000 * settings.vad_frame_samples / settings.sample_rate
        self._min_speech_frames = max(1, int(settings.vad_min_speech_ms / frame_ms))
        self._silence_frames = max(1, int(settings.vad_silence_ms / frame_ms))
        self._max_frames = int(settings.stt_max_utterance_ms / frame_ms)
        self.reset()

    def reset(self) -> None:
        """Clear all accumulation/counters (new stream or after taking an utterance)."""
        self._frames: list[np.ndarray] = []
        self._speech_run = 0
        self._silence_run = 0
        self._in_speech = False
        self._just_started = False

    def update(self, frame: np.ndarray, prob: float) -> None:
        """Process one VAD result; update speech/silence runs and buffer audio.

        Enters the speech state only after ``min_speech`` consecutive above-
        threshold frames (debounce). While in speech, frames are accumulated and
        trailing silence is counted toward the utterance-end decision.
        """
        self._just_started = False
        speech = prob > self.s.vad_threshold
        if speech:
            self._speech_run += 1
            self._silence_run = 0
            if not self._in_speech and self._speech_run >= self._min_speech_frames:
                self._in_speech = True
                self._just_started = True
        else:
            self._silence_run += 1
            self._speech_run = 0
        if self._in_speech:
            self._frames.append(frame)

    def just_started_speech(self) -> bool:
        """True only on the single frame where confirmed speech onset occurred.

        The agent uses this to fire the interrupt event exactly once per barge-in.
        """
        return self._just_started

    def utterance_complete(self) -> bool:
        """True when the buffered utterance should be flushed to STT.

        Triggered by enough trailing silence after speech, or by hitting the
        max-utterance cap (R3) so transcription latency stays bounded.
        """
        if not self._in_speech:
            return False
        return self._silence_run >= self._silence_frames or len(self._frames) >= self._max_frames

    def take(self) -> np.ndarray:
        """Return the accumulated utterance audio as one array and reset the buffer."""
        audio = np.concatenate(self._frames) if self._frames else np.empty(0, np.float32)
        self.reset()
        return audio


class ChatterbotAgentWorker:
    """Per-session orchestrator running the full-duplex loops over a LiveKit room (§9)."""

    def __init__(self, ctx, registry: ServiceRegistry, settings: Settings, state: SessionState):
        """Wire the worker to its LiveKit JobContext, shared services, and session state.

        ``ctx`` is the LiveKit Agents JobContext (already connected to the room).
        Audio I/O handles (``audio_in`` async iterator, ``audio_out`` source) are
        established in :meth:`setup_audio`. The exact LiveKit rtc API is version
        sensitive (recheck R1/R5); the audio wiring is isolated to a few methods.
        """
        self.ctx = ctx
        self.reg = registry
        self.s = settings
        self.state = state
        self.log = get_logger(session_id=state.session_id, component="agent")
        self.audio_in = None        # async iterator of inbound AudioFrames
        self.audio_out = None       # rtc.AudioSource (outbound)
        self._reframer = Reframer(settings.vad_frame_samples)

    # ----------------------------------------------------------- audio setup
    async def setup_audio(self) -> None:
        """Subscribe to the participant's mic and publish the agent's output track.

        Inbound: an AudioStream resampled to the canonical 16 kHz mono (VAD/STT).
        Outbound: an AudioSource at the Piper voice's NATIVE rate (R4) — the two
        streams are independent. This method localises every LiveKit rtc call so a
        version bump only touches here.
        """
        from livekit import rtc

        out_sr = self.reg.tts.native_sr
        self.audio_out = rtc.AudioSource(out_sr, 1)
        track = rtc.LocalAudioTrack.create_audio_track("chatterbot", self.audio_out)
        await self.ctx.room.local_participant.publish_track(track)

        # Attach to the first remote audio track (single-user assumption, doc).
        async for participant in self._iter_remote_audio_tracks():
            self.audio_in = rtc.AudioStream(
                participant, sample_rate=self.s.sample_rate, num_channels=1
            )
            break

    async def _iter_remote_audio_tracks(self):
        """Yield remote audio tracks as participants publish them.

        Abstracted so the (version-sensitive) subscription mechanics are isolated.
        In practice this awaits the room's track-subscribed events; kept minimal
        here as the integration seam.
        """
        for participant in self.ctx.room.remote_participants.values():
            for pub in participant.track_publications.values():
                if pub.track is not None:
                    yield pub.track

    # ------------------------------------------------------------------- run
    async def run(self) -> None:
        """Set up audio then run the listener + speaker loops until the session ends.

        ``asyncio.gather`` runs both loops concurrently; this is the entry point
        the LiveKit Agents ``entrypoint`` calls after connecting (§8.2).
        """
        import asyncio

        await self.setup_audio()
        self.state.phase = SessionPhase.READY
        await asyncio.gather(self._input_loop(), self._narration_loop())

    # ------------------------------------------------------------ listener
    async def _input_loop(self) -> None:
        """Always-on listener: run VAD, detect barge-in, capture utterances (§9.2).

        Never acquires the transition lock (so it can't deadlock with the speaker).
        On confirmed onset during SPEAKING it sets the interrupt event; once an
        utterance completes it schedules :meth:`_on_utterance` to handle it.
        """
        import asyncio

        buf = SpeechBuffer(self.s)
        self.reg.vad.reset()
        async for frame in self.audio_in:
            pcm = np.frombuffer(frame.data, dtype=np.int16)
            for chunk in self._reframer.push(int16_to_float32(pcm)):
                prob = self.reg.vad.probability(chunk)
                buf.update(chunk, prob)
                if buf.just_started_speech() and self.state.phase == SessionPhase.SPEAKING:
                    self.state.interrupt_event.set()    # barge-in!
                if buf.utterance_complete():
                    audio = buf.take()
                    asyncio.create_task(self._on_utterance(audio))

    # ------------------------------------------------------------- speaker
    async def _narration_loop(self) -> None:
        """Speaker loop: deliver the script sentence-by-sentence, honouring drift (§9.2).

        Holds the transition lock only while speaking a single sentence, so the
        utterance handler can interleave. After each sentence it lets the pacing
        service re-evaluate drift and possibly switch the upcoming track.
        """
        import asyncio

        while not self._done():
            if self.state.phase == SessionPhase.PAUSED:
                await asyncio.sleep(0.1)
                continue
            async with self.state.transition_lock:
                sentence = self._next_sentence()
                if sentence is None:
                    break
                if self.state.start_monotonic == 0.0:
                    self.state.start_monotonic = time.monotonic()
                self.state.phase = SessionPhase.SPEAKING
                await self._speak_sentence(sentence)
            self._maybe_repace()
        self.state.phase = SessionPhase.ENDED

    async def _speak_sentence(self, sentence) -> None:
        """Synthesize one sentence and push it to LiveKit, aborting on interrupt (§19).

        Clears the interrupt event first, then streams TTS chunks; between chunks
        it checks the event and, on barge-in, clears the output queue, records a
        sentence-start resume point, and returns immediately. Frame counts feed
        the PlaybackTracker so the resume index is accurate.
        """
        import asyncio

        from livekit import rtc

        self.state.interrupt_event.clear()
        sent_samples = 0
        # synthesize_stream is a blocking generator; pull it via a thread so the
        # event loop (and interrupt checking) keeps running.
        chunks = await asyncio.to_thread(lambda: list(self.reg.tts.synthesize_stream(sentence.text)))
        for chunk in chunks:
            if self.state.interrupt_event.is_set():
                await self.audio_out.clear_queue()      # drop buffered TTS now
                self.state.pending_resume = ResumePoint(
                    slide_index=self.state.current_slide,
                    track=self.state.current_track,
                    sentence_index=self.state.tracker.resume_point(),  # snapped to start
                )
                return
            frame = rtc.AudioFrame(
                data=float32_to_int16(chunk).tobytes(),
                sample_rate=self.reg.tts.native_sr,
                num_channels=1,
                samples_per_channel=len(chunk),
            )
            await self.audio_out.capture_frame(frame)
            self.state.tracker.on_frames_pushed(len(chunk))
            sent_samples += len(chunk)
        # Record the exact synthesized length so subsequent resume math is precise.
        self.state.tracker.set_exact_length(self.state.tracker.current_sentence_index(), sent_samples)

    # ---------------------------------------------------- utterance handler
    async def _on_utterance(self, audio: np.ndarray) -> None:
        """Transcribe + classify a captured utterance and route the action (§9.3).

        The router that realises the expected path: navigation, grounded Q&A,
        stop, or ignore. Holds the transition lock for the whole turn (so it can't
        collide with narration) and re-checks ``turn_id`` before speaking an answer
        so a superseded answer is never played (R11).
        """
        if audio.size == 0:
            return
        async with self.state.transition_lock:
            self.state.turn_id += 1
            my_turn = self.state.turn_id
            self.state.phase = SessionPhase.PROCESSING
            text = await self.reg.stt.transcribe(audio)
            if not text.strip():
                self._resume()                          # spurious VAD → resume
                return
            intent, slide_no = self.reg.intent.classify(text)
            self.log.info(f"utterance='{text}' intent={intent} slide={slide_no}")

            if intent in (Intent.NEXT, Intent.PREV, Intent.GOTO):
                self._navigate(intent, slide_no)
                self._resume()
            elif intent == Intent.QUESTION:
                self.state.phase = SessionPhase.ANSWERING
                ctx = self.reg.kb.retrieve(self.state.job_id, text, self.state.current_slide)
                answer = await self.reg.llm.answer(text, ctx, self._slide_text())
                if my_turn == self.state.turn_id:       # not superseded
                    await self._speak_text(answer)
                self._resume()
            elif intent == Intent.STOP:
                self.state.phase = SessionPhase.PAUSED
            else:  # IGNORE — backchannel/acknowledgment
                self._resume()

    async def _speak_text(self, text: str) -> None:
        """Synthesize and push an arbitrary text (e.g. a Q&A answer) to the output.

        Simpler than :meth:`_speak_sentence` — answers are not part of the tracked
        narration script, so no resume bookkeeping is needed.
        """
        from livekit import rtc

        import asyncio

        chunks = await asyncio.to_thread(lambda: list(self.reg.tts.synthesize_stream(text)))
        for chunk in chunks:
            frame = rtc.AudioFrame(
                data=float32_to_int16(chunk).tobytes(),
                sample_rate=self.reg.tts.native_sr,
                num_channels=1,
                samples_per_channel=len(chunk),
            )
            await self.audio_out.capture_frame(frame)

    # ------------------------------------------------------ action helpers
    def _navigate(self, intent: Intent, slide_no: int | None) -> None:
        """Change the current slide and reset the tracker (Context Integrity, §9.3).

        Clears any stale pending resume (you don't resume an old sentence after the
        user moved) and rebuilds the tracker for the new slide's sentences. Routes
        identically for voice intents and REST navigation (§7.2).
        """
        n_slides = self._slide_count()
        if intent == Intent.NEXT:
            self.state.current_slide = min(self.state.current_slide + 1, n_slides - 1)
        elif intent == Intent.PREV:
            self.state.current_slide = max(self.state.current_slide - 1, 0)
        elif intent == Intent.GOTO and slide_no is not None:
            self.state.current_slide = max(0, min(slide_no, n_slides - 1))
        self.state.pending_resume = None                # integrity: drop stale resume
        self._rebuild_tracker_for_current_slide()

    def _resume(self) -> None:
        """Restore the SPEAKING phase so the narration loop continues (§9.3).

        Seeks the tracker to the pending resume sentence (if any) — always a
        sentence start — guaranteeing zero context loss after a Q&A or spurious
        interrupt.
        """
        if self.state.pending_resume is not None:
            self.state.tracker.seek_to_sentence(self.state.pending_resume.sentence_index)
            self.state.pending_resume = None
        self.state.interrupt_event.clear()
        self.state.phase = SessionPhase.SPEAKING

    def _maybe_repace(self) -> None:
        """Re-evaluate schedule drift and switch the upcoming track if needed (§15.4).

        Compares elapsed wall-clock against the expected elapsed for slides done so
        far; PacingService decides STANDARD/SUMMARY/TURBO. Switching affects only
        upcoming slides, so the current sentence/tracker stays valid (§16.4).
        """
        drift = self._drift_pct()
        new_track = PacingService.select_track(drift, self.state.current_track, self.s)
        if new_track != self.state.current_track:
            self.log.info(f"pacing drift={drift:.3f} → switch {self.state.current_track}->{new_track}")
            self.state.current_track = new_track
            self._rebuild_tracker_for_current_slide()

    # ---------------------------------------------------- script accessors
    def _scripts(self):
        """Return the per-slide scripts for the current track from the pacing plan."""
        return self.state.plan.scripts.get(self.state.current_track, [])

    def _current_script(self):
        """Return the SlideScript for the current slide on the current track, or None."""
        for sc in self._scripts():
            if sc.slide_index == self.state.current_slide:
                return sc
        return None

    def _rebuild_tracker_for_current_slide(self) -> None:
        """Rebuild the PlaybackTracker for the current slide+track's sentences (§16.4)."""
        script = self._current_script()
        sentences = script.sentences if script else []
        wpm = PERSONA_PROFILES[self.state.plan.persona].wpm
        self.state.tracker = PlaybackTracker(sentences, self.reg.tts.native_sr, wpm)

    def _next_sentence(self):
        """Return the next unspoken sentence on the current slide, advancing slides.

        Pulls from the tracker's cursor; when a slide's sentences are exhausted it
        auto-advances to the next slide (rebuilding the tracker). Returns None when
        the whole presentation is finished.
        """
        script = self._current_script()
        if script is None:
            return None
        idx = self.state.tracker.current_sentence_index()
        if idx < len(script.sentences):
            sentence = script.sentences[idx]
            # advance cursor past this sentence for the next call
            self.state.tracker.seek_to_sentence(min(idx + 1, len(script.sentences) - 1))
            if idx + 1 >= len(script.sentences):
                self._advance_slide_after_current()
            return sentence
        self._advance_slide_after_current()
        return self._next_sentence() if not self._done() else None

    def _advance_slide_after_current(self) -> None:
        """Move to the next slide and rebuild the tracker (end-of-slide transition)."""
        if self.state.current_slide < self._slide_count() - 1:
            self.state.current_slide += 1
            self._rebuild_tracker_for_current_slide()
        else:
            self.state.current_slide = self._slide_count()  # sentinel past end

    def _slide_text(self) -> str:
        """Return the spoken text of the current slide (primary Q&A context, §18.2)."""
        script = self._current_script()
        return " ".join(s.text for s in script.sentences) if script else ""

    # --------------------------------------------------------------- status
    def _slide_count(self) -> int:
        """Number of slides in the current track's script."""
        return len(self._scripts())

    def _done(self) -> bool:
        """True when narration has run past the final slide."""
        return self.state.current_slide >= self._slide_count()

    def _drift_pct(self) -> float:
        """Fraction by which the session is over/under its time budget so far.

        Positive = behind schedule (over time). Used by both re-pacing and the
        status endpoint.
        """
        elapsed = self.state.elapsed(time.monotonic())
        done = min(self.state.current_slide, self._slide_count())
        expected = sum(self.state.plan.per_slide_seconds.get(i, 0.0) for i in range(done))
        target = self.state.plan.target_seconds or 1.0
        return (elapsed - expected) / target

    def handle_intent(self, intent: Intent, slide_index: int | None = None) -> None:
        """Public entry for REST-driven navigation (§7.2 parity).

        Lets the session API route NEXT/PREV/GOTO through the exact same handler
        the voice path uses, keeping state consistent.
        """
        self._navigate(intent, slide_index)
        self._resume()
