# Chatterbot-AI — Backend

Real-time, full-duplex voice presentation agent. This package implements the
backend specified in [`../IMPLEMENTATION.md`](../IMPLEMENTATION.md) (read that
first — every module here cites the section it implements).

## Layout (see IMPLEMENTATION.md §3)

```
main.py        FastAPI app + lifespan + LiveKit agent bootstrap (§6, §8.2)
config.py      env-driven settings (§4)
models.py      all pydantic schemas (§5)
deps.py        ServiceRegistry — model singletons (§6)
app_state.py   in-memory job/build/session stores (§7.1)
api/           REST routers: health, presentation, session (§7)
core/          orchestrator + novel mechanisms (agent_worker, pacing,
               playback_tracker, knowledge_base) (§9, §15–§17)
services/      ML wrappers: vad, stt, tts, intent, embeddings, llm, slides
utils/         logging, text, audio helpers
prompts/       LLM prompt templates (§14.2, §18.2)
tests/         pure-logic unit tests + benchmark scaffolds (§21)
```

## Run (local, dev)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r ../requirements.txt
cp .env.example .env                                 # fill in keys
uvicorn main:app --reload
```

The REST control plane (upload / build / RAG) works without LiveKit; live voice
sessions need `CB_LIVEKIT_*` configured. See IMPLEMENTATION.md §22 for Docker.

## Tests

```bash
cd backend
pytest tests/ -q          # pure-logic tests run without the heavy models
```

`test_pacing`, `test_playback_tracker`, and the text-utility tests need no ML
dependencies. `test_intent` requires sentence-transformers (the shared MiniLM).
End-to-end / benchmark suites (§21) run inside the container on reference HW.

## ML smoke validation

`tests/validate_ml.py` loads the real models and exercises each ML service
(downloads weights on first run into `CB_MODELS_DIR`):

```bash
cd backend
CB_MODELS_DIR="$(cd .. && pwd)/models" ../.venv/Scripts/python.exe tests/validate_ml.py
```

Expected: PASS for embeddings, VAD, STT, intent, RAG.

### Platform notes (validated on Windows / Python 3.12)
- **Piper TTS is Linux/macOS only.** `piper-tts` → `piper-phonemize` has no Windows
  wheel (recheck R12). On Windows, install everything except `piper-tts`
  (`grep -v piper-tts ../requirements.txt > req.txt && pip install -r req.txt`)
  and validate TTS inside the Docker image — the deployment target is Linux anyway.
- **VAD uses pure onnxruntime** (no torch.hub / torchaudio); the Silero `.onnx`
  ships with the `silero-vad` package.
- **`livekit`** is resolved by `livekit-agents` — don't hard-pin it (recheck R1).
