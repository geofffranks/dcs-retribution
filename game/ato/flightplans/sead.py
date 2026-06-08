from __future__ import annotations

from datetime import timedelta
from typing import Type

from .formationattack import (
    FormationAttackBuilder,
    FormationAttackFlightPlan,
    FormationAttackLayout,
)
from .uizonedisplay import UiZone, UiZoneDisplay
from ..flightwaypointtype import FlightWaypointType
from ...utils import nautical_miles

# Plain SEAD reactively fires HARMs from its loiter anchor. The planner overlay shows a
# fixed HARM-reach bubble rather than the user-tunable SEAD-sweep engagement range, so
# the orbit is always drawn against a realistic engagement envelope.
SEAD_ENGAGEMENT_RANGE = nautical_miles(20)


class SeadFlightPlan(FormationAttackFlightPlan, UiZoneDisplay):
    @staticmethod
    def builder_type() -> Type[Builder]:
        return Builder

    def default_tot_offset(self) -> timedelta:
        return -timedelta(minutes=1)

    def ui_zone(self) -> UiZone:
        # Centre the HARM-reach bubble on the loiter anchor (where the flight orbits and
        # engages), falling back to the target if no anchor was planned.
        anchor = self.layout.initial or self.tot_waypoint
        return UiZone([anchor.position], SEAD_ENGAGEMENT_RANGE)


class Builder(FormationAttackBuilder[SeadFlightPlan, FormationAttackLayout]):
    def layout(self) -> FormationAttackLayout:
        return self._build(FlightWaypointType.INGRESS_SEAD)

    def build(self, dump_debug_info: bool = False) -> SeadFlightPlan:
        return SeadFlightPlan(self.flight, self.layout())
