"""Route-based selection and presentation data for helicopter navigation pages."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional

from dcs.mapping import Point
from dcs.terrain.terrain import Terrain

from game.ato.flightwaypoint import FlightWaypoint
from game.ato.flightwaypointtype import FlightWaypointType
from game.dcs.beacons import Beacon, BeaconType
from game.utils import nautical_miles


@dataclass(frozen=True)
class RouteLeg:
    index: int
    start_name: str
    end_name: str
    start: Point
    end: Point
    start_waypoint_index: int | None = None
    end_waypoint_index: int | None = None


@dataclass(frozen=True)
class Station:
    reference: str
    beacon_id: str
    beacon: Beacon
    nearest_leg: str
    distance_m: float
    encounter: float
    leg_index: int


VOR_TYPES = {
    BeaconType.BEACON_TYPE_VOR,
    BeaconType.BEACON_TYPE_VOR_DME,
    BeaconType.BEACON_TYPE_VORTAC,
}
NDB_TYPES = {
    BeaconType.BEACON_TYPE_HOMER,
    BeaconType.BEACON_TYPE_AIRPORT_HOMER,
    BeaconType.BEACON_TYPE_AIRPORT_HOMER_WITH_MARKER,
    BeaconType.BEACON_TYPE_ILS_FAR_HOMER,
    BeaconType.BEACON_TYPE_ILS_NEAR_HOMER,
    BeaconType.BEACON_TYPE_NAUTICAL_HOMER,
}


def route_legs(waypoints: Iterable[FlightWaypoint]) -> list[RouteLeg]:
    """Connect consecutive flown waypoints, excluding supplemental markers."""
    supplemental_types = {
        FlightWaypointType.TARGET_POINT,
        FlightWaypointType.TARGET_GROUP_LOC,
        FlightWaypointType.TARGET_SHIP,
        FlightWaypointType.BULLSEYE,
        FlightWaypointType.DIVERT,
    }
    route = [
        (index, waypoint)
        for index, waypoint in enumerate(waypoints)
        if waypoint.waypoint_type not in supplemental_types
    ]
    if len(route) == 1:
        waypoint_index, waypoint = route[0]
        return [
            RouteLeg(
                1,
                waypoint.name,
                waypoint.name,
                waypoint.position,
                waypoint.position,
                waypoint_index,
                waypoint_index,
            )
        ]
    return [
        RouteLeg(
            leg_index,
            start.name,
            end.name,
            start.position,
            end.position,
            start_index,
            end_index,
        )
        for leg_index, ((start_index, start), (end_index, end)) in enumerate(
            zip(route, route[1:]), start=1
        )
    ]


def _segment_distance(point: Point, start: Point, end: Point) -> tuple[float, float]:
    dx, dy = end.x - start.x, end.y - start.y
    length2 = dx * dx + dy * dy
    ratio = (
        0.0
        if length2 == 0
        else max(
            0.0,
            min(1.0, ((point.x - start.x) * dx + (point.y - start.y) * dy) / length2),
        )
    )
    x, y = start.x + ratio * dx, start.y + ratio * dy
    return math.hypot(point.x - x, point.y - y), ratio


def select_stations(
    beacons: Iterable[tuple[str, Beacon]],
    legs: list[RouteLeg],
    corridor_nmi: float,
    terrain: Terrain,
) -> list[Station]:
    """Select positioned VOR/NDB stations by finite-leg distance and encounter order."""
    if not math.isfinite(corridor_nmi) or corridor_nmi < 0:
        raise ValueError("corridor width must be finite and not negative")
    if not legs:
        return []
    max_distance = nautical_miles(corridor_nmi).meters
    candidates: list[tuple[float, str, Beacon, str, float, int]] = []
    for beacon_id, beacon in beacons:
        if (
            beacon.position_x is None
            or beacon.position_y is None
            or beacon.hertz is None
            or beacon.hertz <= 0
        ):
            continue
        if not math.isfinite(beacon.position_x) or not math.isfinite(beacon.position_y):
            continue
        if beacon.beacon_type not in VOR_TYPES | NDB_TYPES:
            continue
        location = Point(beacon.position_x, beacon.position_y, terrain)
        distances = [
            (*_segment_distance(location, leg.start, leg.end), leg, position)
            for position, leg in enumerate(legs)
        ]
        distance, ratio, leg, position = min(
            distances, key=lambda item: (item[0], item[3])
        )
        if distance <= max_distance:
            candidates.append(
                (
                    position + ratio,
                    beacon_id,
                    beacon,
                    f"{leg.start_name}-{leg.end_name}",
                    distance,
                    leg.index,
                )
            )
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [
        Station(f"N{index:03}", item[1], item[2], item[3], item[4], item[0], item[5])
        for index, item in enumerate(candidates, 1)
    ]


def coverage_notice(
    metadata: Optional[dict[str, object]], positioned: int, total: int, selected: int
) -> str:
    """Describe known catalog coverage; unknown/legacy coverage stays explicit."""
    if metadata is None:
        return "Station data coverage unknown (legacy catalog)"
    if metadata.get("terrain_identity_status") == "unconfirmed":
        return "Station data unverified; terrain identity unconfirmed"
    if metadata.get("complete") is not True or positioned < total:
        return "Station data incomplete; some stations may be unavailable"
    return "No nearby VOR/NDB stations" if selected == 0 else "Station data complete"
