# tests/test_extent_fit.py
"""Tests for the front-line fit-extent half-side helper."""

from __future__ import annotations

from dataclasses import dataclass

from game.missiongenerator.kneeboard_recon.extent import frontline_fit_half_side


@dataclass(frozen=True)
class _P:
    x: float
    y: float


def test_fit_none_endpoints_returns_base() -> None:
    base = 18_520.0
    assert (
        frontline_fit_half_side(
            _P(0.0, 0.0),
            None,
            None,
            base_half_m=base,
            outer_corridor_m=18_000.0,
            margin_m=1_500.0,
        )
        == base
    )


def test_fit_wide_front_zooms_out_to_segment_plus_outer_plus_margin() -> None:
    half = frontline_fit_half_side(
        _P(0.0, 0.0),
        _P(0.0, -30_000.0),
        _P(0.0, 30_000.0),
        base_half_m=18_520.0,
        outer_corridor_m=18_000.0,
        margin_m=1_500.0,
    )
    assert half == 30_000.0 + 18_000.0 + 1_500.0  # 49_500


def test_fit_tiny_front_floors_at_base() -> None:
    half = frontline_fit_half_side(
        _P(0.0, 0.0),
        _P(0.0, -100.0),
        _P(0.0, 100.0),
        base_half_m=18_520.0,
        outer_corridor_m=0.0,
        margin_m=0.0,
    )
    assert half == 18_520.0  # floored at base; never zooms IN past it
