# Chatterbot-AI — Production Deployment (Vercel + Fly.io + LiveKit Cloud)

> **Status:** plan + config, **not yet deployed**. `backend/fly.toml` and the Docker image
> are written but have not been built or run on Fly yet — budget a first
> deploy for fixing surprises. Companion docs: [`IMPLEMENTATION.md`](./IMPLEMENTATION.md)
> §22 (image), [`E2E_TESTING.md`](./E2E_TESTING.md) §0 (open gaps).

---

## 1. Topology

```
                       HTTPS (REST: upload, build, session, status, slide images)
   Browser ────────────────────────────────────────────────►  Fly.io: chatterbot-backend
   (Next.js app                                                 FastAPI + models + in-process
    served by Vercel)                                           LiveKit voice agent (1 machine)
      │                                                               │
      │ WebRTC (mic up / narration down)                              │ WebRTC (agent joins room)
      └──────────────────────►  LiveKit Cloud  ◄──────────────────────┘
                                                                      │ HTTPS
                                                   Gemini 3.8 Flash ──┤ (LLM, primary)
                                                   Groq gpt-oss-120b ─┘ (LLM fallback, optional STT)
```

| Piece | Runs on | Notes |
|---|---|---|
| Frontend | **Vercel** (`frontend/`) | Static/SSR Next.js; calls the backend directly from the browser. |
| Backend | **Fly.io** (`backend/fly.toml` + `backend/Dockerfile`, app dir `backend/`) | One always-on machine. Models baked into the image. |
| Media | **LiveKit Cloud** | Existing `LIVEKIT_*` keys. The agent dials out to it, so Fly needs no UDP ports. |
| LLM | Gemini API, Groq API | `GEMINI_API_KEY` primary, `GROQ_API_KEY` fallback. |

---

## 2. Constraints that shape the deployment

These come from the current code, not from Fly or Vercel:

1. **Exactly one backend machine.** Sessions, jobs and the voice agent live in
   process memory (`app_state.py`). A second machine would split state and
   requests would land on the wrong one. Hence: no autoscaling, no auto-stop, and
   watch out for Fly's default of **two machines on first deploy** (§4.4).
2. **One presentation at a time, globally.** `POST /api/sessions` ends any
   existing session ([routes_session.py](../backend/api/routes_session.py)). In
   production, a second visitor starting a talk cuts off the first. Fine for a
   demo; a blocker for multi-user use.
3. **No authentication.** Anyone with the backend URL can upload decks, run
   builds (LLM cost) and open LiveKit rooms (minutes cost). CORS is `*`.
4. **Restart = clean slate.** Job data is cleared on every start, so a deploy or
   crash drops in-flight sessions and uploaded decks. Users re-upload.
5. **CPU real-time audio.** VAD + Whisper STT + Piper TTS run on the machine's
   CPU, so it needs dedicated CPUs, not shared ones.

---

## 3. Prerequisites

- Accounts: Fly.io (payment method on file), Vercel, LiveKit Cloud (existing),
  Google AI Studio (Gemini key), Groq (optional fallback).
