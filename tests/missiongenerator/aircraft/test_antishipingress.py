import logging
from types import SimpleNamespace
from typing import Any, Callable, cast
from unittest.mock import MagicMock

import pytest
from dcs import Point
from dcs.planes import A_10C
from dcs.point import MovingPoint
from dcs.task import (
    AttackGroup,
    Bombing,
    CarpetBombing,
    ControlledTask,
    EngageTargetsInZone,
)
from dcs.terrain import Terrain
from dcs.vehicles import Armor

from game.ato.flighttype import FlightType
from game.data.groups import GroupTask
from game.dcs.groundunittype import GroundUnitType
from game.missiongenerator.aircraft.waypoints.antishipingress import (
    AntiShipIngressBuilder,
)
from game.missiongenerator.aircraft.waypoints.armedreconingress import (
    ArmedReconIngressBuilder,
)
from game.missiongenerator.aircraft.waypoints.baiingress import BaiIngressBuilder
from game.missiongenerator.aircraft.waypoints.strikeingress import StrikeIngressBuilder
from game.missiongenerator.motorpoolpopulator import MotorpoolPopulator
from game.theater import NavalControlPoint, TheaterGroundObject
from game.theater.controlpoint import ControlPoint
from game.theater.presetlocation import PresetLocation
from game.theater.theatergroundobject import MotorpoolGroundObject
from game.utils import Heading


def _model_group(name: str) -> MagicMock:
    group = MagicMock()
    group.group_name = name
    return group


def _build_builder(target: Any, find_group: Callable[[str], Any]) -> tuple[Any, Any]:
    # Bypass __init__ (it needs a full mission/flight); only add_tasks() and the
    # few collaborators it consults are exercised. Typed as Any so the mocks can
    # stand in for the real, strongly-typed attributes.
    builder: Any = object.__new__(AntiShipIngressBuilder)
    builder.register_special_ingress_points = MagicMock()
    builder.package = MagicMock()
    builder.package.target = target
    builder.flight = MagicMock()
    builder.mission = MagicMock()
    builder.mission.find_group.side_effect = find_group
    waypoint = MagicMock()
    waypoint.tasks = []
    return builder, waypoint


def test_naval_control_point_targets_main_tgo_not_first_ground_object() -> None:
    """Regression test: the carrier group must come from find_main_tgo().

    A NavalControlPoint can own several ground objects; ground_objects[0] is
    not necessarily the carrier (it may be a sunk group that never spawns).
    The attack task must target the group the flight plan routes to.
    """
    carrier_group = _model_group("carrier-group")
    main_tgo = MagicMock()
    main_tgo.groups = [carrier_group]

    wrong_group = _model_group("wrong-group")
    wrong_tgo = MagicMock()
    wrong_tgo.groups = [wrong_group]

    target = MagicMock(spec=NavalControlPoint)
    target.find_main_tgo.return_value = main_tgo
    target.ground_objects = [wrong_tgo]  # ground_objects[0] is the wrong group

    miz_group = MagicMock()
    miz_group.id = 42

    def find_group(name: str) -> Any:
        return miz_group if name == "carrier-group" else None

    builder, waypoint = _build_builder(target, find_group)
    builder.add_tasks(waypoint)

    queried = [call.args[0] for call in builder.mission.find_group.call_args_list]
    assert "carrier-group" in queried
    assert "wrong-group" not in queried

    attacks = [task for task in waypoint.tasks if isinstance(task, AttackGroup)]
    assert attacks, "expected at least one AttackGroup task"
    assert all(task.params["groupId"] == 42 for task in attacks)


def test_warns_when_no_attackable_group(caplog: pytest.LogCaptureFixture) -> None:
    """A target whose groups are not in the mission yields a clear warning."""
    group = _model_group("missing-group")
    target = MagicMock(spec=TheaterGroundObject)
    target.groups = [group]

    builder, waypoint = _build_builder(target, lambda name: None)

    with caplog.at_level(logging.WARNING):
        builder.add_tasks(waypoint)

    assert not [t for t in waypoint.tasks if isinstance(t, AttackGroup)]
    assert any("no attackable target group" in rec.message for rec in caplog.records)


def _motorpool_target(unit_positions: list[Point]) -> MotorpoolGroundObject:
    control_point = MagicMock(spec=ControlPoint)
    location = PresetLocation(
        "Garage", Point(0.0, 0.0, MagicMock(spec=Terrain)), Heading.from_degrees(0.0)
    )
    target = MotorpoolGroundObject(
        "Motorpool", location, control_point, GroupTask.MOTORPOOL
    )
    target.groups = cast(
        Any,
        [
            # MotorpoolPopulator reads (but discards) pre-existing groups while
            # reconciling, so the fake group needs an id like a real TheaterGroup.
            SimpleNamespace(
                id=0,
                units=[SimpleNamespace(position=p, alive=True) for p in unit_positions],
            )
        ],
    )
    return target


