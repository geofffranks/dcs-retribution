"""Map and station-directory pages for player helicopter flights."""

from __future__ import annotations

import logging
import math
from pathlib import Path

from PIL import Image, ImageDraw
from dcs.mapping import Point
from dcs.terrain.terrain import Terrain

from game.ato.flightwaypoint import FlightWaypoint
from game.ato.flightwaypointtype import FlightWaypointType
from game.dcs.beacons import BeaconType, Beacons
from game.missiongenerator.aircraft.flightdata import FlightData
from game.missiongenerator.helicopter_navigation import (
    RouteLeg,
    Station,
    coverage_notice,
    route_legs,
    select_stations,
)
from game.missiongenerator.kneeboard_page import KneeboardPage
from game.missiongenerator.kneeboard_recon._fonts import load_font
from game.missiongenerator.kneeboard_recon.basemap import render_basemap
from game.missiongenerator.kneeboard_recon.extent import MapExtent, aspect_correct
from game.missiongenerator.kneeboard_recon.projection import Projector
from game.persistency import tile_cache_dir
from game.theater.conflicttheater import ConflictTheater
from game.utils import nautical_miles

logger = logging.getLogger(__name__)
PAGE_SIZE = (960, 1080)
MAP_SIZE = (960, 850)
DIRECTORY_ROWS = 27


def detail_leg_chunks(
    legs: list[RouteLeg], max_legs: int = 5, max_extent_m: float = 120_000.0
) -> list[list[RouteLeg]]:
    """Return route-ordered detail windows bounded by leg count and distance."""
    if max_legs < 2 or max_extent_m <= 0:
        raise ValueError("detail windows need at least two legs and a positive extent")
    lengths = [
        math.hypot(leg.end.x - leg.start.x, leg.end.y - leg.start.y) for leg in legs
    ]
    if (
        len(legs) <= max_legs
        and sum(lengths) <= max_extent_m
        and all(
            leg.start_name != "CONTINUED" and leg.end_name != "CONTINUED"
            for leg in legs
        )
    ):
        return []
    chunks: list[list[RouteLeg]] = []
    start = 0
    while start < len(legs):
        end = start
        extent = 0.0
        while (
            end < len(legs)
            and end - start < max_legs
            and (
                extent + lengths[end] <= max_extent_m
                or math.isclose(extent + lengths[end], max_extent_m, rel_tol=1e-12)
            )
        ):
            extent += lengths[end]
            end += 1
        if end == start:
            end += 1
        chunks.append(legs[start:end])
        if end >= len(legs):
            break
        # Overlap a leg only when doing so still advances the window. Single-leg
        # windows already share their boundary point with the following segment.
        start = max(start + 1, end - 1)
    return chunks


def split_long_legs(
    legs: list[RouteLeg], max_length_m: float = 60_000.0
) -> list[RouteLeg]:
    """Subdivide long legs without assigning waypoint numbers to interpolated points."""
    if max_length_m <= 0:
        raise ValueError("maximum detail leg length must be positive")
    result: list[RouteLeg] = []
    for leg in legs:
        distance = math.hypot(leg.end.x - leg.start.x, leg.end.y - leg.start.y)
        segments = max(1, math.ceil(distance / max_length_m))
        previous = leg.start
        for part in range(1, segments + 1):
            ratio = part / segments
            endpoint = leg.end if part == segments else leg.start.lerp(leg.end, ratio)
            start_name = leg.start_name if part == 1 else "CONTINUED"
            end_name = leg.end_name if part == segments else "CONTINUED"
            result.append(
                RouteLeg(
                    leg.index,
                    start_name,
                    end_name,
                    previous,
                    endpoint,
                    leg.start_waypoint_index if part == 1 else None,
                    leg.end_waypoint_index if part == segments else None,
                )
            )
            previous = endpoint
    return result


