"""
prompts/script_generation.py — Narration script prompt templates (IMPLEMENTATION.md §14.2).

The build step calls the LLM once per (slide, track). These helpers assemble the
chat messages that constrain the model to produce *spoken* narration that fits a
word budget — the lever PacingService relies on for time-locked delivery.
"""

from __future__ import annotations

from models import Persona, SlideContent, Track

# Persona-specific tone guidance injected into the system prompt.
_PERSONA_TONE = {
    Persona.GENERAL: "a clear, neutral professional tone",
    Persona.TEACHER: "a warm, instructive tone that explains concepts step by step",
    Persona.MEETING: "a concise, businesslike tone suited to a status meeting",
    Persona.TEDX: "an engaging, narrative tone with vivid framing, like a TED talk",
}

# How aggressively each track compresses the narration (see §15.1).
_TRACK_GUIDANCE = {
    Track.STANDARD: "Deliver the full explanation.",
    Track.SUMMARY: "Be noticeably more concise; keep only the key points.",
    Track.TURBO: "Be maximally terse; only the single most essential idea per point.",
}


def build_script_messages(
    slide: SlideContent,
    word_budget: int,
    persona: Persona,
    track: Track,
) -> list[dict]:
    """Build the chat messages for generating one slide's narration on one track.

    Encodes three hard constraints: spoken style (no markdown/bullets), the
    ``word_budget`` (±10%, so synthesized duration tracks the pacing plan), and
    the persona/track tone. Returns OpenAI/Groq-style ``[{role, content}, ...]``.
    """
    system = (
        "You are a presentation narrator. Write narration to be read aloud by a "
        f"text-to-speech engine in {_PERSONA_TONE.get(persona, 'a clear tone')}. "
        "Output ONLY the spoken words: no markdown, no bullet points, no slide "
        "numbers, no stage directions. Use complete sentences. "
        f"{_TRACK_GUIDANCE.get(track, '')} "
        f"Target about {word_budget} words (within plus or minus 10 percent)."
    )
    user = (
        f"Slide title: {slide.title or '(untitled)'}\n\n"
        f"Slide text:\n{slide.body_text or '(none)'}\n\n"
        f"Speaker notes:\n{slide.notes or '(none)'}\n\n"
        f"Text detected in images:\n{slide.ocr_text or '(none)'}\n\n"
        "Write the narration for this slide now."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
