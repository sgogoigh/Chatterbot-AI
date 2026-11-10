# Chatterbot-AI — Project Blueprint & Implementation Plan

> **Status:** Blueprint / planning document.
> **Source of truth:** `../Voice AI Project.docx` (full text mirrored to `../doc-text.txt`).
> **Scope of this document:** A *blueprint* — what the system is, what it must do, the flow, the services, and the expected path of every decision. It deliberately does **not** contain detailed code-level implementation. That comes in a later phase.

---

## Table of Contents

1. [Executive Summary — What the Document Describes](#1-executive-summary--what-the-document-describes)
2. [Problem, Motivation & Research Gaps](#2-problem-motivation--research-gaps)
3. [Objectives & Success Criteria](#3-objectives--success-criteria)
4. [The Six Novel Mechanisms (Core Differentiators)](#4-the-six-novel-mechanisms-core-differentiators)
5. [System Architecture (Layered View)](#5-system-architecture-layered-view)
6. [End-to-End Flow — The Expected Path](#6-end-to-end-flow--the-expected-path)
7. [Backend — Services & Responsibilities](#7-backend--services--responsibilities)
8. [Frontend — Responsibilities & Requirements](#8-frontend--responsibilities--requirements)
9. [AI / ML Model Stack](#9-ai--ml-model-stack)
10. [Data Flow Diagrams (DFD Levels 0–2)](#10-data-flow-diagrams-dfd-levels-02)
11. [Session State Machine](#11-session-state-machine)
12. [Requirements (Functional & Non-Functional)](#12-requirements-functional--non-functional)
13. [Performance Targets & Benchmark Plan](#13-performance-targets--benchmark-plan)
14. [Proposed Repository Structure](#14-proposed-repository-structure)
15. [Implementation Phases, Milestones & Timeline](#15-implementation-phases-milestones--timeline)
16. [Improvements & Open Decisions](#16-improvements--open-decisions)
17. [Risks, Constraints & Out-of-Scope](#17-risks-constraints--out-of-scope)

---

## 1. Executive Summary — What the Document Describes

Chatterbot-AI is a **real-time, full-duplex AI voice interaction system with context-aware interruption handling**, purpose-built for **automated presentation delivery**. It closes the gap between slide-authoring tools (PowerPoint, Google Slides, Prezi) and conversational AI.

The system's documented behavior loop:

1. **Ingests** a set of presentation slides (`.pptx`).
2. **Generates** optimized, time-budgeted narration scripts per slide.
3. **Narrates** the presentation aloud via TTS.
4. **Listens** continuously for user speech (full-duplex barge-in).
5. **Understands** the semantic intent of any interruption in real time.
6. **Answers** questions accurately via a presentation-aware RAG pipeline.
7. **Resumes** narration from the *exact sentence* that was interrupted — with zero semantic loss.

### Headline characteristics (as documented)

- **Lightweight:** the full-duplex core (VAD + intent) is only **~23 MB**, CPU-only, no GPU required.
- **Cascaded pipeline:** `VAD → ASR → Intent → (Router) → LLM/RAG → TTS`.
- **Deterministic context recovery** at the **sentence level** (not probabilistic / utterance-level).
- **Time-locked pacing:** delivers a presentation in *exactly* the requested duration via 3 adaptive tracks.
- **Single containerized pipeline** orchestrated by a **FastAPI backend** + **Next.js frontend** over **LiveKit WebRTC**.

### Two mechanisms the document calls out as distinguishing

- **PlaybackTracker** — guarantees deterministic, sentence-level context recovery after interruption.
- **PacingService** — dynamically moves between delivery tracks to honor a strict presentation schedule.

---

## 2. Problem, Motivation & Research Gaps

### 2.1 Problem statement (from the doc)

Existing voice interaction systems cannot deliver **context-aware interruption management for time-constrained presentation delivery**. Specifically:

- On interruption, audio sentence boundaries are discarded → **information loss** at the disruption point.
- Resume is **arbitrary** → semantic continuity breaks.
- Static time budgets cannot recover from interruptions reliably.
- Intent is reduced to **binary** classification (speech / no-speech, barge-in / backchannel).

### 2.2 Limitations of prior systems

- **Interruption handling:** existing systems either forget the interrupted segment, resume mid-sentence (losing context), or restart entirely. None preserve context at the *sentence* level.
- **No time-locked delivery:** no prior system solves "deliver this presentation in exactly 15 minutes."
- **Heavy footprint:** full-duplex SOTA needs 500 MB–1 GB and GPUs (Duplex Conversation ~500 MB; LLM-DM ~1 GB; SALMONN-Omni >1 GB).
- **Coarse intent:** binary classification cannot distinguish navigation vs. questions vs. commands vs. acknowledgments.

### 2.3 The seven research gaps Chatterbot-AI closes

| # | Gap | Chatterbot-AI's answer |
|---|-----|------------------------|
| 1 | Sentence-level context preservation | `PlaybackTracker` computes sentence boundaries → deterministic restoration |
| 2 | Deterministic vs. probabilistic recovery | Pre-computed boundaries → 100% recovery (vs. 91–93% probabilistic) |
| 3 | Time-locked delivery mechanism | `PacingService` + weighted budget allocation → 99.91% Time Budget Accuracy |
| 4 | Lightweight full-duplex | 23 MB total footprint, CPU-only (43× lighter than LLM-DM) |
| 5 | Semantic intent classification | 6-class `FastIntentClassifier` (NEXT, PREV, GOTO, QUESTION, STOP, IGNORE) @ 9.4 ms |
| 6 | Multi-track adaptive pacing | 3 tracks (STANDARD / SUMMARY / TURBO) auto-switched on schedule deviation |
| 7 | Knowledge-grounded presentation Q&A | Presentation-aware RAG: hybrid BM25 + embeddings, slide-weighted, current-slide priority |

### 2.4 Impact areas (motivation)

Educational technology (automated lecture delivery), accessibility (verbal descriptions for vision-impaired; synthesized voices for speech-impaired), professional productivity (automated routine reports), and future multilingual delivery.

---

## 3. Objectives & Success Criteria

### 3.1 Research objectives
- 100% sentence-level context recovery after interruption.
- Lightweight full-duplex architecture under **25 MB**, CPU-deployable.
- Time-budget pacing achieving **≥95%** Time Budget Accuracy.
- 6-class intent classifier with **≥95%** accuracy.
- Three multi-track adaptive pacing models.
- Presentation-aware RAG with **<5%** hallucination artifacts.

### 3.2 Technical objectives (per-component)
- **VAD:** P99 latency **<5 ms**.
- **ASR:** Word Error Rate **<10%**.
- **TTS:** natural speech, Mean Opinion Score **≥4.0**.
- **Intent:** processing **≤20 ms**.
- **End-to-end:** response **<5 s**.

### 3.3 User-experience objectives
- ≥90% satisfaction; natural, seamless conversation.
- Continuous listening with **<5%** false interrupt detection.
- 100% syntactic content retention on resume.
- Time-locked delivery within **±5%** of the user-set budget.

### 3.4 The six formal success criteria (acceptance gates)

| Criterion | Target | How measured |
|-----------|--------|--------------|
| VAD Latency | < 5 ms | Timing instrumentation |
| Context Recovery | 100% | Sentence-comparison tests |
| Time Budget Accuracy | ≥ 95% | Pacing scenario tests |
| Intent Accuracy | ≥ 95% | Labeled test corpus |
| Model Footprint | < 50 MB | File-size audit |
| End-to-End Latency | < 5 s | Pipeline timing |

> **Note on the documented results:** the baseline achieved Intent Accuracy of **89.9%** (100% on standard cases, lowered by adversarial + noisy-ASR cases) — a **PARTIAL** on this gate. Improving this is an explicit Chatterbot-AI goal ([§16](#16-improvements--open-decisions)).

---

## 4. The Six Novel Mechanisms (Core Differentiators)

These are the heart of the system and must be designed first-class. The doc groups three under "Core System Novel Mechanisms" (transition security) and the rest across the architecture.

1. **PlaybackTracker — Deterministic Sentence-Level Resume.**
   Splits script text into sentences; assigns each an estimated (or WAV-derived exact) sample range; tracks the live playback sample position. On interruption it captures the *active sentence index* and sets the resume point to that **sentence's start** — never mid-sentence. Guarantees 100% context continuity after Q&A.

2. **PacingService — Time-Locked Multi-Track Pacing.**
   Deterministic budget calculation for duration-limited delivery. Computes persona-specific speaking patterns (WPM, buffer ratio), per-slide content-density scores (text + annotations + visual density), distributes words across slides, and produces synchronized budgets for **three script versions** (STANDARD / SUMMARY / TURBO). Assigns slide **priority** (ANCHOR / SUPPORTING / CONTEXTUAL) by placement and density. Auto-switches tracks based on deviation from schedule.

3. **FastIntentClassifier — 6-Class Semantic Intent.**
   MiniLM embeddings + prototype-based classification. Pre-computes prototype embeddings per class at init; at runtime computes cosine similarity between the user phrase and prototypes, applying confidence thresholds to absorb noisy ASR. Slide-number extraction (for GOTO) is handled separately from intent recognition.

4. **Serialized Q&A Transition Locking.**
   An enforced operational lock around questions, answers, and the transition back to narration. Prevents playback race conditions and guarantees no concurrent use of the output audio buffer while an answer is being synthesized (no audio collision).

5. **Deterministic Resume State Management.**
   At the moment VAD processes an interruption, the *pending resume state* captures: (a) the semantic boundary index pointer, (b) the active presentation track identifier, (c) the visual slide context identifier.

6. **Context Integrity Verification.**
   Resuming on any slide context other than the one interrupted is treated as semantically invalid. Rapid user navigation automatically clears stale pending-resume buffers so out-of-date answers are never played.

---

## 5. System Architecture (Layered View)

The system is a **modular, layered architecture** separating concerns across **presentation, service, and infrastructure** layers, packaged in a single container pipeline.

```
┌──────────────────────────────────────────────────────────────────────┐
│ PRESENTATION LAYER (Frontend)                                          │
│   Next.js 14 web app                                                   │
│   • Upload UI (.pptx)        • Slide viewer + progress indicators      │
│   • Mic capture / audio I/O  • Status & conversational UX              │
│   ── LiveKit WebRTC (audio, full-duplex) ──┐   ── REST (control) ──┐   │
└───────────────────────────────────────────┼──────────────────────┼───┘
                                             │                      │
┌────────────────────────────────────────────▼──────────────────────▼───┐
│ SERVICE LAYER (Backend — FastAPI + asyncio)                            │
│                                                                        │
│   ┌──────────────────────  ChatterbotAgentWorker  ──────────────────┐ │
│   │            (central orchestrator / agent loop)                   │ │
│   └──┬──────┬──────┬──────────┬─────────┬─────────┬──────────┬───────┘ │
│      │      │      │          │         │         │          │         │
│   ┌──▼──┐┌──▼──┐┌──▼──┐ ┌─────▼────┐┌───▼───┐┌────▼────┐┌────▼─────┐    │
│   │ VAD ││ STT ││ TTS │ │  Intent  ││  LLM  ││   RAG   ││ Pacing + │    │
│   │Silero││Whis-││Piper│ │Classifier││ Groq  ││Knowledge││ Playback │    │
│   │      ││ per ││     │ │ (MiniLM) ││(Llama)││  Base   ││ Tracker  │    │
│   └─────┘└─────┘└─────┘ └──────────┘└───────┘└─────────┘└──────────┘    │
│                                                                        │
│   SlideProcessor (.pptx → text/images/notes → scripts)                 │
└────────────────────────────────────────────────────────────────────────┘
                                             │
┌────────────────────────────────────────────▼──────────────────────────┐
│ INFRASTRUCTURE LAYER                                                    │
│   Docker + Docker Compose  •  LiveKit Server (WebRTC signaling)         │
│   Groq Cloud API (LLM)     •  FAISS vector index  •  local file store   │
└────────────────────────────────────────────────────────────────────────┘
```

**Communication channels (the expected paths):**
- **Frontend ⇄ LiveKit ⇄ Backend:** real-time, full-duplex **audio** (WebRTC). Mic audio in; synthesized TTS out.
- **Frontend ⇄ FastAPI (REST):** control plane — upload `.pptx`, start/configure session, fetch slides, status, navigation.
- **Backend ⇄ Groq Cloud:** LLM inference (script generation + Q&A answers) over HTTPS, API-key authenticated.
- **Backend internal:** the `ChatterbotAgentWorker` orchestrates all services via Python `asyncio`; model inference is offloaded with `asyncio.to_thread()` to avoid blocking the event loop.

---

## 6. End-to-End Flow — The Expected Path

### Phase A — Upload & Initialization (REST control plane)
1. User uploads a `.pptx` (1–50 slides) via the Next.js frontend → FastAPI endpoint.
2. **SlideProcessor** extracts text, images, and speaker notes per slide.
3. **PacingService** receives the target duration + persona (GENERAL / TEACHER / MEETING / TEDX), computes per-slide content density, allocates word budgets, and produces **three synchronized scripts** (STANDARD / SUMMARY / TURBO) plus slide priorities.
4. **LLM (Groq)** generates the optimized narration text per slide for each track.
5. **KnowledgeBase** indexes slide content: chunk → MiniLM embeddings → FAISS store, persisted per **job ID**.
6. **PlaybackTracker** is primed with the chosen script's sentence boundaries and estimated sample ranges.
7. Frontend receives the rendered slides + a session handle; a LiveKit room is established.

### Phase B — Real-Time Presentation Delivery (audio plane)
8. `ChatterbotAgentWorker` begins narration: feed current sentence → **Piper TTS** → stream audio out via LiveKit. Frontend advances the visible slide in sync.
9. **PlaybackTracker** continuously updates the current sample position (and therefore the active sentence index).
10. **PacingService** monitors elapsed vs. budget; if deviation crosses thresholds, it switches track (e.g., STANDARD → SUMMARY → TURBO) to stay time-locked.

### Phase C — Interruption Handling (the novel core)
11. **Silero VAD** runs on the inbound mic buffer (16 kHz, 512-sample chunks). On speech probability > **0.65**, it raises the interrupt state.
12. **Serialized Q&A Transition Lock** engages; current TTS playback stops; **Deterministic Resume State** is captured (sentence boundary index + active track id + slide context id).
13. Buffered user audio → **Faster-Whisper STT** (`asyncio.to_thread`, beam_size=1, English) → transcript text.
14. **FastIntentClassifier** classifies into one of 6 intents (≤20 ms):
    - **NEXT / PREV / GOTO** → navigation: change slide (GOTO extracts the slide number), update visible slide; **Context Integrity Verification** clears stale resume buffers on rapid navigation.
    - **QUESTION** → query the **RAG KnowledgeBase** (semantic top-k, current-slide priority) → assemble grounded context → **Groq LLM** generates answer → **Piper TTS** synthesizes the answer audio.
    - **STOP** → pause / end narration.
    - **IGNORE** → treat as backchannel/acknowledgment; resume without answering.

### Phase D — Context-Aware Resume
15. After the answer finishes synthesizing, the Q&A lock releases.
16. **Context Integrity Verification** confirms the resume slide context matches the interrupted one (else invalidate).
17. **PlaybackTracker** resumes narration from the **start of the interrupted sentence** — 100% context retained — on the correct track.
18. Loop continues to next sentence/slide until the presentation completes; **Session Management** handles graceful termination.

---

## 7. Backend — Services & Responsibilities

> The doc places files under `core/` and `services/`. Names below preserve the documented module layout;.

### 7.1 What the backend must support (capabilities)
- FastAPI REST endpoints (upload, session lifecycle, slides, status, navigation control).
- LiveKit WebRTC integration for bidirectional audio streaming.
- Async orchestration via `asyncio`; non-blocking model inference via `asyncio.to_thread()`.
- Modular, independently testable services.
- Structured logging (loguru), environment-variable configuration, health checks, graceful degradation/fallbacks.

### 7.2 Service-by-service blueprint

| Module (documented path) | Role | Key inputs → outputs | Notes / expected path |
|--------------------------|------|----------------------|------------------------|
| **ChatterbotAgentWorker** (`core/livekit_worker.py`) | Central orchestrator / agent loop | Audio frames, session state → routed actions | Hosts VAD loop, sets interrupt state, drives Q&A locking, coordinates all services |
| **VAD** (in `core/livekit_worker.py`) | Detect speech onset in real time | 16 kHz mic audio, 512-sample chunks → interrupt signal | Silero VAD via PyTorch Hub; threshold **0.65**; silence-duration threshold ends utterances |
| **STT Service** (`services/stt_service.py`) | Speech → text | ≤2 s audio → transcript | Faster-Whisper `small.en`, CTranslate2 INT8; `asyncio.to_thread`; beam_size=1, best_of=1, English; ~1500 ms; WER <10% |
| **TTS Service** (`services/tts_service.py`) | Text → speech | text → WAV path (streamed) | Piper VITS; quality levels x_low/low/medium/high via env var; auto-downloads missing assets; async subprocess; ~1200 ms |
| **FastIntentClassifier** (`services/fast_intent_classifier.py`) | 6-class semantic intent | transcript → intent (+ slide # for GOTO) | MiniLM embeddings + prototype cosine similarity + confidence thresholds; ~9.4 ms |
| **PacingService** (`core/pacing.py`) | Time-locked budget calculation | slides + duration + persona → 3 track budgets + priorities | Persona WPM/buffer; density scoring; word distribution; ANCHOR/SUPPORTING/CONTEXTUAL priority |
| **PlaybackTracker** (`core/playback_tracker.py`) | Deterministic sentence-level resume | script + live sample position → resume point | Sentence delimiters → sample ranges; captures active sentence on interrupt; resumes at sentence start |
| **KnowledgeBase / RAG** (`core/knowledge_base/knowledge_base.py`) | Index + retrieve for grounded Q&A | slides → FAISS index; question → top-k context | Chunk → MiniLM embed → FAISS (persist per job ID); hybrid BM25 + embeddings; current-slide priority; configurable top-k & max tokens |
| **GroqLLMService** | LLM inference | prompt + context → text | Groq Cloud API (Llama 3 70B); used for script generation + Q&A; LLM TTFT ~129 ms; Ollama fallback |
| **SlideProcessor** | `.pptx` parsing | `.pptx` → text/images/notes per slide | python-pptx; supports 1–50 slides |
| **Session Manager** | Session lifecycle | — | Single concurrent presentation/instance; state across interactions; graceful termination |

### 7.3 Backend stack (documented)
- Python 3.11+, FastAPI 0.104+, uvicorn 0.24, asyncio.
- torch 2.1.0, numpy 1.26.2, faster-whisper 0.10.0, sentence-transformers 2.2.2, livekit 0.11.1, python-pptx 0.6.23, loguru 0.7.2, httpx 0.25.2.
- FAISS for vector storage.

---

## 8. Frontend — Responsibilities & Requirements

**Stack (documented):** Next.js 14.x, React, standard CSS, LiveKit WebRTC client.

The frontend must support:

1. **Presentation upload** — `.pptx` file picker → POST to backend; show processing status (extraction, script generation, indexing).
2. **Session configuration** — let the user set the **target duration** and **persona** (GENERAL / TEACHER / MEETING / TEDX) before start.
3. **Audio I/O over LiveKit WebRTC** — capture microphone (full-duplex), play synthesized TTS audio, handle barge-in (user can speak while system narrates).
4. **Slide viewer** — render the current slide; **synchronize** the displayed slide with spoken narration; update on navigation (NEXT/PREV/GOTO).
5. **Real-time progress indicators** — slide position, elapsed vs. budgeted time, pacing track in use, listening/speaking/processing status.
6. **Conversational UX** — clear visual feedback for the states (idle, loading, speaking, listening, processing, answering, paused) consistent with the documented state machine and conversational-UX principles.
7. **Status & errors** — surface connection/reconnect state, component failures, graceful degradation messaging.
8. **Security** — all transport over HTTPS/WSS.

---

## 9. AI / ML Model Stack

| Model | Purpose | Size | Format / source |
|-------|---------|------|-----------------|
| **Silero VAD** | Voice activity detection | ~1 MB | ONNX — `snakers4/silero-vad` |
| **Faster-Whisper `small.en`** | ASR (speech→text) | ~400 MB | CTranslate2 INT8 — `guillaumekln/faster-whisper` |
| **all-MiniLM-L6-v2** | Sentence embeddings (intent + RAG) | ~22 MB | Sentence-Transformers |
| **Piper TTS (`en_US-lessac-medium`)** | TTS (text→speech) | ~65 MB | ONNX — `rhasspy/piper` |
| **Llama 3 70B** | LLM (script gen + Q&A) | — (cloud) | Groq Cloud API |

- **Full-duplex core footprint = VAD + MiniLM = ~23 MB** (the headline number; ASR/TTS are larger but not part of the "full-duplex" claim).
- **External APIs:** Groq Cloud (LLM, API-key auth) and LiveKit Cloud/Server (WebRTC signaling, API key + secret).
- **Fallback:** if Groq is unavailable, fall back to a **local Ollama** instance (documented mitigation).

---

## 10. Data Flow Diagrams (DFD Levels 0–2)

The doc specifies three DFD levels (rendered as figures 4.4–4.6). Blueprint summary:

**Level 0 — Context Diagram.** Chatterbot-AI is a single process. External entities:
- **User** → provides presentation files, voice commands, questions; ← receives synthesized speech, slide displays, status updates.
- **Groq API** → LLM inference.
- **LiveKit Server** → WebRTC communication.

**Level 1 — System Decomposition.** Three subsystems + three data stores:
- Subsystems: **Presentation Processing** (upload, extraction, script generation), **Real-Time Processing** (VAD, STT, Intent, TTS), **Core Logic** (Agent Worker, PlaybackTracker, KnowledgeBase).
- Data stores: **Scripts**, **Slides**, **Vector Index**.

**Level 2 — Audio Processing Detail.** Three sub-flows:
- **Audio Input Processing:** frame buffering → VAD detection → silence-based speech segmentation.
- **Text Processing:** route classified intent → navigation / Q&A / session control handlers.
- **Response Generation:** synthesize audio output via TTS for all response types.

---

## 11. Session State Machine

Documented lifecycle (Figure 4.8):

```
Idle ──► Loading ──► Ready ──► Speaking ◄──────────────┐
                                  │                     │
                       (VAD interrupt)                  │ resume (sentence start)
                                  ▼                     │
                              Listening                 │
                                  │                     │
                                  ▼                     │
                              Processing ──► Answering ──┘
                                  │
                          ┌───────┴────────┐
                          ▼                ▼
                       Paused        Transitioning
```

- **Speaking** = active narration. **Listening** = capturing a VAD-detected interruption.
- **Processing** = intent classification + routing. **Answering** = RAG/LLM/TTS response.
- **Paused** (STOP intent) and **Transitioning** (navigation/track switch) are control states.
- The machine must guarantee **graceful transitions and error recovery** throughout, and enforce the **Serialized Q&A Transition Lock** between Answering and resume.

---

## 12. Requirements (Functional & Non-Functional)

### 12.1 Functional requirements
1. **Slide upload & processing** — accept `.pptx`; extract text/images/notes; generate per-slide time-based scripts; support 1–50 slides.
2. **Real-time VAD** — detect speech onset <5 ms; distinguish voice vs. non-voice at SNR ≥ 5 dB; trigger interrupt processing.
3. **Speech recognition** — STT with WER <10%; handle ≤2 s segments and continuous streams.
4. **Intent classification** — 6 intents; extract slide # for GOTO; <20 ms.
5. **TTS** — speaking rate configurable 100–180 WPM; 16 kHz mono; first audio <100 ms.
6. **Resume context handling** — track current sentence; detect interruption boundary; resume from interrupted sentence start; retain 100% content.
7. **Time-locked pacing** — accept target duration; per-slide budgets by complexity; 3 tracks; ≥95% Time Budget Accuracy.
8. **Knowledge-grounded Q&A** — index content; retrieve relevant context; answers grounded in slides; prioritize current slide.
9. **Slide synchronization** — display slide matching spoken content; update on navigation; real-time progress indicators.
10. **Session management** — single concurrent presentation; maintain state; graceful termination.

### 12.2 Non-functional requirements
- **Performance:** VAD <5 ms P99 (Critical); intent <20 ms avg (High); TTS first byte <100 ms (High); E2E <5 s (High); ≥1 concurrent session (Medium).
- **Reliability:** 100% context recovery; 99% uptime; <1% request failure; graceful degradation/fallbacks.
- **Scalability:** horizontal scaling via containers; per-session state isolation; <2 GB memory/session.
- **Security:** HTTPS/WSS; API-key validation; input sanitization; temp-file management.
- **Maintainability:** structured logging (loguru); independent services; env-var config; API docs.
- **Portability:** Docker (Linux x86_64); Python 3.11+; external Groq + LiveKit integration.

---

## 13. Performance Targets & Benchmark Plan

### 13.1 Targets vs. documented achievements

| Metric | Target | Documented achieved | Status |
|--------|--------|---------------------|--------|
| VAD Latency | <5 ms | 0.25–0.34 ms | PASS |
| Intent Classification Latency | <20 ms | 9.4–9.5 ms | PASS |
| Intent Accuracy | >95% | 89.9% (100% standard) | **PARTIAL** |
| Context Recovery | 100% | 100% | PASS |
| Time Budget Accuracy | >95% | 99.91% | PASS |
| Model Footprint | <50 MB | 23 MB | PASS |
| E2E Response | <5 s | 3.57–3.6 s | PASS |

> **STT dominates E2E latency** (~1500 ms of the ~3569 ms total). This is the primary optimization lever.

### 13.2 Benchmark suites to build (per the doc)
- `test_benchmarks.py` — component-level (VAD latency over 100 iters w/ warmup; intent accuracy across standard/adversarial/noisy; model footprint audit; pacing scenarios 5–45 min).
- `test_e2e_latency.py` — full pipeline timing (VAD → STT → Intent → LLM TTFT → TTS).
- **Barge-in suite** — interruptions at 25% / 50% / 75% of script; assert detection/recovery/resume all pass.
- All run **inside the Docker container** on the reference machine (Intel i5, 8 GB RAM).

---

## 14. Proposed Repository Structure

> Blueprint layout aligned to documented module paths. To be created during implementation; not all files exist yet.

```
Chatterbot-AI/
├── backend/
│   ├── main.py                       # FastAPI app entrypoint (uvicorn target)
│   ├── core/
│   │   ├── livekit_worker.py         # ChatterbotAgentWorker + Silero VAD loop
│   │   ├── pacing.py                 # PacingService (3-track time-locked budgets)
│   │   ├── playback_tracker.py       # PlaybackTracker (sentence-level resume)
│   │   └── knowledge_base/
│   │       └── knowledge_base.py     # RAG: chunk/embed/FAISS/retrieve
│   ├── services/
│   │   ├── stt_service.py            # Faster-Whisper (CTranslate2 INT8)
│   │   ├── tts_service.py            # Piper TTS (VITS)
│   │   ├── fast_intent_classifier.py # MiniLM prototype intent classifier
│   │   └── groq_llm_service.py       # Groq Cloud LLM client (+ Ollama fallback)
│   ├── slide_processor.py            # python-pptx extraction + script generation
│   ├── requirements.txt
│   └── tests/
│       ├── test_benchmarks.py
│       └── test_e2e_latency.py
├── frontend/                         # Next.js 14 app (upload, slide viewer, LiveKit client)
├── models/                           # Downloaded model assets (gitignored)
├── docker/
│   ├── Dockerfile                    # python:3.11-slim + ffmpeg/libsndfile1/tesseract-ocr
│   └── docker-compose.yml            # backend + LiveKit server orchestration
├── PLAN.md                           # ← this document
├── README.md
└── LICENSE
```

**Container base (documented Dockerfile shape):** `python:3.11-slim`, apt: `ffmpeg`, `libsndfile1`, `tesseract-ocr`; `pip install -r requirements.txt`; expose `8000`; run `uvicorn main:app --host 0.0.0.0 --port 8000`.

---

## 15. Implementation Phases, Milestones & Timeline

Documented as an **8-week, 4-phase** plan.

| Phase | Weeks | Focus |
|-------|-------|-------|
| **Phase 1 — Foundation** | 1–2 | Architecture + environment; Docker config; core processing services (VAD, TTS) |
| **Phase 2 — Core Logic** | 3–4 | FastIntentClassifier; PlaybackTracker deterministic recovery; LiveKit WebRTC bridge |
| **Phase 3 — Integration** | 5–6 | PacingService + RAG + continuous Q&A; stability/stress testing |
| **Phase 4 — Evaluation** | 7–8 | Benchmarks, metrics collection, threshold recording, documentation |

**Milestones (5):**
1. Auditory services validated (TTS synthesis + initial VAD).
2. Interrupt detection + resume proven working.
3. Timing (pacing) + retrieval (RAG) integrated; live demo performed.
4. (implied) Full pipeline integration & stress-tested.
5. (implied) Final benchmarks recorded + report complete.

---

## 16. Improvements & Open Decisions

1. **Close the Intent Accuracy gap (89.9% → ≥95%).** The single failing gate. Levers: richer/more prototypes per class, better confidence-threshold tuning, light fine-tuning, or a small classifier head over MiniLM embeddings — while keeping ≤20 ms latency and the lightweight footprint.
2. **Reduce STT-dominated E2E latency (~1.5 s).** Streaming/chunked Whisper decoding, smaller/quantized variants, or partial-hypothesis early routing. (Doc lists optional GPU offload as future work, but CPU-only is a core constraint.)
3. **Confirm hybrid retrieval.** Doc states both "FAISS semantic search" (module §5.1.7) and "hybrid BM25 + embedding" (RAG gap, ch.6). Decide whether the first release ships pure-embedding or true hybrid BM25+embedding.
4. **Personas vs. tracks.** Clarify the relationship between personas (GENERAL/TEACHER/MEETING/TEDX) and pacing tracks (STANDARD/SUMMARY/TURBO) in the budget model.
5. **LLM model choice.** Doc mentions 
both "Llama 3 70B" (feasibility) and a generic "Groq-backed LLM / Llama." Pin the exact model + the Ollama fallback model.

> **Open questions for the user before build:** scope of "improvements" implied by the rename; whether multilingual / GPU / hybrid-BM25 move from "future work" into v1; target hardware for the improved build.

---

## 17. Risks, Constraints & Out-of-Scope

### 17.1 Risks & mitigations (documented)
- **STT latency above budget** (high prob / high impact) → widen E2E budget; pursue streaming/quantization.
- **LLM API unavailable** (low prob / high impact) → fall back to local Ollama.
- **LiveKit connection issues** → reconnect logic.
- **Model loading failures** → health checks.

### 17.2 Constraints
- Single concurrent presentation per instance.
- English only (current version).
- Requires stable internet for Groq LLM + LiveKit signaling.
- Reference hardware: Intel i5, 8 GB RAM, no GPU.

### 17.3 Out-of-scope (documented future work)
Multi-language support, GPU acceleration, speaker diarization, emotion recognition, gesture integration, mobile native apps.

---

> Keep file paths from the doc (`core/`, `services/`) so the blueprint maps 1:1 onto the documented design; only the product-facing name and the central orchestrator class are renamed.

---

*End of blueprint. Detailed, code-level implementation (APIs, schemas, prompts, algorithms) will be specified in a follow-up design phase once the [§16](#16-improvements--open-decisions) open decisions are confirmed.*
