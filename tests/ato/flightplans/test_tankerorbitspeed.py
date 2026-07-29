"""Tests for the shared tanker orbit-speed selection policy.

This policy is deliberately independent of any concrete flight plan: it takes
the raw per-flight ``props`` mapping, the candidate receiver AAR speeds, and
the tanker path's existing baseline speed, and returns one fixed KIAS value.
Both ``REFUELING`` and ``RECOVERY`` generation paths are expected to call this
same function (in a later task) rather than duplicating the policy.
"""

from __future__ import annotations

from typing import Any, Mapping

from game.ato.flightplans.tankerorbitspeed import (
    TANKER_ORBIT_SPEED_KIAS_PROP,
    TANKER_ORBIT_SPEED_MODE_PROP,
    select_tanker_orbit_speed,
)
from game.utils import Speed, knots

BASELINE = knots(280)


def _select(props: Mapping[str, Any], receiver_speeds: list[Speed | None]) -> Speed:
    return select_tanker_orbit_speed(props, receiver_speeds, BASELINE)


def test_auto_mode_is_default_when_mode_missing() -> None:
    result = _select({}, [knots(300)])
    assert result == knots(300)


def test_auto_mode_chooses_slowest_valid_receiver() -> None:
    result = _select(
        {},
        [knots(320), knots(260), knots(300)],
    )
    assert result == knots(260)


def test_auto_mode_falls_back_to_baseline_when_no_receivers() -> None:
    result = _select({}, [])
    assert result == BASELINE


def test_auto_mode_accepts_receiver_speeds_at_the_expanded_lower_bound() -> None:
    result = _select({}, [knots(220), knots(310)])
    assert result == knots(220)


def test_auto_mode_ignores_receiver_speeds_below_the_expanded_lower_bound() -> None:
    result = _select({}, [knots(219), knots(310)])
    assert result == knots(310)


def test_auto_mode_ignores_receiver_speeds_above_the_upper_bound() -> None:
    result = _select({}, [knots(400), knots(310)])
    assert result == knots(310)


def test_auto_mode_ignores_none_receiver_entries() -> None:
    result = _select({}, [None, knots(290)])
    assert result == knots(290)


def test_auto_mode_falls_back_to_baseline_when_all_receivers_invalid() -> None:
    result = _select({}, [None, knots(1000)])
    assert result == BASELINE


def test_manual_mode_wins_over_receiver_metadata() -> None:
    props = {
        TANKER_ORBIT_SPEED_MODE_PROP: "manual",
        TANKER_ORBIT_SPEED_KIAS_PROP: 320,
    }
    result = _select(props, [knots(260)])
    assert result == knots(320)


def test_manual_mode_accepts_range_boundaries() -> None:
    props_low = {
        TANKER_ORBIT_SPEED_MODE_PROP: "manual",
        TANKER_ORBIT_SPEED_KIAS_PROP: 220,
    }
    props_high = {
        TANKER_ORBIT_SPEED_MODE_PROP: "manual",
        TANKER_ORBIT_SPEED_KIAS_PROP: 350,
    }
    assert _select(props_low, []) == knots(220)
    assert _select(props_high, []) == knots(350)


def test_manual_mode_with_invalid_kias_falls_back_to_baseline() -> None:
    props_out_of_range = {
        TANKER_ORBIT_SPEED_MODE_PROP: "manual",
        TANKER_ORBIT_SPEED_KIAS_PROP: 900,
    }
    result = _select(props_out_of_range, [knots(300)])
    assert result == BASELINE


def test_manual_mode_with_missing_kias_falls_back_to_baseline() -> None:
    props = {TANKER_ORBIT_SPEED_MODE_PROP: "manual"}
    result = _select(props, [])
    assert result == BASELINE


def test_manual_mode_ignores_non_numeric_kias() -> None:
    props = {
        TANKER_ORBIT_SPEED_MODE_PROP: "manual",
        TANKER_ORBIT_SPEED_KIAS_PROP: "fast",
    }
    result = _select(props, [knots(300)])
    assert result == BASELINE


def test_unknown_mode_value_is_treated_as_auto() -> None:
    props = {TANKER_ORBIT_SPEED_MODE_PROP: "bogus-mode"}
    result = _select(props, [knots(300)])
    assert result == knots(300)
