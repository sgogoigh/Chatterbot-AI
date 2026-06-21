"""
prompts/qa_answer.py — Grounded Q&A prompt template (IMPLEMENTATION.md §18.2).

Realises the anti-hallucination requirement (doc: <5% hallucination): the model
must answer ONLY from the supplied slide context, present the current slide's
context as primary, and cite slide numbers as sources.
"""

from __future__ import annotations

from models import RetrievedContext


def build_qa_messages(
    question: str,
    ctx: RetrievedContext,
    current_slide_text: str,
) -> list[dict]:
    """Build the chat messages for answering a user question from slide context.

    The system prompt hard-constrains grounding; the user message lays out the
    current slide first (primary context) followed by retrieved supporting chunks
    with their slide numbers, then the question. Returns Groq/OpenAI-style
    messages. Answers are meant to be short and spoken aloud.
    """
    system = (
        "You are a presentation assistant answering audience questions. "
        "Answer ONLY using the provided slide context. If the answer is not in "
        "the context, say you don't have that information in the slides. "
        "Keep the answer brief and conversational (it will be read aloud). "
        "When relevant, mention which slide the information comes from."
    )

    supporting = "\n\n".join(
        f"[Slide {c.slide_index + 1}] {c.text}" for c in ctx.chunks
    ) or "(no additional context retrieved)"

    user = (
        "PRIMARY CONTEXT (the slide currently on screen):\n"
        f"{current_slide_text or '(none)'}\n\n"
        "SUPPORTING CONTEXT (other relevant slides):\n"
        f"{supporting}\n\n"
        f"Audience question: {question}\n\n"
        "Answer now, grounded only in the context above."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
