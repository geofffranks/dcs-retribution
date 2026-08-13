from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import List, Tuple, Type

from dcs import Point
from dcs.planes import F_14B, F_14BU
from dcs.terrain import Caucasus
from dcs.unittype import FlyingType

from game.ato.flightplans.waypointbuilder import WaypointBuilder
from game.ato.flightwaypoint import FlightWaypoint
from game.ato.flightwaypointtype import FlightWaypointType
from game.missiongenerator.aircraft.waypoints.pydcswaypointbuilder import (
    PydcsWaypointBuilder,
)
from game.missiongenerator.aircraft.waypoints.target import TargetBuilder


def _waypoint(custom_name: str | None) -> FlightWaypoint:
    wp = FlightWaypoint(
        "[OBJ] : Scud #0", FlightWaypointType.TARGET_POINT, Point(0, 0, Caucasus())
    )
    wp.custom_name = custom_name
    return wp


def _base_builder(custom_name: str | None) -> PydcsWaypointBuilder:
    builder = PydcsWaypointBuilder.__new__(PydcsWaypointBuilder)
    builder.waypoint = _waypoint(custom_name)
    return builder


def _target_builder(custom_name: str | None, f15e: bool) -> TargetBuilder:
    builder = TargetBuilder.__new__(TargetBuilder)
    builder.waypoint = _waypoint(custom_name)
    builder.flight = SimpleNamespace(  # type: ignore[assignment]
        unit_type=SimpleNamespace(use_f15e_waypoint_names=f15e)
    )
    return builder


def test_dcs_name_uses_custom_name_when_set() -> None:
    assert _base_builder("SCUD1").dcs_name_for_waypoint() == "SCUD1"


def test_dcs_name_falls_back_to_name_when_unset() -> None:
    assert _base_builder(None).dcs_name_for_waypoint() == "[OBJ] : Scud #0"


def test_dcs_name_ignores_override_for_structural_join_waypoint() -> None:
    # JOIN's generated name is matched downstream (formation/EW jamming); a rename must not
    # reach the .miz or that logic breaks, so the canonical name wins.
    builder = _base_builder("MyJoin")
    builder.waypoint.name = "JOIN"
    assert builder.dcs_name_for_waypoint() == "JOIN"


def test_dcs_name_ignores_override_for_dropoffzone_waypoint() -> None:
    # DROPOFFZONE drives the CTLD air-assault split trigger (landingzone.py).
    builder = _base_builder("LZ Alpha")
    builder.waypoint.name = "DROPOFFZONE"
    assert builder.dcs_name_for_waypoint() == "DROPOFFZONE"


def test_target_builder_non_f15e_uses_custom_name() -> None:
    assert _target_builder("SCUD1", f15e=False).dcs_name_for_waypoint() == "SCUD1"


def test_target_builder_f15e_wraps_custom_name() -> None:
    assert _target_builder("SCUD1", f15e=True).dcs_name_for_waypoint() == "#T SCUD1"


def test_target_builder_f15e_falls_back_to_name() -> None:
    assert (
        _target_builder(None, f15e=True).dcs_name_for_waypoint() == "#T [OBJ] : Scud #0"
    )


def test_divert_and_bullseye_waypoints_are_player_only() -> None:
    # PR #820 propagates a player's waypoint rename to the .miz CDU name. AI-only flights
    # must never receive that rename. The safety is structural, not a guard in
    # dcs_name_for_waypoint: a rename is written only to the player's own flight-plan
    # waypoints (FlightWaypoint.apply_name_edit), and the waypoint types a rename most
    # often targets are constructed only_for_player=True so WaypointGenerator.create
    # waypoints drops them for AI flights (client_count == 0). Lock that flag here -- if
    # it is ever removed, AI flights would silently start emitting these waypoints and the
    # "AI unaffected" guarantee would break. (Target waypoints set the same flag in
    # waypointbuilder.py, near the strike-area / target builders.)
    builder = WaypointBuilder.__new__(WaypointBuilder)
    builder._bullseye = SimpleNamespace(position=Point(0, 0, Caucasus()))  # type: ignore[assignment]
    divert_point = SimpleNamespace(position=Point(1, 1, Caucasus()))

    assert builder.bullseye().only_for_player is True
    divert = builder.divert(divert_point)  # type: ignore[arg-type]
    assert divert is not None
    assert divert.only_for_player is True


T0 = datetime(2020, 1, 1, 12, 0, 0)


class _FakeMovingPoint:
    """Minimal stand-in for a pydcs MovingPoint -- only the ETA fields are exercised."""

    def __init__(self) -> None:
        self.ETA: int = 0
        self.ETA_locked: bool = False
        self.speed_locked: bool = False


def _make_builder(
    client_count: int,
    locked_tot: datetime | None,
    display_tot: datetime | None,
) -> PydcsWaypointBuilder:
    waypoint = FlightWaypoint("wp", FlightWaypointType.NAV, Point(0, 0, Caucasus()))
    flight = SimpleNamespace(
        client_count=client_count,
        unit_type=SimpleNamespace(dcs_unit_type=object()),  # not an AJS37 Viggen
        flight_plan=SimpleNamespace(
            effective_tot_for_waypoint=lambda wp: locked_tot,
            chained_tot_for_waypoint=lambda wp: display_tot,
        ),
    )
    builder = PydcsWaypointBuilder.__new__(PydcsWaypointBuilder)
    builder.waypoint = waypoint
    builder.flight = flight  # type: ignore[assignment]
    builder.now = T0
    return builder


