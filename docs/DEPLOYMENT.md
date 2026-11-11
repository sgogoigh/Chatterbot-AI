# Chatterbot-AI — Production Deployment (Vercel + Fly.io + LiveKit Cloud)

> **Status (2026-11-11):** backend **live** at <https://chatterbot-backend.fly.dev>
> (`shared-cpu-2x`, 4 GB, `ams`); `/healthz` and `/readyz` return 200. It only
> launched after two refusals (no performance machines, then a 2-CPU-core cap per
> machine). The launcher created **two** machines — scale to one (§4.4).
> Frontend **live** at <https://chatterbot-ai.vercel.app> (built with
> `NEXT_PUBLIC_API_BASE=https://chatterbot-backend.fly.dev`).
> **Open:** later the same day the backend stopped answering (`/healthz` timed out
> or returned a Fly-proxy 503 after ~38 s, continuously for 6+ min); cause not
> yet diagnosed (§4.7). No end-to-end session has run in production yet. Companion docs: [`IMPLEMENTATION.md`](./IMPLEMENTATION.md)
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
   CPU. Dedicated (`performance`) CPUs are the right fit; the org can't use them
   yet and is capped at 2 cores per machine, so it runs on `shared-cpu-2x`, which
   can lag under sustained load.

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
- After a deploy, Fly prints *"Creating a second machine for high availability …
  set `min_machines_running = 0`"*. **Don't follow that suggestion:** it would also let
  Fly stop the only machine. Use `--ha=false` / `fly scale count 1` instead.
- `--ha=false` stops Fly from creating the default second machine.
- `--wait-timeout` covers the long first build and the `/readyz` check, which
  only passes once models are warmed. Fly caps `grace_period` at 1 minute, so
  the first checks may fail on a slow boot; the deploy keeps checking.
- Machine size: `shared-cpu-2x`, 4 GB (that size's memory maximum), because the
  org isn't allowed `performance` machines and is capped at 2 CPU cores per
  machine (see "Deploy errors seen so far" below). Shared CPUs burst above a small baseline, so
  live voice may lag under sustained load. Switch to `performance-2x` once Fly
  billing lifts both limits. Neither size has been measured; check CPU and memory in the
  Fly dashboard after a real session.

#### Deploy errors seen so far
| Error | Cause | Fix |
|---|---|---|
| *Could not find a Dockerfile, nor detect a runtime or framework* | Launcher path was not `backend/`, or the commit predates the `backend/` layout | Set the path to `backend`; deploy a commit that has `backend/Dockerfile` |
| *Your organization is not allowed to use performance machines* | New orgs are limited to shared CPUs | Use a shared size, or ask billing@fly.io to unlock performance machines |
| *Your organization is limited to 2 CPU cores per machine* | New-org CPU cap; `shared-cpu-4x` has 4 cores | `size = 'shared-cpu-2x'` in `backend/fly.toml`, or ask billing@fly.io to raise the cap |
| *grace period greater than 1 minute … will be lowered* (warning) | Fly's cap | `grace_period = '1m0s'` |
| *This deployment will: create 2 "app" machines* (happened on the first successful deploy: `811d09da9e4178` + `2862197c446508`) | Fly's high-availability default; dashboard deploys don't pass `--ha=false` | Deploy with `fly deploy --ha=false` from `backend/`, or run `fly scale count 1 -a chatterbot-backend` afterwards |

### 4.5 Verify
Production URL: **<https://chatterbot-backend.fly.dev>**

```bash
curl https://chatterbot-backend.fly.dev/healthz     # {"status":"ok"}
curl https://chatterbot-backend.fly.dev/readyz      # {"status":"ready"} once warm
fly logs | grep "LLM backend"                        # expect gemini OK, groq OK
```
- `ollama ... UNAVAILABLE` in the log is expected: there's no Ollama on Fly.
- **`/` returns 404 `{"detail":"Not Found"}` — expected.** The API has no root
  route (routes are `/healthz`, `/readyz`, `/api/...`), so Fly's post-deploy
  warning *"returned a 404 HTTP code"* is not a fault. The interactive API docs
  are at `/docs`.

Checked 2026-11-11: `/healthz` 200, `/readyz` 200, `/docs` 200, `/` 404.

### 4.6 Production URLs
| Piece | URL |
|---|---|
| Frontend (Vercel) | <https://chatterbot-ai.vercel.app> |
| Backend (Fly.io) | <https://chatterbot-backend.fly.dev> (`/healthz`, `/readyz`, `/docs`) |

### 4.7 Backend not responding (diagnosis)
Symptom seen 2026-11-11: every request times out, or Fly's proxy returns 503
after ~38 s. That means no machine is accepting connections; the app code isn't
the first suspect (uploads, builds and LLM calls all run off the event loop). Check, in order:
```bash
fly status -a chatterbot-backend          # machine state: started / stopped / replacing
fly machine list -a chatterbot-backend    # one machine, checks passing?
fly logs -a chatterbot-backend            # look for "Out of memory", "Killed", tracebacks,
                                          # or repeated startup (restart loop)
```
- **OOM / restart loop:** 4 GB is the ceiling of `shared-cpu-2x`. Whisper +
  MiniLM + Piper + LibreOffice (slide rendering on upload) may not fit. If the
  logs show OOM, the next step is `CB_WHISPER_MODEL=base.en` or rendering slides
  in a lower-memory way. Don't add swap without measuring.
- **Slow boot:** models load before uvicorn listens. On 2 shared cores that may
  take minutes; the proxy returns 503 the whole time.
- **Stopped machine:** `fly machine start <id>`.

### 4.8 Custom domain (optional)
```bash
fly certs add api.example.com      # then add the DNS records it prints
```

---

## 5. Frontend on Vercel

**Deployed:** <https://chatterbot-ai.vercel.app> (Root Directory `frontend`).
Checked 2026-11-11: page 200, and the client bundle contains the Fly backend
URL (the `localhost:8000` string also in it is only the code's fallback).

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
- One always-on Fly machine (`shared-cpu-2x` now, `performance-2x` later): the main fixed cost.
- LiveKit Cloud participant minutes.
- Gemini/Groq tokens: script generation per build, plus each answer.

Check current prices on each provider's pricing page; none were measured here.
