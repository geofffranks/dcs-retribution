from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from PIL import Image
from dcs.mapping import Point
from dcs.terrain.caucasus.caucasus import Caucasus

from game.ato.flightwaypointtype import FlightWaypointType

from game.dcs.beacons import Beacon, BeaconType
from game.missiongenerator.aircraft.flightdata import FlightData
from game.missiongenerator.helicopter_navigation import RouteLeg, Station
from game.theater.conflicttheater import ConflictTheater
from game.missiongenerator.helicopter_navigation_pages import (
    NavigationMapPage,
    StationDirectoryPage,
    generate_navigation_pages,
)

_TERRAIN = Caucasus()
_FLIGHT = cast(
    FlightData,
    SimpleNamespace(
        callsign="TEST 1", aircraft_type=SimpleNamespace(variant_id="UH-1H")
    ),
)


def _leg(index: int, start_x: float, end_x: float) -> RouteLeg:
    return RouteLeg(
        index,
        f"WP{index}",
        f"WP{index + 1}",
        Point(start_x, 0, _TERRAIN),
        Point(end_x, 0, _TERRAIN),
    )


def _station(index: int, x: float, y: float) -> Station:
    beacon = Beacon(
        f"Fixture Station {index}",
        f"F{index:02}",
        BeaconType.BEACON_TYPE_VOR,
        110_000_000,
        None,
        x,
        y,
    )
    return Station(f"N{index:03}", str(index), beacon, f"WP1-WP2", 0, 0, 1)


def _offline_basemap(
    extent: object, width: int, height: int, *, cache_dir: Path
) -> Image.Image:
    del extent, cache_dir
    return Image.new("RGB", (width, height), (225, 215, 195))


def test_actual_page_writer_renders_short_route_and_dense_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import game.missiongenerator.helicopter_navigation_pages as pages_module

    monkeypatch.setattr(pages_module, "render_basemap", _offline_basemap)
    monkeypatch.setattr(pages_module, "tile_cache_dir", lambda: tmp_path)
    legs = [_leg(1, 0, 20_000)]
    stations = [
        _station(index, 1_000 + index * 100, index * 80) for index in range(1, 31)
    ]
    map_page = NavigationMapPage(
        _FLIGHT,
        legs,
        stations,
        _TERRAIN,
        False,
        1,
        1,
        {0: 0, 1: 1},
    )
    map_path = tmp_path / "short-route.png"
    map_page.write(map_path)
    image = Image.open(map_path)
    assert image.size == (960, 1080)
    map_text = map_path.with_suffix(".txt").read_text(encoding="utf-8")
    assert "->" in map_text
    assert "→" not in map_text and "↑" not in map_text
    assert image.getpixel((900, 50)) != image.getpixel((900, 500))

    directory = StationDirectoryPage(_FLIGHT, stations, "Fixture coverage", False, 1, 2)
    directory_path = tmp_path / "dense-stations.png"
    directory.write(directory_path)
    image = Image.open(directory_path)
    assert image.size == (960, 1080)
    text = directory_path.with_suffix(".txt").read_text(encoding="utf-8")
    assert all(station.reference in text for station in stations)

    class FakeBeacons:
        @staticmethod
        def _dict_for_theater(theater: Caucasus) -> dict[str, Beacon]:
            del theater
            return {
                str(index): station.beacon for index, station in enumerate(stations)
            }

        @staticmethod
        def metadata_for_theater(theater: Caucasus) -> dict[str, object]:
            del theater
            return {"complete": True, "total_source_records": len(stations)}

    monkeypatch.setattr(pages_module, "Beacons", FakeBeacons)
    waypoints = [
        SimpleNamespace(
            name="WP1",
            waypoint_type=FlightWaypointType.TAKEOFF,
            position=Point(0, 0, _TERRAIN),
        ),
        SimpleNamespace(
            name="WP2",
            waypoint_type=FlightWaypointType.LANDING_POINT,
            position=Point(20_000, 0, _TERRAIN),
        ),
    ]
    generated = pages_module.generate_navigation_pages(
        cast(
            FlightData,
            SimpleNamespace(
                callsign="TEST 1",
                aircraft_type=SimpleNamespace(variant_id="UH-1H"),
                waypoints=waypoints,
            ),
        ),
        cast(ConflictTheater, SimpleNamespace(terrain=_TERRAIN)),
        25,
        False,
    )
    reference_pages = [
        page for page in generated if isinstance(page, NavigationMapPage)
    ]
    map_references = [
        station.reference for page in reference_pages for station in page.stations
    ]
    assert set(map_references) == {station.reference for station in stations}
    assert max(len(page.stations) for page in reference_pages) <= 5
    route_pages = [page for page in reference_pages if page.title == "ROUTE MAP"]
    assert len(route_pages) == 1
    assert route_pages[0].total == 1
    assert len(route_pages[0].stations) == 5
    reference_sheets = [
        page for page in reference_pages if page.title.startswith("STATION REFERENCES")
    ]
    assert len(reference_sheets) == 6
    assert all(page.total == 6 for page in reference_sheets)
    assert [page.title for page in route_pages] == ["ROUTE MAP"]
    route_path = tmp_path / "overview.png"
    route_pages[0].write(route_path)
    overview_text = route_path.with_suffix(".txt").read_text(encoding="utf-8")
    assert all(
        station.reference in overview_text for station in route_pages[0].stations
    )