def test_player_auto_waypoint_locks_eta() -> None:
    display = T0 + timedelta(minutes=10)
    builder = _make_builder(client_count=1, locked_tot=None, display_tot=display)
    point = _FakeMovingPoint()
    builder._assign_waypoint_tot(point)  # type: ignore[arg-type]
    # Player flights feed waypoint ETAs into the jet's nav computer, so the chained ToT
    # must be locked into the DCS ETA -- otherwise the cockpit time-on-waypoint is wrong.
    assert point.ETA_locked is True
    assert point.ETA == int(timedelta(minutes=10).total_seconds())
    assert builder.waypoint.tot == display


def test_ai_auto_waypoint_leaves_eta_unlocked() -> None:
    display = T0 + timedelta(minutes=10)
    builder = _make_builder(client_count=0, locked_tot=None, display_tot=display)
    point = _FakeMovingPoint()
    builder._assign_waypoint_tot(point)  # type: ignore[arg-type]
    # AI flights keep the DCS ETA flexible (no over-constraint); only the kneeboard model
    # field is populated.
    assert point.ETA_locked is False
    assert builder.waypoint.tot == display


def test_anchored_waypoint_locks_for_ai_and_player() -> None:
    anchor = T0 + timedelta(minutes=5)
    builder = _make_builder(client_count=0, locked_tot=anchor, display_tot=None)
    point = _FakeMovingPoint()
    builder._assign_waypoint_tot(point)  # type: ignore[arg-type]
    # Structural / manual ToTs are always locked, regardless of player presence.
    assert point.ETA_locked is True
    assert point.ETA == int(timedelta(minutes=5).total_seconds())


# ---- register_special_strike_points / register_special_ingress_points ----


def _make_strike_builder(
    unit_type: Type[FlyingType],
    client_count: int = 0,
) -> Tuple[PydcsWaypointBuilder, List[Tuple[Point, str]]]:
    """Minimal builder whose fake group records add_nav_target_point calls."""
    calls: List[Tuple[Point, str]] = []
    group = SimpleNamespace(
        units=[SimpleNamespace(unit_type=unit_type)],
        add_nav_target_point=lambda pos, comment: calls.append((pos, comment)),
    )
    builder = PydcsWaypointBuilder.__new__(PydcsWaypointBuilder)
    builder.group = group  # type: ignore[assignment]
    builder.flight = SimpleNamespace(client_count=client_count)  # type: ignore[assignment]
    builder.waypoint = SimpleNamespace(position=Point(42, 42, Caucasus()))  # type: ignore[assignment]
    return builder, calls


def _fake_targets(n: int) -> List[SimpleNamespace]:
    return [
        SimpleNamespace(position=Point(i * 1000, i * 1000, Caucasus()))
        for i in range(n)
    ]


def test_f14bu_registers_up_to_eight_st_points() -> None:
    """F-14BU gets all targets (up to 8) as ST NavTargetPoints."""
    builder, calls = _make_strike_builder(F_14BU)
    builder.register_special_strike_points(_fake_targets(10))  # type: ignore[arg-type]
    assert len(calls) == 8
    assert all(comment == "ST" for _, comment in calls)


def test_f14bu_all_targets_registered_when_under_eight() -> None:
    """F-14BU with fewer than 8 targets gets one ST point per target."""
    builder, calls = _make_strike_builder(F_14BU)
    builder.register_special_strike_points(_fake_targets(3))  # type: ignore[arg-type]
    assert len(calls) == 3
    assert all(comment == "ST" for _, comment in calls)


def test_f14b_still_registers_single_st_point() -> None:
    """F-14B behavior is unchanged: only the first target gets an ST point."""
    builder, calls = _make_strike_builder(F_14B)
    builder.register_special_strike_points(_fake_targets(5))  # type: ignore[arg-type]
    assert len(calls) == 1
    assert calls[0][1] == "ST"


def test_f14a_135_gr_still_registers_single_st_point() -> None:
    """F-14A-135-GR behavior is unchanged: only the first target gets an ST point."""
    from dcs.planes import F_14A_135_GR

    builder, calls = _make_strike_builder(F_14A_135_GR)
    builder.register_special_strike_points(_fake_targets(5))  # type: ignore[arg-type]
    assert len(calls) == 1
    assert calls[0][1] == "ST"


def test_f14bu_client_flight_gets_ip_point() -> None:
    """F-14BU client flights get an IP nav target point like F-14B/F-14A."""
    builder, calls = _make_strike_builder(F_14BU, client_count=1)
    builder.register_special_ingress_points()
    assert len(calls) == 1
    assert calls[0][1] == "IP"


def test_f14bu_ai_flight_gets_no_ip_point() -> None:
    """F-14BU AI flights (client_count=0) do not get an IP nav target point."""
    builder, calls = _make_strike_builder(F_14BU, client_count=0)
    builder.register_special_ingress_points()
    assert len(calls) == 0
