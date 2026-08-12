from __future__ import annotations

import importlib.util
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING, Callable, Protocol, cast
from unittest.mock import MagicMock, patch

from dcs.mapping import Point
from dcs.terrain import Terrain
from dcs.vehicles import Armor

from game.ato.flighttype import FlightType
from game.commander.objectivefinder import ObjectiveFinder
from game.commander.theatercommander import TheaterCommander
from game.commander.theaterstate import TheaterState
from game.commander.tasks.compound.attackbattlepositions import AttackBattlePositions
from game.commander.tasks.compound.nextaction import PlanNextAction
from game.commander.tasks.primitive.armedrecon import PlanArmedRecon
from game.commander.tasks.primitive.bai import PlanBai
from game.commander.tasks.primitive.motorpool import PlanMotorpoolAttack
from game.ato.flightplans.bai import Builder as BaiBuilder
from game.ato.flightplans.formationattack import FormationAttackLayout
from game.ato.flightplans.strike import Builder as StrikeBuilder
from game.ato.flightplans.waypointbuilder import StrikeTarget
from game.data.groups import GroupTask
from game.missiongenerator.motorpoolpopulator import MotorpoolPopulator
from game.dcs.groundunittype import GroundUnitType
from game.theater.controlpoint import ControlPoint
from game.theater.player import Player
from game.theater.presetlocation import PresetLocation
from game.theater.theatergroundobject import MotorpoolGroundObject
from game.utils import Heading, feet

if TYPE_CHECKING:
    from game.game import Game


class _PopulationGame(Protocol):
    next_group_id: Callable[[], int]
    next_unit_id: Callable[[], int]


class _BuilderFlight(Protocol):
    package: object
    flight_type: FlightType
    client_count: int


class _BuilderTestDouble(Protocol):
    flight: _BuilderFlight
    _build: Callable[[object, list[StrikeTarget]], FormationAttackLayout]

    def layout(self) -> FormationAttackLayout: ...


class _IngressBuilderTestDouble(Protocol):
    package: object
    flight: object
    group: object
    mission: object
    waypoint: object
    register_special_ingress_points: Callable[..., object]
    register_special_strike_points: Callable[..., object]

    def add_tasks(self, waypoint: object) -> None: ...


class _TargetedTask(Protocol):
    target: object


def _task_target(task: object) -> object:
    return cast(_TargetedTask, task).target


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


# --- Standalone motorpool task retirement ------------------------------------


def test_standalone_attack_motorpools_module_is_retired() -> None:
    assert (
        importlib.util.find_spec("game.commander.tasks.compound.attackmotorpools")
        is None
    )


# --- ObjectiveFinder.motorpool_targets ---------------------------------------


def test_plan_missions_populates_motorpools_before_building_state() -> None:
    game = MagicMock()
    player = Player.BLUE
    tracer = MagicMock()
    events: list[str] = []

    def populate() -> None:
        events.append("populate")

    def from_game(*args: object, **kwargs: object) -> MagicMock:
        events.append("from_game")
        return MagicMock()

    def plan(*args: object, **kwargs: object) -> None:
        events.append("plan")

    with (
        patch(
            "game.missiongenerator.motorpoolpopulator.MotorpoolPopulator"
        ) as populator,
        patch.object(TheaterState, "from_game", side_effect=from_game),
        patch.object(TheaterCommander, "plan", side_effect=plan),
    ):
        populator.return_value.populate.side_effect = populate
        TheaterCommander(game, player).plan_missions(MagicMock(), tracer)

    populator.assert_called_once_with(game)
    populator.return_value.populate.assert_called_once_with()
    assert events == ["populate", "from_game", "plan"]


def _populated_motorpool(
    reserve: dict[GroundUnitType, int],
) -> MotorpoolGroundObject:
    tgo, cp = _motorpool_cp(reserve, friendly=False)
    game = cast(_PopulationGame, _game([cp]))
    game.next_group_id = MagicMock(side_effect=[1, 2, 3])
    game.next_unit_id = MagicMock(side_effect=range(1, 20))
    MotorpoolPopulator(cast("Game", game)).populate()
    return tgo