def test_generated_180km_route_has_bounded_continuous_detail_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import game.missiongenerator.helicopter_navigation_pages as pages_module

    monkeypatch.setattr(pages_module, "render_basemap", _offline_basemap)
    monkeypatch.setattr(pages_module, "tile_cache_dir", lambda: tmp_path)
    waypoints = [
        SimpleNamespace(
            name=name,
            waypoint_type=(
                FlightWaypointType.TAKEOFF if index == 0 else FlightWaypointType.NAV
            ),
            position=Point(index * 180_000, 0, _TERRAIN),
        )
        for index, name in enumerate(("Start", "Finish"))
    ]

    class EmptyBeacons:
        @staticmethod
        def _dict_for_theater(theater: Caucasus) -> dict[str, Beacon]:
            del theater
            return {}

        @staticmethod
        def metadata_for_theater(theater: Caucasus) -> dict[str, object]:
            del theater
            return {"complete": True, "total_source_records": 0}

    monkeypatch.setattr(pages_module, "Beacons", EmptyBeacons)
    flight = cast(
        FlightData,
        SimpleNamespace(
            callsign="TEST 1",
            aircraft_type=SimpleNamespace(variant_id="UH-1H"),
            waypoints=waypoints,
        ),
    )
    generated = pages_module.generate_navigation_pages(
        flight, cast(ConflictTheater, SimpleNamespace(terrain=_TERRAIN)), 0, False
    )
    route_pages = [
        page
        for page in generated
        if isinstance(page, NavigationMapPage) and page.title == "ROUTE MAP"
    ]
    assert len(route_pages) >= 3
    details = route_pages[1:]
    segments = [leg for page in details for leg in page.legs]
    assert max(leg.end.distance_to_point(leg.start) for leg in segments) <= 60_000
    assert segments[0].start == waypoints[0].position
    assert segments[-1].end == waypoints[-1].position
    assert all(
        sum(leg.end.distance_to_point(leg.start) for leg in page.legs) <= 120_000
        for page in details
    )
    assert all(
        left.end == right.start or left == right
        for left, right in zip(segments, segments[1:])
    )
    details[0].write(tmp_path / "generated-180km-detail.png")


def test_actual_page_writer_renders_long_route_detail_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import game.missiongenerator.helicopter_navigation_pages as pages_module

    monkeypatch.setattr(pages_module, "render_basemap", _offline_basemap)
    monkeypatch.setattr(pages_module, "tile_cache_dir", lambda: tmp_path)
    legs = [_leg(index, index * 15_000, (index + 1) * 15_000) for index in range(1, 10)]
    page = NavigationMapPage(
        _FLIGHT,
        legs[3:8],
        [_station(50, 70_000, 3_000)],
        _TERRAIN,
        True,
        2,
        3,
        {index: index for index in range(11)},
    )
    path = tmp_path / "long-route-detail.png"
    page.write(path)
    assert Image.open(path).size == (960, 1080)
    content = path.with_suffix(".txt").read_text(encoding="utf-8")
    assert "WP4" in content and "WP9" in content


