"""
tests/test_text_utils.py — Unit tests for utils/text.py (IMPLEMENTATION.md §13, §16).

Covers the dependency-free helpers that the intent classifier and playback tracker
rely on: sentence segmentation, normalisation, number parsing, and GOTO extraction.
No ML dependencies required.
"""

from __future__ import annotations

from utils.text import (
    looks_interrogative,
    normalize,
    parse_goto,
    split_sentences,
    word_count,
    word_to_int,
)


def test_split_sentences_basic():
    """Sentences split on terminal punctuation and are trimmed/non-empty."""
    s = split_sentences("Hello there. How are you? I am fine!")
    assert s == ["Hello there.", "How are you?", "I am fine!"]


def test_split_sentences_empty():
    """Empty/whitespace input yields no sentences."""
    assert split_sentences("   ") == []


def test_word_count():
    """word_count counts whitespace-delimited tokens."""
    assert word_count("one two three") == 3


def test_normalize_strips_filler():
    """Normalisation lowercases and removes filler words + extra whitespace."""
    assert normalize("Um, please   go   NEXT you know") == "go next"


def test_word_to_int_digit_and_word():
    """Digits and spelled-out numbers both convert; junk returns None."""
    assert word_to_int("5") == 5
    assert word_to_int("twelve") == 12
    assert word_to_int("banana") is None


def test_parse_goto_converts_to_zero_based():
    """'go to slide five' -> index 4 (1-based speech -> 0-based index)."""
    assert parse_goto("go to slide five") == 4
    assert parse_goto("jump to slide 10") == 9


def test_parse_goto_requires_cue():
    """Without a goto cue, parse_goto returns None even if a number is present."""
    assert parse_goto("the revenue was five million") is None


def test_looks_interrogative():
    """Question words / trailing '?' are detected; statements are not."""
    assert looks_interrogative("what does this mean")
    assert looks_interrogative("tell me more?")
    assert not looks_interrogative("okay sounds good")
