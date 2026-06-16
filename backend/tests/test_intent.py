"""
tests/test_intent.py — Intent classifier correctness (IMPLEMENTATION.md §13, §21).

Targets the accuracy gate the source doc missed (89.9% < 95%). The hybrid
rule+embedding classifier (§13.2) should nail the lexical navigation/control
commands deterministically and correctly split QUESTION vs IGNORE.

Requires sentence-transformers (the shared MiniLM); skipped automatically if it
is not installed so the pure-logic suite still runs.
"""

from __future__ import annotations

import pytest

st = pytest.importorskip("sentence_transformers", reason="MiniLM not installed")

from config import get_settings  # noqa: E402
from models import Intent  # noqa: E402
from services.embeddings import EmbeddingService  # noqa: E402
from services.fast_intent_classifier import FastIntentClassifier  # noqa: E402


@pytest.fixture(scope="module")
def clf() -> FastIntentClassifier:
    """A real classifier backed by the shared MiniLM (loaded once per module)."""
    s = get_settings()
    emb = EmbeddingService(s.minilm_model, s.models_dir)
    return FastIntentClassifier(emb)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("next slide please", Intent.NEXT),
        ("go forward", Intent.NEXT),
        ("go back to the previous slide", Intent.PREV),
        ("stop talking", Intent.STOP),
        ("pause for a second", Intent.STOP),
    ],
)
def test_navigation_rules(clf, text, expected):
    """Lexical commands are classified deterministically by the rule layer."""
    intent, _ = clf.classify(text)
    assert intent == expected


def test_goto_extracts_slide_number(clf):
    """GOTO returns the 0-based slide index parsed from the phrase."""
    intent, slide = clf.classify("go to slide five")
    assert intent == Intent.GOTO
    assert slide == 4


def test_prev_not_misread_as_next(clf):
    """'go back' must resolve to PREV even though 'go' appears (ordering, §13.2)."""
    intent, _ = clf.classify("go back")
    assert intent == Intent.PREV


def test_question_vs_ignore(clf):
    """Interrogative phrasing -> QUESTION; acknowledgement -> IGNORE."""
    assert clf.classify("what does this term mean")[0] == Intent.QUESTION
    assert clf.classify("okay got it")[0] == Intent.IGNORE
