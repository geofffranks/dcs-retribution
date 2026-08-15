from __future__ import annotations

import importlib.util
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING, Callable, Protocol, cast
from unittest.mock import MagicMock, patch

from dcs.mapping import Point
from dcs.terrain import Terrain
from dcs.vehicles import Armor

from game.ato.flighttype import FlightType
from game.ato.flightwaypointtype import FlightWaypointType
from game.commander.objectivefinder import ObjectiveFinder
from game.commander.theatercommander import TheaterCommander
from game.commander.theaterstate import TheaterState
from game.commander.tasks.compound.attackbattlepositions import AttackBattlePositions
from game.commander.tasks.compound.nextaction import PlanNextAction
from game.commander.tasks.primitive.armedrecon import PlanArmedRecon
from game.commander.tasks.primitive.bai import PlanBai
from game.commander.tasks.primitive.motorpool import PlanMotorpoolAttack
from game.ato.flightplans.armedrecon import Builder as ArmedReconBuilder
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
from game.theater.theatergroundobject import (
    MotorpoolGroundObject,
    VehicleGroupGroundObject,
)
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


# --- TheaterCommander / ObjectiveFinder.motorpool_targets ---------------------


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
    flight_type: FlightType = FlightType.BAI,
) -> list[StrikeTarget]:
    builder = cast(_BuilderTestDouble, object.__new__(builder_type))
    builder.flight = cast(
        _BuilderFlight,
        SimpleNamespace(
            package=SimpleNamespace(target=tgo),
            flight_type=flight_type,
            client_count=0,
        ),
    )
    captured: list[StrikeTarget] = []

    def capture(_ingress: object, targets: list[StrikeTarget]) -> FormationAttackLayout:
        captured.extend(targets)
        return cast(FormationAttackLayout, object())

    builder._build = capture
    builder.layout()
    return captured


def test_motorpool_layouts_when_reserve_is_empty() -> None:
    tgo = _populated_motorpool({_gut(): 0})

    # BAI always treats a motorpool as a single zone target (convoy-style),
    # even when nothing is parked; STRIKE keeps the upstream empty-list
    # semantics (single target-area waypoint, no per-unit targets).
    bai_targets = _capture_builder_targets(BaiBuilder, tgo)
    assert len(bai_targets) == 1
    assert bai_targets[0].target is tgo
    assert _capture_builder_targets(StrikeBuilder, tgo) == []


def test_motorpool_bai_layout_targets_the_motorpool_once() -> None:
    abrams = _gut()
    bradley = next(GroundUnitType.for_dcs_type(Armor.M_2_Bradley))
    tgo = _populated_motorpool({abrams: 2, bradley: 1})

    # One zone target for the whole motorpool, not one per vehicle-type group.
    targets = _capture_builder_targets(BaiBuilder, tgo)
    assert len(targets) == 1
    assert targets[0].target is tgo


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


# --- MotorpoolGroundObject.mission_types --------------------------------------


def test_motorpool_mission_types_offer_armed_recon_and_bai() -> None:
    tgo, _ = _motorpool_cp({_gut(): 2}, friendly=False)
    mission_types = list(tgo.mission_types(Player.BLUE))
    # Armed recon leads (the autoplanned attack type); BAI is offered for
    # manual planning; STRIKE remains available through the inherited path.
    assert mission_types[0] is FlightType.ARMED_RECON
    assert FlightType.BAI in mission_types
    assert FlightType.STRIKE in mission_types
    assert len(mission_types) == len(set(mission_types))


def test_friendly_motorpool_offers_no_attack_mission_types() -> None:
    tgo, _ = _motorpool_cp({_gut(): 2}, friendly=True)
    mission_types = list(tgo.mission_types(Player.BLUE))
    # Only defensive missions (e.g. BARCAP) may be planned against friendly TGOs.
    assert FlightType.ARMED_RECON not in mission_types
    assert FlightType.BAI not in mission_types
    assert FlightType.STRIKE not in mission_types


# --- PlanMotorpoolAttack ------------------------------------------------------


def _motorpool_target(reserve_count: int) -> MotorpoolGroundObject:
    gut = _gut()
    tgo, cp = _motorpool_cp({gut: reserve_count}, friendly=False)
    settings = cp.coalition.game.settings
    settings.motorpool_spawn_cap = 10
    settings.motorpool_enabled = True
    settings.autoplan_tankers_for_strike = False
    # Deterministic flight sizing: always a 2-ship.
    settings.fpa_2ship_weight = 1
    settings.fpa_3ship_weight = 0
    settings.fpa_4ship_weight = 0
    # Render the reserve slice into the motorpool's persisted groups so the
    # per-location projection (tgo.units) reflects what is actually parked.
    game = cast(_PopulationGame, _game([cp, _friendly_cp()]))
    game.next_group_id = MagicMock(side_effect=[1, 2, 3])
    game.next_unit_id = MagicMock(side_effect=range(1, 20))
    MotorpoolPopulator(cast("Game", game)).populate()
    return tgo


def test_motorpool_attack_proposes_armed_recon_plus_escorts() -> None:
    tgo = _motorpool_target(8)
    task = PlanMotorpoolAttack(tgo)
    task.propose_flights()
    flights = list(task.flights)
    assert [flight.task for flight in flights] == [
        FlightType.ARMED_RECON,
        FlightType.SEAD_ESCORT,
        FlightType.ESCORT,
        FlightType.SEAD_SWEEP,
    ]
    armed_recon = flights[0]
    assert armed_recon.num_aircraft == 2


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


