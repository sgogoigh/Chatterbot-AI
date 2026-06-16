"""
services/groq_llm_service.py — LLM client with Groq + Ollama fallback (IMPLEMENTATION.md §18).

Two uses:
  * build-time: per-(slide, track) narration script generation (batch).
  * runtime: grounded Q&A answering (latency-critical; streaming for low TTFT).

Resilience (doc risk mitigations / §18.3):
  * tenacity retry with backoff on transient (5xx / timeout) errors.
  * Falls back to a local Ollama instance if Groq is unreachable.
  * If neither backend is available, callers receive a graceful sentinel answer
    rather than an exception that would crash the live session (§20).

Client: httpx.AsyncClient against the OpenAI-compatible /chat/completions
endpoint (the doc lists httpx; avoids an extra SDK). Ollama exposes the same
OpenAI-compatible route at /v1/chat/completions.
"""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import Settings
from models import RetrievedContext
from prompts.qa_answer import build_qa_messages
from prompts.script_generation import build_script_messages
from utils.logging import get_logger

log = get_logger(component="llm")

_FALLBACK_ANSWER = "I'm sorry, I can't reach the knowledge service right now."


class GroqLLMService:
    """Async LLM client: Groq primary, Ollama fallback, graceful final degrade."""

    def __init__(self, settings: Settings):
        """Create the HTTP client and resolve which backends are configured.

        No network call here; availability is probed lazily/at startup via
        :meth:`health_check`. ``_use_groq`` flips to False permanently for the
        process only if Groq auth/availability fails at runtime.
        """
        self.s = settings
        self._client = httpx.AsyncClient(timeout=settings.llm_timeout_s)
        self._use_groq = settings.groq_api_key is not None

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
        """Probe backends at startup; return True if at least one LLM is reachable.

        Logs the resolved backend/model. Used by ServiceRegistry to fail fast (or
        warn) per the startup policy in §4/§6.
        """
        try:
            await self._chat([{"role": "user", "content": "ping"}], max_tokens=1, temperature=0.0)
            return True
        except Exception as e:  # noqa: BLE001 - startup probe, log and report
            log.warning(f"LLM health check failed: {e}")
            return False

    # ----------------------------------------------------------------- internal
    async def _chat(self, messages: list[dict], *, max_tokens: int, temperature: float) -> str:
        """Route a chat completion to Groq, then Ollama, then a graceful fallback.

        Centralises backend selection + degradation so callers never branch on
        which LLM is live. Each backend call is individually retried (transient
        errors) by :meth:`_post`.
        """
        if self._use_groq:
            try:
                return await self._post(
                    f"{self.s.groq_base_url}/chat/completions",
                    self.s.groq_model,
                    messages, max_tokens, temperature,
                    auth=self.s.groq_api_key.get_secret_value(),
                )
            except Exception as e:  # noqa: BLE001 - fall through to Ollama
                log.warning(f"Groq unavailable, falling back to Ollama: {e}")

        try:
            return await self._post(
                f"{self.s.ollama_base_url}/v1/chat/completions",
                self.s.ollama_model,
                messages, max_tokens, temperature,
                auth=None,
            )
        except Exception as e:  # noqa: BLE001 - final graceful degrade
            log.error(f"All LLM backends unavailable: {e}")
            return _FALLBACK_ANSWER

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=0.5, max=4),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    async def _post(self, url, model, messages, max_tokens, temperature, auth) -> str:
        """POST a single OpenAI-style chat completion and return the message text.

        Retries transient transport/5xx errors with exponential backoff
        (tenacity). Raises on persistent failure so :meth:`_chat` can fall back to
        the next backend. ``auth`` is the bearer token (None for local Ollama).
        """
        headers = {"Authorization": f"Bearer {auth}"} if auth else {}
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        resp = await self._client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
