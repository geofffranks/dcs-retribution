from __future__ import annotations

from types import SimpleNamespace
from typing import Any, TYPE_CHECKING, cast
from unittest.mock import MagicMock

from dcs.mapping import Point
from dcs.terrain import Terrain
from dcs.vehicles import Armor

from game.ato.flighttype import FlightType
from game.ato.flightwaypoint import FlightWaypoint
from game.ato.flightwaypointtype import FlightWaypointType
from game.commander.objectivefinder import ObjectiveFinder
from game.commander.tasks.compound.attackbattlepositions import AttackBattlePositions
from game.commander.tasks.primitive.motorpool import PlanMotorpoolAttack
from game.ato.flightplans.armedrecon import Builder as ArmedReconBuilder
from game.ato.flightplans.formationattack import (
    FormationAttackBuilder,
    FormationAttackLayout,
)
from game.ato.flightplans.strike import Builder as StrikeBuilder, StrikeFlightPlan
from game.ato.flightplans.waypointbuilder import StrikeTarget
from game.data.groups import GroupTask
from game.dcs.groundunittype import GroundUnitType
from game.theater.controlpoint import ControlPoint
from game.theater.player import Player
from game.theater.presetlocation import PresetLocation
from game.theater.theatergroundobject import MotorpoolGroundObject
from game.utils import Heading
from game.missiongenerator.aircraft.waypoints.strikeingress import StrikeIngressBuilder

if TYPE_CHECKING:
    from game.game import Game


def _gut() -> GroundUnitType:
    return next(GroundUnitType.for_dcs_type(Armor.M_1_Abrams))


def _motorpool_cp(
    reserve: dict[GroundUnitType, int], friendly: bool, name: str = "CP"
) -> tuple[MotorpoolGroundObject, ControlPoint]:
    cp = MagicMock(spec=ControlPoint)
    cp.is_friendly = MagicMock(return_value=friendly)
    cp.captured = Player.BLUE if friendly else Player.RED
    cp.base = SimpleNamespace(armor=dict(reserve), total_armor=sum(reserve.values()))
    cp.connected_points = []  # rear CP: reserve == full pool
    cp.name = name
    loc = PresetLocation(
        "G", Point(0.0, 0.0, MagicMock(spec=Terrain)), Heading.from_degrees(0.0)
    )
    tgo = MotorpoolGroundObject(f"{name} Motorpool 0", loc, cp, GroupTask.MOTORPOOL)
    if reserve_total := sum(reserve.values()):
        tgo.groups = cast(Any, [SimpleNamespace(alive_units=reserve_total, units=[])])
    tgo.distance_to = MagicMock(return_value=100.0)  # type: ignore[method-assign]
    cp.ground_objects = [tgo]
    return tgo, cp


def _game(
    controlpoints: list[ControlPoint], cap: int = 10, enabled: bool = True
) -> object:
    return SimpleNamespace(
        theater=SimpleNamespace(controlpoints=controlpoints),
        settings=SimpleNamespace(motorpool_enabled=enabled, motorpool_spawn_cap=cap),
    )


def _friendly_cp() -> ControlPoint:
    cp = MagicMock(spec=ControlPoint)
    cp.is_friendly = MagicMock(return_value=True)
    cp.captured = Player.BLUE
    return cp


# --- ObjectiveFinder.motorpool_targets ---------------------------------------


def test_motorpool_targets_yields_enemy_motorpool_with_reserve() -> None:
    gut = _gut()
    enemy_tgo, enemy_cp = _motorpool_cp({gut: 4}, friendly=False)
    game = _game([enemy_cp, _friendly_cp()])
    targets = list(ObjectiveFinder(cast("Game", game), Player.BLUE).motorpool_targets())
    assert targets == [enemy_tgo]


def test_motorpool_targets_excludes_friendly_motorpools() -> None:
    gut = _gut()
    _friendly_tgo, friendly_cp = _motorpool_cp({gut: 4}, friendly=True)
    game = _game([friendly_cp, _friendly_cp()])
    assert (
        list(ObjectiveFinder(cast("Game", game), Player.BLUE).motorpool_targets()) == []
    )


