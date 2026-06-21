# Chatterbot-AI — End-to-End Pipeline Testing Plan

> **Purpose:** define how to test the *whole* pipeline — not the individual ML
> services (those are already validated, see below), but their **integration**:
> the full-duplex narrate → barge-in → understand → answer → resume loop, plus
> the control plane (upload → build → session) and the time-locked pacing.
> **Companion docs:** [`IMPLEMENTATION.md`](./IMPLEMENTATION.md) (what each module
> does), [`ERRORS.md`](./ERRORS.md) (issues already fixed), [`PLAN.md`](./PLAN.md).

---

## 1. Where we are now (baseline)

**Already validated (component level, local venv):**

| Service | Status | Evidence |
|---|---|---|
| Embeddings (MiniLM) | ✅ | dim=384, normalized |
| VAD (Silero/onnxruntime) | ✅ | 0.20 ms latency |
| STT (Faster-Whisper) | ✅ | loads + transcribes |
| Intent (hybrid) | ✅ | 100% on smoke cases |
| RAG (FAISS+BM25) | ✅ | retrieves + cites sources |
| Pure-logic units | ✅ | `pytest` 30 passed / 1 skipped |

**NOT yet tested (this plan's scope):**
- TTS (Piper) — Linux-only, never run (R12).
- The **agent orchestration**: barge-in, transition lock, deterministic resume, intent routing, drift re-pacing — `core/agent_worker.py` has **zero** integration coverage.
- The **LiveKit media loop** — WebRTC audio in/out, agent dispatch (an unverified, version-sensitive seam, R5).
- The **REST control flow** end-to-end (upload → build → session) against a real `.pptx`.
- **Grounded Q&A** with a real LLM (Groq/Ollama) — never called.
- The doc's **benchmark tables** (E2E latency, context recovery, TBA, intent accuracy on the full corpus).

---

## 2. The pipeline under test (the "expected path")

```
                          CONTROL PLANE (REST)                    MEDIA PLANE (LiveKit)
 upload .pptx ─► extract ─► build ─► session+token ─► [browser joins room] ─► agent joins
   (SlideProcessor)   (Pacing + 3-track scripts + RAG index)                       │
                                                                                   ▼
        ┌──────────────────────────── narration loop ◄──────────────── TTS (Piper) stream
        │                                   │
        │  user speaks  ─► VAD onset ─► INTERRUPT ─► transition lock ─► capture utterance
        │                                                                   │
        │                                                          STT ─► Intent (6-class)
        │                                                                   │
        │     ┌─────────────┬───────────────┬──────────────┬───────────────┤
        │   NEXT/PREV/GOTO  QUESTION         STOP           IGNORE
        │     navigate      RAG→LLM→TTS      pause          (backchannel)
        │         │           answer            │               │
        └─────────┴───────────┴── RESUME (snap to interrupted sentence start) ◄┘
```

E2E testing must prove **every arrow** works *together*, and that the six
**novel mechanisms** hold under integration:
1. Deterministic sentence-level resume (100% context recovery)
2. Serialized Q&A transition locking (no audio collision)
3. 6-class intent routing
4. Time-locked multi-track pacing (TBA ≥ 95%)
5. Context integrity verification (rapid nav clears stale resume)
6. Presentation-aware grounded RAG Q&A (<5% hallucination)

---

## 3. Testing strategy — four tiers

Real-time voice is hard to assert on directly, so we layer from
deterministic/cheap to realistic/flaky. **Tiers 1–2 are the automated core**;
Tier 3 is integration smoke; Tier 4 is human acceptance.

### Tier 1 — Logic integration (fakes for audio + transport; stubs for slow ML) ✅ BUILT & PASSING
Drive `ChatterbotAgentWorker` directly with **fake audio I/O** and **stub
services**, so the orchestration logic (interrupt → lock → route → resume) is
tested **deterministically and fast**, with no LiveKit / GPU / network.
- *Why first:* this is where the real risk lives (concurrency, resume math,
  routing) and where bugs are cheapest to find. No Linux/LLM needed → runs on
  Windows in CI.
- *Mechanisms covered:* 1, 2, 3, 5 (resume, lock, routing, integrity).
- *Status:* harness in `tests/harness.py`, scenarios in `tests/test_agent_e2e.py`
  — **12 tests passing** (S1–S8 + S-full). Building it **found and fixed two real
  bugs** in `agent_worker.py`: resume landed on the *next* sentence (PROBLEM 8) and
  the speaker wasn't paused on barge-in (PROBLEM 9) — see [`ERRORS.md`](./ERRORS.md).
  Full suite: **42 passed, 1 skipped**.

### Tier 2 — Service-real, transport-fake (real models, WAV in / WAV out)
Same fake transport, but **real STT/TTS/intent/RAG/LLM**. Feed pre-recorded WAV
utterances into the input loop; capture the agent's pushed audio to a WAV file;
assert transcripts, answers, and timing.
- *Why:* proves the actual models behave in-pipeline (e.g. Whisper transcribes the
  barge-in, Piper renders the answer, RAG+LLM grounds it).
- *Needs:* **Linux/Docker** (Piper) + **Groq key or Ollama**.
- *Mechanisms covered:* 4, 6 + benchmark tables.

### Tier 3 — Full LiveKit loop (real WebRTC)
A real **LiveKit server** + a **synthetic participant** (test client) that
publishes a WAV track and subscribes to the agent's output track. Validates the
WebRTC transport, the in-process agent dispatch (R5 seam), token auth, and
reconnect.
- *Why:* the only tier that exercises the media plane + `setup_audio()` /
  `AudioSource` / `AudioStream` code that fakes bypass.
- *Needs:* LiveKit server (compose `--dev` or Cloud), a headless LiveKit client.

### Tier 4 — Manual / human acceptance (browser + mic)
A person runs the Next.js frontend, presents, interrupts, asks questions. Final
UX acceptance + the ≥90% satisfaction objective.
- *Blocked on:* the frontend (not yet built).

---

## 4. Test scenarios

Each maps to the tier(s) that can run it, the mechanism it proves, and the
assertion. IDs reused across tiers.

| ID | Scenario | Tier | Mechanism | Key assertion | Status |
|----|----------|------|-----------|---------------|--------|
| S1 | Happy-path narration of a full deck | 1,2,3 | pacing | all slides delivered; ends within TBA | ✅ T1 |
| S2 | Barge-in mid-sentence → QUESTION → answer → resume | 1,2,3 | 1,2,6 | resume index == interrupted sentence start; answer produced; queue cleared | ✅ T1 |
| S3 | NEXT / PREV / GOTO during narration | 1,2,3 | 3 | current_slide changes correctly; GOTO parses number; resets sentence | ✅ T1 |
| S4 | STOP then resume (control) | 1,3 | — | phase → PAUSED | ✅ T1 |
| S5 | IGNORE ("okay") does not derail narration | 1,2 | 3 | no route taken; resume from same sentence | ✅ T1 |
| S6 | Nav clears stale resume; turn_id guard suppresses stale answer | 1 | 5 | pending_resume cleared on nav; superseded answer not spoken | ✅ T1 |
| S7 | Pacing drift → track switch STANDARD→SUMMARY→TURBO | 1,2 | 4 | upcoming track changes on drift; no switch on schedule | ✅ T1 |
| S8 | Spurious VAD (noise) → empty STT → resume | 1,2 | 1 | no intent fired; zero context loss | ✅ T1 |
| S-full | Both loops together: barge-in detected + no deadlock | 1 | 1,2,3 | burst transcribed, question answered, gather completes | ✅ T1 |
| S9 | LLM (Groq) down → Ollama fallback → graceful degrade | 2 | 6 | answer via fallback or graceful message; session survives | ⏳ T2 |
| S10 | Second concurrent session rejected | HTTP | — | `409` (single-presentation constraint) | ✅ API |
| S11 | Long question (>2 s) handled | 2 | — | utterance capped at `stt_max_utterance_ms`; completes (R3) | ⏳ T2 |
| S12 | Out-of-deck question → no hallucination | 2 | 6 | answer states info not in slides | ⏳ T2 |
| S13 | Upload → build (3 tracks) → status → session | HTTP | control | build completes; 3 tracks; navigate/status work | ✅ API |
| S14 | Agent joins LiveKit room + streams narration to a participant | 3 | all | agent dispatched, track subscribed, audio frames received | ✅ live (agent→participant) |
| S14b | Participant mic → agent answer round-trip | 3 | all | agent transcribes published mic + answers | ⏳ needs browser mic |

✅ T1 = `tests/test_agent_e2e.py` (12). ✅ API = `tests/test_api_integration.py` (8,
real HTTP via httpx ASGITransport). Full Windows pipeline verified by
`run_local.py --selftest` (real STT→intent→RAG→Groq→edge-TTS). ⏳ = pending (T2/T3
exercise the remaining live-audio + WebRTC paths). **Suite: 50 passed, 1 skipped.**

---

## 5. Benchmark replication (doc Tables 5.1–5.11)

Re-measure the source doc's headline numbers **inside the container on reference
HW** (Intel i5, 8 GB RAM) and assert against the six success gates.

| Metric | Gate | Harness | Tier |
|--------|------|---------|------|
| VAD latency (P50/P95/P99) | < 5 ms | 100 warmed iters | 1 (done: 0.2 ms) |
| Intent accuracy | ≥ 95% | full 68/22/9 corpus | 1/2 |
| Context recovery | 100% | 105 interrupt positions (25/50/75% + sweep) | 1 |
| Time Budget Accuracy | ≥ 95% | 7 scenarios, 5–45 min | 2 |
| Model footprint | < 50 MB | size audit of VAD+MiniLM | 1 |
| E2E latency | < 5 s | VAD→STT→Intent→LLM TTFT→TTS sum | 2 |

`test_benchmarks.py` + `test_e2e_latency.py` already scaffold these; they need the
fixtures (§7) and a `make bench` target that runs them in the image.

---

## 6. Test harness components to BUILD

These don't exist yet and are required for Tiers 1–3:

| Component | What it does | For |
|-----------|--------------|-----|
| `FakeAudioSource` | records `capture_frame`/`clear_queue` calls instead of sending to LiveKit; lets tests inspect pushed audio + assert queue-clear on interrupt | T1,T2 |
| `FakeAudioStream` | async-iterates synthetic/recorded frames into `_input_loop`; can inject a "speech" burst at a controlled time to fire barge-in | T1,T2 |
| Stub services | `StubSTT` (returns scripted text), `StubLLM` (returns canned answer), `StubTTS` (emits N silent frames) for deterministic T1 | T1 |
| `AgentTestHarness` | builds a `SessionState` + worker wired to fakes; helpers: `narrate_until(sentence)`, `inject_utterance(text/wav)`, `assert_resume_at(idx)` | T1,T2 |
| WAV fixtures generator | renders the intent utterances to 16 kHz mono WAV (via Piper in-container or recorded) | T2 |
| LiveKit test client | headless participant: publishes a WAV track, subscribes to agent output, dumps received audio | T3 |
| Latency collector | parses the loguru per-stage DEBUG lines into a per-stage timing report | T2 benchmarks |
| `docker-compose.test.yml` | backend + livekit(`--dev`) + optional ollama, with fixtures mounted | T2,T3 |

---

## 7. What I need (prerequisites checklist)

**Environment**
- [ ] **Linux host or the Docker image** — mandatory for Piper TTS (R12) and for reproducible benchmarks. Windows can only run Tier 1.
- [ ] Docker + docker-compose (already have `docker/`); add a `test` profile.
- [ ] `make`/task runner for `make bench`, `make e2e` (optional but tidy).

**Credentials / services**
- [ ] **Groq API key** (`CB_GROQ_API_KEY`) for real Q&A + script generation — OR
- [ ] **Ollama** running locally with `llama3.1:8b` pulled (fallback path; also lets S9 be tested).
- [ ] **LiveKit** for Tier 3: either self-hosted (`livekit-server --dev`, keys via env) or LiveKit Cloud creds (`CB_LIVEKIT_URL/API_KEY/API_SECRET`).

**Test assets / fixtures**
- [ ] A **sample `.pptx`** (~5–10 slides): mixed text + speaker notes + at least one image containing text (to exercise OCR) + a "facts" slide (revenue/numbers) for RAG grounding checks.
- [ ] **Recorded/synthesized WAV utterances** (16 kHz mono), one per intent path: `"next slide"`, `"go back"`, `"go to slide three"`, `"what was the revenue"`, `"stop"`, `"okay"`, plus a long (>2 s) question for S11 and an out-of-deck question for S12.
- [ ] **Labeled intent corpus** matching the doc's split (68 standard / 22 adversarial / 9 noisy) to assert the ≥95% gate. ← *needed to formally close the accuracy gate; the doc lists counts but not the phrases.*
- [ ] **Ground-truth transcripts** for the WAV utterances (STT WER measurement).
- [ ] **Expected-answer / source-slide map** for RAG grounding assertions.

**Code/spec confirmations (seams to lock down first)**
- [ ] Verify the **Piper streaming API** in the pinned `piper-tts` (`synthesize_stream_raw` vs `synthesize`) and the chunk type — adjust `tts_service.py` if needed (known version-sensitive seam).
- [ ] Verify the **LiveKit Agents dispatch** mechanism for the pinned `livekit-agents` (in-process task vs `cli.run_app`) and finalize `main._run_agent_worker` / `agent_worker.setup_audio` (R5).
- [ ] Confirm `AudioFrame` / `AudioSource.capture_frame` / `clear_queue` signatures match the pinned `livekit` rtc version.

---

## 8. Exit criteria (when is E2E "passing"?)

E2E is green when, **in the container**:
1. Tier 1 scenarios S1–S8, S10, S13 pass (orchestration logic correct).
2. Tier 2 scenarios S1–S3, S5, S7, S9, S11, S12 pass with real models + LLM.
3. Tier 3 S14 passes (one full WebRTC round-trip).
4. Benchmark gates met: VAD <5 ms, context recovery 100%, TBA ≥95%, intent ≥95%, footprint <50 MB, E2E <5 s.
5. No audio-collision and no context loss observed across the barge-in matrix (25/50/75% positions × all intent types).

Tier 4 (human) is acceptance, gated on the frontend.

---

## 9. Recommended sequencing

1. **Lock the seams** (§7 last group): Piper API + LiveKit dispatch/rtc signatures. Cheap, unblocks everything.
2. **Build the Tier-1 harness** (fakes + stubs + `AgentTestHarness`) → run S1–S8, S10, S13. *Highest value, no Linux/LLM needed.*
3. **Build fixtures** (sample `.pptx`, WAV utterances, intent corpus).
4. **Stand up `docker-compose.test.yml`**; wire Groq/Ollama → run **Tier 2** (real models) → benchmark replication.
5. **Tier 3** LiveKit client → S14 + reconnect.
6. **Tier 4** once the frontend exists.

---

## 10. Risks & known gaps

- **TTS only on Linux** — all TTS-touching tests must run in-container; Windows dev is Tier-1-only.
- **LiveKit dispatch is unverified** (R5) — Tier 3 is where it gets exercised/finalized; expect iteration there.
- **Intent corpus must be authored** — the doc gives counts, not phrases; the ≥95% gate can't be formally closed without it.
- **Real-time flakiness** — Tier 3 timing assertions should use tolerances, not exact equality; prefer Tier 1/2 for deterministic correctness and reserve Tier 3 for "does the transport work."
- **Groq cost/limits** — keep Tier 2 LLM calls small; cache/scripts can be pre-generated to avoid re-billing on every run.
