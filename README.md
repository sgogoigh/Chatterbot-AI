# Chatterbot-AI

A real-time, full-duplex **voice AI that delivers your slide decks** — narrating
within a strict time budget, letting you **interrupt with your voice** to ask
questions (answered from the deck via RAG), then **resuming the exact sentence**
it left off on.

- **Backend** — FastAPI + Python: VAD (Silero) · STT (Faster-Whisper) · intent
  (MiniLM) · RAG (FAISS + BM25) · LLM (Groq) · TTS (edge-tts on Windows / Piper in
  Docker), orchestrated over LiveKit WebRTC.
- **Frontend** — Next.js 16 + React 19 + Tailwind 4: upload → configure → build →
  present, with a live voice session.

> New here? Read [`PLAN.md`](./PLAN.md) for what it is, [`IMPLEMENTATION.md`](./IMPLEMENTATION.md)
> for how the backend works, and [`WINDOWS_TESTING.md`](./WINDOWS_TESTING.md) for the
> Windows-only setup notes.

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11+ | backend (3.12 works) |
| Node.js | 20+ | frontend (22 recommended) |
| Groq API key | — | LLM + grounded answers · free tier at [console.groq.com](https://console.groq.com) |
| LiveKit Cloud | optional | only for the live **voice** session ([cloud.livekit.io](https://cloud.livekit.io)) |

Everything runs on **Windows, macOS, or Linux — no Docker required.**

---

## 1. Credentials (`.env`)

Create `.env` in this folder (`Chatterbot-AI/.env`). The backend reads it
automatically from any working directory.

```ini
# Required for LLM answers + script generation
GROQ_API_KEY=gsk_your_key_here

# Optional — only needed for the live voice session ("Go live")
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_key
LIVEKIT_API_SECRET=your_livekit_secret

# Optional — higher STT accuracy via Groq instead of local Whisper
# CB_STT_BACKEND=groq
```

You can still run the whole upload → build → present flow **without** LiveKit; only
the in-browser voice ("Go live") needs those three keys.

---

## 2. Backend (FastAPI) — terminal 1

```bash
cd Chatterbot-AI

# one-time: create the virtual env + install deps
python -m venv .venv
#   Windows (PowerShell):  .venv\Scripts\Activate.ps1
#   Windows (cmd):         .venv\Scripts\activate.bat
#   Git Bash / macOS / Linux: source .venv/Scripts/activate   (Windows)
#                              source .venv/bin/activate        (macOS/Linux)
pip install -r requirements.txt

# run the API (from the backend/ folder)
cd backend
python -m uvicorn main:app --port 8000
```

- **First run downloads ~570 MB of models** (MiniLM, Silero VAD, Faster-Whisper
  `small.en`) into `backend/models/` and warms them — startup takes ~30–60 s. The
  log prints `models loaded + warmed; backend ready`.
- To **reuse an already-downloaded cache** (e.g. the repo's `models/`), point the
  backend at it: `CB_MODELS_DIR=../models python -m uvicorn main:app --port 8000`.
- Check it's up: open <http://localhost:8000/healthz> → `{"status":"ok"}`.

> Windows note: TTS uses **edge-tts** (free, automatic). The Linux-only Piper
> package is excluded from `requirements.txt` and lives in `requirements-linux.txt`
> for the Docker image — you don't need it on Windows.

---

## 3. Frontend (Next.js) — terminal 2

```bash
cd Chatterbot-AI/frontend

# one-time
npm install
cp .env.local.example .env.local      # default points at http://localhost:8000

npm run dev
```

Open **<http://localhost:3000>**. The header shows a **backend** dot — teal when it
can reach the API.

---

## 4. Using it

1. **Upload** a `.pptx` — drag it onto the dropzone. (Try the included sample:
   `test_assets/terragrid_q3.pptx`.)
2. **Delivery** — pick a voice persona (General / Teacher / Meeting / TEDx) and a
   time budget, then build. The backend writes three pacing tracks and indexes the
   slides for Q&A (progress shown live).
3. **Present** — the slide viewer, transport controls (prev / next / go-to / pause
   / end), the time-budget rail, and the voice orb. Navigation works immediately.
4. **Go live** (needs LiveKit keys) — connects your mic so you can interrupt and
   ask questions out loud; the agent answers from the deck and resumes.

---

## 5. Configuration reference

All backend settings are env vars (prefix `CB_`, or the conventional names below).
Defaults are sensible; override in `.env`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `GROQ_API_KEY` | — | LLM (answers + script generation) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq chat model |
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | — | live voice session |
| `CB_STT_BACKEND` | `local` | `local` (Faster-Whisper, offline) or `groq` (whisper-large-v3-turbo, more accurate) |
| `CB_TTS_BACKEND` | `auto` | `auto` → edge-tts on Windows, Piper in Docker |
| `CB_MODELS_DIR` | `models` | where models are cached/downloaded |
| `NEXT_PUBLIC_API_BASE` *(frontend)* | `http://localhost:8000` | backend URL the UI calls |

---

## 6. Testing

From `backend/` with the venv active:

```bash
# unit + HTTP integration tests (offline; intent/benchmark tests load local models)
CB_MODELS_DIR=../models python -m pytest -q
#   pure-logic only (no models at all): python -m pytest -q tests/test_pacing.py \
#     tests/test_playback_tracker.py tests/test_text_utils.py tests/test_speech_buffer.py

# full pipeline self-test on Windows (edge-TTS → Whisper → intent → RAG → Groq → TTS)
CB_MODELS_DIR=../models python run_local.py --selftest

# run real voice clips through the pipeline (graded vs the TerraGrid deck)
CB_MODELS_DIR=../models python tests/run_clips.py

# verify the agent joins a LiveKit room and streams audio (needs LiveKit keys)
CB_MODELS_DIR=../models python tests/verify_livekit_agent.py
```

Try the live mic loop end-to-end (needs a microphone):
`CB_MODELS_DIR=../models python run_local.py` — talk to the demo deck in your terminal.

See [`E2E_TESTING.md`](./E2E_TESTING.md) for the full test strategy and current
status, and [`ERRORS.md`](./ERRORS.md) for issues found + fixed along the way.

---

## 7. Troubleshooting

- **Backend dot stays red / "offline".** The API isn't reachable — confirm
  uvicorn is running on port 8000 and `http://localhost:8000/healthz` responds. If
  the frontend runs on a different host, set `NEXT_PUBLIC_API_BASE` in
  `frontend/.env.local`.
- **First request is slow / `503` on `/readyz`.** Models are still loading on first
  start (downloading ~570 MB). Wait for `backend ready` in the log.
- **"Go live" connects but you hear nothing.** Voice needs the three `LIVEKIT_*`
  keys set in `.env`; without them the slide/navigation flow still works.
- **A spoken question is misheard.** Local `small.en` is fast but small; set
  `CB_STT_BACKEND=groq` in `.env` for higher accuracy (needs the Groq key).
- **`pip install` fails on `piper-tts` (Windows).** It shouldn't — Piper isn't in
  `requirements.txt`. Make sure you're installing `requirements.txt`, not
  `requirements-linux.txt`.

---

## 8. Project layout

```
Chatterbot-AI/
├── backend/         FastAPI app, services, agent (see backend/README.md)
├── frontend/        Next.js app (see frontend/README.md)
├── test_assets/     sample deck + voice clips for testing
├── docker/          Dockerfile + compose (Linux deploy)
├── requirements.txt           cross-platform backend deps (Windows-friendly)
├── requirements-linux.txt     Piper TTS (container only)
├── PLAN.md · IMPLEMENTATION.md · WINDOWS_TESTING.md · E2E_TESTING.md · ERRORS.md
└── .env             your credentials (not committed)
```

## Deployment (Linux / Docker)

For a containerized deploy (with Piper TTS), use `docker/Dockerfile` +
`docker/docker-compose.yml` and install `requirements.txt -r requirements-linux.txt`.
See [`WINDOWS_TESTING.md`](./WINDOWS_TESTING.md) §1 for why Docker is optional locally.