def _capture_builder_targets(
    builder_type: type[object],
    tgo: MotorpoolGroundObject,
    client_count: int = 0,
    flight_type: FlightType = FlightType.BAI,
) -> list[StrikeTarget] | None:
    builder = cast(_BuilderTestDouble, object.__new__(builder_type))
    builder.flight = cast(
        _BuilderFlight,
        SimpleNamespace(
            package=SimpleNamespace(target=tgo),
            flight_type=flight_type,
            client_count=client_count,
        ),
    )
    captured: list[StrikeTarget] | None = None

    def capture(
        _ingress: object, targets: list[StrikeTarget] | None = None
    ) -> FormationAttackLayout:
        nonlocal captured
        captured = targets
        return cast(FormationAttackLayout, object())

    builder._build = capture
    builder.layout()
    return captured


def test_player_motorpool_bai_layout_targets_motorpool_location_once() -> None:
    abrams = _gut()
    bradley = next(GroundUnitType.for_dcs_type(Armor.M_2_Bradley))
    tgo = _populated_motorpool({abrams: 2, bradley: 1})

    targets = _capture_builder_targets(BaiBuilder, tgo, client_count=1)

    assert targets is not None
    assert len(targets) == 1
    assert targets[0].target is tgo


def test_ai_motorpool_bai_layout_uses_a_target_zone() -> None:
    abrams = _gut()
    bradley = next(GroundUnitType.for_dcs_type(Armor.M_2_Bradley))
    tgo = _populated_motorpool({abrams: 2, bradley: 1})

    targets = _capture_builder_targets(BaiBuilder, tgo)

    assert targets is None


def test_motorpool_strike_layout_exposes_populated_vehicles_to_ai() -> None:
    abrams = _gut()
    bradley = next(GroundUnitType.for_dcs_type(Armor.M_2_Bradley))
    tgo = _populated_motorpool({abrams: 2, bradley: 1})

    targets = _capture_builder_targets(StrikeBuilder, tgo)

    assert targets is not None
    assert [target.target for target in targets] == tgo.strike_targets


def test_motorpool_layouts_have_no_targets_when_reserve_is_empty() -> None:
    tgo = _populated_motorpool({_gut(): 0})

    assert _capture_builder_targets(BaiBuilder, tgo) is None
    assert _capture_builder_targets(StrikeBuilder, tgo) == []


def _build_motorpool_ingress_builder(
    builder_type: type[object],
    tgo: MotorpoolGroundObject,
    waypoint_type: object | None = None,
) -> tuple[_IngressBuilderTestDouble, SimpleNamespace]:
    builder = cast(_IngressBuilderTestDouble, object.__new__(builder_type))
    settings = SimpleNamespace(motorpool_spawn_cap=25)
    flight = SimpleNamespace(
        is_helo=False,
        coalition=SimpleNamespace(game=SimpleNamespace(settings=settings)),
        client_count=0,
        unit_type=SimpleNamespace(dcs_unit_type=object()),
        custom_targets=[],
    )
    builder.package = SimpleNamespace(target=tgo)
    builder.flight = flight
    builder.group = SimpleNamespace(
        units=[SimpleNamespace(unit_type=object())], task="Ground Attack"
    )
    builder.mission = SimpleNamespace(
        find_group=lambda name: SimpleNamespace(id=hash(name) % 10000)
    )
    builder.register_special_ingress_points = MagicMock()
    builder.register_special_strike_points = MagicMock()
    builder.waypoint = SimpleNamespace(
        waypoint_type=waypoint_type,
        targets=list(tgo.strike_targets),
        alt=feet(1000),
    )
    return builder, SimpleNamespace(tasks=[], alt=304.8)


def test_motorpool_bai_creates_attack_group_per_group() -> None:
    abrams = _gut()
    bradley = next(GroundUnitType.for_dcs_type(Armor.M_2_Bradley))
    tgo = _populated_motorpool({abrams: 2, bradley: 1})
    from game.missiongenerator.aircraft.waypoints.baiingress import BaiIngressBuilder

    builder, waypoint = _build_motorpool_ingress_builder(BaiIngressBuilder, tgo)
    builder.add_tasks(waypoint)

    from dcs.task import AttackGroup, EngageTargetsInZone

    attack_tasks = [task for task in waypoint.tasks if isinstance(task, AttackGroup)]
    # One AttackGroup per motorpool vehicle-type group. AttackGroup is a waypoint
    # task (not an enroute task), so it does not propagate to subsequent waypoints
    # the way EngageTargetsInZone did.
    assert len(attack_tasks) == len(tgo.groups)
    assert not [
        task for task in waypoint.tasks if isinstance(task, EngageTargetsInZone)
    ]


