"""
core/knowledge_base/chunker.py — Slide → retrievable chunks (IMPLEMENTATION.md §17.1).

Splits each slide's combined text (body + notes + OCR) into overlapping,
token-bounded chunks tagged with their source slide index, so retrieval can cite
the originating slide and the current-slide boost can be applied (§17).

"Tokens" here are approximated by whitespace words to stay dependency-free; the
chunk size is a retrieval-quality knob, not a hard model limit.
"""

from __future__ import annotations

from models import Chunk, SlideContent


def chunk_slides(
    slides: list[SlideContent], chunk_words: int, overlap: int
) -> list[Chunk]:
    """Produce overlapping word-bounded chunks across all slides.

    Each slide contributes one or more chunks; every chunk carries its
    ``slide_index`` so the retriever can attribute sources and boost the active
    slide. Overlap preserves context that would otherwise be split across a chunk
    boundary. Returns a flat list ready for embedding + BM25 indexing.
    """
    chunks: list[Chunk] = []
    for slide in slides:
        combined = "\n".join(
            part for part in (slide.title or "", slide.body_text, slide.notes, slide.ocr_text) if part
        ).strip()
        if not combined:
            continue
        words = combined.split()
        step = max(1, chunk_words - overlap)
        start = 0
        n = 0
        while start < len(words):
            piece = " ".join(words[start : start + chunk_words])
            chunks.append(
                Chunk(chunk_id=f"{slide.index}:{n}", slide_index=slide.index, text=piece)
            )
            start += step
            n += 1
    return chunks
