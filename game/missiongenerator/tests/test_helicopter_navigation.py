from __future__ import annotations

import math

from dcs.mapping import Point
from dcs.terrain.caucasus.caucasus import Caucasus

from game.ato.flightwaypoint import FlightWaypoint
from game.ato.flightwaypointtype import FlightWaypointType
from game.dcs.beacons import Beacon, BeaconType
from game.missiongenerator.helicopter_navigation import (
    RouteLeg,
    Station,
    coverage_notice,
    route_legs,
    select_stations,
)
from game.missiongenerator.helicopter_navigation_pages import (
    detail_leg_chunks,
    split_long_legs,
    stations_for_detail,
)
from game.missiongenerator.helicopter_navigation_pages import NavigationMapPage
from game.missiongenerator.kneeboard import KneeboardPage as IntegratedKneeboardPage
from game.missiongenerator.kneeboard_page import KneeboardPage


def _beacon(
    name: str,
    kind: BeaconType,
    x: float | None,
    y: float | None,
    hertz: int = 110_000_000,
) -> Beacon:
    return Beacon(name, "ABC", kind, hertz, None, x, y)


_TERRAIN = Caucasus()


def _point(x: float, y: float) -> Point:
    return Point(x, y, _TERRAIN)


def test_station_selection_uses_finite_route_segment_and_endpoint_neighborhood() -> (
    None
):
    route = [RouteLeg(1, "A", "B", _point(0, 0), _point(10_000, 0))]
    stations = select_stations(
        [
            ("near", _beacon("Near", BeaconType.BEACON_TYPE_VOR, 10_500, 0)),
            ("far", _beacon("Far", BeaconType.BEACON_TYPE_VOR, 11_000, 0)),
        ],
        route,
        0.3,
        _TERRAIN,
    )
    assert [station.beacon_id for station in stations] == ["near"]
    assert stations[0].nearest_leg == "A-B"


def test_zero_corridor_selects_exact_route_and_distinct_ids_not_same_callsign() -> None:
    route = [RouteLeg(1, "A", "B", _point(0, 0), _point(100, 0))]
    stations = select_stations(
        [
            ("on", _beacon("On", BeaconType.BEACON_TYPE_VOR_DME, 50, 0)),
            ("off", _beacon("Off", BeaconType.BEACON_TYPE_VOR, 50, 0.1)),
            ("tacan", _beacon("Tacan", BeaconType.BEACON_TYPE_TACAN, 50, 0)),
            ("same-call", _beacon("Distinct", BeaconType.BEACON_TYPE_VORTAC, 60, 0)),
            ("missing", _beacon("Missing", BeaconType.BEACON_TYPE_VOR, None, None)),
            ("nan", _beacon("NaN", BeaconType.BEACON_TYPE_VOR, float("nan"), 1)),
        ],
        route,
        0,
        _TERRAIN,
    )
    assert [station.beacon_id for station in stations] == ["on", "same-call"]


def test_single_waypoint_route_is_preserved_as_a_zero_length_leg() -> None:
    route = route_legs(
        [FlightWaypoint("Only point", FlightWaypointType.TAKEOFF, _point(12, 34))]
    )
    assert len(route) == 1
    assert route[0].start == route[0].end == _point(12, 34)
    assert route[0].start_name == route[0].end_name == "Only point"


def test_supplemental_points_are_not_connected_but_flown_route_remains_continuous() -> (
    None
):
    route = route_legs(
        [
            FlightWaypoint("A", FlightWaypointType.TAKEOFF, _point(0, 0)),
            FlightWaypoint(
                "Target annotation", FlightWaypointType.TARGET_POINT, _point(100, 100)
            ),
            FlightWaypoint("B", FlightWaypointType.LANDING_POINT, _point(200, 0)),
        ]
    )
    assert len(route) == 1
    assert route[0].start_name == "A"
    assert route[0].end_name == "B"
    assert route[0].start_waypoint_index == 0
    assert route[0].end_waypoint_index == 2


def test_divert_is_supplemental_and_never_creates_a_fabricated_route_leg() -> None:
    route = route_legs(
        [
            FlightWaypoint("A", FlightWaypointType.TAKEOFF, _point(0, 0)),
            FlightWaypoint("Divert", FlightWaypointType.DIVERT, _point(50_000, 50_000)),
            FlightWaypoint("B", FlightWaypointType.LANDING_POINT, _point(100, 0)),
        ]
    )
    assert len(route) == 1
    assert (route[0].start_name, route[0].end_name) == ("A", "B")
    assert (route[0].start_waypoint_index, route[0].end_waypoint_index) == (0, 2)


def test_repeated_names_keep_distinct_original_waypoint_occurrences() -> None:
    route = route_legs(
        [
            FlightWaypoint("WP", FlightWaypointType.TAKEOFF, _point(0, 0)),
            FlightWaypoint("WP", FlightWaypointType.NAV, _point(100, 0)),
            FlightWaypoint("WP", FlightWaypointType.NAV, _point(200, 0)),
        ]
    )
    assert [(leg.start_waypoint_index, leg.end_waypoint_index) for leg in route] == [
        (0, 1),
        (1, 2),
    ]


