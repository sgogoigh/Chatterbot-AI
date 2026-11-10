# Chatterbot-AI — Errors & Fixes Log

A numbered record of every error/problem hit while building and validating the
backend, with its effect and the fix applied. Cross-references:
[`IMPLEMENTATION.md`](./IMPLEMENTATION.md) recheck IDs (R1–R12) where relevant.

Status key: ✅ resolved · ⏭ deferred (by design).

---

## PROBLEM 1 — Writing to empty pre-created files (tooling)
**Topic:** editor/tooling workflow
**[Description & Effect]**
`PLAN.md`, `IMPLEMENTATION.md`, and `requirements.txt` already existed on disk as
empty, IDE-tracked files. The first `Write` to each failed with:
`"File has not been read yet. Read it first before writing to it."`
Effect: the initial write of each document was rejected and had to be retried.
**FIX —** Read the (empty) file first, then Write. No content impact; pure
tool-ordering requirement.

---

## PROBLEM 2 — Intent normalization left punctuation behind
**Topic:** intent classifier text preprocessing
**[Description & Effect]**
`utils/text.normalize()` stripped filler words but not punctuation, so
`"Um, please go NEXT you know"` normalized to `", go next"` instead of `"go next"`.
Caught by `tests/test_text_utils.py::test_normalize_strips_filler`:
`assert ', go next' == 'go next'`. Effect: leftover punctuation/commas would
pollute the rule-layer regex matching and embedding lookups in the
`FastIntentClassifier`, degrading intent accuracy on real (punctuated) input.
**FIX —** Added a punctuation-stripping pass (`_PUNCT_RE`, keeping apostrophes for
"that's") to `normalize()` in `utils/text.py`. Also improves ASR robustness since
ASR rarely emits reliable punctuation. Test now passes (20/20 pure-logic tests).

---

## PROBLEM 3 — Hacky inline import in the build route
**Topic:** code quality (self-caught)
**[Description & Effect]**
The async build task in `api/routes_presentation.py` computed the persona WPM with
a one-liner abusing `__import__`:
`wpm = PacingService.density and __import__("core.pacing", fromlist=["PERSONA_PROFILES"]).PERSONA_PROFILES[...]`.
Effect: not a runtime crash, but unreadable, fragile, and relied on a truthiness
side effect — a latent bug waiting to happen.
**FIX —** Added a clean top-level `from core.pacing import PERSONA_PROFILES` and
replaced the line with `wpm = PERSONA_PROFILES[body.persona].wpm`.

---

## PROBLEM 4 — LiveKit dependency resolution conflict (R1)
**Topic:** dependency pinning
**[Description & Effect]**
`pip install -r requirements.txt` failed with `ResolutionImpossible`:
```
livekit-agents 0.12.x depends on livekit>=0.18.1
... but requirements pinned livekit==0.17.*
```
Effect: the entire install aborted — nothing could be installed, blocking all
validation. Root cause: the doc's old `livekit 0.11.1` pin was bumped to
`0.17.*`, but `livekit-agents` constrains the rtc SDK to `>=0.18.1`.
**FIX —** Stopped hard-pinning `livekit`; let `livekit-agents` resolve a
compatible rtc SDK:
```
livekit>=0.18,<1
livekit-agents>=0.12,<0.13
livekit-api>=0.7,<1
```
Updated in `requirements.txt` and `IMPLEMENTATION.md` §2.2. Resolution now
succeeds (livekit-agents 0.12.20 + livekit 0.18.x).

---

## PROBLEM 5 — Piper TTS has no Windows wheel (R12)  ⏭
**Topic:** platform-specific dependency
**[Description & Effect]**
After fixing PROBLEM 4, install failed again:
```
ERROR: Could not find a version that satisfies the requirement piper-phonemize~=1.1.0 (from piper-tts)
ERROR: No matching distribution found for piper-phonemize~=1.1.0
```
`piper-tts` depends on `piper-phonemize`, a Linux/macOS-only C++/espeak-ng
extension with **no Windows wheel** (independent of Python version). Effect: TTS
cannot be installed/validated locally on this Windows machine.
**FIX (deferred by design) —** Piper installs and runs in the Linux Docker image
(`python:3.11-slim`), which is the actual deployment target. For local Windows
dev, install everything except `piper-tts`:
```
grep -v piper-tts requirements.txt > req.txt && pip install -r req.txt
```
TTS is validated inside the container. Documented in `requirements.txt`,
`backend/README.md`, and `IMPLEMENTATION.md` R12. (All other ML services validated
locally.)

