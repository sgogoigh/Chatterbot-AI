# Chatterbot-AI — Frontend

Next.js 16 + React 19 + Tailwind 4 web UI for the Chatterbot-AI voice presentation
agent. It drives the FastAPI backend (`../backend`) and connects to LiveKit for the
live voice session.

## Design

A "control-room" identity where **color encodes the agent's state** — amber for
*speaking / on air*, teal for *listening* — on a deep blue-charcoal ground. The
present screen's hero is a Canvas **voice-orb** that visualizes the full-duplex
state. Type: Space Grotesk (display) · Inter (body) · JetBrains Mono (the clock /
time-budget data). See `app/globals.css` for the committed palette/tokens.

## Run

```bash
cd frontend
cp .env.local.example .env.local      # set NEXT_PUBLIC_API_BASE if backend isn't on :8000
npm install                           # already done if you scaffolded
npm run dev                           # http://localhost:3000
```

Start the backend too (separate terminal):

```bash
cd ../backend
CB_MODELS_DIR=../models ../.venv/Scripts/python -m uvicorn main:app
```

The header shows a **backend** indicator (teal = reachable). `npm run build` for a
production build; `npm start` to serve it.

## Flow

1. **Deck** (`components/Uploader.tsx`) — drag-drop a `.pptx`.
2. **Delivery** (`components/Configurator.tsx`) — pick a voice persona + time budget.
3. **Building** (`components/BuildProgress.tsx`) — polls the async build (3 tracks + RAG index).
4. **Present** (`components/Presenter.tsx`) — slide viewer, voice orb, transport
   (prev/next/goto/pause/end), "Go live" mic, and the time-budget rail.
   Every control works over REST; **Go live** adds the LiveKit voice loop.

## Structure

```
app/         layout (fonts) · page.tsx (step orchestrator + hero) · globals.css (design system)
components/  Uploader · Configurator · BuildProgress · Presenter · VoiceOrb
lib/         api.ts (typed backend client) · types.ts · livekit.ts (voice connect)
```

## Notes

- Slide images load directly from the backend via `<img>` (no `next/image` remote
  config needed).
- The live voice path needs the backend's LiveKit agent dispatched to the room
  (the W3 path in `../WINDOWS_TESTING.md`); slide navigation works regardless.
