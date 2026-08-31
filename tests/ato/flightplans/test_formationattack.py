from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from dcs import Point
from dcs.terrain import Caucasus, Terrain
from unittest.mock import MagicMock

from game.ato.flight import Flight
from game.ato.flightplans.armedrecon import ArmedReconFlightPlan
from game.ato.flightplans.bai import BaiFlightPlan
from game.ato.flightplans.formationattack import (
    FormationAttackFlightPlan,
    FormationAttackLayout,
)
from game.ato.flightplans.strike import StrikeFlightPlan
from game.ato.flighttype import FlightType
from game.data.groups import GroupTask
from game.theater import TheaterGroundObject
from game.theater.controlpoint import ControlPoint
from game.theater.presetlocation import PresetLocation
from game.theater.theatergroundobject import MotorpoolGroundObject
from game.utils import Heading
from game.ato.flightwaypoint import FlightWaypoint
from game.ato.flightwaypointtype import FlightWaypointType
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


def test_empty_motorpool_strike_uses_target_area_waypoint(monkeypatch: Any) -> None:
    terrain = Caucasus()
    control_point = cast(ControlPoint, SimpleNamespace())
    target = MotorpoolGroundObject(
        "Motorpool",
        PresetLocation("Garage", Point(0, 0, terrain), Heading.from_degrees(0)),
        control_point,
        GroupTask.MOTORPOOL,
    )
    package = cast(
        Package,
        SimpleNamespace(
            target=target,
            waypoints=SimpleNamespace(
                join=FlightWaypoint(
                    "JOIN", FlightWaypointType.JOIN, Point(0, 0, terrain)
                ),
                ingress=FlightWaypoint(
                    "INGRESS", FlightWaypointType.INGRESS_STRIKE, Point(0, 0, terrain)
                ),
                split=Point(0, 0, terrain),
            ),
        ),
    )
    flight = cast(
        Flight,
        SimpleNamespace(
            package=package,
            flight_type=FlightType.STRIKE,
            is_helo=False,
            departure=SimpleNamespace(position=Point(0, 0, terrain)),
            arrival=SimpleNamespace(position=Point(0, 0, terrain)),
            divert=None,
        ),
    )
    builder = cast(Any, object.__new__(StrikeFlightPlan.builder_type()))
    builder.flight = flight
    builder._hold_point = lambda: Point(0, 0, terrain)
    builder._get_split = lambda: Point(0, 0, terrain)
    builder._build_refuel = lambda _builder: None

    class FakeWaypointBuilder:
        get_combat_altitude = cast(Any, None)

        def __init__(self, _flight: Flight, _targets: object) -> None:
            pass

        def __getattr__(self, _name: str) -> Any:
            return lambda *_args: FlightWaypoint(
                "TARGET AREA", FlightWaypointType.TARGET_GROUP_LOC, target.position
            )

    monkeypatch.setattr(
        "game.ato.flightplans.formationattack.WaypointBuilder", FakeWaypointBuilder
    )

    layout = builder.layout()
    plan = StrikeFlightPlan.__new__(StrikeFlightPlan)
    plan.flight = flight
    plan.layout = layout

    assert len(layout.targets) == 1
    assert plan.tot_waypoint is layout.targets[0]


def _motorpool_with_groups(
    terrain: Terrain, groups: list[Any]
) -> MotorpoolGroundObject:
    target = MotorpoolGroundObject(
        "Motorpool",
        PresetLocation("Garage", Point(0, 0, terrain), Heading.from_degrees(0)),
        cast(ControlPoint, SimpleNamespace()),
        GroupTask.MOTORPOOL,
    )
    target.groups = cast(Any, groups)
    return target