def test_motorpool_attack_precondition_fails_when_reserve_is_empty() -> None:
    tgo = _motorpool_target(0)
    state = SimpleNamespace(motorpool_targets=[tgo])
    task = PlanMotorpoolAttack(tgo)
    assert task.preconditions_met(state) is False  # type: ignore[arg-type]


def test_attack_battle_positions_yields_single_motorpool_armed_recon() -> None:
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

    assert len(motorpool_tasks) == 1
    assert motorpool_tasks[0].target is motorpool


def test_plan_next_action_orders_motorpool_attack_before_armed_recon() -> None:
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

    proposals = list(battle_position_tasks[0].each_valid_method(state))
    proposal_tasks = [method[0] for method in proposals]
    assert [type(task) for task in proposal_tasks] == [
        PlanBai,
        PlanMotorpoolAttack,
        PlanArmedRecon,
    ]
    assert [_task_target(task) for task in proposal_tasks] == [
        battle_position,
        motorpool,
        recon_target,
    ]


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

    proposals = list(battle_position_tasks[0].each_valid_method(state))
    proposal_tasks = [method[0] for method in proposals]
    assert [type(task) for task in proposal_tasks] == [PlanBai, PlanArmedRecon]
    assert [_task_target(task) for task in proposal_tasks] == [
        battle_position,
        recon_target,
    ]


# --- FormationAttackBuilder routes motorpool targets to the area waypoint -----


def _build_with_real_build(
    target: object, targets: list[StrikeTarget]
) -> tuple[Any, Any]:
    """Run the real FormationAttackBuilder._build with stub collaborators.

    Returns (layout, waypoint_builder_double) so tests can assert which
    target-waypoint methods the builder chose.
    """
    builder: Any = object.__new__(BaiBuilder)
    # is_helo=True deliberately bypasses _hold_point (HoldZoneGeometry) and
    # refuel planning; is_helo never interacts with the target-waypoint
    # branch under test, so this keeps the double minimal.
    position = Point(-20000.0, 0.0, MagicMock(spec=Terrain))
    builder.flight = SimpleNamespace(
        is_helo=True,
        is_hercules=False,
        flight_type=FlightType.BAI,
        client_count=0,
        coalition=SimpleNamespace(
            air_wing=SimpleNamespace(can_auto_plan=MagicMock(return_value=False))
        ),
        package=SimpleNamespace(
            target=target,
            primary_flight=SimpleNamespace(
                is_helo=True, arrival=SimpleNamespace(position=position)
            ),
            waypoints=SimpleNamespace(join=position, ingress=position, split=position),
        ),
        departure=SimpleNamespace(position=position),
        arrival=SimpleNamespace(position=position),
        divert=None,
    )

    with patch("game.ato.flightplans.formationattack.WaypointBuilder") as wb:
        waypoint_builder = wb.return_value
        waypoint_builder.strike_area = MagicMock(
            return_value=SimpleNamespace(name="AREA")
        )
        waypoint_builder.bai_group = MagicMock(
            return_value=SimpleNamespace(name="TARGET_POINT")
        )
        layout = builder._build(FlightWaypointType.INGRESS_BAI, targets)
    return layout, waypoint_builder


def test_motorpool_target_uses_area_waypoint_despite_unit_targets() -> None:
    tgo = _populated_motorpool({_gut(): 2})
    targets = [StrikeTarget("zone", tgo)]

    layout, waypoint_builder = _build_with_real_build(tgo, targets)

    # The motorpool `or` in FormationAttackBuilder._build routes to the single
    # target-area waypoint even though a non-empty target list was passed.
    assert [waypoint.name for waypoint in layout.targets] == ["AREA"]
    waypoint_builder.strike_area.assert_called_once()
    waypoint_builder.bai_group.assert_not_called()


def test_non_motorpool_target_keeps_per_target_waypoints() -> None:
    cp = MagicMock(spec=ControlPoint)
    cp.captured = Player.RED
    loc = PresetLocation(
        "V", Point(0.0, 0.0, MagicMock(spec=Terrain)), Heading.from_degrees(0.0)
    )
    vehicle_group = VehicleGroupGroundObject(
        "Vehicles", loc, cp, GroupTask.BASE_DEFENSE
    )
    targets = [StrikeTarget("group", vehicle_group)]

    layout, waypoint_builder = _build_with_real_build(vehicle_group, targets)

    # Non-motorpool targets keep the upstream per-target waypoint behavior.
    assert [waypoint.name for waypoint in layout.targets] == ["TARGET_POINT"]
    waypoint_builder.strike_area.assert_not_called()
    waypoint_builder.bai_group.assert_called_once()


# --- Armed recon needs no motorpool-specific planning --------------------------


def test_armed_recon_builder_accepts_motorpool_target() -> None:
    """Armed recon planning is generic over MissionTarget: the builder performs
    no target-type validation and passes no per-unit targets, so a motorpool
    TGO plans through the generic target-area flyover waypoint (the ingress's
    EngageTargetsInZone then makes the AI attack the parked vehicles)."""
    tgo, _ = _motorpool_cp({_gut(): 2}, friendly=False)

    builder = cast(_BuilderTestDouble, object.__new__(ArmedReconBuilder))
    builder.flight = cast(
        _BuilderFlight,
        SimpleNamespace(
            package=SimpleNamespace(target=tgo),
            flight_type=FlightType.ARMED_RECON,
            client_count=0,
        ),
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

    assert captured["ingress"] is FlightWaypointType.INGRESS_ARMED_RECON
    # No per-unit targets: the layout uses the generic target-area waypoint.
    assert captured["targets"] is None
