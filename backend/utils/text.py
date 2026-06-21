"""
utils/text.py — Text helpers: sentence segmentation, normalisation, number parsing.

Used by:
  * PlaybackTracker / SlideProcessor — :func:`split_sentences` (§14, §16)
  * FastIntentClassifier — :func:`normalize`, :func:`word_to_int`,
    :func:`parse_goto`, :func:`looks_interrogative` (§13)

Deliberately dependency-free (pure stdlib + regex) so it is trivially unit
testable and adds nothing to the model footprint.
"""

from __future__ import annotations

import re

# Sentence boundary: end punctuation followed by whitespace. Kept simple and
# deterministic so the PlaybackTracker's sentence indices are reproducible.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# Filler words stripped during intent normalisation to stabilise matching.
_FILLER_RE = re.compile(r"\b(um+|uh+|like|you know|please|kindly)\b", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
# Punctuation stripped during normalisation (kept apostrophes for "that's" etc.).
_PUNCT_RE = re.compile(r"[^\w\s']")

# Spelled-out numbers 0–20 plus the common tens, enough for slide indices.
_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    # ordinals occasionally surface from ASR ("slide first"):
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

# Navigation / control regexes (anchored & ordered, see §13.2). PREV is checked
# before NEXT in the classifier so "go back" never reads as "go".
RE_NEXT = re.compile(r"\b(next|forward|continue|move on|go on|advance|proceed)\b", re.I)
RE_PREV = re.compile(r"\b(prev(ious)?|back|backward|last slide|go back|return)\b", re.I)
RE_STOP = re.compile(r"\b(stop|pause|halt|wait|hold on|that'?s enough|quiet)\b", re.I)
_GOTO_RE = re.compile(r"\b(go to|goto|jump to|switch to|open|show me|slide)\b", re.I)
_INTERROGATIVE_RE = re.compile(
    r"\b(what|why|how|when|where|who|which|can you|could you|explain|tell me|"
    r"does|do|is|are|define)\b",
    re.I,
)


def split_sentences(text: str) -> list[str]:
    """Split ``text`` into trimmed, non-empty sentences.

    Used to build the deterministic sentence list that the PlaybackTracker maps
    to audio sample ranges. A naive but stable splitter is intentional: resume
    determinism depends on segmentation being reproducible, not linguistically
    perfect.
    """
    if not text or not text.strip():
        return []
    parts = _SENTENCE_RE.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def word_count(text: str) -> int:
    """Return the number of whitespace-delimited words in ``text``.

    The unit PacingService uses to convert between words and seconds.
    """
    return len(text.split())


def normalize(text: str) -> str:
    """Lower-case, strip filler words, and collapse whitespace for intent matching.

    Reduces ASR noise variance before the rule layer / embedding lookup in the
    FastIntentClassifier so equivalent phrasings hash to the same form.
    """
    t = text.lower().strip()
    t = _PUNCT_RE.sub(" ", t)       # drop punctuation (ASR rarely produces it reliably)
    t = _FILLER_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t)
    return t.strip()


def word_to_int(token: str) -> int | None:
    """Convert a single numeric token (digit or spelled-out word) to an int.

    Returns ``None`` if the token is not a recognised number. Backs
    :func:`parse_goto` slide-number extraction.
    """
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _WORD_NUMBERS.get(token)


def parse_goto(text: str) -> int | None:
    """Extract a 0-based slide index from a GOTO-style phrase, else ``None``.

    Recognises both "go to slide 5" and "jump to twelve". The *spoken* number is
    treated as 1-based (humans say "slide one" for the first slide) and converted
    to a 0-based index. Returns ``None`` when no GOTO cue + number is present, so
    the classifier can fall through to other intents.
    """
    if not _GOTO_RE.search(text):
        return None
    for tok in re.findall(r"[a-z]+|\d+", text.lower()):
        n = word_to_int(tok)
        if n is not None and n >= 1:
            return n - 1            # 1-based speech -> 0-based index
    return None


def looks_interrogative(text: str) -> bool:
    """Heuristic: does ``text`` look like a question?

    Used as the tie-breaker between QUESTION and IGNORE when the embedding
    similarity is below threshold (§13.2).
    """
    return bool(_INTERROGATIVE_RE.search(text)) or text.strip().endswith("?")
