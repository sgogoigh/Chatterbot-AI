"""
core/pacing.py — PacingService: time-locked budgeting (IMPLEMENTATION.md §15).

The deterministic engine behind time-locked delivery (doc TBA 99.91%). Pure
computation, no I/O — fully unit testable.

Model (§15.1, resolving PLAN §16.4):
  * PERSONA sets base speaking rate (WPM) and a time buffer ratio.
  * TRACK is a verbosity variant of the script (STANDARD / SUMMARY / TURBO);
    all tracks aim at the same total duration but use fewer words, giving the
    runtime drift handler room to recover time by switching tracks.

Allocation: distribute the usable time across slides in proportion to per-slide
CONTENT DENSITY, then convert each slide's seconds to a word budget via the
persona WPM. Word budgets drive the LLM script generation (§14.2).
"""

from __future__ import annotations

from dataclasses import dataclass

from models import Persona, SlideContent, SlidePriority, Track
from utils.text import word_count


@dataclass(frozen=True)
class PersonaProfile:
    """Speaking characteristics for a persona (WPM + reserved time buffer)."""

    wpm: float
    buffer_ratio: float


# Persona table (§15.1). Buffer reserves slack for natural pauses + Q&A overhead.
PERSONA_PROFILES: dict[Persona, PersonaProfile] = {
    Persona.GENERAL: PersonaProfile(wpm=150, buffer_ratio=0.10),
    Persona.TEACHER: PersonaProfile(wpm=130, buffer_ratio=0.15),
    Persona.MEETING: PersonaProfile(wpm=160, buffer_ratio=0.08),
    Persona.TEDX: PersonaProfile(wpm=140, buffer_ratio=0.12),
}

# Relative verbosity of each track (multiplier on the STANDARD word budget).
TRACK_WORD_RATIO: dict[Track, float] = {
    Track.STANDARD: 1.0,
    Track.SUMMARY: 0.7,
    Track.TURBO: 0.5,
}


class PacingService:
    """Static, deterministic time-budget calculations for a presentation."""

    @staticmethod
    def density(slide: SlideContent) -> float:
        """Compute a content-density score for one slide.

        Weighted sum of body words, notes words, OCR words, plus a visual-density
        factor (number of images). Denser slides earn proportionally more of the
        time budget. Floor of 1.0 ensures even an empty slide gets some time.
        """
        body = word_count(slide.body_text)
        notes = word_count(slide.notes)
        ocr = word_count(slide.ocr_text)
        visual = len(slide.image_paths) * 8.0  # each image ≈ a few seconds of explanation
        return max(1.0, body * 1.0 + notes * 0.6 + ocr * 0.4 + visual)

    @staticmethod
    def priorities(densities: list[float]) -> dict[int, SlidePriority]:
        """Assign ANCHOR / SUPPORTING / CONTEXTUAL priority per slide (§15.3).

        Top-tercile density (and always the first + last slide) → ANCHOR; middle
        tercile → SUPPORTING; bottom → CONTEXTUAL. The drift handler compresses
        CONTEXTUAL slides before touching ANCHOR ones, preserving the key content.
        """
        n = len(densities)
        if n == 0:
            return {}
        order = sorted(range(n), key=lambda i: densities[i], reverse=True)
        rank = {idx: pos for pos, idx in enumerate(order)}
        out: dict[int, SlidePriority] = {}
        for i in range(n):
            if i == 0 or i == n - 1 or rank[i] < n / 3:
                out[i] = SlidePriority.ANCHOR
            elif rank[i] < 2 * n / 3:
                out[i] = SlidePriority.SUPPORTING
            else:
                out[i] = SlidePriority.CONTEXTUAL
        return out

    @staticmethod
    def allocate_seconds(
        slides: list[SlideContent], target_seconds: float, persona: Persona
    ) -> dict[int, float]:
        """Distribute usable time across slides in proportion to density.

        ``usable = target_seconds * (1 - buffer_ratio)`` reserves slack; the
        remainder is split by normalised density weights. Returns
        slide_index -> seconds, summing to ``usable``.
        """
        profile = PERSONA_PROFILES[persona]
        usable = target_seconds * (1 - profile.buffer_ratio)
        dens = [PacingService.density(s) for s in slides]
        total = sum(dens) or 1.0
        return {s.index: usable * (dens[i] / total) for i, s in enumerate(slides)}

    @staticmethod
    def word_budgets(
        per_slide_seconds: dict[int, float], persona: Persona, track: Track
    ) -> dict[int, int]:
        """Convert each slide's second-budget into a word budget for a given track.

        ``words = seconds * WPM / 60`` (STANDARD), scaled by the track ratio so
        SUMMARY/TURBO scripts are shorter. These budgets are handed to the LLM
        during script generation (§14.2).
        """
        wpm = PERSONA_PROFILES[persona].wpm
        ratio = TRACK_WORD_RATIO[track]
        return {
            idx: max(10, int(round(secs * wpm / 60 * ratio)))
            for idx, secs in per_slide_seconds.items()
        }

    @staticmethod
    def select_track(drift_pct: float, current: Track, settings) -> Track:
        """Pick the track for upcoming slides given current schedule drift (§15.4).

        Positive drift = running over time → compress (SUMMARY, then TURBO).
        Sufficiently negative drift = ahead of schedule → relax back toward
        STANDARD. Thresholds come from settings. Only affects *upcoming* slides;
        the current sentence always finishes on its original track so the
        PlaybackTracker stays valid (§16.4).
        """
        if drift_pct > settings.pacing_drift_turbo_pct:
            return Track.TURBO
        if drift_pct > settings.pacing_drift_summary_pct:
            return Track.SUMMARY
        if drift_pct < -settings.pacing_drift_summary_pct and current != Track.STANDARD:
            return Track.STANDARD
        return current
