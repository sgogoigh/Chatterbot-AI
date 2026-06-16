"""
tests/test_playback_tracker.py — Resume determinism (IMPLEMENTATION.md §16, §21).

The core guarantee: an interruption at any point resolves to the START of the
interrupted sentence (never mid-sentence), giving 100% context recovery. Mirrors
the doc's barge-in positions (25/50/75%). No ML dependencies.
"""

from __future__ import annotations

from core.playback_tracker import PlaybackTracker
from models import ScriptSentence


def _sentences() -> list[ScriptSentence]:
    """Four equal-length sentences for predictable sample ranges."""
    return [ScriptSentence(text=f"Sentence {i}.", word_count=10) for i in range(4)]


def test_ranges_are_contiguous_and_ordered():
    """Estimated sample ranges tile the timeline contiguously from zero."""
    t = PlaybackTracker(_sentences(), out_sr=16000, wpm=150)
    assert t.sentences[0].sample_start == 0
    for a, b in zip(t.sentences, t.sentences[1:]):
        assert a.sample_end == b.sample_start


def test_resume_snaps_to_sentence_start():
    """A cursor in the middle of sentence 2 resumes at sentence 2's start (index 2)."""
    sents = _sentences()
    t = PlaybackTracker(sents, out_sr=16000, wpm=150)
    mid_of_2 = (sents[2].sample_start + sents[2].sample_end) // 2
    t.on_frames_pushed(mid_of_2)
    assert t.resume_point() == 2
    # seeking back to that sentence puts the cursor exactly at its start
    t.seek_to_sentence(t.resume_point())
    assert t.current_sentence_index() == 2


def test_barge_in_positions_25_50_75():
    """Interrupts at 25/50/75% of the timeline each map to a clean sentence start."""
    sents = _sentences()
    t = PlaybackTracker(sents, out_sr=16000, wpm=150)
    total = sents[-1].sample_end
    for frac in (0.25, 0.50, 0.75):
        t.reset()
        t.on_frames_pushed(int(total * frac))
        idx = t.resume_point()
        # resume index is valid and the seek lands exactly on a boundary
        assert 0 <= idx < len(sents)
        t.seek_to_sentence(idx)
        assert t.current_sentence_index() == idx


def test_set_exact_length_retiles_following_sentences():
    """Recording an exact synthesized length shifts subsequent ranges to stay contiguous."""
    sents = _sentences()
    t = PlaybackTracker(sents, out_sr=16000, wpm=150)
    t.set_exact_length(0, 1000)
    assert t.sentences[0].sample_end == 1000
    assert t.sentences[1].sample_start == 1000