def test_station_encounter_uses_list_order_not_external_leg_number() -> None:
    route = [
        RouteLeg(20, "First", "Second", _point(0, 0), _point(100, 0)),
        RouteLeg(3, "Later", "End", _point(1000, 0), _point(1100, 0)),
    ]
    stations = select_stations(
        [
            ("later", _beacon("Later", BeaconType.BEACON_TYPE_VOR, 1000, 0)),
            ("first", _beacon("First", BeaconType.BEACON_TYPE_VOR, 50, 0)),
        ],
        route,
        0,
        _TERRAIN,
    )
    assert [station.beacon_id for station in stations] == ["first", "later"]


def test_long_single_leg_subdivision_preserves_route_endpoints_without_phantom_waypoints() -> (
    None
):
    source = RouteLeg(7, "Start", "Destination", _point(0, 0), _point(180_000, 0))
    segments = split_long_legs([source], max_length_m=60_000)

    assert len(segments) == 3
    assert all(segment.index == 7 for segment in segments)
    assert segments[0].start_name == "Start"
    assert segments[-1].end_name == "Destination"
    assert all(segment.end_name == "CONTINUED" for segment in segments[:-1])
    assert all(left.end == right.start for left, right in zip(segments, segments[1:]))
    assert all(
        segment.end.distance_to_point(segment.start) <= 60_000 for segment in segments
    )
    assert {segment.start_name for segment in segments} <= {"Start", "CONTINUED"}
    assert {segment.end_name for segment in segments} <= {"Destination", "CONTINUED"}
    station = Station(
        "N777",
        "long-leg-station",
        _beacon("Long leg station", BeaconType.BEACON_TYPE_VOR, 90_000, 1_000),
        "Start-Destination",
        1_000,
        0.5,
        source_leg_index := segments[0].index,
    )
    chunks = detail_leg_chunks(segments, max_legs=2)
    assert chunks
    assert all(
        sum(leg.end.distance_to_point(leg.start) for leg in chunk) <= 120_000
        for chunk in chunks
    )
    station_windows = [
        chunk for chunk in chunks if stations_for_detail([station], chunk, 25_000)
    ]
    assert station_windows
    assert all(
        stations_for_detail([station], chunk, 25_000) == [station]
        for chunk in station_windows
    )
    remote_station = Station(
        "N778",
        "remote-station",
        _beacon("Remote station", BeaconType.BEACON_TYPE_VOR, 600_000, 0),
        "Start-Destination",
        0,
        10,
        source_leg_index,
    )
    assert stations_for_detail([remote_station], chunks[0], 25_000) == []


def test_long_detail_windows_cover_all_legs_and_stations() -> None:
    legs = [
        RouteLeg(
            index, f"W{index}", f"W{index+1}", _point(index, 0), _point(index + 1, 0)
        )
        for index in range(1, 14)
    ]
    chunks = detail_leg_chunks(legs, max_legs=5)
    assert chunks[0][0] == legs[0]
    assert chunks[-1][-1] == legs[-1]
    assert all(left[-1] == right[0] for left, right in zip(chunks, chunks[1:]))
    stations = [
        Station(
            f"N{i}",
            str(i),
            _beacon(str(i), BeaconType.BEACON_TYPE_VOR, i + 0.5, 0),
            "",
            0,
            0,
            i,
        )
        for i in range(1, 14)
    ]
    included = {
        station.leg_index
        for chunk in chunks
        for station in stations_for_detail(stations, chunk)
    }
    assert included == set(range(1, 14))


def test_coverage_notice_separates_unknown_incomplete_and_complete_empty() -> None:
    assert "unknown" in coverage_notice(None, 0, 0, 0).lower()
    assert "incomplete" in coverage_notice({"complete": False}, 1, 2, 0).lower()
    assert coverage_notice({"complete": True}, 2, 2, 0) == "No nearby VOR/NDB stations"
    assert "unverified" in coverage_notice(
        {"complete": True, "terrain_identity_status": "unconfirmed"}, 0, 0, 0
    )


def test_detail_windows_advance_for_rotated_and_single_segment_limits() -> None:
    start = _point(123456.789, 234567.891)
    angle = math.radians(29)
    end = _point(
        start.x + 180_000 * math.cos(angle), start.y + 180_000 * math.sin(angle)
    )
    segments = split_long_legs([RouteLeg(0, "Start", "End", start, end)])
    for limit in (120_000.0, 60_001.0):
        chunks = detail_leg_chunks(segments, max_extent_m=limit)
        assert 1 <= len(chunks) <= len(segments)
        assert chunks[0][0].start == start
        assert chunks[-1][-1].end == end
        assert all(
            sum(leg.start.distance_to_point(leg.end) for leg in chunk) <= limit + 1e-6
            for chunk in chunks
        )
        assert {id(leg) for chunk in chunks for leg in chunk} == {
            id(leg) for leg in segments
        }


def test_navigation_pages_use_shared_kneeboard_page_base() -> None:
    assert KneeboardPage is IntegratedKneeboardPage
    assert issubclass(NavigationMapPage, IntegratedKneeboardPage)