def _attack_flight(target: Any, flight_type: FlightType) -> Flight:
    terrain = Caucasus()
    ingress_type = {
        FlightType.STRIKE: FlightWaypointType.INGRESS_STRIKE,
        FlightType.BAI: FlightWaypointType.INGRESS_BAI,
        FlightType.ARMED_RECON: FlightWaypointType.INGRESS_ARMED_RECON,
    }[flight_type]
    package = cast(
        Package,
        SimpleNamespace(
            target=target,
            waypoints=SimpleNamespace(
                join=FlightWaypoint(
                    "JOIN", FlightWaypointType.JOIN, Point(0, 0, terrain)
                ),
                ingress=FlightWaypoint("INGRESS", ingress_type, Point(0, 0, terrain)),
                split=Point(0, 0, terrain),
            ),
        ),
    )
    return cast(
        Flight,
        SimpleNamespace(
            package=package,
            flight_type=flight_type,
            is_helo=False,
            departure=SimpleNamespace(position=Point(0, 0, terrain)),
            arrival=SimpleNamespace(position=Point(0, 0, terrain)),
            divert=None,
        ),
    )


def _layout_with_fake_builder(
    monkeypatch: Any,
    flight: Flight,
    builder_type: type[Any],
    waypoint_builder: type[Any],
) -> FormationAttackLayout:
    builder = cast(Any, object.__new__(builder_type))
    builder.flight = flight
    builder._hold_point = lambda: flight.package.target.position
    builder._get_split = lambda: flight.package.target.position
    builder._build_refuel = lambda _builder: None
    monkeypatch.setattr(
        "game.ato.flightplans.formationattack.WaypointBuilder", waypoint_builder
    )
    return cast(FormationAttackLayout, builder.layout())


def test_motorpool_bai_with_live_groups_uses_single_area_waypoint(
    monkeypatch: Any,
) -> None:
    """Spec: motorpool BAI gets ONE target waypoint for the motorpool total —
    the same target-area waypoint naming targetless BAI flights use — not one
    waypoint per unit-type group."""
    terrain = Caucasus()
    group = cast(Any, SimpleNamespace(units=[object()], group_name="Armor"))
    target = _motorpool_with_groups(terrain, [group])
    flight = _attack_flight(target, FlightType.BAI)

    class FakeWaypointBuilder:
        get_combat_altitude = cast(Any, None)

        def __init__(self, _flight: Flight, _targets: object) -> None:
            pass

        def bai_group(self, target: object) -> FlightWaypoint:
            return FlightWaypoint(
                "BAI GROUP", FlightWaypointType.TARGET_POINT, Point(1, 1, terrain)
            )

        def strike_area(self, _target: object) -> FlightWaypoint:
            return FlightWaypoint(
                "STRIKE AREA", FlightWaypointType.TARGET_GROUP_LOC, Point(2, 2, terrain)
            )

        def __getattr__(self, _name: str) -> Any:
            return lambda *_args: FlightWaypoint(
                "OTHER", FlightWaypointType.TARGET_GROUP_LOC, target.position
            )

    layout = _layout_with_fake_builder(
        monkeypatch, flight, BaiFlightPlan.builder_type(), FakeWaypointBuilder
    )

    assert len(layout.targets) == 1
    assert layout.targets[0].name == "STRIKE AREA"


def test_non_motorpool_bai_keeps_per_target_waypoints(monkeypatch: Any) -> None:
    """Spec: only motorpool BAI collapses to a single area waypoint; BAI
    against an ordinary TGO keeps one waypoint per target group."""
    terrain = Caucasus()
    target = MagicMock(spec=TheaterGroundObject)
    target.name = "Enemy Armor"
    target.position = Point(0, 0, terrain)
    target.groups = [SimpleNamespace(units=[object()], group_name="Armor")]
    flight = _attack_flight(target, FlightType.BAI)

    class FakeWaypointBuilder:
        get_combat_altitude = cast(Any, None)

        def __init__(self, _flight: Flight, _targets: object) -> None:
            pass

        def bai_group(self, target: object) -> FlightWaypoint:
            return FlightWaypoint(
                "BAI GROUP", FlightWaypointType.TARGET_POINT, Point(1, 1, terrain)
            )

        def strike_area(self, _target: object) -> FlightWaypoint:
            return FlightWaypoint(
                "STRIKE AREA", FlightWaypointType.TARGET_GROUP_LOC, Point(2, 2, terrain)
            )

        def __getattr__(self, _name: str) -> Any:
            return lambda *_args: FlightWaypoint(
                "OTHER", FlightWaypointType.TARGET_GROUP_LOC, target.position
            )

    layout = _layout_with_fake_builder(
        monkeypatch, flight, BaiFlightPlan.builder_type(), FakeWaypointBuilder
    )

    assert len(layout.targets) == 1
    assert layout.targets[0].name == "BAI GROUP"