def test_motorpool_targets_excludes_motorpool_with_no_reserve() -> None:
    gut = _gut()
    _empty_tgo, enemy_cp = _motorpool_cp({gut: 0}, friendly=False)
    game = _game([enemy_cp, _friendly_cp()])
    assert (
        list(ObjectiveFinder(cast("Game", game), Player.BLUE).motorpool_targets()) == []
    )


def test_motorpool_targets_projects_pre_render_reserve() -> None:
    gut = _gut()
    target, enemy_cp = _motorpool_cp({gut: 4}, friendly=False)
    target.groups = []
    game = _game([enemy_cp, _friendly_cp()])

    assert list(
        ObjectiveFinder(cast("Game", game), Player.BLUE).motorpool_targets()
    ) == [target]


def test_motorpool_targets_excludes_dead_only_groups() -> None:
    gut = _gut()
    target, enemy_cp = _motorpool_cp({gut: 4}, friendly=False)
    cast(Any, target.groups[0]).alive_units = 0
    game = _game([enemy_cp, _friendly_cp()])

    assert (
        list(ObjectiveFinder(cast("Game", game), Player.BLUE).motorpool_targets()) == []
    )


def test_motorpool_targets_use_rendered_units_even_when_setting_disabled() -> None:
    gut = _gut()
    target, enemy_cp = _motorpool_cp({gut: 4}, friendly=False)
    game = _game([enemy_cp, _friendly_cp()], enabled=False)
    assert list(
        ObjectiveFinder(cast("Game", game), Player.BLUE).motorpool_targets()
    ) == [target]


def test_motorpool_targets_sorted_nearest_first() -> None:
    gut = _gut()
    near_tgo, near_cp = _motorpool_cp({gut: 1}, friendly=False, name="Near")
    far_tgo, far_cp = _motorpool_cp({gut: 1}, friendly=False, name="Far")
    near_tgo.distance_to = MagicMock(return_value=10.0)  # type: ignore[method-assign]
    far_tgo.distance_to = MagicMock(return_value=500.0)  # type: ignore[method-assign]
    game = _game([far_cp, near_cp, _friendly_cp()])
    targets = list(ObjectiveFinder(cast("Game", game), Player.BLUE).motorpool_targets())
    assert targets == [near_tgo, far_tgo]


# --- PlanMotorpoolAttack ------------------------------------------------------


def _motorpool_target(reserve_count: int) -> MotorpoolGroundObject:
    gut = _gut()
    tgo, cp = _motorpool_cp({gut: reserve_count}, friendly=False)
    settings = cp.coalition.game.settings
    settings.motorpool_spawn_cap = 10
    settings.motorpool_enabled = True
    settings.autoplan_tankers_for_strike = False
    settings.fpa_2ship_weight = 1
    settings.fpa_3ship_weight = 0
    settings.fpa_4ship_weight = 0
    tgo.groups = cast(
        Any, [SimpleNamespace(alive_units=reserve_count, units=[SimpleNamespace()])]
    )
    return tgo


def test_motorpool_attack_proposes_armed_recon_with_escorts() -> None:
    tgo = _motorpool_target(8)
    task = PlanMotorpoolAttack(tgo)
    task.propose_flights()
    flight_tasks = [flight.task for flight in task.flights]
    assert flight_tasks == [
        FlightType.ARMED_RECON,
        FlightType.SEAD_ESCORT,
        FlightType.ESCORT,
        FlightType.SEAD_SWEEP,
    ]
    assert task.flights[0].num_aircraft == 2


def test_attack_battle_positions_yields_one_armed_recon_per_motorpool() -> None:
    tgo = _motorpool_target(4)
    state = SimpleNamespace(
        enemy_battle_positions={},
        motorpool_targets=[tgo],
        control_point_priority_queue=[],
    )
    methods = list(
        AttackBattlePositions().each_valid_method(state)  # type: ignore[arg-type]
    )
    assert len(methods) == 1
    task = methods[0][0]
    assert isinstance(task, PlanMotorpoolAttack)
    assert task.target is tgo


def test_motorpool_attack_effect_removes_target() -> None:
    tgo = _motorpool_target(4)
    other = _motorpool_target(4)
    state = SimpleNamespace(motorpool_targets=[tgo, other])
    task = PlanMotorpoolAttack(tgo)
    task.package = None  # super().apply_effects is a no-op with no package
    task.apply_effects(state)  # type: ignore[arg-type]
    assert state.motorpool_targets == [other]