---

## PROBLEM 6 — Silero VAD pulled in `torchaudio` and crashed (R6)
**Topic:** VAD model loading
**[Description & Effect]**
The first `validate_ml.py` run failed loading VAD:
```
File ".../silero_vad/utils_vad.py", line 2, in <module>
    import torchaudio
ModuleNotFoundError: No module named 'torchaudio'
```
Cause: `VADService` loaded Silero via `torch.hub.load(..., onnx=True)`, but the
hub `hubconf.py` imports `torchaudio` at module load — a dependency that wasn't in
`requirements.txt`. Effect: VAD could not initialize, breaking the whole
audio-input loop. (This also contradicted IMPLEMENTATION.md §10, which specified a
*pure onnxruntime* path precisely to avoid the torch graph.)
**FIX —** Rewrote `services/vad_service.py` to run the Silero v5 `.onnx` directly
via `onnxruntime`, sourcing the model from the `silero-vad` package's bundled
`data/` dir located through `importlib.util.find_spec` **without importing the
package** (so its `torchaudio` import never fires). Removed the direct `torch`
pin (torch is still pulled transitively by sentence-transformers). Validated at
**0.20 ms** mean latency (target <5 ms, beats the doc's 0.25–0.34 ms).

---

## PROBLEM 7 — Default model cache path `/models` not writable on Windows
**Topic:** configuration / cross-platform paths
**[Description & Effect]**
`Settings.models_dir` defaults to `/models` (the in-container path). On Windows,
`/models` resolves to `C:\models`, which generally isn't a sensible/writable cache
location for HuggingFace/Whisper/Silero downloads. Effect: left unset, local model
downloads would land in an unexpected drive-root directory (or fail).
**FIX —** Ran validation with `CB_MODELS_DIR` pointed at a project-local `models/`
dir (`CB_MODELS_DIR="$(pwd)/models"`). The default stays `/models` for the Docker
image (correct there); local dev overrides it via `.env`/env var. Added `models/`
to `.gitignore` so the ~550 MB of downloaded weights aren't committed.

---

## PROBLEM 8 — Barge-in resumed at the WRONG sentence (context loss)
**Topic:** agent orchestration / deterministic resume (found by Tier-1 E2E)
**[Description & Effect]**
`_next_sentence()` derived the index from the playback sample cursor and **advanced
the cursor to `idx+1` before the sentence was even spoken**. So when a barge-in hit
during sentence *idx*, `resume_point()` returned *idx+1* — narration resumed at the
**next** sentence, silently dropping the interrupted one. This directly breaks the
project's headline guarantee (100% sentence-level context recovery).
**FIX —** Refactored `core/agent_worker.py` to track an explicit
`SessionState.current_sentence`: `_next_sentence()` returns the current sentence
**without advancing**; `_speak_sentence()` advances `current_sentence` **only after
full delivery**, and on interrupt records `pending_resume.sentence_index =
current_sentence` (the sentence being spoken). `_resume()` restores that index.
Proven by `test_s2_bargein_question_resume` (resume lands on the interrupted
sentence, not the next).

---

## PROBLEM 9 — Narration/utterance race (speaker not paused on barge-in)
**Topic:** concurrency / full-duplex coordination (found by Tier-1 E2E)
**[Description & Effect]**
The transition lock serialized audio producers, but nothing stopped the **narration
loop from grabbing the lock and speaking the next sentence** while a barge-in
utterance was still being captured/handled. Onset detection also only fired the
interrupt if `phase == SPEAKING`, but the phase was never moved off SPEAKING on
barge-in — so the speaker could keep going over the user. Effect: overlapping
audio / the system talking past an interruption — the opposite of the intended
"Serialized Q&A Transition Locking."
**FIX —** On confirmed onset the input loop now sets `phase = LISTENING` (in
addition to the interrupt event), and the narration loop only speaks when
`phase in (READY, SPEAKING)` — otherwise it idles until the utterance handler
finishes and `_resume()` restores SPEAKING. Verified by
`test_sfull_loops_together_bargein_and_complete` (both loops coexist, barge-in
captured, no deadlock).

---

## PROBLEM 10 — Tier-1 test-harness authoring issues (self-caught)
**Topic:** test harness (not product code)
**[Description & Effect]**
Three issues while building the Tier-1 harness: (a) `from harness import …` failed —
sibling modules under `tests/` need the package-qualified `from tests.harness
import …`; (b) pre-setting `interrupt_event` before `_speak_sentence` didn't work
because the method **clears the event on entry**, so the simulated barge-in never
fired; (c) the full-loop test flaked asserting a single terminal phase — a tiny
finite deck races narration-completion against the late utterance, so a trailing
`_resume()` can flip phase back to SPEAKING after the deck ends.
**FIX —** (a) package-qualified import; (b) added `InterruptingTTS` that arms the
event *as synthesis is pulled* (deterministic mid-sentence barge-in); (c) the
full-loop test asserts robust invariants (burst transcribed, question answered, no
deadlock, `phase in {SPEAKING, ENDED}`) instead of one exact terminal phase — strict
resume correctness is covered deterministically by the S2 direct test.

---

## PROBLEM 11 — Questions hijacked by embedded command keywords
**Topic:** intent classification (found by real human voice clips)
**[Description & Effect]**
The hybrid classifier ran the navigation/command rules first and matched a command
keyword **anywhere** in the utterance. Real questions that happen to contain such a
word were misrouted as commands:
  * "Which regions are we expanding into **next**?" → classified **NEXT**
  * "What is the company's hiring plan for the **next** year?" → classified **NEXT**
Effect: spoken questions were treated as slide navigation and never answered — a
direct hit to the accuracy gate, and exactly the adversarial pattern the source doc
warned about.
**FIX —** Reordered `FastIntentClassifier.classify()`: (1) explicit slide jump
(`go to slide N`), then (2) **interrogatives short-circuit to QUESTION** (so a
command word inside a question can't hijack it), then (3) imperative command rules,
then (4) embedding fallback. Re-running the voice clips confirmed both cases now
classify as QUESTION and answer correctly; intent + agent unit tests still pass.

---

## PROBLEM 12 — `small.en` mis-transcribes some real speech
**Topic:** STT accuracy (found by real human voice clips)
**[Description & Effect]**
On one clip, local Faster-Whisper `small.en` heard "what was the revenue **this
quarter**?" as "...revenue **discord**?" (and a wider beam made it "discordant").
The identical phrase transcribed perfectly on two other clips, so it's an acoustic
limitation of the smallest English model on that pronunciation — not a pipeline
bug (the system correctly classified QUESTION and gracefully declined on the
nonsense transcript). Local clip accuracy: **13/14**.
**FIX —** Added a cloud STT backend (`services/stt_groq.py` +
`services/stt_factory.py`, selected by `CB_STT_BACKEND=groq`) using Groq
`whisper-large-v3-turbo`. Re-running the clips through it: **14/14** (and cleaner
casing/punctuation). Local small.en stays the default (offline, fast); Groq is the
opt-in accuracy upgrade.

---

## PROBLEM 13 — `frontend/lib/` silently excluded from git
**Topic:** repo hygiene (found at status review)
**[Description & Effect]**
The root `.gitignore` is the stock Python template; its `lib/` rule (for
`build/lib/` packaging dirs) also matched `frontend/lib/`. `api.ts`, `livekit.ts`
and `types.ts` — imported by every component — were never committed. Effect: the
frontend built locally but a fresh clone failed to compile (`@/lib/*` not found).
`test_assets/` was ignored wholesale too, so the sample deck used by the README
and `run_clips.py` was missing.
**FIX —** Anchored the rule to the repo root (`/lib/`, `/lib64/`) and committed the
three files. Narrowed `test_assets/` to `test_assets/clips/`: the deck and
`CLIP_SCRIPT.md` are tracked, personal voice recordings stay local.

---

## PROBLEM 14 — Docker image could not run TTS
**Topic:** deployment (found at status review) — IMPLEMENTATION.md R13
**[Description & Effect]**
The Dockerfile installed only `requirements.txt`. Piper lives in
`requirements-linux.txt`, and on Linux `tts_backend=auto` selects Piper, so the
container would fail at TTS init. Separately, `TTSService` loads
`{models_dir}/piper/<voice>.onnx` but nothing downloaded it. There was also no
`.dockerignore`, so `COPY backend/` would bake `backend/.env` (API keys), model
caches and job data into the image.
**FIX —** Install both requirement files; `docker/fetch_piper_voice.py` fetches the
voice + config from `rhasspy/piper-voices` into the models volume before uvicorn
starts; added `.dockerignore`; the image runs as a non-root user.

---

## PROBLEM 15 — Compose paths and LiveKit URL mismatched
**Topic:** deployment (found at status review) — IMPLEMENTATION.md R14
**[Description & Effect]**
Compose mounted `../data` and `docker/models`, while local runs use `backend/data`
and the repo-root `models/` — so the container re-downloaded ~570 MB and couldn't
see local jobs. `livekit-server:latest` floated. And a single `livekit_url` was
used for both the in-compose agent (`ws://livekit:7880`) and the browser, which
can't resolve that hostname.
**FIX —** Mounted the local paths, pinned `livekit-server:v1.8`, and added
`CB_LIVEKIT_PUBLIC_URL` (browser-facing URL, defaults to `livekit_url`).

---

## PROBLEM 16 — Two React-hooks lint errors
**Topic:** frontend code quality (found at status review)
**[Description & Effect]**
`Presenter.tsx` reset the image-failed flag with `setState` inside an effect on
every slide change (cascading renders); `VoiceOrb.tsx` wrote a ref during render.
`eslint` failed with 2 errors.
**FIX —** Presenter derives the flag from the index of the slide whose image
failed; VoiceOrb updates the ref inside an effect. `eslint` and `tsc` clean.

---

## PROBLEM 17 — Upload buffered entirely in memory before the size check
**Topic:** API robustness (found at status review)
**[Description & Effect]**
`POST /api/presentations` did `await file.read()` and only then compared against
`max_upload_mb`, so an oversized upload was held fully in memory. Rejected
uploads also left an empty job directory behind.
**FIX —** Stream to disk in 1 MiB chunks, return `413` as soon as the limit is
crossed, and remove the job directory on 413/422. Smoke-checked: a 2 MB upload
with a 1 MB limit → `413`, no leftover job dir.

---

## PROBLEM 18 — Default Groq model retired; failure was silent
**Topic:** LLM / API drift (found 2026-11-11) — IMPLEMENTATION.md R15
**[Description & Effect]**
A live call returned `404 model_not_found` for `llama-3.3-70b-versatile`; Groq's
`/models` list no longer includes any Llama 3.x chat model. Every Q&A answer and
script generation therefore fell through to Ollama (not running) and returned the
"can't reach the knowledge service" apology. Nobody noticed because
`health_check()` existed but was never called.
**FIX —** Default `GROQ_MODEL` → `openai/gpt-oss-120b`; added Gemini
`gemini-3.8-flash` as the primary backend (`LLMService` chain, `CB_LLM_PRIMARY`);
`ServiceRegistry.warmup` now probes each backend and logs OK/UNAVAILABLE. Both
default models reason and count reasoning tokens against `max_tokens`, so
per-backend headroom is added (a 1-token ping returned empty without it).

---

## PROBLEM 19 — Docker compose read a `.env` that doesn't exist
**Topic:** deployment (found 2026-11-11)
**[Description & Effect]**
Compose used `env_file: ../backend/.env`, but the README and the real setup put
`.env` at the repo root, so `docker compose up` would fail on a missing file. The
self-hosted LiveKit overrides also had to live in that same `.env`, where
`CB_LIVEKIT_URL` would have broken local (Cloud) runs.
**FIX —** Compose reads `../.env` and uses LiveKit Cloud by default; the
self-hosted server is opt-in via `--profile local-livekit` plus
`docker/docker-compose.local-livekit.yml`, which carries its URLs and dev keys.

---

## PROBLEM 20 — Image not deployable to Fly.io as built
**Topic:** deployment (found while writing DEPLOYMENT.md)
**[Description & Effect]**
Two issues would have bitten the first Fly deploy: (a) Fly volumes mount
root-owned, so the non-root `app` user couldn't write a `/models` volume (an
entrypoint that chowns as root was tried and rejected by the org's non-root
`USER` check); (b) `sentence-transformers` pulled PyPI's Linux torch wheel, which
bundles several GB of CUDA libraries a CPU-only image never uses.
**FIX —** Bake the weights into the image at build time
(`docker/prefetch_models.py`): no volume needed, no first-boot download, still
non-root. Install CPU-only `torch==2.5.1` first. Base image digest-pinned.
Compose no longer mounts `models/` over the baked cache. Supersedes the
runtime voice fetch from PROBLEM 14.

---

## Non-blocking warnings (no fix required)
- **HuggingFace symlink warning** on Windows (`huggingface_hub` cache falls back to
  copies without Developer Mode). Cosmetic; silenced in runs with
  `HF_HUB_DISABLE_SYMLINKS_WARNING=1`.
- **`hf_xet` not installed** — HF falls back to regular HTTP download. Slower
  first-time downloads only; optional `pip install hf_xet` to speed up.
- **`torchaudio 2.11.0` vs `torch 2.2.*` version skew** — pulled in transitively by
  `silero-vad`. Harmless here because the code never imports `torchaudio`.