def test_far_endpoint_station_is_not_in_first_detail_extent_but_remains_listed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import game.missiongenerator.helicopter_navigation_pages as pages_module

    monkeypatch.setattr(pages_module, "render_basemap", _offline_basemap)
    monkeypatch.setattr(pages_module, "tile_cache_dir", lambda: tmp_path)
    stations = [_station(1, 30_000, 0), _station(2, 600_000, 0)]

    class RouteBeacons:
        @staticmethod
        def _dict_for_theater(theater: Caucasus) -> dict[str, Beacon]:
            del theater
            return {station.beacon_id: station.beacon for station in stations}

        @staticmethod
        def metadata_for_theater(theater: Caucasus) -> dict[str, object]:
            del theater
            return {"complete": True, "total_source_records": len(stations)}

    monkeypatch.setattr(pages_module, "Beacons", RouteBeacons)
    waypoints = [
        SimpleNamespace(
            name="Start",
            waypoint_type=FlightWaypointType.TAKEOFF,
            position=Point(0, 0, _TERRAIN),
        ),
        SimpleNamespace(
            name="Finish",
            waypoint_type=FlightWaypointType.LANDING_POINT,
            position=Point(600_000, 0, _TERRAIN),
        ),
    ]
    flight = cast(
        FlightData,
        SimpleNamespace(
            callsign="TEST 1",
            aircraft_type=SimpleNamespace(variant_id="UH-1H"),
            waypoints=waypoints,
        ),
    )
    generated = pages_module.generate_navigation_pages(
        flight, cast(ConflictTheater, SimpleNamespace(terrain=_TERRAIN)), 25, False
    )
    route_pages = [
        page
        for page in generated
        if isinstance(page, NavigationMapPage) and page.title == "ROUTE MAP"
    ]
    assert len(route_pages) > 2
    first_detail = route_pages[1]
    assert [station.beacon_id for station in first_detail.stations] == ["1"]
    assert (
        pages_module._extent_for(
            first_detail.legs, first_detail.stations, _TERRAIN
        ).span_x_m
        < 200_000
    )
    reference_pages = [
        page
        for page in generated
        if isinstance(page, NavigationMapPage)
        and page.title.startswith("STATION REFERENCES")
    ]
    directory_pages = [
        page for page in generated if isinstance(page, StationDirectoryPage)
    ]
    assert {s.beacon_id for page in reference_pages for s in page.stations} == {
        "1",
        "2",
    }
    assert {s.beacon_id for page in directory_pages for s in page.stations} == {
        "1",
        "2",
    }


def test_missing_station_catalog_keeps_route_and_marks_directory_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import game.missiongenerator.helicopter_navigation_pages as pages_module

    monkeypatch.setattr(pages_module, "render_basemap", _offline_basemap)
    monkeypatch.setattr(pages_module, "tile_cache_dir", lambda: tmp_path)

    class MissingBeacons:
        @staticmethod
        def _dict_for_theater(theater: Caucasus) -> dict[str, Beacon]:
            del theater
            raise RuntimeError("catalog missing")

        @staticmethod
        def metadata_for_theater(theater: Caucasus) -> dict[str, object]:
            del theater
            raise RuntimeError("catalog missing")

    monkeypatch.setattr(pages_module, "Beacons", MissingBeacons)
    waypoints = [
        SimpleNamespace(
            name="Start",
            waypoint_type=FlightWaypointType.TAKEOFF,
            position=Point(0, 0, _TERRAIN),
        ),
        SimpleNamespace(
            name="Finish",
            waypoint_type=FlightWaypointType.LANDING_POINT,
            position=Point(20_000, 0, _TERRAIN),
        ),
    ]
    flight = cast(
        FlightData,
        SimpleNamespace(
            callsign="TEST 1",
            aircraft_type=SimpleNamespace(variant_id="UH-1H"),
            waypoints=waypoints,
        ),
    )
    generated = pages_module.generate_navigation_pages(
        flight, cast(ConflictTheater, SimpleNamespace(terrain=_TERRAIN)), 25, False
    )
    assert any(
        isinstance(page, NavigationMapPage) and page.title == "ROUTE MAP"
        for page in generated
    )
    directories = [page for page in generated if isinstance(page, StationDirectoryPage)]
    assert len(directories) == 1
    assert (
        "unavailable" in directories[0].notice.lower()
        or "unknown" in directories[0].notice.lower()
    )
