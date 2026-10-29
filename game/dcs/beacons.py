from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from game.radio.radios import RadioFrequency
from game.radio.tacan import TacanBand, TacanChannel

if TYPE_CHECKING:
    from game.theater import ConflictTheater

BEACONS_RESOURCE_PATH = Path("resources/dcs/beacons")


class BeaconType(IntEnum):
    BEACON_TYPE_NULL = 0
    BEACON_TYPE_VOR = 1
    BEACON_TYPE_DME = 2
    BEACON_TYPE_VOR_DME = 3
    BEACON_TYPE_TACAN = 4
    BEACON_TYPE_VORTAC = 5
    BEACON_TYPE_RSBN = 128
    BEACON_TYPE_BROADCAST_STATION = 1024

    BEACON_TYPE_HOMER = 8
    BEACON_TYPE_AIRPORT_HOMER = 4104
    BEACON_TYPE_AIRPORT_HOMER_WITH_MARKER = 4136
    BEACON_TYPE_ILS_FAR_HOMER = 16408
    BEACON_TYPE_ILS_NEAR_HOMER = 16424

    BEACON_TYPE_ILS_LOCALIZER = 16640
    BEACON_TYPE_ILS_GLIDESLOPE = 16896

    BEACON_TYPE_PRMG_LOCALIZER = 33024
    BEACON_TYPE_PRMG_GLIDESLOPE = 33280

    BEACON_TYPE_ICLS_LOCALIZER = 131328
    BEACON_TYPE_ICLS_GLIDESLOPE = 131584

    BEACON_TYPE_NAUTICAL_HOMER = 65536

    BEACON_TYPE_TACAN_RANGE = 262144


@dataclass(frozen=True)
class Beacon:
    name: str
    callsign: str
    beacon_type: BeaconType
    hertz: Optional[int]
    channel: Optional[int]
    # Exact DCS world coordinates from beacons.lua `position` (x, y, z).
    # None for legacy catalogs or records without usable source positions.
    position_x: Optional[float] = None
    position_y: Optional[float] = None

    @property
    def frequency(self) -> Optional[RadioFrequency]:
        return RadioFrequency(self.hertz) if self.hertz is not None else None

    @property
    def is_tacan(self) -> bool:
        return self.beacon_type in (
            BeaconType.BEACON_TYPE_VORTAC,
            BeaconType.BEACON_TYPE_TACAN,
        )

    @property
    def tacan_channel(self) -> TacanChannel:
        assert self.is_tacan
        assert self.channel is not None
        return TacanChannel(self.channel, TacanBand.X)


class Beacons:
    _by_terrain: dict[str, dict[str, Beacon]] = {}
    _metadata_by_terrain: dict[str, dict[str, object]] = {}

    @classmethod
    def metadata_for_theater(
        cls, theater: ConflictTheater
    ) -> Optional[dict[str, object]]:
        cls._load_for_theater_if_needed(theater)
        return cls._metadata_by_terrain.get(theater.terrain.name)

    @classmethod
    def _load_for_theater_if_needed(cls, theater: ConflictTheater) -> None:
        if theater.terrain.name in cls._by_terrain:
            return

        beacons_filename_mapper = {
            "sinaimap": "sinai",
            "germanycw": "germanycoldwar",
            "marianaislands": "marianas",
        }
        filename = theater.terrain.name.lower()
        filename = beacons_filename_mapper.get(filename, filename)
        beacons_path = BEACONS_RESOURCE_PATH / f"{filename}.json"
        if not beacons_path.exists():
            raise RuntimeError(f"Beacon file {beacons_path.resolve()} is missing")

        catalog = json.loads(beacons_path.read_text(encoding="utf-8"))
        records = catalog.get("beacons", catalog) if isinstance(catalog, dict) else {}
        if isinstance(catalog, dict) and isinstance(catalog.get("metadata"), dict):
            cls._metadata_by_terrain[theater.terrain.name] = catalog["metadata"]
        beacons = {}
        for bid, beacon in records.items():
            position = beacon.get("position")
            if (
                position is None
                and beacon.get("position_x") is not None
                and beacon.get("position_y") is not None
            ):
                position = {"x": beacon["position_x"], "y": beacon["position_y"]}
            if position is None and isinstance(beacon.get("position_geo"), dict):
                position = beacon["position_geo"]
            if isinstance(position, dict):
                position_x = position.get("x")
                position_y = position.get("y")
            elif isinstance(position, list) and len(position) >= 2:
                position_x, position_y = position[0], position[1]
            else:
                position_x = position_y = None
            try:
                coordinate_x = float(position_x) if position_x is not None else None
                coordinate_y = float(position_y) if position_y is not None else None
            except (TypeError, ValueError):
                coordinate_x = coordinate_y = None
            if (
                coordinate_x is not None
                and coordinate_y is not None
                and (not math.isfinite(coordinate_x) or not math.isfinite(coordinate_y))
            ):
                coordinate_x = coordinate_y = None
            beacons[bid] = Beacon(
                name=beacon["name"],
                callsign=beacon["callsign"],
                beacon_type=BeaconType(beacon["beacon_type"]),
                hertz=beacon["hertz"],
                channel=beacon.get("channel"),
                position_x=coordinate_x,
                position_y=coordinate_y,
            )
        cls._by_terrain[theater.terrain.name] = beacons

    @classmethod
    def _dict_for_theater(cls, theater: ConflictTheater) -> dict[str, Beacon]:
        cls._load_for_theater_if_needed(theater)
        return cls._by_terrain[theater.terrain.name]

    @classmethod
    def iter_theater(cls, theater: ConflictTheater) -> Iterator[Beacon]:
        yield from cls._dict_for_theater(theater).values()

    @classmethod
    def with_id(cls, beacon_id: str, theater: ConflictTheater) -> Beacon:
        return cls._dict_for_theater(theater)[beacon_id]