def test_motorpool_strike_ingress_uses_bombing_for_ai() -> None:
    tgo = _populated_motorpool({_gut(): 10})
    from game.missiongenerator.aircraft.waypoints.strikeingress import (
        StrikeIngressBuilder,
    )

    builder, waypoint = _build_motorpool_ingress_builder(StrikeIngressBuilder, tgo)
    builder.add_tasks(waypoint)

    from dcs.task import Bombing

    assert [task for task in waypoint.tasks if isinstance(task, Bombing)]


def test_motorpool_strike_ingress_bombs_each_target_once() -> None:
    abrams = _gut()
    bradley = next(GroundUnitType.for_dcs_type(Armor.M_2_Bradley))
    tgo = _populated_motorpool({abrams: 2, bradley: 1})
    from game.missiongenerator.aircraft.waypoints.strikeingress import (
        StrikeIngressBuilder,
    )

    builder, waypoint = _build_motorpool_ingress_builder(StrikeIngressBuilder, tgo)
    builder.add_tasks(waypoint)

    from dcs.task import Bombing

    bombing_tasks = [task for task in waypoint.tasks if isinstance(task, Bombing)]
    targets = list(tgo.strike_targets)
    # One coordinate Bombing task per rendered target, at that target's position.
    assert len(bombing_tasks) == len(targets)
    expected_positions = {(t.position.x, t.position.y) for t in targets}
    actual_positions = {(task.params["x"], task.params["y"]) for task in bombing_tasks}
    assert actual_positions == expected_positions


def test_motorpool_strike_client_waypoints_match_targets() -> None:
    abrams = _gut()
    bradley = next(GroundUnitType.for_dcs_type(Armor.M_2_Bradley))
    tgo = _populated_motorpool({abrams: 2, bradley: 1})

    targets = _capture_builder_targets(
        StrikeBuilder, tgo, flight_type=FlightType.STRIKE
    )

    assert targets is not None
    # One StrikeTarget per rendered motorpool unit; client target waypoints are
    # built one-per-target by the formation layout.
    assert len(targets) == len(tgo.strike_targets)
    assert [target.target for target in targets] == tgo.strike_targets


def test_empty_strike_targets_do_not_divide_by_zero() -> None:
    tgo = _populated_motorpool({_gut(): 0})
    from game.missiongenerator.aircraft.waypoints.strikeingress import (
        StrikeIngressBuilder,
    )

    builder, waypoint = _build_motorpool_ingress_builder(StrikeIngressBuilder, tgo)
    # Zero targets must produce no Bombing task and no exception (no division).
    builder.add_tasks(waypoint)

    from dcs.task import Bombing

    assert not [task for task in waypoint.tasks if isinstance(task, Bombing)]


def test_motorpool_targets_yields_enemy_motorpool_with_reserve() -> None:
    gut = _gut()
    enemy_tgo, enemy_cp = _motorpool_cp({gut: 4}, friendly=False)
    game = cast(_PopulationGame, _game([enemy_cp, _friendly_cp()]))
    game.next_group_id = MagicMock(side_effect=[1, 2, 3])
    game.next_unit_id = MagicMock(side_effect=range(1, 20))
    MotorpoolPopulator(cast("Game", game)).populate()
    targets = list(ObjectiveFinder(cast("Game", game), Player.BLUE).motorpool_targets())
    assert targets == [enemy_tgo]


def test_empty_motorpool_is_not_a_target() -> None:
    gut = _gut()
    enemy_tgo, enemy_cp = _motorpool_cp({gut: 4}, friendly=False)
    game = _game([enemy_cp, _friendly_cp()])

    assert enemy_tgo.groups == []
    assert (
        list(ObjectiveFinder(cast("Game", game), Player.BLUE).motorpool_targets()) == []
    )


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


def test_motorpool_targets_excludes_motorpool_when_disabled() -> None:
    gut = _gut()
    _disabled_tgo, enemy_cp = _motorpool_cp({gut: 4}, friendly=False)
    game = _game([enemy_cp, _friendly_cp()], enabled=False)
    assert (
        list(ObjectiveFinder(cast("Game", game), Player.BLUE).motorpool_targets()) == []
    )