def stations_for_detail(
    stations: list[Station], legs: list[RouteLeg], max_distance_m: float = 0.0
) -> list[Station]:
    """Return stations spatially associated with this finite route window."""
    selected: list[Station] = []
    for station in stations:
        beacon = station.beacon
        if beacon.position_x is None or beacon.position_y is None:
            continue
        for leg in legs:
            dx, dy = leg.end.x - leg.start.x, leg.end.y - leg.start.y
            length_squared = dx * dx + dy * dy
            ratio = (
                0.0
                if length_squared == 0
                else max(
                    0.0,
                    min(
                        1.0,
                        (
                            (beacon.position_x - leg.start.x) * dx
                            + (beacon.position_y - leg.start.y) * dy
                        )
                        / length_squared,
                    ),
                )
            )
            nearest_x = leg.start.x + ratio * dx
            nearest_y = leg.start.y + ratio * dy
            if (
                math.hypot(beacon.position_x - nearest_x, beacon.position_y - nearest_y)
                <= max_distance_m
            ):
                selected.append(station)
                break
    return selected


def _point_for_station(station: Station, terrain: Terrain) -> Point | None:
    beacon = station.beacon
    if beacon.position_x is None or beacon.position_y is None:
        return None
    return Point(beacon.position_x, beacon.position_y, terrain)


def _extent_for(
    legs: list[RouteLeg], stations: list[Station], terrain: Terrain
) -> MapExtent:
    points = [point for leg in legs for point in (leg.start, leg.end)]
    points.extend(
        point
        for station in stations
        if (point := _point_for_station(station, terrain)) is not None
    )
    if not points:
        raise ValueError("cannot render a route map without a usable route")
    min_x, max_x = min(point.x for point in points), max(point.x for point in points)
    min_y, max_y = min(point.y for point in points), max(point.y for point in points)
    margin = max(max_x - min_x, max_y - min_y, 5_000.0) * 0.12
    extent = MapExtent(
        min_x - margin, max_x + margin, min_y - margin, max_y + margin, terrain
    )
    return aspect_correct(extent, *MAP_SIZE)


def _station_row(station: Station) -> str:
    beacon = station.beacon
    is_vor = beacon.beacon_type in {
        BeaconType.BEACON_TYPE_VOR,
        BeaconType.BEACON_TYPE_VOR_DME,
        BeaconType.BEACON_TYPE_VORTAC,
    }
    category = "VOR" if is_vor else "NDB"
    frequency = (
        "unavailable"
        if beacon.hertz is None
        else (
            f"{beacon.hertz / 1_000_000:.3f} MHz"
            if is_vor
            else f"{beacon.hertz / 1_000:.1f} kHz"
        )
    )
    return f"{station.reference}  {beacon.name} / {beacon.callsign}  {category}  {frequency}  {station.nearest_leg}"


