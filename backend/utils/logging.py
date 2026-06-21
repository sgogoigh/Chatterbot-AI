"""
utils/logging.py — Loguru configuration (IMPLEMENTATION.md §20).

A single place to configure structured logging. The agent worker binds
``session_id`` / ``turn_id`` context to every log line so the benchmark harness
(§21) can parse per-stage latencies, and so a live session's events can be
traced end-to-end.
"""

from __future__ import annotations

import sys

from loguru import logger

_CONFIGURED = False


def setup_logging(level: str = "INFO") -> None:
    """Initialise loguru once with a structured console sink.

    Idempotent: repeated calls (e.g. test imports + app startup) are no-ops after
    the first. The format includes any bound context (session/turn) when present.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        backtrace=False,
        diagnose=False,
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> | "
            "{extra} | <level>{message}</level>"
        ),
    )
    _CONFIGURED = True


def get_logger(**context):
    """Return a loguru logger pre-bound with ``context`` (e.g. ``session_id=...``).

    Convenience wrapper so call sites read ``log = get_logger(session_id=sid)``
    and every subsequent line carries that context.
    """
    return logger.bind(**context)