def test_motorpool_strike_with_live_units_keeps_per_target_waypoints(
    monkeypatch: Any,
) -> None:
    """Spec: motorpool STRIKE keeps one player-facing target waypoint per
    parked unit (player-only via only_for_player, like any strike flight)."""
    terrain = Caucasus()
    units = [
        SimpleNamespace(
            alive=True,
            type=SimpleNamespace(id=f"Unit {i}"),
            position=Point(i, i, terrain),
        )
        for i in range(3)
    ]
    group = cast(Any, SimpleNamespace(units=units, group_name="Armor"))
    target = _motorpool_with_groups(terrain, [group])
    flight = _attack_flight(target, FlightType.STRIKE)

    class FakeWaypointBuilder:
        get_combat_altitude = cast(Any, None)

        def __init__(self, _flight: Flight, _targets: object) -> None:
            pass

        def strike_point(self, target: object) -> FlightWaypoint:
            return FlightWaypoint(
                f"STRIKE POINT {cast(Any, target).name}",
                FlightWaypointType.TARGET_POINT,
                Point(0, 0, terrain),
            )

        def __getattr__(self, _name: str) -> Any:
            return lambda *_args: FlightWaypoint(
                "OTHER", FlightWaypointType.TARGET_GROUP_LOC, target.position
            )

    layout = _layout_with_fake_builder(
        monkeypatch, flight, StrikeFlightPlan.builder_type(), FakeWaypointBuilder
    )

    assert [waypoint.name for waypoint in layout.targets] == [
        # StrikeFlightPlan names each target "f'{unit.type.id} #{idx}'".
        "STRIKE POINT Unit 0 #0",
        "STRIKE POINT Unit 1 #1",
        "STRIKE POINT Unit 2 #2",
    ]


def test_motorpool_armed_recon_uses_single_area_waypoint(monkeypatch: Any) -> None:
    """Spec: motorpool armed recon gets ONE target waypoint for the motorpool
    total, named the way every other armed-recon flight names its target
    waypoint (armed_recon_area)."""
    terrain = Caucasus()
    group = cast(Any, SimpleNamespace(units=[object()], group_name="Armor"))
    target = _motorpool_with_groups(terrain, [group])
    flight = _attack_flight(target, FlightType.ARMED_RECON)

    class FakeWaypointBuilder:
        get_combat_altitude = cast(Any, None)

        def __init__(self, _flight: Flight, _targets: object) -> None:
            pass

        def armed_recon_area(self, _target: object) -> FlightWaypoint:
            return FlightWaypoint(
                "ARMED RECON AREA",
                FlightWaypointType.TARGET_GROUP_LOC,
                Point(3, 3, terrain),
            )

        def __getattr__(self, _name: str) -> Any:
            return lambda *_args: FlightWaypoint(
                "OTHER", FlightWaypointType.TARGET_GROUP_LOC, target.position
            )

    layout = _layout_with_fake_builder(
        monkeypatch, flight, ArmedReconFlightPlan.builder_type(), FakeWaypointBuilder
    )

    assert len(layout.targets) == 1
    assert layout.targets[0].name == "ARMED RECON AREA"
