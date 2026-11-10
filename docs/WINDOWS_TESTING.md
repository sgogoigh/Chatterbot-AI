# Chatterbot-AI — Windows-Only Testing Plan (no Docker, no Linux)

> **Context:** Docker/Linux aren't available. This plan shows how to run and test
> the **full pipeline on Windows**, using the Groq + LiveKit credentials already
> in `.env`. Companion: [`E2E_TESTING.md`](./E2E_TESTING.md) (the tier strategy),
> [`ERRORS.md`](./ERRORS.md), [`IMPLEMENTATION.md`](./IMPLEMENTATION.md).
>
> **STATUS: IMPLEMENTED & VERIFIED.** The full pipeline runs on Windows.
> `python backend/run_local.py --selftest` passes — a real round trip through
> **edge-TTS → Faster-Whisper → hybrid intent → FAISS+BM25 RAG → Groq LLM →
> edge-TTS**, with Groq returning a correctly grounded answer. Full test suite:
> **42 passed, 1 skipped**. Live mic mode: `python backend/run_local.py`.

---

## 1. The key realization — Docker was never actually required

Re-checking the two things I'd flagged as "needs Linux":

| Blocker (old assumption) | Reality on Windows |
|---|---|
| **LiveKit needs Docker** | ❌ False. We only needed Docker to *self-host* a LiveKit server. You have **LiveKit Cloud** creds (`wss://…`), and `livekit` + `livekit-agents` have **Windows wheels** (already installed). LiveKit agents officially run on Windows and even have a **`console` mode** (terminal voice, no browser). |
| **Piper TTS needs Linux** | ✅ True for the *pip* package (`piper-phonemize`, R12) — but there are 3 Windows-friendly TTS paths (below). |
| Benchmarks on reference HW | Nice-to-have, not a blocker. |

So the **only** real Windows gap is the TTS engine. Everything else already runs
on Windows and is validated:

| Component | Windows status |
|---|---|
| VAD (Silero/onnxruntime) | ✅ validated (0.2 ms) |
| STT (faster-whisper) | ✅ validated; **or** offload to Groq Whisper |
| Intent (MiniLM hybrid) | ✅ validated (100% smoke) |
| RAG (FAISS + BM25) | ✅ validated |
| LLM | ✅ Groq Cloud (key in `.env`) |
| Orchestration (agent loops) | ✅ Tier-1: 12 tests green |
| **TTS** | ⛔ needs a Windows backend (this plan) |
| Transport (audio I/O) | ⛔ needs a Windows path (this plan) |

---

## 2. What I confirmed from the docs you linked

**Groq** (`https://console.groq.com/docs`): OpenAI-compatible.
- Chat: `POST /openai/v1/chat/completions`, `Authorization: Bearer $GROQ_API_KEY`.
  Models: `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `openai/gpt-oss-20b/120b`.
  (Param is `max_completion_tokens`; `max_tokens` still accepted.)
- **STT**: `POST /openai/v1/audio/transcriptions` — `whisper-large-v3`,
  `whisper-large-v3-turbo`. ⇒ we can run STT in the cloud too.
- **TTS**: `POST /openai/v1/audio/speech` — `canopylabs/orpheus-v1-english` (preview).
  ⇒ **Groq can do our TTS**, no local Piper needed.

**LiveKit** (`https://docs.livekit.io/agents/start/voice-ai/`):
- `livekit-agents` runs on **macOS / Linux / Windows**, **no Docker**.
- Agents have a **`console`** run mode = talk in the terminal with no browser.
- Needs `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` (you have these).

**Config fix already applied:** `config.py` now reads your **unprefixed** env names
(`GROQ_API_KEY`, `LIVEKIT_URL`, …) *and* the `CB_`-prefixed ones, and finds the
repo-root `.env` regardless of working directory. Verified loading your `.env`.

---

## 3. The two changes that unlock Windows testing

### 3.1 Pluggable TTS (replaces the Linux-only Piper pip package)
Introduce a small TTS interface with selectable backends (set `CB_TTS_BACKEND`):

| Backend | How | Cost / deps | Offline? | Notes |
|---|---|---|---|---|
| **`edge`** | `edge-tts` (Microsoft Edge neural voices) | free, `pip install edge-tts` | needs internet | High quality, trivial, **recommended default on Windows** |
| **`groq`** | Groq `/audio/speech` (Orpheus) | uses your Groq key; ~$22 / 1M chars | no | Everything stays on Groq; preview model |
| **`piper_exe`** | bundled `piper.exe` via subprocess | ~30 MB download | yes | Faithful to production Piper, fully local |
| **`pyttsx3`** | Windows SAPI5 | free, offline | yes | Robotic, zero network — good for CI |
| **`piper`** | pip Piper (Linux/container) | — | yes | Unchanged; used in Docker deploy |

All backends return float32 PCM at a known sample rate, so the agent's
`PlaybackTracker` and outbound transport work unchanged. `auto` picks `edge` on
Windows, `piper` elsewhere.

### 3.2 Pluggable audio transport (decouple the worker from LiveKit rtc)
Today `ChatterbotAgentWorker` builds `rtc.AudioFrame`/`AudioSource` inline. Extract
an `AudioTransport` interface (inbound float32 frame iterator + outbound
`play_chunk` / `stop_playback`) with three implementations:

