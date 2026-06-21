"""
services/fast_intent_classifier.py — 6-class semantic intent (IMPLEMENTATION.md §13).

This is the component that missed its accuracy gate in the source doc
(89.9% vs >95%). The Chatterbot-AI improvement (§13.2) is a HYBRID classifier:

  1. A deterministic, high-precision RULE layer handles the lexical commands
     (NEXT / PREV / GOTO / STOP) — these are where adversarial/noisy cases hurt.
  2. An EMBEDDING prototype layer (cosine similarity vs per-class prototypes)
     arbitrates only the genuinely semantic split (QUESTION vs IGNORE).

Latency stays well under the 20 ms budget: rules are regex (microseconds) and the
embedding fallback is a single shared-MiniLM encode (~9  ms).
"""

from __future__ import annotations

import numpy as np

from models import Intent
from services.embeddings import EmbeddingService
from utils.text import (
    RE_NEXT,
    RE_PREV,
    RE_STOP,
    looks_interrogative,
    normalize,
    parse_goto,
)

# Prototype phrase banks per class. Expanded (≥ several phrases each) including
# ASR-corrupted variants so the embedding centroid is robust to noisy input.
# Only QUESTION and IGNORE are truly resolved by embeddings; the navigation
# banks exist as a safety net if the rule layer ever misses.
_PROTOTYPES: dict[Intent, list[str]] = {
    Intent.NEXT: ["next slide", "go forward", "move on", "continue", "nex slide", "next please"],
    Intent.PREV: ["previous slide", "go back", "back up", "last slide", "previous one"],
    Intent.GOTO: ["go to slide five", "jump to slide ten", "switch to slide three", "open slide two"],
    Intent.QUESTION: [
        "what does this mean", "why is that the case", "how does this work",
        "can you explain that", "tell me more about this", "what is the revenue",
        "which option is better", "define this term",
    ],
    Intent.STOP: ["stop", "pause", "hold on", "wait a moment", "that's enough"],
    Intent.IGNORE: ["okay", "i see", "got it", "uh huh", "sure", "alright", "mm hmm"],
}


class FastIntentClassifier:
    """Hybrid rule + MiniLM-prototype intent classifier (§13)."""

    def __init__(self, embeddings: EmbeddingService, threshold: float = 0.45):
        """Precompute per-class prototype embeddings from the phrase banks.

        Uses the SHARED MiniLM (§3) so no extra model is loaded. ``threshold`` is
        the minimum cosine similarity below which the result is treated as
        uncertain and resolved by the interrogative heuristic (QUESTION vs IGNORE).
        Tunable; swept in the intent benchmark (§21).
        """
        self.emb = embeddings
        self.threshold = threshold
        self._intents: list[Intent] = list(_PROTOTYPES.keys())
        # Prototype = mean (then re-normalised) of its phrase-bank embeddings.
        protos = []
        for intent in self._intents:
            vecs = self.emb.encode(_PROTOTYPES[intent], normalize=True)
            mean = vecs.mean(axis=0)
            mean = mean / (np.linalg.norm(mean) + 1e-9)
            protos.append(mean)
        self._proto_matrix = np.vstack(protos).astype(np.float32)  # (6, dim)

    def classify(self, text: str) -> tuple[Intent, int | None]:
        """Classify ``text`` into one of 6 intents, plus a slide index for GOTO.

        Order matters (§13.2): PREV is checked before NEXT so "go back" never
        reads as a forward move; GOTO is checked via :func:`parse_goto` which
        requires both a goto cue and a number. Only when no command rule fires do
        we fall back to the embedding prototypes, and below ``threshold`` we use
        the interrogative heuristic to split QUESTION from IGNORE. Returns
        ``(intent, slide_index_or_None)``.
        """
        t = normalize(text)
        if not t:
            return Intent.IGNORE, None

        # ---- 1) deterministic command rules (precision-first) ----
        if (n := parse_goto(t)) is not None:
            return Intent.GOTO, n
        if RE_PREV.search(t):
            return Intent.PREV, None
        if RE_NEXT.search(t):
            return Intent.NEXT, None
        if RE_STOP.search(t):
            return Intent.STOP, None

        # ---- 2) embedding prototype fallback (semantic) ----
        emb = self.emb.encode(t, normalize=True)
        sims = self._proto_matrix @ emb            # cosine (unit vectors), shape (6,)
        best = int(np.argmax(sims))
        intent = self._intents[best]

        if float(sims[best]) < self.threshold:
            # Uncertain: decide between asking something vs. acknowledging.
            intent = Intent.QUESTION if looks_interrogative(t) else Intent.IGNORE
        return intent, None

    def warmup(self) -> None:
        """Run one classification so the first live call isn't cold (§6)."""
        self.classify("next slide")
