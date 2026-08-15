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
from game.ato.flightplans.airassault import (
    AirAssaultLayout,
    Builder as AirAssaultBuilder,
)
from game.ato.flightplans.bai import Builder as BaiBuilder
from game.ato.flightplans.formationattack import FormationAttackLayout
from game.ato.flightplans.strike import Builder as StrikeBuilder
from game.ato.flightplans.waypointbuilder import StrikeTarget
from game.data.groups import GroupTask
from game.missiongenerator.motorpoolpopulator import MotorpoolPopulator
from game.dcs.groundunittype import GroundUnitType
from game.theater.controlpoint import ControlPoint, ControlPointType
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


def test_motorpool_layouts_have_no_targets_when_reserve_is_empty() -> None:
    tgo = _populated_motorpool({_gut(): 0})

    # Upstream semantics: with no rendered groups the builders pass an empty
    # target list, so the layout falls back to a single target-area waypoint.
    assert _capture_builder_targets(BaiBuilder, tgo) == []
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


def test_motorpool_mission_types_offer_air_assault_not_bai() -> None:
    tgo, _ = _motorpool_cp({_gut(): 2}, friendly=False)
    mission_types = list(tgo.mission_types(Player.BLUE))
    assert FlightType.AIR_ASSAULT in mission_types
    assert FlightType.BAI not in mission_types
    # STRIKE remains available through the inherited generic TGO path.
    assert FlightType.STRIKE in mission_types


def test_friendly_motorpool_offers_no_attack_mission_types() -> None:
    tgo, _ = _motorpool_cp({_gut(): 2}, friendly=True)
    mission_types = list(tgo.mission_types(Player.BLUE))
    # Only defensive missions (e.g. BARCAP) may be planned against friendly TGOs.
    assert FlightType.AIR_ASSAULT not in mission_types
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


def test_motorpool_attack_proposes_air_assault_plus_escorts() -> None:
    tgo = _motorpool_target(8)
    task = PlanMotorpoolAttack(tgo)
    task.propose_flights()
    flights = list(task.flights)
    assert [flight.task for flight in flights] == [
        FlightType.AIR_ASSAULT,
        FlightType.SEAD_ESCORT,
        FlightType.ESCORT,
        FlightType.SEAD_SWEEP,
    ]
    air_assault = flights[0]
    assert air_assault.num_aircraft == 2


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


def test_attack_battle_positions_yields_single_motorpool_air_assault() -> None:
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


def test_plan_next_action_orders_motorpool_air_assault_before_armed_recon() -> None:
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
    assert len(top_level_methods) == 11

    proposals = list(battle_position_tasks[0].each_valid_method(state))
    proposal_tasks = [method[0] for method in proposals]
    assert [type(task) for task in proposal_tasks] == [PlanBai, PlanArmedRecon]
    assert [_task_target(task) for task in proposal_tasks] == [
        battle_position,
        recon_target,
    ]


# --- Air assault needs no motorpool-specific planning --------------------------


def _air_assault_builder(tgo: MotorpoolGroundObject) -> AirAssaultBuilder:
    terrain = MagicMock(spec=Terrain)
    departure_position = Point(-60000.0, 40000.0, terrain)
    ingress_position = Point(-20000.0, 0.0, terrain)

    doctrine = SimpleNamespace(
        min_combat_altitude=feet(1000), max_combat_altitude=feet(30000)
    )
    settings = SimpleNamespace(heli_combat_alt_agl=500)
    theater = SimpleNamespace(nearest_land_pos=lambda pos: pos)
    coalition = SimpleNamespace(
        doctrine=doctrine,
        opponent=SimpleNamespace(threat_zone=None),
        nav_mesh=SimpleNamespace(shortest_path=lambda a, b: [a, b]),
        bullseye=SimpleNamespace(position=Point(0.0, 0.0, terrain)),
        game=SimpleNamespace(settings=settings, theater=theater),
    )

    package = SimpleNamespace(
        target=tgo,
        waypoints=SimpleNamespace(
            ingress=ingress_position,
            initial=ingress_position,
            join=ingress_position,
            split=departure_position,
            refuel=ingress_position,
        ),
    )
    flight: Any = SimpleNamespace(
        is_helo=True,
        is_hercules=False,
        coalition=coalition,
        package=package,
        departure=SimpleNamespace(
            cptype=ControlPointType.LHA_GROUP, position=departure_position
        ),
        arrival=SimpleNamespace(position=departure_position),
        divert=None,
        unit_type=SimpleNamespace(
            preferred_cruise_altitude=feet(2000),
            preferred_combat_altitude=feet(1000),
        ),
        plane_altitude_offset=0,
    )

    builder: Any = object.__new__(AirAssaultBuilder)
    builder.flight = flight
    builder.settings = settings
    return builder


def test_air_assault_layout_builds_against_motorpool_target() -> None:
    """Air assault planning is generic over MissionTarget: a motorpool TGO must
    produce a valid layout — drop-off short of the depot and the CTLD target
    zone at the depot — without any motorpool-specific planning code."""
    tgo, _ = _motorpool_cp({_gut(): 2}, friendly=False)

    layout = _air_assault_builder(tgo).layout()
    assert isinstance(layout, AirAssaultLayout)

    # Troops are landed a combat drop away from the depot, not on it.
    assert layout.drop_off is not None
    drop_distance = tgo.position.distance_to_point(layout.drop_off.position)
    assert 900 <= drop_distance <= 1500

    # The assault area (CTLD target zone the troops advance to) is the depot.
    assert len(layout.targets) == 1
    assert layout.targets[0].position == tgo.position
    assert layout.targets[0].waypoint_type is FlightWaypointType.TARGET_GROUP_LOC

    # The layout is flyable end to end, with the drop-off before the assault
    # area so transports land before the troops advance.
    waypoints = list(layout.iter_waypoints())
    assert layout.drop_off in waypoints
    assert layout.targets[0] in waypoints
    assert waypoints.index(layout.drop_off) < waypoints.index(layout.targets[0])
