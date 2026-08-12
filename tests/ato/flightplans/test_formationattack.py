from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest
from dcs import Point
from dcs.terrain import Caucasus, Terrain

from game.ato.flight import Flight
from game.ato.flightplans.formationattack import (
    FormationAttackBuilder,
    FormationAttackFlightPlan,
    FormationAttackLayout,
)
from game.ato.flighttype import FlightType
from game.ato.flightwaypoint import FlightWaypoint
from game.ato.flightwaypointtype import FlightWaypointType
from game.ato.flightplans.waypointbuilder import WaypointBuilder
from game.ato.package import Package
from game.utils import Speed, knots


class _StubPackage:
    def __init__(self, formation_speed: Speed | None) -> None:
        self._formation_speed = formation_speed

    def formation_speed(self, is_helo: bool) -> Speed | None:
        return self._formation_speed


class _StubFlight:
    def __init__(self, package: Package) -> None:
        self.package = package
        self.is_helo = False


class _FormationAttackUnderTest(FormationAttackFlightPlan):
    """Minimal concrete flight plan exercising ``speed_between_waypoints``.

    The real collaborators (package, flight, formation speed) are stubbed so the
    test focuses on how the target-area segment is priced.
    """

    def __init__(self, formation_speed: Speed | None, fallback_speed: Speed) -> None:
        package = cast(Package, _StubPackage(formation_speed))
        self.flight = cast(Flight, _StubFlight(package))
        self._fallback_speed = fallback_speed

    @property
    def best_flight_formation_speed(self) -> Speed:
        return self._fallback_speed


@pytest.fixture(name="target_waypoint")
def target_waypoint_fixture() -> FlightWaypoint:
    terrain: Terrain = Caucasus()
    return FlightWaypoint(
        "TARGET AREA",
        FlightWaypointType.TARGET_GROUP_LOC,
        Point(0, 0, terrain),
    )


def test_uses_package_formation_speed_at_target_when_available(
    target_waypoint: FlightWaypoint,
) -> None:
    formation_speed = knots(400)
    plan = _FormationAttackUnderTest(formation_speed, fallback_speed=knots(250))

    speed = plan.speed_between_waypoints(target_waypoint, target_waypoint)

    assert speed == formation_speed


def test_falls_back_to_flight_speed_when_package_has_no_formation_speed(
    target_waypoint: FlightWaypoint,
) -> None:
    fallback_speed = knots(250)
    plan = _FormationAttackUnderTest(
        formation_speed=None, fallback_speed=fallback_speed
    )

    speed = plan.speed_between_waypoints(target_waypoint, target_waypoint)

    assert speed == fallback_speed


# --- Task 8: FormationAttackBuilder._build distinguishes targets=None vs [] ---


class _FormationBuildBuilder(
    FormationAttackBuilder[FormationAttackFlightPlan, FormationAttackLayout]
):
    """Concrete builder that can be constructed without a real flight/coalition.

    Overrides the IBuilder properties and heavy helpers so ``_build`` can run
    with stub collaborators, isolating the target-waypoint decision logic.
    """

    _terrain: Terrain
    _stub_flight: object
    _stub_package: object

    def build(self, dump_debug_info: bool = False) -> FormationAttackFlightPlan:
        raise NotImplementedError

    @property
    def flight(self) -> Flight:
        return cast(Flight, self._stub_flight)

    @flight.setter
    def flight(self, value: object) -> None:
        self._stub_flight = value

    @property
    def package(self) -> Package:
        return cast(Package, self._stub_package)

    @package.setter
    def package(self, value: object) -> None:
        self._stub_package = value

    def _hold_point(self) -> Point:
        return Point(0, 0, self._terrain)

    def _build_refuel(self, builder: WaypointBuilder) -> FlightWaypoint | None:
        return None


def _build_layout(ingress_type: object, targets: object) -> FormationAttackLayout:
    """Drive ``FormationAttackBuilder._build`` with minimal stubbed collaborators.

    The WaypointBuilder is patched so no real coalition/game/flight wiring is
    required. We only care about how ``_build`` decides to construct target
    waypoints given ``targets=None`` vs ``targets=[]``.
    """
    terrain: Terrain = Caucasus()
    builder = _FormationBuildBuilder.__new__(_FormationBuildBuilder)
    builder._terrain = terrain
    target_area_wp = FlightWaypoint(
        "STRIKE AREA",
        FlightWaypointType.TARGET_GROUP_LOC,
        Point(0, 0, terrain),
    )
    target_point_wp = FlightWaypoint(
        "STRIKE TGT",
        FlightWaypointType.TARGET_POINT,
        Point(1, 1, terrain),
    )

    waypoint_builder = MagicMock()
    waypoint_builder.takeoff.return_value = target_area_wp
    waypoint_builder.hold.return_value = target_area_wp
    waypoint_builder.join.return_value = target_area_wp
    waypoint_builder.split.return_value = target_area_wp
    waypoint_builder.ingress.return_value = target_area_wp
    waypoint_builder.nav.return_value = target_area_wp
    waypoint_builder.refuel.return_value = None
    waypoint_builder.land.return_value = target_area_wp
    waypoint_builder.divert.return_value = None
    waypoint_builder.bullseye.return_value = target_area_wp
    waypoint_builder.nav_path.return_value = [target_area_wp]
    waypoint_builder.strike_area.return_value = target_area_wp
    waypoint_builder.strike_point.return_value = target_point_wp

    builder.flight = MagicMock(
        is_helo=False,
        flight_type=FlightType.BAI,
        departure=MagicMock(position=Point(0, 0, terrain)),
        arrival=MagicMock(position=Point(0, 0, terrain)),
        divert=None,
    )
    builder.package = MagicMock(
        waypoints=MagicMock(
            join=Point(0, 0, terrain),
            ingress=Point(0, 0, terrain),
            split=Point(0, 0, terrain),
            refuel=Point(0, 0, terrain),
        ),
        target=MagicMock(position=Point(0, 0, terrain)),
        primary_flight=MagicMock(is_helo=False),
    )

    import game.ato.flightplans.formationattack as fa_module

    orig_builder = fa_module.WaypointBuilder
    fa_module.WaypointBuilder = MagicMock(return_value=waypoint_builder)  # type: ignore[misc]
    try:
        layout = builder._build(cast(FlightWaypointType, ingress_type), targets)  # type: ignore[arg-type]
    finally:
        fa_module.WaypointBuilder = orig_builder  # type: ignore[misc]
    return layout


def test_formation_targets_none_uses_generic_area() -> None:
    layout = _build_layout(FlightWaypointType.INGRESS_STRIKE, None)

    # targets=None means generic target-area behavior requested: exactly one
    # target waypoint built via the area constructor (strike_area).
    assert len(layout.targets) == 1
    assert layout.targets[0].waypoint_type == FlightWaypointType.TARGET_GROUP_LOC


def test_formation_empty_targets_create_no_target_waypoint() -> None:
    layout = _build_layout(FlightWaypointType.INGRESS_STRIKE, [])

    # targets=[] means explicitly no targets: no target waypoints and no tasks.
    assert layout.targets == []
