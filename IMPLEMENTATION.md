# Chatterbot-AI — Backend Implementation Specification

> **Scope:** This document is the **code-level implementation spec for the backend only**. The frontend is deliberately out of scope and will be specified separately.
> **Companion docs:** [`PLAN.md`](./PLAN.md) (blueprint / what & why), `../doc-text.txt` (full source text of *Voice AI Project.docx*).
> **Status legend used below:** ✅ documented & confirmed · 🔧 implementation decision made here (records a choice the doc left open) · ⚠️ **recheck / gap** — a documented inconsistency or risk; see [§24 Recheck Log](#24-recheck-log-mistakes-caught--gaps-flagged).
> **Audience:** a developer building the backend from scratch. Code blocks are **reference skeletons** (signatures, key algorithms, schemas), not final files. Where the doc and reality conflict, this spec follows reality and flags it.

---

## Table of Contents

1. [How to read this spec](#1-how-to-read-this-spec)
2. [Tech stack, dependencies & version policy](#2-tech-stack-dependencies--version-policy)
3. [Module layout (concrete)](#3-module-layout-concrete)
4. [Configuration & environment](#4-configuration--environment)
5. [Domain data models (schemas)](#5-domain-data-models-schemas)
6. [Application lifecycle & model loading](#6-application-lifecycle--model-loading)
7. [FastAPI REST API](#7-fastapi-rest-api)
8. [LiveKit integration & token service](#8-livekit-integration--token-service)
9. [The Agent Worker — orchestration core](#9-the-agent-worker--orchestration-core)
10. [VAD service (Silero)](#10-vad-service-silero)
11. [STT service (Faster-Whisper)](#11-stt-service-faster-whisper)
12. [TTS service (Piper)](#12-tts-service-piper)
13. [Intent classifier (MiniLM + hybrid rules)](#13-intent-classifier-minilm--hybrid-rules)
14. [SlideProcessor (.pptx ingestion + script generation)](#14-slideprocessor-pptx-ingestion--script-generation)
15. [PacingService (time-locked budgeting)](#15-pacingservice-time-locked-budgeting)
16. [PlaybackTracker (deterministic resume)](#16-playbacktracker-deterministic-resume)
17. [KnowledgeBase / RAG](#17-knowledgebase--rag)
18. [GroqLLMService (+ Ollama fallback)](#18-groqllmservice--ollama-fallback)
19. [Concurrency, locking & interrupt semantics](#19-concurrency-locking--interrupt-semantics)
20. [Error handling, logging & graceful degradation](#20-error-handling-logging--graceful-degradation)
21. [Testing & benchmark harness](#21-testing--benchmark-harness)
22. [Docker & compose](#22-docker--compose)
23. [Build order (sequenced checklist)](#23-build-order-sequenced-checklist)
24. [Recheck log: mistakes caught & gaps flagged](#24-recheck-log-mistakes-caught--gaps-flagged)

---

## 1. How to read this spec

The backend is a **single FastAPI process** that also hosts a **LiveKit Agent worker**. There are two planes:

- **Control plane (REST/HTTP):** upload `.pptx`, build the presentation, mint LiveKit tokens, query status.
- **Media plane (LiveKit WebRTC):** the agent joins a room, receives the user's mic audio, runs the full-duplex loop (VAD → STT → Intent → action → TTS), and publishes synthesized audio.

Everything is `asyncio`. Blocking ML inference (Whisper, MiniLM encode, Piper) is pushed off the event loop with `asyncio.to_thread()` or a bounded executor. Heavy models are **loaded once at startup** and shared as process-global singletons.

A presentation is identified by a **`job_id`** (created at upload, used for artifacts, scripts and the FAISS index). A live conversation is a **`session`** bound to one `job_id` and one LiveKit room. The doc constrains the system to **one concurrent presentation per instance** — we still namespace by `job_id` so multiple presentations can be *prepared*, only one *played* at a time.

---

## 2. Tech stack, dependencies & version policy

### 2.1 Runtime
- **Python 3.11+** (matches Dockerfile `python:3.11-slim`).
- **FastAPI + uvicorn** (ASGI), `asyncio` runtime.

### 2.2 `requirements.txt` (populated alongside this doc)

```txt
# ---- web / async ----
fastapi==0.115.*
uvicorn[standard]==0.30.*
python-multipart==0.0.*          # multipart upload (.pptx)
pydantic==2.*
pydantic-settings==2.*
httpx==0.27.*                    # async HTTP (Groq, Ollama fallback)

# ---- realtime media ----
livekit==0.17.*                  # rtc SDK (AudioStream/AudioSource)
livekit-agents==0.12.*           # agent worker framework
livekit-api==0.7.*               # AccessToken / room admin

# ---- ML / audio ----
onnxruntime==1.18.*              # Silero VAD inference (pure ONNX path, §10)
silero-vad>=5.1                  # bundles the Silero v5 .onnx (loaded via onnxruntime, NOT torch.hub)
faster-whisper==1.0.*            # CTranslate2 INT8 ASR (no torch)
sentence-transformers==2.7.*     # all-MiniLM-L6-v2 embeddings (pulls torch transitively)
piper-tts==1.2.*                 # Piper VITS TTS — ⚠️ Linux/macOS only (R12); no Windows wheel
numpy==1.26.*
scipy==1.13.*                    # resampling (signal.resample_poly)
soundfile==0.12.*                # WAV read/write

# ---- RAG ----
faiss-cpu==1.8.*
rank-bm25==0.2.*                 # lexical retrieval for hybrid search

# ---- ingestion ----
python-pptx==0.6.*
pillow==10.*                     # image handling for OCR
pytesseract==0.3.*               # OCR wrapper (tesseract installed in image)

# ---- ops ----
loguru==0.7.*
tenacity==8.*                    # retry/backoff for external APIs
pytest==8.*
pytest-asyncio==0.23.*
```

### 2.3 ⚠️ Version policy (recheck item R1)
The source doc pins very old versions (`livekit 0.11.1`, `faster-whisper 0.10.0`, `sentence-transformers 2.2.2`, `torch 2.1.0`) from ~2023. **Two corrections vs. the doc:**
1. The doc names `core/livekit_worker.py` but only lists the low-level `livekit` package. The real worker pattern requires **`livekit-agents`** (worker lifecycle, job dispatch, `AudioStream`/`AudioSource` helpers). Added above.
2. LiveKit / faster-whisper APIs changed materially since the pinned versions. We pin to **current minor lines** and treat exact APIs as drift-prone — see callouts in §8–§12. Lock versions in CI once the first green build exists.

---

## 3. Module layout (concrete)

```
backend/
├── main.py                      # FastAPI app factory + lifespan + agent bootstrap
├── config.py                    # pydantic-settings Settings (env-driven)
├── models.py                    # all pydantic domain + API schemas (§5)
├── deps.py                      # singleton registry / dependency providers
│
├── api/
│   ├── routes_presentation.py   # upload, build, slides, status
│   ├── routes_session.py        # start/stop session, LiveKit token, navigation
│   └── routes_health.py         # /healthz, /readyz
│
├── core/
│   ├── agent_worker.py          # ChatterbotAgentWorker (orchestrator) — §9
│   ├── session_state.py         # SessionState dataclass + state machine enum
│   ├── pacing.py                # PacingService — §15
│   ├── playback_tracker.py      # PlaybackTracker — §16
│   └── knowledge_base/
│       ├── __init__.py
│       ├── chunker.py           # slide → chunks
│       ├── store.py             # FAISS + BM25 persistence
│       └── knowledge_base.py    # KnowledgeBase facade (index + hybrid retrieve)
│
├── services/
│   ├── vad_service.py           # Silero VAD — §10
│   ├── stt_service.py           # Faster-Whisper — §11
│   ├── tts_service.py           # Piper — §12
│   ├── fast_intent_classifier.py# MiniLM prototype + rules — §13
│   ├── embeddings.py            # shared MiniLM SentenceTransformer singleton
│   ├── groq_llm_service.py      # Groq client (+ Ollama fallback) — §18
│   └── slide_processor.py       # python-pptx + OCR + script gen — §14
│
├── utils/
│   ├── audio.py                 # resample, float32<->int16, framing helpers
│   ├── text.py                  # sentence segmentation, word/number parsing
│   └── logging.py               # loguru config
│
├── prompts/
│   ├── script_generation.py     # per-track narration prompt templates
│   └── qa_answer.py             # grounded Q&A prompt template
│
├── data/                        # runtime artifacts (gitignored)
│   └── jobs/<job_id>/           # slides/, scripts.json, pacing.json, faiss/, bm25.pkl
│
└── tests/
    ├── test_benchmarks.py
    ├── test_e2e_latency.py
    ├── test_intent.py
    ├── test_pacing.py
    ├── test_playback_tracker.py
    └── fixtures/
```

🔧 **Decision:** `embeddings.py` hosts **one** shared MiniLM instance used by *both* the intent classifier and the RAG knowledge base. The doc loads MiniLM "once" conceptually but describes it in two modules; sharing the model avoids loading 22 MB twice and keeps the 23 MB footprint claim honest.

---

## 4. Configuration & environment

`config.py` uses `pydantic-settings`; all values come from env / `.env`. Nothing hard-codes secrets.

```python
class Settings(BaseSettings):
    # --- server ---
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # --- audio pipeline ---
    sample_rate: int = 16000          # canonical internal rate (VAD/STT)  ⚠️ R4
    vad_frame_samples: int = 512      # Silero requirement @16k (32 ms)    ✅
    vad_threshold: float = 0.65       # speech-prob interrupt threshold     ✅
    vad_min_speech_ms: int = 160      # consecutive speech to confirm onset 🔧
    vad_silence_ms: int = 600         # trailing silence => utterance end   🔧
    stt_max_utterance_ms: int = 8000  # hard cap on a buffered utterance    🔧 ⚠️ R3

    # --- models ---
    whisper_model: str = "small.en"
    whisper_compute_type: str = "int8"
    whisper_device: str = "cpu"
    minilm_model: str = "all-MiniLM-L6-v2"
    piper_voice: str = "en_US-lessac-medium"
    piper_quality: str = "medium"     # x_low | low | medium | high
    models_dir: str = "/models"

    # --- LLM ---
    groq_api_key: SecretStr | None = None
    groq_model: str = "llama-3.3-70b-versatile"  # ⚠️ R7 model id drifts
    groq_base_url: str = "https://api.groq.com/openai/v1"
    llm_timeout_s: float = 20.0
    ollama_base_url: str = "http://localhost:11434"   # fallback
    ollama_model: str = "llama3.1:8b"

    # --- LiveKit ---
    livekit_url: str                  # wss://...
    livekit_api_key: str
    livekit_api_secret: SecretStr

    # --- RAG ---
    rag_top_k: int = 5
    rag_max_context_tokens: int = 1200
    rag_current_slide_boost: float = 0.25   # additive score boost  🔧
    rag_chunk_tokens: int = 180
    rag_chunk_overlap: int = 40

    # --- pacing defaults ---
    default_persona: str = "GENERAL"
    pacing_drift_summary_pct: float = 0.08   # switch STANDARD->SUMMARY if >8% over
    pacing_drift_turbo_pct: float = 0.18     # switch ->TURBO if >18% over

    model_config = SettingsConfigDict(env_file=".env", env_prefix="CB_")
```

A `.env.example` ships with every key (no secrets). Startup fails fast if `livekit_*` or `groq_api_key` are missing **and** no Ollama fallback is reachable (§20).

---

## 5. Domain data models (schemas)

All in `models.py` (pydantic v2). These are the contracts between every layer.

```python
# ---------- enums ----------
class Persona(str, Enum):
    GENERAL = "GENERAL"; TEACHER = "TEACHER"; MEETING = "MEETING"; TEDX = "TEDX"

class Track(str, Enum):
    STANDARD = "STANDARD"; SUMMARY = "SUMMARY"; TURBO = "TURBO"

class SlidePriority(str, Enum):
    ANCHOR = "ANCHOR"; SUPPORTING = "SUPPORTING"; CONTEXTUAL = "CONTEXTUAL"

class Intent(str, Enum):
    NEXT = "NEXT"; PREV = "PREV"; GOTO = "GOTO"
    QUESTION = "QUESTION"; STOP = "STOP"; IGNORE = "IGNORE"

class SessionPhase(str, Enum):           # mirrors PLAN §11 state machine
    IDLE="IDLE"; LOADING="LOADING"; READY="READY"; SPEAKING="SPEAKING"
    LISTENING="LISTENING"; PROCESSING="PROCESSING"; ANSWERING="ANSWERING"
    PAUSED="PAUSED"; TRANSITIONING="TRANSITIONING"; ENDED="ENDED"

# ---------- ingestion ----------
class SlideContent(BaseModel):
    index: int                      # 0-based
    title: str | None
    body_text: str                  # extracted shape text
    notes: str                      # speaker notes
    ocr_text: str = ""              # text recovered from images via tesseract
    image_paths: list[str] = []

class ScriptSentence(BaseModel):
    text: str
    word_count: int
    # filled by PlaybackTracker once audio exists:
    sample_start: int | None = None
    sample_end: int | None = None

class SlideScript(BaseModel):
    slide_index: int
    track: Track
    sentences: list[ScriptSentence]
    target_seconds: float           # budget allocated by PacingService
    priority: SlidePriority

class PacingPlan(BaseModel):
    job_id: str
    persona: Persona
    target_seconds: float
    per_slide_seconds: dict[int, float]      # slide_index -> seconds
    priorities: dict[int, SlidePriority]
    scripts: dict[Track, list[SlideScript]]  # one full script per track

# ---------- RAG ----------
class Chunk(BaseModel):
    chunk_id: str
    slide_index: int
    text: str

class RetrievedContext(BaseModel):
    chunks: list[Chunk]
    sources: list[int]              # slide indices cited

# ---------- API ----------
class BuildRequest(BaseModel):
    target_minutes: float = Field(gt=0, le=120)
    persona: Persona = Persona.GENERAL

class BuildResponse(BaseModel):
    job_id: str
    slide_count: int
    tracks_built: list[Track]
    estimated_seconds: dict[Track, float]

class StartSessionRequest(BaseModel):
    job_id: str
    room_name: str | None = None

class StartSessionResponse(BaseModel):
    session_id: str
    room_name: str
    livekit_url: str
    token: str                      # participant join token

class NavigateRequest(BaseModel):
    session_id: str
    intent: Literal["NEXT", "PREV", "GOTO"]
    slide_index: int | None = None  # required for GOTO

class StatusResponse(BaseModel):
    session_id: str
    phase: SessionPhase
    current_slide: int
    current_track: Track
    elapsed_seconds: float
    budget_seconds: float
    drift_pct: float
```

---

## 6. Application lifecycle & model loading

`main.py` uses FastAPI **lifespan** to load models once and to start the LiveKit agent worker.

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    registry = ServiceRegistry()                # holds singletons (deps.py)
    # --- load heavy models once, off the event loop ---
    await asyncio.gather(
        registry.load_vad(settings),            # Silero (~1 MB)
        registry.load_embeddings(settings),     # MiniLM (~22 MB) shared
        registry.load_stt(settings),            # Faster-Whisper (~400 MB)
        registry.load_tts(settings),            # Piper handle (~65 MB)
    )
    await registry.warmup()                     # 1 dummy inference each (ONNX/CT2 warmup)
    app.state.registry = registry
    # --- start LiveKit agent worker as a background task ---
    app.state.agent_task = asyncio.create_task(run_agent_worker(settings, registry))
    try:
        yield
    finally:
        app.state.agent_task.cancel()
        await registry.aclose()
```

- **Warmup** matters: the VAD benchmark explicitly measures *post-warmup* latency (ONNX/CT2 first call is slow). Without warmup the <5 ms target is unreachable on the first request.
- `ServiceRegistry` (`deps.py`) exposes typed getters used by FastAPI `Depends(...)` and by the agent worker. One source of truth for every model handle.
- **Readiness:** `/readyz` returns 200 only after all models loaded + warmed; `/healthz` is a cheap liveness ping.

---

## 7. FastAPI REST API

All routes are `async`. Validation via pydantic. Errors return RFC-7807-ish JSON `{detail, code}`.

| Method | Path | Body | Returns | Purpose / expected path |
|--------|------|------|---------|--------------------------|
| `POST` | `/api/presentations` | multipart `file=.pptx` | `{job_id, slide_count}` | Save upload → `SlideProcessor.extract()` → persist `SlideContent[]`. **No** script gen yet. |
| `POST` | `/api/presentations/{job_id}/build` | `BuildRequest` | `202 {build_id}` | Run `PacingService` + LLM script gen (3 tracks) + `KnowledgeBase.index()`. Long-running → see §7.1. |
| `GET` | `/api/presentations/{job_id}/build/status` | — | `{state, slides_done, total}` | Poll build progress. |
| `GET` | `/api/presentations/{job_id}/slides` | — | `SlideContent[]` (sans raw images) | Frontend slide render metadata. |
| `GET` | `/api/presentations/{job_id}/slides/{i}/image` | — | image bytes | Slide raster for the viewer. |
| `POST` | `/api/sessions` | `StartSessionRequest` | `StartSessionResponse` | Create session, mint LiveKit token, register pending agent job for the room. |
| `POST` | `/api/sessions/{id}/navigate` | `NavigateRequest` | `StatusResponse` | Manual navigation from UI buttons (same router the voice intents hit). |
| `POST` | `/api/sessions/{id}/control` | `{action: pause\|resume\|stop}` | `StatusResponse` | Session control plane. |
| `GET` | `/api/sessions/{id}/status` | — | `StatusResponse` | Poll phase / slide / drift. |
| `GET` | `/healthz` / `/readyz` | — | `{status}` | Liveness / readiness. |

### 7.1 ⚠️ Long-running `build` (recheck R2)
`build` does PowerPoint parse + several LLM calls (one per slide × up to 3 tracks) + embedding/index. That can exceed an HTTP timeout for 50 slides. **Decision 🔧:** make `build` enqueue a background task and return `202 Accepted` immediately; expose progress via `GET /api/presentations/{job_id}/build/status` (`{state, slides_done, total}`). Single-process `asyncio.Task` is sufficient (no external queue) given the one-presentation constraint, but it must be cancel-safe and persist partial artifacts.

### 7.2 Navigation parity
Voice intents (NEXT/PREV/GOTO/STOP) and REST navigation **must route through the same handler** in the agent worker so state stays consistent. `routes_session.navigate` just forwards to `ChatterbotAgentWorker.handle_intent(...)`.

---

## 8. LiveKit integration & token service

### 8.1 Token minting (control plane)
```python
from livekit import api

def mint_join_token(settings, room: str, identity: str) -> str:
    return (api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret.get_secret_value())
            .with_identity(identity)
            .with_grants(api.VideoGrants(room_join=True, room=room,
                                         can_publish=True, can_subscribe=True))
            .to_jwt())
```
The browser uses this token to join; the **agent** joins the same room as a separate identity (`chatterbot-agent`).

### 8.2 Agent worker bootstrap (media plane)
```python
from livekit.agents import WorkerOptions, cli, JobContext

async def entrypoint(ctx: JobContext):
    await ctx.connect()                              # join the dispatched room
    registry = current_registry()                    # singletons
    worker = ChatterbotAgentWorker(ctx, registry, settings)
    await worker.run()                               # the orchestration loop (§9)
```
- ⚠️ **R5:** LiveKit Agents has two run modes — its own CLI process **or** programmatic dispatch from inside our FastAPI process. The doc implies a single container running both FastAPI and the worker. **Decision 🔧:** run the agent worker **in-process** as a background asyncio task (started in lifespan) using the Agents framework's programmatic API, dispatched to the room named in `StartSessionResponse`. This keeps "single containerized pipeline" true. If the in-process dispatch API proves unstable for the pinned version, fall back to a sidecar `python -m ...agent` process in the same container (documented in §22).

### 8.3 Audio I/O
- **Inbound:** subscribe to the participant's audio track → `rtc.AudioStream(track, sample_rate=16000, num_channels=1)`. LiveKit resamples WebRTC's 48 kHz down to our 16 kHz canonical rate, so VAD/STT receive correct frames. Each yielded `AudioFrame` carries int16 PCM → convert to float32 `[-1,1]`.
- **Outbound:** `source = rtc.AudioSource(sample_rate, 1)`; publish a track once; push synthesized frames via `source.capture_frame(frame)`. Interrupt = `source.clear_queue()` (drop buffered TTS instantly). See §12 for the sample-rate decision and §19 for interrupt timing.

---

## 9. The Agent Worker — orchestration core

`ChatterbotAgentWorker` (`core/agent_worker.py`) is the heart. It owns the `SessionState`, runs two concurrent loops, and enforces the transition lock.

### 9.1 SessionState
```python
@dataclass
class SessionState:
    session_id: str
    job_id: str
    phase: SessionPhase = SessionPhase.READY
    current_slide: int = 0
    current_track: Track = Track.STANDARD
    plan: PacingPlan = ...
    tracker: PlaybackTracker = ...
    start_monotonic: float = 0.0          # set on first SPEAKING
    turn_id: int = 0                      # bumped each interrupt/answer (R11)
    # interrupt coordination:
    interrupt_event: asyncio.Event = field(default_factory=asyncio.Event)
    transition_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending_resume: ResumePoint | None = None

@dataclass
class ResumePoint:                         # Deterministic Resume State (PLAN §4.5)
    slide_index: int
    track: Track
    sentence_index: int                    # snapped to sentence START
```

### 9.2 Two concurrent tasks
```python
async def run(self):
    self.state.phase = SessionPhase.READY
    await asyncio.gather(
        self._input_loop(),       # always-on: VAD + utterance capture
        self._narration_loop(),   # speaks the script, yields on interrupt
    )
```

**Input loop (full-duplex listener):**
```python
async def _input_loop(self):
    buf = SpeechBuffer(self.settings)          # framing + silence tracking
    async for frame in self.audio_in:          # 16k mono float32, re-chunked to 512
        prob = self.vad.probability(frame)     # Silero, ~0.34 ms
        buf.update(frame, prob)
        if buf.just_started_speech() and self.state.phase == SessionPhase.SPEAKING:
            self.state.interrupt_event.set()    # barge-in! (see §19)
        if buf.utterance_complete():            # trailing silence reached
            audio = buf.take()                  # numpy float32, <= stt_max_utterance_ms
            asyncio.create_task(self._on_utterance(audio))
```

**Narration loop (speaker):**
```python
async def _narration_loop(self):
    while not self._done():
        async with self.state.transition_lock:           # serialized w/ Q&A
            sentence = self._next_sentence()              # from tracker/resume point
            if sentence is None: break
            self.state.phase = SessionPhase.SPEAKING
            await self._speak_sentence(sentence)          # synth + push frames
        await self._maybe_repace()                        # PacingService drift check
```

`_speak_sentence` streams frames and checks the interrupt event between frames (§19); on interrupt it clears the AudioSource queue, records `pending_resume` (snapped to this sentence's start), and returns.

### 9.3 Utterance handler — the router (the expected path)
```python
async def _on_utterance(self, audio: np.ndarray):
    async with self.state.transition_lock:        # Serialized Q&A Transition Lock
        self.state.turn_id += 1
        my_turn = self.state.turn_id
        self.state.phase = SessionPhase.PROCESSING
        text = await self.stt.transcribe(audio)    # Faster-Whisper, ~1.5 s
        if not text.strip():
            return self._resume()                  # spurious VAD -> just resume
        intent, slide_no = self.intent.classify(text)   # ~9.4 ms

        match intent:
            case Intent.NEXT | Intent.PREV | Intent.GOTO:
                self._navigate(intent, slide_no)   # clears stale resume (integrity)
                self._resume()
            case Intent.QUESTION:
                self.state.phase = SessionPhase.ANSWERING
                ctx = self.kb.retrieve(text, current_slide=self.state.current_slide)
                answer = await self.llm.answer(text, ctx, self._slide_text())
                if my_turn == self.state.turn_id:  # not superseded (R11)
                    await self._speak_text(answer) # TTS the answer
                self._resume()                     # Context-Aware Resume
            case Intent.STOP:
                self.state.phase = SessionPhase.PAUSED
            case Intent.IGNORE:
                self._resume()                     # backchannel -> continue
```

`_navigate` implements **Context Integrity Verification**: any NEXT/PREV/GOTO clears `pending_resume` (you don't resume an old sentence after the user moved slides) and sets a fresh resume point at the new slide's first sentence. `_resume` restores `phase=SPEAKING` and lets `_narration_loop` continue from `pending_resume` (sentence start) on the correct track.

> The transition lock guarantees **no audio collision**: narration cannot push frames while an answer is synthesizing, because both hold the same lock. This is the doc's "Serialized Q&A Transition Locking."

---

## 10. VAD service (Silero)

`services/vad_service.py`.

- **Model:** Silero VAD. 🔧 **Decision (validated):** load the **ONNX** model and run it via **`onnxruntime`** directly — no `torch.hub`, no `torchaudio`. The `.onnx` is sourced from the `silero-vad` pip package's bundled `data/` dir, located via `importlib.util.find_spec` **without importing the package** (its `__init__` imports `torchaudio`, which we avoid). This is the path that yields sub-millisecond latency. ✅ Measured **0.19–0.20 ms** mean on the dev machine — beats the doc's 0.25–0.34 ms. *(Originally drafted against `torch.hub(..., onnx=True)`, which transitively requires `torchaudio` and failed at validation — see R6/R12; corrected to pure onnxruntime.)*
- **Contract:** `probability(frame_f32_512) -> float` — one Silero forward pass per 512-sample (32 ms @ 16 kHz) chunk. Silero is **stateful** (LSTM); keep the hidden state across calls within one stream and reset on session start.
- **Framing:** the input loop must hand exactly 512 samples per call (Silero v4/v5 requirement at 16 kHz). `SpeechBuffer` re-chunks the incoming `AudioFrame`s to 512.

```python
class VADService:
    def __init__(self, onnx_path: str): self._sess = ort.InferenceSession(onnx_path); self._reset()
    def _reset(self): self._state = np.zeros(...)   # LSTM state + sr tensor
    def probability(self, frame: np.ndarray) -> float:
        out, self._state = self._sess.run(None, {"input": frame[None], "state": self._state, "sr": SR})
        return float(out[0][0])
    def warmup(self): [self.probability(np.zeros(512, np.float32)) for _ in range(5)]
```

**Onset / interrupt logic (in `SpeechBuffer`, config-driven):**
- Enter speech when ≥ `vad_min_speech_ms` (≈5 frames) of consecutive `prob > vad_threshold` — debounces coughs/clicks.
- End utterance after `vad_silence_ms` of `prob < threshold`.
- ⚠️ **R6 — "5 ms onset" is a misread.** The doc's "VAD latency < 5 ms" is the **model inference time** (0.34 ms). The *perceptual onset* latency is bounded by the 32 ms frame + the `min_speech` debounce (~160 ms). We meet the inference target; we **must not** claim 5 ms end-to-end onset. Documented in §24.

---

## 11. STT service (Faster-Whisper)

`services/stt_service.py`.

```python
class STTService:
    def __init__(self, s: Settings):
        self.model = WhisperModel(s.whisper_model, device=s.whisper_device,
                                  compute_type=s.whisper_compute_type,
                                  download_root=s.models_dir)
    async def transcribe(self, audio_f32_16k: np.ndarray) -> str:
        return await asyncio.to_thread(self._transcribe_sync, audio_f32_16k)
    def _transcribe_sync(self, audio):
        segments, _ = self.model.transcribe(
            audio, language="en", beam_size=1, best_of=1,
            vad_filter=False, condition_on_previous_text=False)
        return " ".join(seg.text for seg in segments).strip()
```

- Runs in `asyncio.to_thread` → never blocks the event loop (doc requirement).
- Input is the float32 mono 16 kHz buffer captured by `SpeechBuffer` (already correct rate — no resample needed because `AudioStream` produced 16 kHz).
- `beam_size=1, best_of=1` = greedy, lowest latency (~1.5 s for 2 s audio), per doc. WER target < 10%.
- ⚠️ **R3 — utterance length cap.** Doc says STT "process audio streams of 2 seconds or less," but a real question ("what does this term mean and why does it matter?") is longer than 2 s. **Decision 🔧:** the 2 s figure is the *benchmark window*, not a hard limit. Cap buffered utterances at `stt_max_utterance_ms` (default 8 s) so transcription latency stays bounded; the silence-based segmenter, not a 2 s window, decides the boundary.

---

## 12. TTS service (Piper)

`services/tts_service.py`.

```python
class TTSService:
    def __init__(self, s: Settings):
        self.voice = PiperVoice.load(self._voice_path(s), config_path=...)
        self.native_sr = self.voice.config.sample_rate   # e.g. 22050 for lessac-medium
    async def synthesize(self, text: str) -> np.ndarray:
        # returns float32 PCM at self.native_sr, mono
        return await asyncio.to_thread(self._synth_sync, text)
    def _synth_sync(self, text) -> np.ndarray:
        chunks = [c.audio_int16_bytes for c in self.voice.synthesize(text)]
        return int16_bytes_to_f32(b"".join(chunks))
```

### 12.1 ⚠️ R4 — sample-rate mismatch (real bug to avoid)
The functional requirement says "16 kHz mono output," but **`en_US-lessac-medium` is 22 050 Hz**. If we push 22 050 Hz frames into a 16 000 Hz `AudioSource`, audio plays at the wrong pitch/speed.

🔧 **Decision:** make the **`AudioSource` sample rate = Piper voice native rate** (22 050 for lessac-medium), and keep the **VAD/STT path at 16 kHz** (independent, inbound). They are separate streams; they do not need to match. The doc's "16 kHz mono output" is treated as nominal, not binding. If a strict 16 kHz output is ever required, resample Piper output with `scipy.signal.resample_poly(audio, 16000, native_sr)` in `utils/audio.py`. This must be decided **before** PlaybackTracker computes sample ranges (§16), since ranges are in output-stream samples.

### 12.2 Streaming & first-byte
- Piper's `synthesize()` yields audio in chunks; push each chunk to the AudioSource as it arrives → low time-to-first-audio (doc target < 100 ms first byte; realistic ~ few hundred ms for first chunk on CPU — flagged R8).
- Quality via `piper_quality` env (`x_low`→speed, `medium/high`→quality), validated at startup; auto-download missing voice assets to `models_dir`.
- Speaking rate (100–180 WPM, doc FR-5) is controlled by Piper's `length_scale` parameter — expose as a per-persona setting fed by PacingService.

---

## 13. Intent classifier (MiniLM + hybrid rules)

`services/fast_intent_classifier.py`. **This is the component that missed its accuracy gate (89.9% vs >95%) — the spec deliberately strengthens it.**

### 13.1 Documented baseline
MiniLM embedding of the user phrase → cosine similarity to **per-class prototype embeddings** (mean of representative phrases) → argmax with a confidence threshold; below threshold → `IGNORE`. ~9.4 ms.

### 13.2 🔧 Improvement (Chatterbot-AI delta): hybrid rule + embedding
The accuracy loss was on **adversarial (68%) and noisy-ASR (67%)** cases. Navigation/control intents are highly lexical and deterministic, so handle them with a **high-precision rule layer first**, fall back to embeddings only for the genuinely semantic split (QUESTION vs IGNORE):

```python
def classify(self, text: str) -> tuple[Intent, int | None]:
    t = normalize(text)                       # lowercase, strip filler, fix common ASR slips
    # 1) deterministic command rules (precision-first)
    if RE_NEXT.search(t):  return Intent.NEXT, None
    if RE_PREV.search(t):  return Intent.PREV, None
    if (n := parse_goto(t)) is not None: return Intent.GOTO, n   # "go to slide five" -> 5
    if RE_STOP.search(t):  return Intent.STOP, None
    # 2) embedding prototype fallback (semantic)
    emb = self.embed(t)                       # shared MiniLM (§3)
    sims = cosine(emb, self.prototypes)       # (6,) vector
    intent = ARGMAX_INTENT(sims)
    if sims.max() < self.threshold:           # noisy/uncertain
        intent = Intent.QUESTION if looks_interrogative(t) else Intent.IGNORE
    return intent, None
```

- `parse_goto` uses `utils/text.word_to_int` to map "one".."twenty" and digits → slide index; clamps to `[0, slide_count)`.
- Prototype phrase banks expanded to ≥12 phrases/class incl. ASR-corrupted variants ("next slide"/"nex slide"/"move on"/"continue").
- `RE_*` regexes are anchored and ordered so "go back to the next point" doesn't misfire (PREV vs NEXT disambiguation tested in `test_intent.py`).
- Threshold tuned on the labeled corpus; default `0.45`, swept in benchmark.

### 13.3 Accuracy target
Goal ≥ 95% overall. The rule layer should take standard + most adversarial navigation cases to ~100% precision; embeddings only arbitrate QUESTION/IGNORE. ⚠️ If hybrid still misses 95% on the noisy set, escalate to a small logistic-regression head over MiniLM embeddings (still < 1 MB, < 20 ms) — recorded as a fallback in R-intent.

---

## 14. SlideProcessor (.pptx ingestion + script generation)

`services/slide_processor.py`. Two distinct responsibilities, called by two different endpoints.

### 14.1 Extraction (`POST /api/presentations`)
```python
def extract(self, pptx_path: str) -> list[SlideContent]:
    prs = Presentation(pptx_path)
    out = []
    for i, slide in enumerate(prs.slides):
        title = _title_of(slide)
        body  = "\n".join(sh.text for sh in slide.shapes if sh.has_text_frame)
        notes = slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else ""
        imgs  = _export_pictures(slide, job_dir)           # PNGs for the viewer
        ocr   = "\n".join(pytesseract.image_to_string(Image.open(p)) for p in imgs)
        out.append(SlideContent(index=i, title=title, body_text=body,
                                notes=notes, ocr_text=ocr, image_paths=imgs))
    return out
```
- Supports 1–50 slides (doc). Reject > 50 with `422`.
- `tesseract-ocr` is installed in the image (Dockerfile) → recovers text baked into images/diagrams, which feeds RAG and script generation.
- ⚠️ **R9 — slide rasterization.** `python-pptx` extracts *embedded pictures*, but it does **not** render a slide to a full-slide image. The frontend "slide viewer" needs a rendered slide. **Decision 🔧:** render via **LibreOffice headless** (`soffice --convert-to pdf` then pdf→png via `pdf2image`/poppler) at build time. Requires adding `libreoffice-core` + `poppler-utils` to the image. Flagged because the doc's Dockerfile omits these. Alternative: ship only embedded images and render slide chrome on the frontend (deferred to frontend phase).

### 14.2 Script generation (`build`)
For each slide × track, call the LLM with `prompts/script_generation.py`:
- Inputs: slide title/body/notes/ocr, target word budget for that (slide, track) from PacingService, persona tone, and a strict "spoken narration, N words ±10%, no markdown" instruction.
- Output: plain narration text → segmented into `ScriptSentence[]` by `utils/text.split_sentences`.
- Determinism: `temperature` low (≈0.3) so word counts track budgets; retry once if word count is >20% off budget.

---

## 15. PacingService (time-locked budgeting)

`core/pacing.py`. Pure, deterministic, mostly static methods (doc: "PacingService provides static methods"). **No I/O, fully unit-testable.**

### 15.1 🔧 Persona vs Track model (resolves PLAN §16.4)
- **Persona** sets *base speaking rate* and *buffer ratio*:

  | Persona | base WPM | buffer ratio |
  |---|---|---|
  | GENERAL | 150 | 0.10 |
  | TEACHER | 130 | 0.15 |
  | MEETING | 160 | 0.08 |
  | TEDX | 140 | 0.12 |

- **Track** is a *verbosity variant of the script* (how many words get written), produced by the LLM to fit progressively tighter budgets:
  - STANDARD = full budget, SUMMARY ≈ 0.7×, TURBO ≈ 0.5× word counts.
- They **compose**: `seconds(words) = words / WPM(persona) * 60`. All three tracks target the *same total duration*; they differ in pacing headroom, so when STANDARD drifts over, switching to SUMMARY/TURBO recovers time without speeding the voice unnaturally.

### 15.2 Budget allocation algorithm
```python
@staticmethod
def build_plan(slides: list[SlideContent], target_seconds: float, persona: Persona) -> PacingPlan:
    density = [PacingService.density(s) for s in slides]      # see 15.3
    weights = normalize(density)                              # sum = 1
    buffer  = PERSONA[persona].buffer_ratio
    usable  = target_seconds * (1 - buffer)
    per_slide = {i: usable * w for i, w in enumerate(weights)}
    priorities = PacingService.priorities(density)            # ANCHOR/SUPPORTING/CONTEXTUAL
    # word budgets per track derived from per_slide seconds & persona WPM:
    word_budget = {i: per_slide[i] * WPM(persona)/60 for i in ...}
    return PacingPlan(per_slide_seconds=per_slide, priorities=priorities, ...)
```

### 15.3 Density & priority
- `density(slide)` = weighted sum of: body word count, notes word count, OCR word count, and a **visual density factor** (image count / area). Normalized across the deck.
- **Priority:** top-tercile density (and slide 0 / last slide) → `ANCHOR`; mid → `SUPPORTING`; low → `CONTEXTUAL`. Used by the drift handler to decide *which* slides to compress first (compress CONTEXTUAL before ANCHOR).

### 15.4 Runtime re-pacing (drift control)
`_maybe_repace` after each slide:
```python
drift = (elapsed - expected_elapsed) / target_seconds
if drift > pacing_drift_turbo_pct:    switch_track(TURBO)
elif drift > pacing_drift_summary_pct: switch_track(SUMMARY)
elif drift < -pacing_drift_summary_pct and track != STANDARD: relax_track()
```
Switching tracks swaps the *upcoming* slides' scripts (already generated for all 3 tracks at build time), never the current sentence — so PlaybackTracker stays valid. Target: ≥ 95% Time Budget Accuracy (doc achieved 99.91%).

---

## 16. PlaybackTracker (deterministic resume)

`core/playback_tracker.py`. The mechanism behind 100% context recovery.

### 16.1 Construction
For the active track's script, concatenate sentences in delivery order. Each `ScriptSentence` gets a **sample range** in the **output audio stream's** sample rate (§12.1 decision):
- **Estimated (pre-synthesis):** proportional to word count: `samples = words / WPM * 60 * out_sr`.
- **Exact (post-synthesis, preferred):** when a sentence is synthesized, set `sample_start/sample_end` from the actual WAV length. The doc explicitly allows "estimated proportionally **or** accurate from WAV data."

### 16.2 Runtime tracking
```python
class PlaybackTracker:
    def __init__(self, sentences: list[ScriptSentence], out_sr: int): ...
    def on_frames_pushed(self, n_samples: int):       # called by _speak_sentence
        self._cursor += n_samples
    def current_sentence_index(self) -> int:          # binary search cursor in ranges
        return bisect_ranges(self._ranges, self._cursor)
    def resume_point(self) -> int:                    # SNAP to sentence start
        return self.current_sentence_index()          # never mid-sentence
    def seek_to_sentence(self, idx: int):
        self._cursor = self._ranges[idx].start
```

### 16.3 ⚠️ R10 — push-vs-playout lag (must handle)
We count **frames pushed** to the AudioSource, but LiveKit buffers them; actual playout lags pushes. If we tracked an exact mid-sentence sample, the resume point would be wrong by the buffer depth. **Why the design is robust:** we always **snap resume to the sentence start**, and we **push one sentence at a time** (clearing the queue on interrupt with `source.clear_queue()`). So the "currently pushed/playing" sentence is unambiguous and the buffer lag (< one sentence) never crosses a sentence boundary in a way that loses context. This is the concrete reason the doc's "deterministic, 100%" claim holds — documented here so no one "optimizes" it into sample-exact resume and reintroduces the bug.

### 16.4 Interaction with re-pacing
On track switch, the tracker is rebuilt for the **upcoming** slides only; the current sentence finishes on its original track. `seek_to_sentence` maps by slide+sentence ordinal, not raw sample, so a track swap doesn't corrupt the cursor.

---

## 17. KnowledgeBase / RAG

`core/knowledge_base/`. Presentation-aware hybrid retrieval.

### 17.1 🔧 Hybrid retrieval (resolves PLAN §16.3 contradiction)
The doc says both "FAISS semantic search" and "hybrid BM25 + embedding." **Decision: implement hybrid** (it's the stronger, gap-closing claim from Ch.2/Ch.6):
- **Indexing (`build`):** chunk each slide (`rag_chunk_tokens`/`overlap`), tagging each chunk with `slide_index`. Build (a) a **FAISS** flat-IP index over MiniLM embeddings (normalized → cosine), and (b) a **BM25** index (`rank_bm25`) over chunk tokens. Persist both under `data/jobs/<job_id>/` (`faiss.index`, `bm25.pkl`, `chunks.json`).
- **Retrieval:** run both, fuse with **Reciprocal Rank Fusion** (`score = Σ 1/(k+rank)`, k=60), then apply **current-slide boost** (`+rag_current_slide_boost` to chunks whose `slide_index == current_slide`), take top-`rag_top_k`, trim to `rag_max_context_tokens`.

```python
class KnowledgeBase:
    def index(self, job_id, slides: list[SlideContent]): ...
    def load(self, job_id): ...                          # lazy per session
    def retrieve(self, query: str, current_slide: int) -> RetrievedContext:
        dense = self._faiss_search(self.embed(query), k=20)
        lexical = self._bm25.get_top_n(tokenize(query), k=20)
        fused = rrf(dense, lexical)
        for c in fused: c.score += boost if c.slide_index == current_slide else 0
        top = trim_tokens(sorted(fused)[:self.top_k], self.max_tokens)
        return RetrievedContext(chunks=top, sources=sorted({c.slide_index for c in top}))
```

### 17.2 Persistence & lifecycle
- Index built once at `build`, keyed by `job_id`. A session **loads** it lazily on first QUESTION (or at session start to avoid first-question latency 🔧).
- Embeddings come from the shared MiniLM (§3) → no extra model.
- RAG retrieval target ~50 ms (doc).

---

## 18. GroqLLMService (+ Ollama fallback)

`services/groq_llm_service.py`. Used for **script generation** (build-time, batch) and **Q&A answering** (runtime, latency-critical).

### 18.1 Client
🔧 **Decision:** use `httpx.AsyncClient` against Groq's **OpenAI-compatible** `/chat/completions` (doc lists `httpx`; avoids an extra SDK). Streaming enabled for Q&A to minimize TTFT (doc measured ~129 ms).
```python
class GroqLLMService:
    def __init__(self, s: Settings): self._client = httpx.AsyncClient(base_url=s.groq_base_url, ...)
    async def answer(self, question, ctx: RetrievedContext, slide_text: str) -> str:
        msgs = build_qa_messages(question, ctx, slide_text)   # prompts/qa_answer.py
        return await self._chat(msgs, stream=True, max_tokens=300, temperature=0.3)
    async def generate_script(self, slide, words, persona, track) -> str:
        return await self._chat(build_script_messages(...), temperature=0.3)
```

### 18.2 Grounding / anti-hallucination (doc: < 5% hallucination)
Q&A system prompt enforces: answer **only** from provided slide context; if unsupported, say so; cite slide numbers (`sources`). Current-slide context is presented first and labeled as primary. This realizes the doc's "explicit source attribution."

### 18.3 Resilience (doc risk mitigations)
- `tenacity` retry (exp backoff, 2 attempts) on 5xx/timeouts.
- ⚠️ **R7 — model id drift.** "Llama 3 70B" isn't a current Groq model id. Default to `llama-3.3-70b-versatile`, configurable via `CB_GROQ_MODEL`; validate the id at startup with a cheap call and log the resolved model.
- **Fallback to local Ollama** (doc): on Groq unavailability (network/401/exhausted), switch to `ollama_base_url` with `ollama_model`. A `LLMRouter` wraps both; health-checked at startup, hot-swappable at runtime. If neither is reachable, QUESTION intents degrade gracefully to a spoken "I can't reach the knowledge service right now" rather than crashing the session (§20).

---

## 19. Concurrency, locking & interrupt semantics

The trickiest correctness area. Consolidated rules:

1. **Two long-lived tasks** per session: `_input_loop` (listener, never blocks on the lock) and `_narration_loop` (speaker, holds the lock while speaking a sentence).
2. **`transition_lock` (asyncio.Lock)** serializes everything that writes audio: speaking a narration sentence, and the whole QUESTION→answer→TTS sequence. ⇒ **no two audio producers run at once** = the doc's "no audio collision / Serialized Q&A Transition Locking."
3. **`interrupt_event` (asyncio.Event)** is set by the input loop on confirmed barge-in. `_speak_sentence` polls it between frame pushes:
   ```python
   for chunk in tts_chunks:
       if self.state.interrupt_event.is_set():
           self.audio_out.clear_queue()                 # drop buffered TTS now
           self.state.pending_resume = ResumePoint(
               slide_index=self.state.current_slide,
               track=self.state.current_track,
               sentence_index=self.tracker.resume_point())  # snapped to start
           return                                       # release lock -> handler runs
       await self.audio_out.capture_frame(chunk)
       self.tracker.on_frames_pushed(len(chunk))
   ```
4. **Ordering:** the interrupt-detecting input loop only *sets the event and buffers user audio*; it does **not** take the lock. The narration loop sees the event, bails out, **releases** the lock; `_on_utterance` (scheduled by the input loop once silence completes) then **acquires** the lock and runs STT→intent→action. This prevents deadlock (listener never competes for the lock the speaker holds).
5. **Event reset:** cleared at the start of each `_speak_sentence` and after `_resume()`.
6. **Spurious interrupts** (VAD fired but STT empty / IGNORE): `_resume()` continues from the same sentence start — zero loss.
7. **Rapid navigation during answering:** `_navigate` clears `pending_resume` and the tracker re-seeks → Context Integrity Verification; a late answer for a since-abandoned slide is never spoken (guarded via the monotonically increasing `turn_id` checked before `_speak_text`).

⚠️ **R11:** scheduling `_on_utterance` as a separate task while the narration loop may still hold the lock is intentional, but the handler **must** re-check `phase`/`turn_id` after acquiring the lock (state may have changed). Encoded as the `my_turn == self.state.turn_id` guard in §9.3.

---

## 20. Error handling, logging & graceful degradation

- **Logging:** `loguru`, structured, one logger configured in `utils/logging.py`; per-session `session_id`/`turn_id` bound via `logger.bind(...)`. Latency of each stage logged at DEBUG for the benchmark harness to parse.
- **Degradation matrix (doc reliability + risk table):**

  | Failure | Behavior |
  |---|---|
  | Groq down | Fall back to Ollama; if both down, speak a graceful apology, stay in session (no crash) |
  | STT returns empty | Treat as spurious VAD → resume |
  | TTS synth error | Skip sentence with WARN, advance (never deadlock the loop) |
  | LiveKit track drop | Agents reconnect logic; on hard drop, mark session `ENDED`, persist state |
  | Model load failure at boot | `/readyz` stays 503; process refuses traffic (fail fast) |
- **Input sanitation:** filename/path sanitation on upload; reject non-`.pptx`; cap upload size; clean temp files (doc security NFR).
- **Secrets:** only via env / `SecretStr`; never logged.

---

## 21. Testing & benchmark harness

Mirrors the doc's two suites + the success-criteria gates. `pytest` + `pytest-asyncio`.

| File | Covers | Key assertions / method |
|---|---|---|
| `test_benchmarks.py` | VAD latency, intent accuracy, model footprint, pacing | 100-iter warmed VAD timing (P50/P95/P99 < 5 ms); intent accuracy on standard/adversarial/noisy corpora (target ≥95%); size audit (< 50 MB core); pacing scenarios 5–45 min (TBA ≥ 95%) |
| `test_e2e_latency.py` | Pipeline timing | VAD→STT→Intent→LLM TTFT→TTS summed < 5 s; reports per-stage means |
| `test_intent.py` | Classifier correctness | Per-class precision/recall; GOTO number parsing; PREV/NEXT disambiguation; noisy-ASR variants |
| `test_pacing.py` | PacingService math | Budgets sum to target×(1−buffer); track switch logic; density/priority assignment |
| `test_playback_tracker.py` | Resume determinism | Interrupt at 25/50/75% → resume index == interrupted sentence start; 105-scenario sweep → 100% |
| `test_rag.py` | Retrieval | Current-slide boost ordering; RRF fusion; token-budget trim; grounded-answer cites correct sources |

- **Fixtures:** a tiny sample `.pptx`, a labeled intent corpus (reuse the doc's 68 standard / 22 adversarial / 9 noisy split), and recorded WAV utterances for STT.
- ⚠️ Benchmarks run **inside the container** on reference hardware (doc) — provide `make bench` that runs them in the image so numbers are comparable to the doc's tables.

---

## 22. Docker & compose

### 22.1 Dockerfile (corrected vs. doc — see R9)
```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 tesseract-ocr \
    libreoffice-core poppler-utils \      # ADDED: slide rasterization (R9)
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ /app/
ENV CB_MODELS_DIR=/models
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```
- Models are downloaded to a **mounted volume** `/models` on first run (Whisper/MiniLM/Piper auto-download); cache it to avoid re-pulling ~500 MB each boot.

### 22.2 docker-compose
- `backend` (this image, ports 8000), env from `.env`.
- `livekit` (official `livekit/livekit-server`) for self-hosted WebRTC signaling, OR use LiveKit Cloud (then only `backend`).
- Optional `ollama` service for the LLM fallback.
- Volumes: `./data` (job artifacts), `./models` (model cache).

---

## 23. Build order (sequenced checklist)

Maps to PLAN §15 phases; each step is independently testable before the next.

**Phase 1 — Foundation**
1. `config.py`, `models.py`, `utils/*`, logging, FastAPI skeleton + `/healthz`/`/readyz`.
2. `vad_service.py` + `test`: warmup + latency benchmark green (< 5 ms).
3. `tts_service.py` + `test`: synthesize a WAV; resolve R4 sample-rate decision here.
4. Dockerfile builds; models cache to `/models`.

**Phase 2 — Core logic**
5. `stt_service.py` + `test` (WER on fixtures).
6. `fast_intent_classifier.py` + `embeddings.py` + `test_intent` (drive to ≥95%).
7. `playback_tracker.py` + `test` (resume determinism).
8. LiveKit token route + in-process agent bootstrap; echo audio round-trip.

**Phase 3 — Integration**
9. `slide_processor.py` (extract + rasterize) + upload endpoint.
10. `pacing.py` + `test`; wire `build` endpoint (background task, §7.1).
11. `knowledge_base/*` (hybrid RAG) + `test_rag`.
12. `groq_llm_service.py` (+ Ollama) + prompts; script-gen and Q&A.
13. `agent_worker.py`: assemble both loops, locking, interrupt, resume (§9/§19). Full barge-in test.

**Phase 4 — Evaluation**
14. `test_e2e_latency.py`, full benchmark suite in-container; record vs. targets.
15. Harden degradation matrix; finalize version pins.

---

## 24. Recheck log: mistakes caught & gaps flagged

This section is the deliverable's "rechecks for mistakes / gaps." Each item is actionable.

| ID | Type | Issue (vs. source doc) | Resolution in this spec |
|----|------|------------------------|--------------------------|
| **R1** | Dependency gap | Doc lists low-level `livekit` only and very old pins; the worker pattern needs `livekit-agents`/`livekit-api`. | Added both; bumped pins to current minors; §2.3. |
| **R2** | Architecture gap | `build` (parse + N×LLM + index) can exceed HTTP timeout for 50 slides. | Background task + `202` + progress endpoint; §7.1. |
| **R3** | Spec ambiguity | "STT processes ≤2 s audio" contradicts real multi-second questions. | 2 s = benchmark window, not limit; silence-segmented cap at 8 s; §11. |
| **R4** | **Real bug** | "16 kHz mono TTS output" vs. lessac-medium's 22 050 Hz → wrong-pitch playback if forced to 16 k. | AudioSource = voice native rate; VAD/STT stay 16 k (separate streams); §12.1. |
| **R5** | Deployment ambiguity | Doc implies one container runs FastAPI + worker; Agents usually runs as its own CLI. | In-process background agent task; sidecar fallback documented; §8.2. |
| **R6** | **Metric misread** | "VAD onset < 5 ms" conflates *inference* (0.34 ms) with *perceptual onset* (≥32 ms frame + debounce). | We claim inference target only; onset bounded by frame+min-speech; §10. |
| **R7** | API drift | "Llama 3 70B" is not a current Groq model id. | Default `llama-3.3-70b-versatile`, configurable + startup-validated; §18.3. |
| **R8** | Optimistic target | "TTS first byte < 100 ms" is hard on CPU; realistic first-chunk is higher. | Stream Piper chunks for lowest achievable TTFT; flag target as stretch; §12.2. |
| **R9** | Dockerfile gap | python-pptx can't render full slides; Dockerfile lacks a renderer. | Add `libreoffice-core` + `poppler-utils`; render at build; §14.1/§22. |
| **R10** | Concurrency bug risk | Push-vs-playout lag would break sample-exact resume. | Snap resume to sentence start + push one sentence at a time + `clear_queue`; §16.3. |
| **R11** | Concurrency bug risk | Handler task may run while narration still holds the lock / state changed. | Listener never takes the lock; handler re-checks `phase`/`turn_id` post-acquire; §19. |
| **R-intent** | Acceptance gap | Intent accuracy 89.9% < 95% gate (PARTIAL in doc). | Hybrid rules + embeddings; expanded prototypes; LR-head fallback; §13. |
| **R-hybrid** | Spec contradiction | RAG described as both FAISS-only and BM25+embedding. | Implement hybrid (FAISS + BM25 via RRF) + current-slide boost; §17.1. |
| **R-persona** | Spec gap | Persona vs Track relationship undefined. | Persona = WPM/buffer; Track = verbosity variant; they compose; §15.1. |
| **R12** | **Platform gap (found at validation)** | `piper-tts` → `piper-phonemize` has **no Windows wheel** (Linux/macOS-only C++/espeak-ng ext). Blocks local Windows installs. | Piper runs in the Linux Docker image (deployment target). For local Windows dev, install everything except `piper-tts`; validate TTS in-container. Documented in backend/README. |
| **R1-fix** | **Dependency conflict (found at validation)** | `livekit==0.17.*` pin conflicts with `livekit-agents` (needs `livekit>=0.18.1`). | Don't hard-pin `livekit`; let `livekit-agents>=0.12,<0.13` resolve a compatible rtc SDK (`livekit>=0.18,<1`). Fixed in requirements.txt + §2.2. |
| **R6-fix** | **Bug (found at validation)** | VAD drafted via `torch.hub(onnx=True)` transitively needs `torchaudio` (not in reqs) → import crash. | Rewrote `VADService` to pure onnxruntime over the `silero-vad`-bundled `.onnx` (located without importing the package). No torch.hub/torchaudio. Validated 0.2 ms. §10. |

### Validation status (local venv, Python 3.12 / Windows)
Ran `backend/tests/validate_ml.py` against real model weights:

| Service | Result | Note |
|---------|--------|------|
| Embeddings (MiniLM) | ✅ PASS | dim=384, normalised |
| VAD (Silero, onnxruntime) | ✅ PASS | 0.20 ms mean latency (target <5 ms) |
| STT (Faster-Whisper small.en) | ✅ PASS | loads + transcribes (CTranslate2 INT8) |
| Intent (hybrid rule+embedding) | ✅ PASS | **100%** on the smoke cases incl. GOTO parsing |
| RAG (FAISS + BM25 hybrid) | ✅ PASS | indexes + cites correct sources |
| TTS (Piper) | ⏭ DEFERRED | Linux container only (R12) |

`pytest tests/` in the venv: **30 passed, 1 skipped** (e2e placeholder).

**Still open for the user (decisions, not blockers):**
- Self-hosted LiveKit server vs. LiveKit Cloud (affects compose + ops).
- Whether slide rasterization lives in backend (LibreOffice) or is pushed to the frontend phase.
- Confirm Groq model tier (70B vs 8B-instant) for the Q&A latency/quality trade-off.

---

*End of backend implementation spec. Frontend (`Next.js`) implementation is intentionally deferred to a separate document.*
