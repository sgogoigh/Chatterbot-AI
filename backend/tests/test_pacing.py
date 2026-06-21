"""
tests/test_pacing.py — Unit tests for PacingService (IMPLEMENTATION.md §15, §21).

Validates the deterministic time-budget math: allocation sums to the usable
budget (target minus buffer), denser slides get more time, word budgets shrink by
track, and drift-driven track selection behaves. No ML dependencies.
"""

from __future__ import annotations

import pytest

from core.pacing import PERSONA_PROFILES, PacingService
from models import Persona, SlideContent, SlidePriority, Track


def _slides() -> list[SlideContent]:
    """Three slides of increasing text density for allocation tests."""
    return [
        SlideContent(index=0, title="Intro", body_text="short intro"),
        SlideContent(index=1, title="Body", body_text="a much longer slide " * 20),
        SlideContent(index=2, title="End", body_text="closing remarks here"),
    ]


def test_allocate_sums_to_usable_budget():
    """Allocated seconds sum to target*(1-buffer) for the persona."""
    slides = _slides()
    target = 600.0
    per_slide = PacingService.allocate_seconds(slides, target, Persona.GENERAL)
    usable = target * (1 - PERSONA_PROFILES[Persona.GENERAL].buffer_ratio)
    assert sum(per_slide.values()) == pytest.approx(usable, rel=1e-6)


def test_denser_slide_gets_more_time():
    """The high-density middle slide receives the largest share."""
    per_slide = PacingService.allocate_seconds(_slides(), 600.0, Persona.GENERAL)
    assert per_slide[1] > per_slide[0]
    assert per_slide[1] > per_slide[2]


def test_word_budgets_shrink_by_track():
    """SUMMARY/TURBO word budgets are progressively smaller than STANDARD."""
    per_slide = PacingService.allocate_seconds(_slides(), 600.0, Persona.GENERAL)
    std = PacingService.word_budgets(per_slide, Persona.GENERAL, Track.STANDARD)
    summ = PacingService.word_budgets(per_slide, Persona.GENERAL, Track.SUMMARY)
    turbo = PacingService.word_budgets(per_slide, Persona.GENERAL, Track.TURBO)
    assert std[1] > summ[1] > turbo[1]


def test_priorities_mark_first_and_last_as_anchor():
    """First and last slides are always ANCHOR (§15.3)."""
    densities = [PacingService.density(s) for s in _slides()]
    pri = PacingService.priorities(densities)
    assert pri[0] == SlidePriority.ANCHOR
    assert pri[2] == SlidePriority.ANCHOR


class _DriftSettings:
    """Minimal settings stub exposing only the drift thresholds select_track needs."""

    pacing_drift_summary_pct = 0.08
    pacing_drift_turbo_pct = 0.18


def test_select_track_escalates_and_relaxes():
    """Track escalates with positive drift and relaxes when well ahead."""
    s = _DriftSettings()
    assert PacingService.select_track(0.05, Track.STANDARD, s) == Track.STANDARD
    assert PacingService.select_track(0.10, Track.STANDARD, s) == Track.SUMMARY
    assert PacingService.select_track(0.25, Track.SUMMARY, s) == Track.TURBO
    assert PacingService.select_track(-0.20, Track.TURBO, s) == Track.STANDARD