- CLIs: [`flyctl`](https://fly.io/docs/flyctl/install/) and optionally the
  [Vercel CLI](https://vercel.com/docs/cli) (`npm i -g vercel`).
- The repo pushed to GitHub (Vercel imports from it).
- Values for: `GEMINI_API_KEY`, `GROQ_API_KEY`, `LIVEKIT_URL`,
  `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` (the same ones in your local `.env`).

---

## 4. Backend on Fly.io

### 4.1 What's in the image
**`backend/` is the Fly app directory and the Docker build context.** Everything
the image needs lives there: [`backend/Dockerfile`](../backend/Dockerfile),
[`backend/fly.toml`](../backend/fly.toml), [`backend/.dockerignore`](../backend/.dockerignore),
`backend/requirements*.txt` and `backend/scripts/`.

The image:
- `python:3.11-slim`, digest-pinned.
- System packages: ffmpeg, libsndfile, tesseract, and LibreOffice + poppler for slide images.
- CPU-only torch.
- `requirements.txt` + `requirements-linux.txt` (Piper).
- Runs as the non-root user `app`.
- `scripts/prefetch_models.py` bakes Whisper `small.en`, MiniLM and the Piper voice
  into `/models` at build time, so boot downloads nothing and no volume is needed.

Expect a large image (several GB) and a slow first build.

### 4.2 Create the app
**Option A — Fly dashboard ("Launch from GitHub"):**
1. Set the app's **working directory / path to `backend`**. Fly then finds
   `backend/fly.toml` and `backend/Dockerfile`. With the repo root (or with an
   older checkout that predates this layout), launch fails with *"Could not find
   a Dockerfile, nor detect a runtime or framework"*.
2. Use the same app name and region as in `fly.toml`
   (`chatterbot-backend`, `ams`), or edit `fly.toml` to match what you pick.
3. Add the secrets from §4.3 in the launcher's environment section.
4. After the first deploy, run `fly scale count 1 -a chatterbot-backend`. The
   launcher may create two machines (§4.4).

**Option B — CLI**, from `backend/`:

```bash
fly auth login
cd backend
# Edit fly.toml first if needed: unique `app` name, `primary_region`
# (`fly platform regions`; pick one near your users AND your LiveKit Cloud region).
fly apps create chatterbot-backend          # same name as in fly.toml
```

### 4.3 Secrets
Secrets are encrypted and injected as env vars. The backend reads these names directly:

```bash
fly secrets set \
  GEMINI_API_KEY=... \
  GROQ_API_KEY=... \
  LIVEKIT_URL=wss://<project>.livekit.cloud \
  LIVEKIT_API_KEY=... \
  LIVEKIT_API_SECRET=...
```

Do **not** set `CB_LIVEKIT_PUBLIC_URL`. With LiveKit Cloud the browser and the
agent use the same `wss://` URL.

Non-secret settings live in `[env]` in [`backend/fly.toml`](../backend/fly.toml): `CB_TTS_BACKEND=piper`,
`CB_LLM_PRIMARY=gemini`, `CB_LOG_LEVEL=INFO`. Any `CB_*` setting from the README's
configuration table can be added there.

### 4.4 Deploy
From `backend/`:

```bash
fly deploy --ha=false --wait-timeout 15m
fly scale count 1            # make sure exactly ONE machine exists (constraint 1)
fly status
```
- `--ha=false` stops Fly from creating the default second machine.
- `--wait-timeout` covers the long first build and the `/readyz` check, which
  returns 503 until models are warmed (`grace_period = 180s` in `fly.toml`).
- Machine size: `performance-2x`, 4 GB. This is an estimate, not a measurement;
  check memory in the Fly dashboard after a real session and adjust
  `[[vm]]` in `fly.toml`.

### 4.5 Verify
```bash
curl https://chatterbot-backend.fly.dev/healthz     # {"status":"ok"}
curl https://chatterbot-backend.fly.dev/readyz      # {"status":"ready"} once warm
fly logs | grep "LLM backend"                        # expect gemini OK, groq OK
```
`ollama ... UNAVAILABLE` in the log is expected: there's no Ollama on Fly.

### 4.6 Custom domain (optional)
```bash
fly certs add api.example.com      # then add the DNS records it prints
```

---

## 5. Frontend on Vercel

The frontend calls the backend from the browser through `NEXT_PUBLIC_API_BASE`
([frontend/lib/api.ts](../frontend/lib/api.ts)). It is inlined **at build
time**, so changing it requires a redeploy.

### 5.1 Dashboard
1. **Add New → Project** → import the GitHub repo.
2. **Root Directory:** `frontend`. The framework preset auto-detects Next.js.
3. **Environment Variables** (Production, and Preview if you use previews):
   `NEXT_PUBLIC_API_BASE = https://chatterbot-backend.fly.dev` (or your custom
   domain; no trailing slash).
4. Deploy.

### 5.2 CLI (alternative)
```bash
cd frontend
vercel link                                   # choose/create the project; root = frontend
vercel env add NEXT_PUBLIC_API_BASE production
vercel --prod
```

### 5.3 Notes
- The backend URL must be `https://`, or the browser blocks the calls as mixed
  content. Fly serves HTTPS by default (`force_https = true`).
- **Preview deployments share the single production backend** (constraint 2): a
  preview session will end a production session. Point previews at a separate
  Fly app if that matters.
- CORS is currently `*`, so Vercel origins work without backend changes.

---

## 6. Before real users (hardening checklist)

| Item | Why | Effort |
|---|---|---|
| Add auth (even a shared token or Vercel-side login) | Anyone can spend your LLM/LiveKit budget (constraint 3) | small–medium |
| Restrict CORS to the Vercel domain | `allow_origins=["*"]` is hard-coded in `backend/main.py` | small (make it configurable) |
| Per-key spend limits / alerts in Google AI Studio, Groq and LiveKit | Cost containment | console only |
| Multi-session support (state in Redis, agent per room) | Constraint 1–2; required to scale past one presenter | large |
| CI: run `pytest` + `eslint` on push, then `flyctl deploy` from GitHub Actions | No CI exists today | small |
| Sentry or similar error reporting | Only logs today | small |
| Test the container end-to-end before the first deploy | Piper + LibreOffice + LiveKit have never run in this image | see E2E_TESTING.md |

---

## 7. Operations

| Task | Command |
|---|---|
| Logs | `fly logs` |
| Shell into the machine | `fly ssh console` |
| Restart (drops live sessions) | `fly machine restart <id>` |
| List releases | `fly releases --image` |
| Roll back | `fly deploy --image <previous image ref>` |
| Rotate a key | `fly secrets set GEMINI_API_KEY=...` (triggers a restart) |
| Switch LLM primary to Groq | set `CB_LLM_PRIMARY = "groq"` in `backend/fly.toml`, `fly deploy` from `backend/` |
| Frontend rollback | Vercel dashboard → Deployments → promote a previous one |

**Cost drivers:**
- One always-on dedicated-CPU Fly machine: the main fixed cost.
- LiveKit Cloud participant minutes.
- Gemini/Groq tokens: script generation per build, plus each answer.

Check current prices on each provider's pricing page; none were measured here.