def test_motorpool_targets_sorted_nearest_first() -> None:
    gut = _gut()
    near_tgo, near_cp = _motorpool_cp({gut: 1}, friendly=False, name="Near")
    far_tgo, far_cp = _motorpool_cp({gut: 1}, friendly=False, name="Far")
    near_tgo.distance_to = MagicMock(return_value=10.0)  # type: ignore[method-assign]
    far_tgo.distance_to = MagicMock(return_value=500.0)  # type: ignore[method-assign]
    game = cast(_PopulationGame, _game([far_cp, near_cp, _friendly_cp()]))
    game.next_group_id = MagicMock(side_effect=range(1, 20))
    game.next_unit_id = MagicMock(side_effect=range(1, 20))
    MotorpoolPopulator(cast("Game", game)).populate()
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
    # Render the reserve slice into the motorpool's persisted groups so the
    # per-location projection (tgo.units) reflects what is actually strikeable.
    game = cast(_PopulationGame, _game([cp, _friendly_cp()]))
    game.next_group_id = MagicMock(side_effect=[1, 2, 3])
    game.next_unit_id = MagicMock(side_effect=range(1, 20))
    MotorpoolPopulator(cast("Game", game)).populate()
    return tgo


def test_motorpool_bai_proposes_bai_plus_escorts() -> None:
    tgo = _motorpool_target(8)
    task = PlanMotorpoolAttack(tgo, FlightType.BAI)
    task.propose_flights()
    flight_tasks = [f.task for f in task.flights]
    assert FlightType.BAI in flight_tasks
    assert FlightType.STRIKE not in flight_tasks
    assert FlightType.SEAD_ESCORT in flight_tasks
    assert FlightType.ESCORT in flight_tasks
    assert FlightType.SEAD_SWEEP in flight_tasks


def test_motorpool_strike_proposes_strike_plus_escorts() -> None:
    tgo = _motorpool_target(8)
    task = PlanMotorpoolAttack(tgo, FlightType.STRIKE)
    task.propose_flights()
    flight_tasks = [f.task for f in task.flights]
    assert FlightType.STRIKE in flight_tasks
    assert FlightType.BAI not in flight_tasks
    assert FlightType.SEAD_ESCORT in flight_tasks


def test_motorpool_attack_effect_removes_target() -> None:
    tgo = _motorpool_target(4)
    other = _motorpool_target(4)
    state = SimpleNamespace(motorpool_targets=[tgo, other])
    task = PlanMotorpoolAttack(tgo, FlightType.BAI)
    task.package = None  # super().apply_effects is a no-op with no package
    task.apply_effects(state)  # type: ignore[arg-type]
    assert state.motorpool_targets == [other]


def test_motorpool_attack_precondition_fails_when_not_listed() -> None:
    tgo = _motorpool_target(4)
    state = SimpleNamespace(motorpool_targets=[])
    task = PlanMotorpoolAttack(tgo, FlightType.BAI)
    # Target absent: short-circuits before the heavy fulfillment path.
    assert task.preconditions_met(state) is False  # type: ignore[arg-type]


def test_motorpool_attack_precondition_fails_when_reserve_is_empty() -> None:
    tgo = _motorpool_target(0)
    state = SimpleNamespace(motorpool_targets=[tgo])
    task = PlanMotorpoolAttack(tgo, FlightType.BAI)
    assert task.preconditions_met(state) is False  # type: ignore[arg-type]


