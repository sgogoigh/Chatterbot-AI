"""
core/playback_tracker.py — Deterministic sentence-level resume (IMPLEMENTATION.md §16).

The mechanism behind the doc's 100% context recovery. It maps the active track's
sentences to sample ranges in the OUTBOUND audio stream, tracks the live playback
cursor, and — crucially — always snaps the resume point to a SENTENCE START.

⚠️ R10 (push-vs-playout lag): we count frames *pushed* to LiveKit, which leads
actual playout by the buffer depth. Sample-exact mid-sentence resume would
therefore be wrong. The design avoids this entirely by (a) snapping resume to the
sentence start and (b) pushing one sentence at a time + clearing the queue on
interrupt — so the buffer lag never crosses a sentence boundary destructively.
Do NOT "optimise" this into sample-exact resume.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from models import ScriptSentence


@dataclass
class _Range:
    """Inclusive-start, exclusive-end sample range for one sentence."""

    start: int
    end: int


class PlaybackTracker:
    """Tracks playback position over a sentence list and computes resume points."""

    def __init__(self, sentences: list[ScriptSentence], out_sr: int, wpm: float = 150):
        """Build sample ranges for ``sentences`` in the OUTBOUND stream's sample rate.

        Ranges are estimated proportionally from word counts up front (so a resume
        point exists before any audio is synthesized) and refined to exact lengths
        as each sentence is actually synthesized (:meth:`set_exact_length`). The
        doc explicitly permits either estimate or WAV-derived ranges (§16.1).
        """
        self.out_sr = out_sr
        self.wpm = wpm
        self.sentences = sentences
        self._ranges: list[_Range] = []
        self._cursor = 0
        self._build_estimated_ranges()

    def _build_estimated_ranges(self) -> None:
        """Populate ``_ranges`` using a words→samples estimate per sentence.

        ``samples = words / wpm * 60 * out_sr``. Replaced by exact values once a
        sentence is synthesized; until then this gives a usable resume target.
        """
        cursor = 0
        self._ranges = []
        for s in self.sentences:
            dur_s = (s.word_count / self.wpm) * 60.0 if self.wpm else 0.0
            n = max(1, int(dur_s * self.out_sr))
            self._ranges.append(_Range(cursor, cursor + n))
            s.sample_start, s.sample_end = cursor, cursor + n
            cursor += n

    def set_exact_length(self, sentence_index: int, n_samples: int) -> None:
        """Replace an estimated range with the exact synthesized length and re-tile.

        Called right after a sentence is synthesized so subsequent ranges (and the
        cursor math) reflect real audio. Re-tiles all following sentences to keep
        ranges contiguous.
        """
        if not (0 <= sentence_index < len(self._ranges)):
            return
        start = self._ranges[sentence_index].start
        end = start + max(1, n_samples)
        self._ranges[sentence_index] = _Range(start, end)
        cursor = end
        for i in range(sentence_index + 1, len(self._ranges)):
            length = self._ranges[i].end - self._ranges[i].start
            self._ranges[i] = _Range(cursor, cursor + length)
            self.sentences[i].sample_start = cursor
            self.sentences[i].sample_end = cursor + length
            cursor += length
        self.sentences[sentence_index].sample_start = start
        self.sentences[sentence_index].sample_end = end

    def on_frames_pushed(self, n_samples: int) -> None:
        """Advance the playback cursor by ``n_samples`` (called while pushing TTS)."""
        self._cursor += n_samples

    def current_sentence_index(self) -> int:
        """Return the index of the sentence the cursor currently falls within.

        Binary search over the contiguous ranges. Clamped to the last sentence so
        a cursor past the end (playback finished) maps sensibly.
        """
        if not self._ranges:
            return 0
        starts = [r.start for r in self._ranges]
        idx = bisect_right(starts, self._cursor) - 1
        return max(0, min(idx, len(self._ranges) - 1))

    def resume_point(self) -> int:
        """Return the resume sentence index, SNAPPED to a sentence start (§16.2).

        This is the whole point of the tracker: after an interruption we resume at
        the *beginning* of the interrupted sentence, never mid-sentence, which is
        what guarantees zero semantic loss.
        """
        return self.current_sentence_index()

    def seek_to_sentence(self, idx: int) -> None:
        """Move the cursor to the start of sentence ``idx`` (used on resume / nav)."""
        if self._ranges:
            idx = max(0, min(idx, len(self._ranges) - 1))
            self._cursor = self._ranges[idx].start

    def reset(self) -> None:
        """Reset the cursor to the beginning (new slide / track rebuild, §16.4)."""
        self._cursor = 0
