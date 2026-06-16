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

## Non-blocking warnings (no fix required)
- **HuggingFace symlink warning** on Windows (`huggingface_hub` cache falls back to
  copies without Developer Mode). Cosmetic; silenced in runs with
  `HF_HUB_DISABLE_SYMLINKS_WARNING=1`.
- **`hf_xet` not installed** — HF falls back to regular HTTP download. Slower
  first-time downloads only; optional `pip install hf_xet` to speed up.
- **`torchaudio 2.11.0` vs `torch 2.2.*` version skew** — pulled in transitively by
  `silero-vad`. Harmless here because the code never imports `torchaudio`.
