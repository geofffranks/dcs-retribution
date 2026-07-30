import inspect
from typing import Any
from unittest.mock import MagicMock

from dcs.task import ControlledTask, OrbitAction, RecoveryTanker

from game.ato import FlightType
from game.ato.flightplans.tankerorbitspeed import (
    TANKER_ORBIT_SPEED_KIAS_PROP,
    TANKER_ORBIT_SPEED_MODE_PROP,
)
from game.ato.flightplans.patrolling import PatrollingFlightPlan
from game.ato.flightplans.shiprecoverytanker import RecoveryTankerFlightPlan
from game.missiongenerator.aircraft.aircraftbehavior import AircraftBehavior
from game.missiongenerator.aircraft.waypoints.racetrack import RaceTrackBuilder
from game.utils import knots


def test_ordinary_tanker_uses_slowest_receiver_in_its_package() -> None:
    tanker = MagicMock()
    receiver_fast = MagicMock()
    receiver_fast.unit_type.aar_receiver_speed = knots(320)
    receiver_slow = MagicMock()
    receiver_slow.unit_type.aar_receiver_speed = knots(260)
    tanker.package.flights = [tanker, receiver_fast, receiver_slow]
    tanker.flight_plan.patrol_speed = knots(280)

    builder = object.__new__(RaceTrackBuilder)
    builder.flight = tanker

    assert builder.tanker_orbit_speed() == knots(260)


def test_ordinary_tanker_manual_speed_overrides_package_receivers() -> None:
    tanker = MagicMock()
    tanker.props = {
        TANKER_ORBIT_SPEED_MODE_PROP: "manual",
        TANKER_ORBIT_SPEED_KIAS_PROP: 330,
    }
    receiver = MagicMock()
    receiver.unit_type.aar_receiver_speed = knots(260)
    tanker.package.flights = [tanker, receiver]
    tanker.flight_plan.patrol_speed = knots(280)

    builder = object.__new__(RaceTrackBuilder)
    builder.flight = tanker

    assert builder.tanker_orbit_speed() == knots(330)


def test_ordinary_tanker_without_receiver_metadata_keeps_patrol_baseline() -> None:
    tanker = MagicMock()
    receiver = MagicMock()
    receiver.unit_type.aar_receiver_speed = None
    tanker.package.flights = [tanker, receiver]
    tanker.flight_plan.patrol_speed = knots(280)

    builder = object.__new__(RaceTrackBuilder)
    builder.flight = tanker

    assert builder.tanker_orbit_speed() == knots(280)


def test_recovery_tanker_manual_speed_overrides_baseline() -> None:
    flight = MagicMock()
    flight.props = {
        TANKER_ORBIT_SPEED_MODE_PROP: "manual",
        TANKER_ORBIT_SPEED_KIAS_PROP: 310,
    }

    assert AircraftBehavior.tanker_orbit_speed(flight) == knots(310)


def test_recovery_tanker_without_relationship_keeps_250_kias_baseline() -> None:
    flight = MagicMock()
    flight.props = {}

    assert AircraftBehavior.tanker_orbit_speed(flight) == knots(250)


def test_ordinary_generation_constructs_orbit_action_with_selected_kph() -> None:
    tanker = MagicMock()
    tanker.flight_type = FlightType.REFUELING
    tanker.props = {
        TANKER_ORBIT_SPEED_MODE_PROP: "manual",
        TANKER_ORBIT_SPEED_KIAS_PROP: 330,
    }
    tanker.package.flights = [tanker]
    tanker.flight_plan = MagicMock(spec=PatrollingFlightPlan)
    tanker.flight_plan.patrol_speed = knots(280)
    tanker.flight_plan.patrol_start_time = MagicMock()
    tanker.flight_plan.patrol_end_time = MagicMock()
    tanker.coalition.game.settings.ai_unlimited_fuel = False
    tanker.coalition.game.settings.plugins = {}

    waypoint = MagicMock()
    waypoint.tasks = []
    waypoint.add_task.side_effect = waypoint.tasks.append
    builder: Any = object.__new__(RaceTrackBuilder)
    builder.flight = tanker
    builder.now = MagicMock()
    builder.package = tanker.package
    builder.configure_refueling_actions = MagicMock()
    builder.set_waypoint_tot = MagicMock()
    builder.mission = MagicMock()
    builder.add_tasks(waypoint)

    controlled = next(
        task for task in waypoint.tasks if isinstance(task, ControlledTask)
    )
    assert controlled.params["task"]["params"]["speed"] == int(knots(330).kph) / 3.6


def test_recovery_generation_constructs_recovery_tanker_with_selected_mps() -> None:
    flight = MagicMock()
    flight.props = {
        TANKER_ORBIT_SPEED_MODE_PROP: "manual",
        TANKER_ORBIT_SPEED_KIAS_PROP: 310,
    }
    flight.flight_plan = MagicMock(spec=RecoveryTankerFlightPlan)
    flight.package = MagicMock()
    flight.squadron.coalition.game.conditions.weather.clouds = None
    flight.coalition.game.settings.desired_tanker_on_station_time.total_seconds.return_value = (
        60
    )

    group = MagicMock()
    group_point = MagicMock()
    group_point.tasks = []
    group_point.add_task.side_effect = group_point.tasks.append
    group.points = [group_point]
    carrier_group = MagicMock()
    carrier_group.id = 42
    carrier_group.points = [MagicMock()]

    behavior: Any = object.__new__(AircraftBehavior)
    behavior.configure_refueling = MagicMock()
    behavior.configure_tanker_tacan = MagicMock()
    behavior._get_carrier_group = MagicMock(return_value=carrier_group)
    behavior.configure_recovery(group, flight)

    controlled = next(
        task
        for task in group.points[0].tasks
        if isinstance(task, ControlledTask)
        and task.params["task"]["id"] == "RecoveryTanker"
    )
    assert controlled.params["task"]["params"]["speed"] == knots(310).meters_per_second


def test_tanker_speed_selection_has_no_runtime_retasking_path() -> None:
    source = inspect.getsource(RaceTrackBuilder) + inspect.getsource(AircraftBehavior)
    assert "RefuelingEvent" not in source
    assert "retask" not in source.lower()
    assert source.count("select_tanker_orbit_speed") == 2


def test_tanker_speed_selection_is_stable_for_repeated_planning_calls() -> None:
    """Planning reads one fixed selection; it never retasks at runtime."""
    flight = MagicMock()
    flight.props = {
        TANKER_ORBIT_SPEED_MODE_PROP: "manual",
        TANKER_ORBIT_SPEED_KIAS_PROP: 315,
    }
    flight.package.flights = [flight]
    flight.flight_plan.patrol_speed = knots(280)

    builder = object.__new__(RaceTrackBuilder)
    builder.flight = flight

    assert builder.tanker_orbit_speed() == knots(315)
    assert builder.tanker_orbit_speed() == knots(315)
