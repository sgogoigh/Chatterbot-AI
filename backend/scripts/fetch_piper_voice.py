"""
scripts/fetch_piper_voice.py — download the configured Piper voice if missing.

Piper's TTSService loads ``{CB_MODELS_DIR}/piper/{CB_PIPER_VOICE}.onnx`` (plus its
``.onnx.json`` config) but nothing else downloads it. ``prefetch_models`` calls
this during ``docker build`` so the voice is baked into the image; it is a no-op
when the files already exist.

Voice names follow Piper's ``<lang>_<REGION>-<name>-<quality>`` convention, e.g.
``en_US-lessac-medium``, which maps to the rhasspy/piper-voices repo layout
``en/en_US/lessac/medium/``.
"""

from __future__ import annotations

import os
import sys
import urllib.request

BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"


def voice_url_dir(voice: str) -> str:
    """Map ``en_US-lessac-medium`` to ``en/en_US/lessac/medium``."""
    locale, name, quality = voice.split("-", 2)
    lang = locale.split("_", 1)[0]
    return f"{lang}/{locale}/{name}/{quality}"


def main() -> int:
    """Fetch the voice model + config into the models dir; no-op if present."""
    models_dir = os.environ.get("CB_MODELS_DIR", "/models")
    voice = os.environ.get("CB_PIPER_VOICE", "en_US-lessac-medium")
    out_dir = os.path.join(models_dir, "piper")
    os.makedirs(out_dir, exist_ok=True)

    for suffix in (".onnx", ".onnx.json"):
        dest = os.path.join(out_dir, voice + suffix)
        if os.path.exists(dest):
            continue
        url = f"{BASE}/{voice_url_dir(voice)}/{voice}{suffix}"
        print(f"fetching Piper voice {url}", flush=True)
        tmp = dest + ".part"
        try:
            urllib.request.urlretrieve(url, tmp)
            os.replace(tmp, dest)
        except Exception as exc:  # leave TTS to fail loudly at startup, not here
            print(f"could not fetch {url}: {exc}", file=sys.stderr, flush=True)
            if os.path.exists(tmp):
                os.remove(tmp)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