def _motorpool_ingress_builder(
    builder_type: type[Any], target: MotorpoolGroundObject
) -> tuple[Any, MovingPoint]:
    builder: Any = object.__new__(builder_type)
    builder.package = SimpleNamespace(target=target)
    builder.flight = SimpleNamespace(
        is_helo=False,
        client_count=0,
        coalition=SimpleNamespace(
            game=SimpleNamespace(
                settings=SimpleNamespace(armed_recon_engagement_range_distance=5)
            )
        ),
        package=SimpleNamespace(target=target),
        flight_plan=SimpleNamespace(
            tot_waypoint=SimpleNamespace(position=target.position)
        ),
    )
    builder.register_special_ingress_points = MagicMock()
    builder.mission = MagicMock()
    waypoint = MovingPoint(target.position)
    return builder, waypoint


def test_strike_motorpool_uses_post_population_units_for_bombing_task() -> None:
    target = _motorpool_target([])
    control_point = target.control_point
    unit_type = next(GroundUnitType.for_dcs_type(Armor.M_1_Abrams))
    control_point.base = cast(Any, SimpleNamespace(armor={unit_type: 1}))
    control_point.connected_points = []
    cast(Any, control_point).ground_objects = [target]

    counters = {"unit": 0, "group": 0}

    def next_unit_id() -> int:
        counters["unit"] += 1
        return counters["unit"]

    def next_group_id() -> int:
        counters["group"] += 1
        return counters["group"]

    game = SimpleNamespace(
        theater=SimpleNamespace(controlpoints=[control_point]),
        settings=SimpleNamespace(motorpool_enabled=True, motorpool_spawn_cap=10),
        next_unit_id=next_unit_id,
        next_group_id=next_group_id,
    )
    MotorpoolPopulator(cast(Any, game)).populate()

    builder: Any = object.__new__(StrikeIngressBuilder)
    builder.group = SimpleNamespace(
        units=[SimpleNamespace(unit_type=A_10C)], task="Ground Attack"
    )
    builder.waypoint = SimpleNamespace(targets=[])
    builder.package = SimpleNamespace(target=target)
    builder.flight = SimpleNamespace(
        flight_type=FlightType.STRIKE,
        client_count=0,
        unit_type=SimpleNamespace(dcs_unit_type=A_10C),
    )
    builder.register_special_ingress_points = MagicMock()
    waypoint = MovingPoint(target.position)

    builder.add_tasks(waypoint)

    assert [
        task for task in waypoint.tasks if isinstance(task, (Bombing, CarpetBombing))
    ]


def test_bai_motorpool_uses_one_zone_task_for_rendered_units() -> None:
    target = _motorpool_target(
        [
            Point(3.0, 4.0, MagicMock(spec=Terrain)),
            Point(0.0, 10.0, MagicMock(spec=Terrain)),
        ]
    )
    builder, waypoint = _motorpool_ingress_builder(BaiIngressBuilder, target)

    cast(Any, builder).add_tasks(waypoint)

    zone_tasks = [
        task for task in waypoint.tasks if isinstance(task, EngageTargetsInZone)
    ]
    assert len(zone_tasks) == 1
    assert not [task for task in waypoint.tasks if isinstance(task, AttackGroup)]
    assert zone_tasks[0].params["zoneRadius"] == 11
    builder.mission.find_group.assert_not_called()


def test_armed_recon_motorpool_radius_uses_rendered_units_only() -> None:
    target = _motorpool_target(
        [
            Point(6.0, 8.0, MagicMock(spec=Terrain)),
            Point(0.0, 10.0, MagicMock(spec=Terrain)),
        ]
    )
    builder, waypoint = _motorpool_ingress_builder(ArmedReconIngressBuilder, target)

    cast(Any, builder).add_tasks(waypoint)

    task = next(
        task
        for task in waypoint.tasks
        if isinstance(task, ControlledTask)
        and task.params["task"]["id"] == "EngageTargetsInZone"
    )
    assert task.params["task"]["params"]["zoneRadius"] == 11


def test_armed_recon_motorpool_empty_target_has_zero_radius() -> None:
    target = _motorpool_target([])
    builder, waypoint = _motorpool_ingress_builder(ArmedReconIngressBuilder, target)

    cast(Any, builder).add_tasks(waypoint)

    task = next(
        task
        for task in waypoint.tasks
        if isinstance(task, ControlledTask)
        and task.params["task"]["id"] == "EngageTargetsInZone"
    )
    assert task.params["task"]["params"]["zoneRadius"] == 0