class NavigationMapPage(KneeboardPage):
    def __init__(
        self,
        flight: FlightData,
        legs: list[RouteLeg],
        stations: list[Station],
        terrain: Terrain,
        dark: bool,
        index: int,
        total: int,
        waypoint_numbers: dict[int, int],
        title: str = "ROUTE MAP",
    ) -> None:
        self.flight = flight
        self.legs = legs
        self.stations = stations
        self.terrain = terrain
        self.dark = dark
        self.index = index
        self.total = total
        self.waypoint_numbers = waypoint_numbers
        self.title = title

    def write(self, path: Path) -> None:
        flight = self.flight
        extent = _extent_for(self.legs, self.stations, self.terrain)
        background = (10, 5, 5) if self.dark else (210, 210, 210)
        image = Image.new("RGB", PAGE_SIZE, background)
        basemap = render_basemap(extent, *MAP_SIZE, cache_dir=tile_cache_dir())
        image.paste(basemap.resize(MAP_SIZE), (0, 0))
        draw = ImageDraw.Draw(image)
        projector = Projector(extent, *MAP_SIZE)
        fg = (235, 235, 235) if self.dark else (15, 15, 15)
        draw.line((900, 95, 900, 45), fill=fg, width=4)
        draw.polygon([(900, 32), (891, 48), (909, 48)], fill=fg)
        draw.text((884, 100), "N", font=load_font(20, bold=True), fill=fg)
        font = load_font(18)
        route_text: list[str] = []
        for leg in self.legs:
            start, end = projector.project(leg.start), projector.project(leg.end)
            draw.line((start, end), fill=(200, 30, 30), width=4)
            if start != end:
                dx, dy = end[0] - start[0], end[1] - start[1]
                length = math.hypot(dx, dy)
                ux, uy = dx / length, dy / length
                mid_x, mid_y = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
                tip = (mid_x + ux * 8, mid_y + uy * 8)
                base = (mid_x - ux * 5, mid_y - uy * 5)
                wing = (-uy * 5, ux * 5)
                draw.polygon(
                    [
                        tip,
                        (base[0] + wing[0], base[1] + wing[1]),
                        (base[0] - wing[0], base[1] - wing[1]),
                    ],
                    fill=(200, 30, 30),
                )
            for pixel, name, waypoint_index in (
                (start, leg.start_name, leg.start_waypoint_index),
                (end, leg.end_name, leg.end_waypoint_index),
            ):
                draw.ellipse(
                    (pixel[0] - 6, pixel[1] - 6, pixel[0] + 6, pixel[1] + 6),
                    fill=(20, 70, 220),
                )
                if name != "CONTINUED":
                    number = (
                        self.waypoint_numbers.get(waypoint_index, 0)
                        if waypoint_index is not None
                        else 0
                    )
                    label = f"{number} {name}" if waypoint_index is not None else name
                    draw.text((pixel[0] + 8, pixel[1] + 5), label, font=font, fill=fg)
            if leg.start_name != "CONTINUED" and leg.end_name != "CONTINUED":
                start_number = (
                    self.waypoint_numbers.get(leg.start_waypoint_index, 0)
                    if leg.start_waypoint_index is not None
                    else ""
                )
                end_number = (
                    self.waypoint_numbers.get(leg.end_waypoint_index, 0)
                    if leg.end_waypoint_index is not None
                    else ""
                )
                route_text.append(
                    f"{start_number} {leg.start_name} -> {end_number} {leg.end_name}"
                )
        for station_index, station in enumerate(self.stations):
            point = _point_for_station(station, self.terrain)
            if point is None:
                continue
            pos = projector.project(point)
            draw.ellipse(
                (pos[0] - 8, pos[1] - 8, pos[0] + 8, pos[1] + 8),
                outline=(255, 190, 0),
                width=3,
            )
            anchor = (MAP_SIZE[0] - 120, 80 + station_index * 28)
            draw.line(
                (pos[0], pos[1], anchor[0] - 4, anchor[1] + 8),
                fill=(255, 190, 0),
                width=2,
            )
            draw.text(anchor, station.reference, font=font, fill=fg)
        draw.rectangle((0, MAP_SIZE[1], PAGE_SIZE[0], PAGE_SIZE[1]), fill=background)
        draw.text(
            (24, 866),
            f"{flight.callsign} | {flight.aircraft_type.variant_id} | {self.title} {self.index}/{self.total}",
            font=load_font(21, bold=True),
            fill=fg,
        )
        draw.text(
            (24, 900),
            "NORTH (grid north)   RED: planned route   BLUE: waypoint   YELLOW: station ref",
            font=font,
            fill=fg,
        )
        scale_nm = max(
            1, int(max(extent.span_x_m, extent.span_y_m) / 5 / nautical_miles(1).meters)
        )
        scale_pixels = projector.meters_to_px(scale_nm * nautical_miles(1).meters)
        scale_pixels = max(1, min(scale_pixels, 400))
        draw.line((24, 948, 24 + scale_pixels, 948), fill=fg, width=4)
        draw.line((24, 941, 24, 955), fill=fg, width=3)
        draw.line((24 + scale_pixels, 941, 24 + scale_pixels, 955), fill=fg, width=3)
        draw.text((34 + scale_pixels, 938), f"{scale_nm} nm", font=font, fill=fg)
        for row, station in enumerate(self.stations):
            label = (
                f"{station.reference}: {station.beacon.name} ({station.nearest_leg})"
            )
            draw.text((24, 970 + row * 18), label, font=load_font(14), fill=fg)
        if not self.stations:
            for row, text in enumerate(route_text[:3]):
                draw.text((24, 970 + row * 24), text, font=load_font(15), fill=fg)
        image.save(path)
        path.with_suffix(".txt").write_text(
            f"{flight.callsign} {self.title} {self.index}/{self.total}\n"
            + "\n".join(route_text)
            + "\n"
            + "\n".join(
                f"{station.reference}: {station.beacon.name} ({station.nearest_leg})"
                for station in self.stations
            ),
            encoding="utf-8",
        )


