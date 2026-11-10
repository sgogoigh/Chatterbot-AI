"""
tests/conftest.py — Pytest path setup.

Puts the ``backend/`` directory on sys.path so tests import modules the same way
the app does at runtime (``from core.pacing import ...``), since the Docker image
runs with WORKDIR=/app and code copied flat. Run ``pytest`` from ``backend/``.
"""

from __future__ import annotations

import os
import sys

# backend/ is the parent of tests/ — ensure it's importable as the package root.
BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

# Default the model cache to the repo-root models/ that the README and local runs
# use, so a bare ``pytest`` doesn't re-download ~570 MB into backend/models/.
# An explicit CB_MODELS_DIR still wins.
os.environ.setdefault(
    "CB_MODELS_DIR", os.path.join(os.path.dirname(BACKEND_ROOT), "models")
)
