"""Shared fixed-speed selection policy for tanker orbit speed.

Both the ``REFUELING`` and ``RECOVERY`` flight-generation paths need to pick one
fixed orbit speed for a tanker. This module owns that decision in one place so
neither path has to duplicate it. It intentionally knows nothing about flight
plans, waypoints, or mission generation: it is a pure function over per-flight
properties (``Flight.props``), candidate receiver speeds, and a caller-supplied
baseline.

No runtime refueling event/controller behavior lives here — this is
planning-time speed selection only.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

_SPEED_BOUNDARY_EPSILON = 1e-6

from game.utils import Speed, knots

#: ``Flight.props`` key selecting how the tanker orbit speed is determined.
#: Missing or any value other than ``"manual"`` means Auto.
TANKER_ORBIT_SPEED_MODE_PROP = "tanker_orbit_speed_mode"

#: ``Flight.props`` key for the fixed manual orbit speed, in KIAS. Only
#: consulted when ``TANKER_ORBIT_SPEED_MODE_PROP`` is ``"manual"``.
TANKER_ORBIT_SPEED_KIAS_PROP = "tanker_orbit_speed_kias"

#: Inclusive valid range, in KIAS, for both the manual override and any known
#: receiver AAR speed. Values outside this range (or of the wrong type) are
#: treated as absent/invalid and ignored.
MIN_TANKER_ORBIT_SPEED_KIAS = 220
MAX_TANKER_ORBIT_SPEED_KIAS = 350


def _valid_speed(speed: Optional[Speed]) -> Optional[Speed]:
    """Return `speed` unchanged if it is known and within the safe envelope."""
    if speed is None:
        return None
    if (
        MIN_TANKER_ORBIT_SPEED_KIAS - _SPEED_BOUNDARY_EPSILON
        <= speed.knots
        <= MAX_TANKER_ORBIT_SPEED_KIAS + _SPEED_BOUNDARY_EPSILON
    ):
        return speed
    return None


def select_tanker_orbit_speed(
    props: Mapping[str, Any],
    receiver_speeds: Sequence[Optional[Speed]],
    baseline: Speed,
) -> Speed:
    """Select the fixed orbit speed to use for a tanker's racetrack.

    Precedence:

    1. Manual mode (``props[TANKER_ORBIT_SPEED_MODE_PROP] == "manual"``) with a
       valid ``props[TANKER_ORBIT_SPEED_KIAS_PROP]`` (250-350 KIAS inclusive)
       always wins, even when receiver metadata is present.
    2. Invalid or missing manual speed falls back directly to `baseline` so
       malformed manual properties cannot select a receiver-specific speed.
    3. Otherwise (mode missing or unknown): Auto. The slowest valid, known
       receiver speed among `receiver_speeds` is used.
    4. If no receiver has valid speed metadata, `baseline` (the tanker path's
       existing default speed) is returned unchanged.

    Absent or malformed properties/metadata are ignored safely; this function
    never raises for bad input.
    """
    if props.get(TANKER_ORBIT_SPEED_MODE_PROP) == "manual":
        manual = props.get(TANKER_ORBIT_SPEED_KIAS_PROP)
        if isinstance(manual, (int, float)) and not isinstance(manual, bool):
            if (
                MIN_TANKER_ORBIT_SPEED_KIAS - _SPEED_BOUNDARY_EPSILON
                <= manual
                <= MAX_TANKER_ORBIT_SPEED_KIAS + _SPEED_BOUNDARY_EPSILON
            ):
                return knots(float(manual))
        return baseline

    valid_receivers = [
        speed
        for speed in (_valid_speed(candidate) for candidate in receiver_speeds)
        if speed is not None
    ]
    return (
        min(valid_receivers, key=lambda speed: speed.knots)
        if valid_receivers
        else baseline
    )
