"""
tests/test_e2e_latency.py — End-to-end latency scaffold (IMPLEMENTATION.md §21).

Measures the full pipeline (VAD → STT → Intent → LLM TTFT → TTS) and asserts the
summed mean is < 5 s (doc E2E target). Requires every heavy model + a reachable
LLM, so it is marked ``e2e`` and skipped unless explicitly run inside the
container on reference hardware.

Run:  pytest tests/test_e2e_latency.py -m e2e -s
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.skip(reason="E2E latency runs inside the container with all models + LLM (see §21)")
def test_end_to_end_under_5s():
    """Placeholder for the in-container E2E measurement.

    The real harness: feed a recorded WAV question through VAD+STT, classify,
    retrieve+answer via the LLM, synthesize the reply, and sum per-stage means,
    asserting < 5000 ms and printing the per-stage breakdown to compare with
    Table 5.8 in the source document.
    """
    ...