| Transport | Use | Needs |
|---|---|---|
| **`LocalAudioTransport`** (`sounddevice`) | **Talk to your laptop in a terminal** — mic in, speakers out, real barge-in | `pip install sounddevice` (Windows wheel ✓), a mic |
| **`LiveKitTransport`** | Real WebRTC via your LiveKit Cloud; connect a browser client | LiveKit creds (have) |
| **Fakes** (done) | Tier-1 deterministic tests | nothing |

This also makes the Tier-1 fakes first-class and removes rtc from the core loop.

---

## 4. The recommended Windows test ladder

| Level | What you do | Exercises | Needs |
|---|---|---|---|
| **W0 — Tier-1** (done) | `pytest tests/test_agent_e2e.py` | orchestration logic | nothing ✅ |
| **W1 — Pipeline, file I/O** | feed a WAV question → get answer WAV; assert transcript/answer | real STT+intent+RAG+**Groq LLM**+TTS, no audio devices | Groq key, a TTS backend |
| **W2 — Pipeline, live mic** | `python run_local.py` → present, speak, interrupt, hear answers | the **whole loop** incl. real barge-in, on your laptop | mic, speakers, Groq key, TTS |
| **W3 — Real WebRTC** | run the agent against LiveKit Cloud; join via LiveKit **Agents Playground** (browser) or `console` | the actual WebRTC transport (R5 seam) | LiveKit creds (have) |

**W2 is the sweet spot**: it validates every novel mechanism (barge-in, resume,
pacing, grounded Q&A) end-to-end on Windows, with only `sounddevice` + a TTS
backend added. W3 additionally proves the LiveKit transport.

---

## 5. Build plan (once backend + transport are chosen)

1. **TTS abstraction** `services/tts/` — `base.py` (interface) + `edge.py`,
   `groq.py`, `piper_exe.py`, `pyttsx3.py`, `piper.py`; a `make_tts(settings)`
   factory. Swap `ServiceRegistry.load_tts` to use it. (`StubTTS` already conforms.)
2. **Transport abstraction** `core/transport/` — `base.py` + `local_audio.py`
   (sounddevice) + `livekit_transport.py` (move the rtc code here from the worker).
   Refactor `agent_worker` to call `transport.input_frames()/play_chunk()/stop_playback()`.
3. **`run_local.py`** — CLI: load a built deck (or build one inline from a sample
   `.pptx`), wire real services + `LocalAudioTransport`, run the worker. This is W2.
4. **`tests/test_pipeline_w1.py`** — file-in/file-out pipeline test (W1) using real
   services + Groq; marked `integration` (needs network + key), opt-in.
5. **LiveKit entrypoint** — finalize `main._run_agent_worker` for `livekit-agents`
   0.12 against LiveKit Cloud; document joining via the Playground (W3).
6. **requirements**: add `edge-tts`, `sounddevice` (Windows-friendly); keep
   `piper-tts` for the container only (move to an extras/requirements-linux.txt so
   Windows `pip install` doesn't choke on `piper-phonemize`).

---

## 6. Cost & privacy notes
- **Groq LLM**: tiny for testing (Q&A is ~300 tokens; ~$0.0002/answer on 70B).
- **Groq TTS (Orpheus)**: ~$22/1M chars → a 200-char answer ≈ $0.004. Fine for
  testing, but `edge`/`piper_exe` are free if you prefer zero spend.
- **edge-tts**: free, but sends text to a Microsoft endpoint (fine for test decks).
- **Fully offline option**: `stt_backend=local` + `tts_backend=piper_exe`/`pyttsx3`
  + Ollama for LLM → no cloud at all (Groq still simplest for the LLM).

---

## 7. Decisions made & what was built
- **TTS backend:** edge-tts (free). Implemented in `services/tts_edge.py` + factory
  `services/tts_factory.py` (`CB_TTS_BACKEND=auto` → edge on Windows, piper in container).
- **Transport:** local mic/speakers first. Implemented `core/transport/local_audio.py`
  (sounddevice) + `livekit_transport.py` (W3 seam) + `base.py`. The worker is now
  transport-agnostic (`audio_in` iterator + `audio_out` sink).
- **STT:** kept local Faster-Whisper (validated; transcribed the self-test perfectly).
- **Entry point:** `backend/run_local.py` — `--selftest` (headless) and live mic mode.

### Verified self-test output (real services)
```
STT    -> 'What was the revenue this quarter?'
Intent -> QUESTION
RAG    -> sources slides [0, 1]
ANSWER -> 'Our revenue this quarter was four million dollars, which is a
           twenty five percent growth. That's from the current slide.'
SELFTEST PASS
```

## 8. W3 (real WebRTC) — WIRED & VERIFIED (agent→participant)
The voice agent is now **dispatched automatically** when a session starts:
`POST /api/sessions` launches `core/agent_session.py`, which joins the session's
LiveKit Cloud room as `chatterbot-agent`, publishes its voice track, and runs the
full-duplex loop. So the frontend's **"Go live" now completes** — the browser joins
the same room and hears the agent.

Verified live (headless) with `backend/tests/verify_livekit_agent.py`:
```
agent_seen=True  track_subscribed=True  audio_frames=27
LIVEKIT AGENT DISPATCH PASS — agent joined the room and streamed narration to the participant.
```

**Still requires a real browser mic (can't be headless-tested here):** the
participant **mic → agent answer** direction (S14b). The plumbing is in place
(the agent subscribes to the participant's audio track and feeds it to VAD/STT);
it just needs a human with a microphone clicking "Go live" to confirm the
question→answer→resume round-trip live.