def test_motorpool_attack_propose_uses_per_location_rendered_units() -> None:
    # A CP with a large reserve spread across two motorpools: each motorpool
    # renders only its own slice, so flight sizing follows the per-location
    # rendered count, not the control-point-wide reserve total.
    gut = _gut()
    loc_a = PresetLocation(
        "A", Point(0.0, 0.0, MagicMock(spec=Terrain)), Heading.from_degrees(0.0)
    )
    loc_b = PresetLocation(
        "B", Point(0.0, 0.0, MagicMock(spec=Terrain)), Heading.from_degrees(0.0)
    )
    cp = MagicMock(spec=ControlPoint)
    cp.is_friendly = MagicMock(return_value=False)
    cp.captured = Player.RED
    cp.base = SimpleNamespace(armor={gut: 8}, total_armor=8)
    cp.connected_points = []  # rear CP: reserve == full pool
    cp.name = "CP"
    tgo_a = MotorpoolGroundObject("CP Motorpool 0", loc_a, cp, GroupTask.MOTORPOOL)
    tgo_b = MotorpoolGroundObject("CP Motorpool 1", loc_b, cp, GroupTask.MOTORPOOL)
    tgo_a.distance_to = MagicMock(return_value=100.0)  # type: ignore[method-assign]
    tgo_b.distance_to = MagicMock(return_value=100.0)  # type: ignore[method-assign]
    cp.ground_objects = [tgo_a, tgo_b]
    settings = cp.coalition.game.settings
    settings.motorpool_spawn_cap = 4
    settings.motorpool_enabled = True
    settings.autoplan_tankers_for_strike = False
    game = cast(_PopulationGame, _game([cp, _friendly_cp()], cap=4))
    game.next_group_id = MagicMock(side_effect=range(1, 40))
    game.next_unit_id = MagicMock(side_effect=range(1, 40))
    MotorpoolPopulator(cast("Game", game)).populate()

    # Two motorpools split the 4-unit cap 2/2.
    assert tgo_a.alive_unit_count == 2
    assert tgo_b.alive_unit_count == 2

    # The BAI flight for tgo_a is sized from its own 2 rendered units, not the
    # CP-wide reserve of 8.
    task = PlanMotorpoolAttack(tgo_a, FlightType.BAI)
    task.propose_flights()
    bai = next(f for f in task.flights if f.task == FlightType.BAI)
    assert bai.num_aircraft == min(4, (2 // 4) + 1)


def test_attack_battle_positions_preserves_motorpool_bai_then_strike_fallback() -> None:
    motorpool = _motorpool_target(4)
    state = cast(
        "TheaterState",
        SimpleNamespace(
            enemy_battle_positions={},
            motorpool_targets=[motorpool],
            control_point_priority_queue=[],
        ),
    )

    methods = list(AttackBattlePositions().each_valid_method(state))
    motorpool_tasks = [
        method[0] for method in methods if isinstance(method[0], PlanMotorpoolAttack)
    ]

    assert [task.task for task in motorpool_tasks] == [
        FlightType.BAI,
        FlightType.STRIKE,
    ]
    assert [task.target for task in motorpool_tasks] == [motorpool, motorpool]


def test_plan_next_action_orders_bai_motorpool_fallback_before_armed_recon() -> None:
    battle_position = object()
    motorpool = _motorpool_target(4)
    recon_target = SimpleNamespace(is_fleet=False)
    state = cast(
        "TheaterState",
        SimpleNamespace(
            enemy_battle_positions={
                "front": SimpleNamespace(in_priority_order=[battle_position])
            },
            motorpool_targets=[motorpool],
            control_point_priority_queue=[recon_target],
        ),
    )

    top_level_methods = list(PlanNextAction(False).each_valid_method(state))
    battle_position_tasks = [
        method[0]
        for method in top_level_methods
        if isinstance(method[0], AttackBattlePositions)
    ]
    assert len(battle_position_tasks) == 1
    assert len(top_level_methods) == 11

    proposals = list(battle_position_tasks[0].each_valid_method(state))
    proposal_tasks = [method[0] for method in proposals]
    assert [type(task) for task in proposal_tasks] == [
        PlanBai,
        PlanMotorpoolAttack,
        PlanMotorpoolAttack,
        PlanArmedRecon,
    ]
    assert [_task_target(task) for task in proposal_tasks] == [
        battle_position,
        motorpool,
        motorpool,
        recon_target,
    ]
    assert [
        task.task for task in proposal_tasks if isinstance(task, PlanMotorpoolAttack)
    ] == [FlightType.BAI, FlightType.STRIKE]


def test_plan_next_action_omits_empty_motorpool_proposals() -> None:
    battle_position = object()
    recon_target = SimpleNamespace(is_fleet=False)
    state = cast(
        "TheaterState",
        SimpleNamespace(
            enemy_battle_positions={
                "front": SimpleNamespace(in_priority_order=[battle_position])
            },
            motorpool_targets=[],
            control_point_priority_queue=[recon_target],
        ),
    )

    top_level_methods = list(PlanNextAction(False).each_valid_method(state))
    battle_position_tasks = [
        method[0]
        for method in top_level_methods
        if isinstance(method[0], AttackBattlePositions)
    ]
    assert len(battle_position_tasks) == 1
    assert len(top_level_methods) == 11

    proposals = list(battle_position_tasks[0].each_valid_method(state))
    proposal_tasks = [method[0] for method in proposals]
    assert [type(task) for task in proposal_tasks] == [PlanBai, PlanArmedRecon]
    assert [_task_target(task) for task in proposal_tasks] == [
        battle_position,
        recon_target,
    ]