def test_motorpool_attack_precondition_fails_when_not_listed() -> None:
    tgo = _motorpool_target(4)
    state = SimpleNamespace(motorpool_targets=[])
    task = PlanMotorpoolAttack(tgo)
    # Target absent: short-circuits before the heavy fulfillment path.
    assert task.preconditions_met(state) is False  # type: ignore[arg-type]


def test_motorpool_attack_precondition_fails_when_no_rendered_units() -> None:
    tgo = _motorpool_target(0)
    tgo.groups = []
    state = SimpleNamespace(motorpool_targets=[tgo])
    task = PlanMotorpoolAttack(tgo)
    assert task.preconditions_met(state) is False  # type: ignore[arg-type]


def test_manual_motorpool_strike_keeps_target_area_waypoint_when_empty(
    monkeypatch: Any,
) -> None:
    target, _cp = _motorpool_cp({}, friendly=False)
    terrain = MagicMock(spec=Terrain)
    package_waypoints = SimpleNamespace(
        join=FlightWaypoint("JOIN", FlightWaypointType.JOIN, Point(0, 0, terrain)),
        ingress=FlightWaypoint(
            "INGRESS", FlightWaypointType.INGRESS_STRIKE, Point(0, 0, terrain)
        ),
        split=Point(0, 0, terrain),
    )
    package = SimpleNamespace(target=target, waypoints=package_waypoints)
    flight = cast(
        Any,
        SimpleNamespace(
            package=package,
            flight_type=FlightType.STRIKE,
            is_helo=False,
            departure=SimpleNamespace(position=object()),
            arrival=SimpleNamespace(position=object()),
            divert=None,
        ),
    )
    builder = cast(Any, object.__new__(StrikeBuilder))
    builder.flight = flight
    builder._hold_point = lambda: object()
    builder._get_split = lambda: object()
    builder._build_refuel = lambda _builder: None

    class FakeWaypointBuilder:
        def __init__(self, _flight: object, _targets: object) -> None:
            self.get_combat_altitude = object()

        def __getattr__(self, _name: str) -> Any:
            def build_waypoint(*_args: object) -> Any:
                return SimpleNamespace(
                    position=target.position,
                    waypoint_type=FlightWaypointType.TARGET_GROUP_LOC,
                )

            return build_waypoint

    monkeypatch.setattr(
        "game.ato.flightplans.formationattack.WaypointBuilder", FakeWaypointBuilder
    )

    layout = builder.layout()

    assert len(layout.targets) == 1
    assert layout.targets[0].waypoint_type == FlightWaypointType.TARGET_GROUP_LOC
    plan = StrikeFlightPlan.__new__(StrikeFlightPlan)
    plan.flight = flight
    plan.layout = layout
    assert plan.tot_waypoint is layout.targets[0]


def test_motorpool_armed_recon_uses_target_area_waypoint() -> None:
    tgo = _motorpool_target(2)
    builder = cast(Any, object.__new__(ArmedReconBuilder))
    builder.flight = SimpleNamespace(
        package=SimpleNamespace(target=tgo),
        flight_type=FlightType.ARMED_RECON,
        client_count=0,
    )
    captured: dict[str, object] = {}

    def capture(
        ingress_type: object, targets: list[StrikeTarget] | None = None
    ) -> FormationAttackLayout:
        captured["ingress"] = ingress_type
        captured["targets"] = targets
        return cast(FormationAttackLayout, object())

    builder._build = capture
    builder.layout()

    assert captured == {
        "ingress": FlightWaypointType.INGRESS_ARMED_RECON,
        "targets": None,
    }


def test_strike_ingress_skips_bombing_tasks_without_targets() -> None:
    builder = cast(Any, object.__new__(StrikeIngressBuilder))
    builder.waypoint = SimpleNamespace(targets=[])
    builder.group = SimpleNamespace(units=[SimpleNamespace(unit_type=object())])
    builder.register_special_strike_points = MagicMock()
    waypoint = SimpleNamespace(tasks=[])

    builder.add_strike_tasks(waypoint)

    assert waypoint.tasks == []
    builder.register_special_strike_points.assert_not_called()