class StationDirectoryPage(KneeboardPage):
    def __init__(
        self,
        flight: FlightData,
        stations: list[Station],
        notice: str,
        dark: bool,
        page: int,
        pages: int,
    ) -> None:
        self.flight = flight
        self.stations = stations
        self.notice = notice
        self.dark = dark
        self.page = page
        self.pages = pages

    def write(self, path: Path) -> None:
        image = Image.new(
            "RGB", PAGE_SIZE, (10, 5, 5) if self.dark else (210, 210, 210)
        )
        draw = ImageDraw.Draw(image)
        fg = (235, 235, 235) if self.dark else (15, 15, 15)
        font = load_font(16)
        title = f"{self.flight.callsign} | {self.flight.aircraft_type.variant_id} | VOR/NDB {self.page}/{self.pages}"
        draw.text((24, 20), title, font=load_font(24, bold=True), fill=fg)
        draw.text((24, 56), self.notice, font=font, fill=fg)
        draw.text(
            (20, 100),
            "REF  NAME / IDENT  TYPE  FREQUENCY  NEAREST ROUTE LEG",
            font=font,
            fill=fg,
        )
        for index, station in enumerate(self.stations):
            y = 134 + index * 32
            draw.text((20, y), _station_row(station), font=font, fill=fg)
        image.save(path)
        path.with_suffix(".txt").write_text(
            title
            + "\n"
            + self.notice
            + "\n"
            + "\n".join(_station_row(s) for s in self.stations),
            encoding="utf-8",
        )


def generate_navigation_pages(
    flight: FlightData, theater: ConflictTheater, corridor_nmi: float, dark: bool
) -> list[KneeboardPage]:
    legs = route_legs(flight.waypoints)
    if not legs:
        logger.warning("No usable helicopter navigation route for %s", flight.callsign)
        return []
    try:
        catalog = Beacons._dict_for_theater(theater)
        metadata = Beacons.metadata_for_theater(theater)
    except RuntimeError as error:
        logger.warning(
            "Station data unavailable for %s: %s", theater.terrain.name, error
        )
        catalog = {}
        metadata = None
    stations = select_stations(catalog.items(), legs, corridor_nmi, theater.terrain)
    positioned = sum(
        b.position_x is not None and b.position_y is not None for b in catalog.values()
    )
    raw_total = (
        metadata.get("total_source_records", len(catalog))
        if metadata is not None
        else len(catalog)
    )
    total_source = raw_total if isinstance(raw_total, int) else len(catalog)
    notice = coverage_notice(metadata, positioned, total_source, len(stations))
    waypoint_numbers: dict[int, int] = {}
    supplemental = {
        FlightWaypointType.TARGET_POINT,
        FlightWaypointType.TARGET_GROUP_LOC,
        FlightWaypointType.TARGET_SHIP,
        FlightWaypointType.BULLSEYE,
        FlightWaypointType.DIVERT,
    }
    for waypoint_index, waypoint in enumerate(flight.waypoints):
        if waypoint.waypoint_type not in supplemental:
            waypoint_numbers[waypoint_index] = waypoint_index
    detail_legs = split_long_legs(legs)
    details = detail_leg_chunks(detail_legs)
    map_chunks = [legs, *details]
    station_groups = [
        stations[index : index + 5] for index in range(0, len(stations), 5)
    ]
    map_page_total = len(map_chunks)
    pages: list[KneeboardPage] = []
    for index, chunk in enumerate(map_chunks, start=1):
        shown = stations_for_detail(
            stations, chunk, nautical_miles(corridor_nmi).meters
        )[:5]
        pages.append(
            NavigationMapPage(
                flight,
                chunk,
                shown,
                theater.terrain,
                dark,
                index,
                map_page_total,
                waypoint_numbers,
            )
        )
    for group_index, group in enumerate(station_groups, start=1):
        pages.append(
            NavigationMapPage(
                flight,
                legs,
                group,
                theater.terrain,
                dark,
                group_index,
                len(station_groups),
                waypoint_numbers,
                f"STATION REFERENCES {group_index}/{len(station_groups)}",
            )
        )
    page_count = max(1, math.ceil(len(stations) / DIRECTORY_ROWS))
    for index in range(page_count):
        pages.append(
            StationDirectoryPage(
                flight,
                stations[index * DIRECTORY_ROWS : (index + 1) * DIRECTORY_ROWS],
                notice,
                dark,
                index + 1,
                page_count,
            )
        )
    return pages
