from __future__ import annotations

from datetime import datetime

from dcs.point import MovingPoint
from dcs.task import OrbitAction

from game.utils import meters
from .pydcswaypointbuilder import PydcsWaypointBuilder


def _loiter_stop_seconds(tot: datetime | None, now: datetime, window: int) -> int:
    """Mission-elapsed seconds at which the loiter orbit is force-stopped.

    `window` is the on-station time allowed after the flight reaches the anchor (its
    TOT); with no known TOT we fall back to the bare window. Floored at 1 s so a 0 s
    window -- or a TOT that has already elapsed at generation time -- can never produce
    a stop time of 0, which DCS treats as "fire immediately" and would kill the orbit at
    mission start.
    """
    if tot is None:
        return max(1, window)
    return max(1, int((tot - now).total_seconds()) + window)


class SeadLoiterBuilder(PydcsWaypointBuilder):
    """Plain-SEAD standoff loiter: the flight holds here and engages radars
    reactively (via the SEAD main task) until it RTBs on winchester. The orbit is
    bounded by a backstop window so it never loiters forever."""

    def add_tasks(self, waypoint: MovingPoint) -> None:
        # Base add_tasks only injects an AI-despawn RunScript at the *arrival*
        # waypoint; this anchor is not the arrival, so it is a no-op here.
        super().add_tasks(waypoint)
        window = self.flight.coalition.game.settings.sead_loiter_max_window_seconds
        tot = self.flight.flight_plan.tot_for_waypoint(self.waypoint)
        stop = _loiter_stop_seconds(tot, self.now, window)
        speed = self.flight.squadron.aircraft.preferred_patrol_speed(
            meters(waypoint.alt)
        )
        self.add_stopping_orbit(
            waypoint,
            speed_kph=speed.kph,
            pattern=OrbitAction.OrbitPattern.Circle,
            stop_time=stop,
        )
