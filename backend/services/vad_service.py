"""
services/vad_service.py — Silero Voice Activity Detection (IMPLEMENTATION.md §10).

Detects speech onset on the inbound mic stream to drive barge-in. Per §10 the VAD
runs as a PURE onnxruntime model — no torch / torchaudio graph — which is what
yields the sub-millisecond inference and keeps the footprint tiny.

  * The Silero v5 ONNX model file is sourced from the ``silero-vad`` pip package's
    bundled data dir, located WITHOUT importing the package (its __init__ imports
    torchaudio, which we deliberately avoid). A models_dir copy / torch.hub cache
    are used as fallbacks.
  * Silero is STATEFUL (LSTM): the recurrent ``state`` tensor is carried across
    calls within a stream and reset on session start.
  * Caller must hand exactly ``vad_frame_samples`` (512 @ 16 kHz = 32 ms) per
    inference — the input loop uses utils.audio.Reframer to guarantee this.

⚠️ R6: "VAD latency < 5 ms" refers to *inference* time, not perceptual onset.
Onset is bounded by the 32 ms frame + the min-speech debounce in SpeechBuffer.
"""

from __future__ import annotations

import glob
import importlib.util
import os

import numpy as np

from config import Settings


def _find_silero_onnx(models_dir: str) -> str:
    """Locate a Silero VAD ``.onnx`` file without importing torch/torchaudio.

    Search order: (1) a copy under ``models_dir/silero``; (2) the installed
    ``silero-vad`` package's bundled ``data/`` dir (found via ``find_spec``, which
    does NOT execute the package body — so its torchaudio import never fires);
    (3) the torch.hub cache if a previous run populated it. Raises FileNotFoundError
    if none is found so startup fails loudly.
    """
    # (1) explicit models_dir copy
    local = glob.glob(os.path.join(models_dir, "silero", "*.onnx"))
    if local:
        return local[0]

    # (2) bundled with the silero-vad pip package (no import of the package body)
    spec = importlib.util.find_spec("silero_vad")
    if spec is not None and spec.origin:
        data_dir = os.path.join(os.path.dirname(spec.origin), "data")
        bundled = glob.glob(os.path.join(data_dir, "*.onnx"))
        if bundled:
            # Prefer a 16k model if multiple are shipped.
            bundled.sort(key=lambda p: ("16k" not in p, p))
            return bundled[0]

    # (3) torch.hub cache (populated if torch.hub.load ran previously)
    hub = glob.glob(os.path.expanduser("~/.cache/torch/hub/**/silero_vad*.onnx"), recursive=True)
    if hub:
        return hub[0]

    raise FileNotFoundError(
        "Silero VAD .onnx not found. Install `silero-vad` or place the model under "
        f"{models_dir}/silero/."
    )


class VADService:
    """Silero VAD wrapper (onnxruntime) exposing ``probability(frame) -> float``."""

    def __init__(self, settings: Settings):
        """Load the Silero ONNX model into an onnxruntime session and init state.

        Single-threaded session config keeps the per-call latency low and stable
        (the doc's benchmark measures exactly this). Adapts to the model's actual
        input names so it works with the v5 unified-``state`` signature (and v4's
        separate h/c if an older model is supplied).
        """
        import onnxruntime as ort

        self.sample_rate = settings.sample_rate
        self.frame_samples = settings.vad_frame_samples
        path = _find_silero_onnx(settings.models_dir)

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._sess = ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])
        self._input_names = {i.name for i in self._sess.get_inputs()}
        self._sr_arr = np.array(self.sample_rate, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        """Reset the LSTM recurrent state. Call at the start of each stream/session."""
        # v5 uses a single (2, batch, 128) state tensor.
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def probability(self, frame: np.ndarray) -> float:
        """Return the speech probability [0, 1] for one 512-sample float32 frame.

        One Silero forward pass (~sub-ms post-warmup). Threads the recurrent state
        through successive calls so temporal context is preserved. Frames must
        already be the configured size and sample rate (the Reframer guarantees the
        size upstream).
        """
        feeds = {"input": frame.astype(np.float32)[None, :], "sr": self._sr_arr}
        if "state" in self._input_names:
            feeds["state"] = self._state
        outputs = self._sess.run(None, feeds)
        prob = float(outputs[0].squeeze())
        if len(outputs) > 1:                 # carry forward the new recurrent state
            self._state = outputs[1]
        return prob

    def warmup(self, iters: int = 5) -> None:
        """Run a few dummy inferences so benchmark/runtime latency is post-warmup (§6, §10)."""
        for _ in range(iters):
            self.probability(np.zeros(self.frame_samples, dtype=np.float32))
        self.reset()
