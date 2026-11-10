"""
services/llm_service.py — LLM client: Gemini / Groq / Ollama chain (IMPLEMENTATION.md §18).

Two uses:
  * build-time: per-(slide, track) narration script generation (batch).
  * runtime: grounded Q&A answering (latency-critical).

Backend order (``CB_LLM_PRIMARY``, default ``gemini``):
  gemini -> groq -> ollama   or   groq -> gemini -> ollama
Only backends with an API key configured are tried; Ollama (local, keyless) is
always the last resort.

Resilience (doc risk mitigations / §18.3):
  * tenacity retry with backoff on transient (transport / HTTP status) errors.
  * Each failing backend falls through to the next one.
  * If no backend answers, callers receive a graceful sentinel answer rather
    than an exception that would crash the live session (§20).

Client: httpx.AsyncClient against the OpenAI-compatible /chat/completions route
that all three backends expose (Gemini: ``/v1beta/openai``; Ollama: ``/v1``), so
no provider SDK is needed.

Both default cloud models reason: Gemini 3.x Flash always thinks
(``reasoning_effort`` low/medium/high; ``minimal`` is rejected) and Groq's
``openai/gpt-oss-120b`` is a reasoning model. Reasoning tokens count against
``max_tokens``, so each backend adds its ``*_thinking_headroom_tokens`` on top of
the caller's cap; otherwise a 300-token answer cap (or the 1-token health ping)
can return an empty message.

Measured 2026-11-11 (warm, one short answer): Groq gpt-oss-120b ~0.65 s,
Gemini 3.8 Flash ~1.7 s at effort=low (4.4 s at medium). Set
``CB_LLM_PRIMARY=groq`` when live Q&A latency matters most.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import Settings
from models import RetrievedContext
from prompts.qa_answer import build_qa_messages
from prompts.script_generation import build_script_messages
from utils.logging import get_logger

log = get_logger(component="llm")

_FALLBACK_ANSWER = "I'm sorry, I can't reach the knowledge service right now."


@dataclass(frozen=True)
class _Backend:
    """One OpenAI-compatible chat endpoint in the fallback chain."""

    name: str
    url: str
    model: str
    auth: str | None
    extra: dict = field(default_factory=dict)   # provider-specific payload fields
    token_headroom: int = 0                     # added to max_tokens (thinking models)


class LLMService:
    """Async LLM client over an ordered backend chain with a graceful final degrade."""

    def __init__(self, settings: Settings):
        """Create the HTTP client and resolve the configured backend chain.

        No network call here; availability is probed at startup via
        :meth:`health_check`.
        """
        self.s = settings
        self._client = httpx.AsyncClient(timeout=settings.llm_timeout_s)
        self._backends = self._resolve_backends(settings)
        log.info("LLM chain: " + " -> ".join(f"{b.name}({b.model})" for b in self._backends))

    @staticmethod
    def _resolve_backends(s: Settings) -> list[_Backend]:
        """Build the ordered chain from configured keys + ``llm_primary``."""
        cloud: dict[str, _Backend] = {}
        if s.gemini_api_key is not None:
            cloud["gemini"] = _Backend(
                name="gemini",
                url=f"{s.gemini_base_url}/chat/completions",
                model=s.gemini_model,
                auth=s.gemini_api_key.get_secret_value(),
                extra={"reasoning_effort": s.gemini_reasoning_effort},
                token_headroom=s.gemini_thinking_headroom_tokens,
            )
        if s.groq_api_key is not None:
            cloud["groq"] = _Backend(
                name="groq",
                url=f"{s.groq_base_url}/chat/completions",
                model=s.groq_model,
                auth=s.groq_api_key.get_secret_value(),
                extra={"reasoning_effort": s.groq_reasoning_effort} if s.groq_reasoning_effort else {},
                token_headroom=s.groq_thinking_headroom_tokens,
            )
        primary = s.llm_primary.lower()
        order = [primary] + [n for n in ("gemini", "groq") if n != primary]
        chain = [cloud[n] for n in order if n in cloud]
        chain.append(
            _Backend(
                name="ollama",
                url=f"{s.ollama_base_url}/v1/chat/completions",
                model=s.ollama_model,
                auth=None,
            )
        )
        return chain

    async def aclose(self) -> None:
        """Close the underlying HTTP client (called on app shutdown, §6)."""
        await self._client.aclose()

    # ------------------------------------------------------------------ public
    async def generate_script(self, slide, word_budget, persona, track) -> str:
        """Generate one slide's narration for one track (build-time, §14.2).

        Low temperature so word counts track the pacing budget; the SlideProcessor
        retries once if the count is far off. Returns plain spoken text.
        """
        messages = build_script_messages(slide, word_budget, persona, track)
        return await self._chat(messages, max_tokens=600, temperature=0.3)

    async def answer(self, question: str, ctx: RetrievedContext, slide_text: str) -> str:
        """Answer a user question grounded in slide context (runtime, §18.2).

        Uses the anti-hallucination prompt and a tight token cap so the spoken
        answer stays short. Returns the graceful fallback string if every backend
        is unavailable (never raises into the live session).
        """
        messages = build_qa_messages(question, ctx, slide_text)
        return await self._chat(messages, max_tokens=self.s.llm_max_answer_tokens, temperature=0.3)

    async def health_check(self) -> bool:
        """Probe backends at startup; return True if at least one LLM is reachable."""
        reply = await self._chat(
            [{"role": "user", "content": "ping"}], max_tokens=1, temperature=0.0
        )
        if reply == _FALLBACK_ANSWER:
            log.warning("LLM health check failed: no backend reachable")
            return False
        return True

    # ----------------------------------------------------------------- internal
    async def _chat(self, messages: list[dict], *, max_tokens: int, temperature: float) -> str:
        """Try each backend in order; return the first answer or a graceful fallback.

        Centralises backend selection + degradation so callers never branch on
        which LLM is live. Each backend call is individually retried (transient
        errors) by :meth:`_post`.
        """
        for backend in self._backends:
            try:
                return await self._post(backend, messages, max_tokens, temperature)
            except Exception as e:  # noqa: BLE001 - fall through to the next backend
                log.warning(f"LLM backend {backend.name} failed, trying next: {e}")
        log.error("All LLM backends unavailable")
        return _FALLBACK_ANSWER

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=0.5, max=4),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    async def _post(self, backend: _Backend, messages, max_tokens, temperature) -> str:
        """POST one OpenAI-style chat completion to ``backend`` and return its text.

        Retries transient transport/HTTP errors with exponential backoff
        (tenacity). Raises on persistent failure or an empty message so
        :meth:`_chat` falls through to the next backend.
        """
        headers = {"Authorization": f"Bearer {backend.auth}"} if backend.auth else {}
        payload = {
            "model": backend.model,
            "messages": messages,
            "max_tokens": max_tokens + backend.token_headroom,
            "temperature": temperature,
            "stream": False,
            **backend.extra,
        }
        resp = await self._client.post(backend.url, json=payload, headers=headers)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"].get("content")
        if not content or not content.strip():
            raise ValueError(f"{backend.name} returned an empty message")
        return content.strip()
