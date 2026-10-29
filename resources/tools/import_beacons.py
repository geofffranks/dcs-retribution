"""Generate compatible beacon catalogs from actual DCS terrain data.

DCS ``position`` is a Lua array ``{northing, altitude, easting}``; the map
plane is its first and third entries. Source is parsed as declarative table
data, never executed.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any, Optional, Union

from game.dcs.beacons import BEACONS_RESOURCE_PATH, Beacon, BeaconType

THIS_DIR = Path(__file__).parent.resolve()
SRC_DIR = THIS_DIR.parents[1]
EXPORT_DIR = SRC_DIR / BEACONS_RESOURCE_PATH
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_RECORDS = 100_000


@dataclasses.dataclass(frozen=True)
class TerrainBeaconImport:
    records: list[tuple[str, Beacon]]
    source_record_count: int
    unusable_record_count: int
    source_sha256: str
    dcs_build: str


class _LuaTableParser:
    """Parse the supported literal-table subset without evaluating Lua."""

    TOKEN = re.compile(
        r"\s+|--[^\n]*|(?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")|"
        r"[A-Za-z_][A-Za-z0-9_]*|-?\d+(?:\.\d+)?|[{}\[\]=,;]"
    )

    def __init__(self, source: str) -> None:
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError("Lua source exceeds import size limit")
        source = re.sub(r"^\s*dofile\([^\n]*\)\s*$", "", source, flags=re.MULTILINE)
        source = re.sub(r"^\s*local[^\n]*$", "", source, flags=re.MULTILINE)
        source = re.sub(
            r"^\s*beaconsTableFormat\s*=\s*\d+\s*$", "", source, flags=re.MULTILINE
        )
        source = re.sub(r"_\(('[^'\n]*'|\"[^\"\n]*\")\)", r"\1", source)
        self.source = source
        self.tokens = [
            match.group(0)
            for match in self.TOKEN.finditer(source)
            if not match.group(0).isspace() and not match.group(0).startswith("--")
        ]
        # Reject non-whitespace/non-comment bytes not covered by the allowlist.
        cursor = 0
        for match in self.TOKEN.finditer(source):
            if source[cursor : match.start()].strip():
                raise ValueError("Unsupported Lua syntax outside literal data")
            cursor = match.end()
        if source[cursor:].strip():
            raise ValueError("Unsupported Lua syntax outside literal data")
        self.index = 0

    def parse(self) -> list[dict[str, Any]]:
        marker = re.search(r"\bbeacons\s*=\s*{", self.source)
        if marker is None:
            raise ValueError("Missing beacons table")
        while self.index < len(self.tokens) and self.tokens[self.index] != "beacons":
            self.index += 1
        self.index += 2
        self._expect("{")
        records: list[dict[str, Any]] = []
        while self._peek() != "}":
            if self._peek() is None:
                raise ValueError("Unterminated beacons table")
            if self._peek() == "[":
                self.index += 1
                key = self._peek()
                if key is None or not key.isdigit():
                    raise ValueError("Only numeric literal table indices are supported")
                self.index += 1
                self._expect("]")
                self._expect("=")
            self._expect("{")
            record: dict[str, Any] = {}
            while self._peek() != "}":
                key = self._expect_identifier()
                self._expect("=")
                record[key] = self._value()
                self._separator()
            self._expect("}")
            records.append(record)
            if len(records) > MAX_SOURCE_RECORDS:
                raise ValueError("Lua source exceeds record limit")
            if self._peek() in (";", ","):
                self.index += 1
        self._expect("}")
        return records

    def _peek(self) -> Optional[str]:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _expect(self, expected: str) -> str:
        actual = self._peek()
        if actual != expected:
            raise ValueError(f"Expected {expected!r}, got {actual!r}")
        self.index += 1
        return expected

    def _expect_identifier(self) -> str:
        token = self._peek()
        if token is None or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
            raise ValueError(f"Expected field name, got {token!r}")
        self.index += 1
        return token

    def _separator(self) -> None:
        if self._peek() in (";", ","):
            self.index += 1
        elif self._peek() != "}":
            raise ValueError("Expected table separator")

    def _value(self) -> Any:
        token = self._peek()
        if token == "{":
            self.index += 1
            values: list[Any] = []
            fields: dict[str, Any] = {}
            while self._peek() != "}":
                if self._peek() is None:
                    raise ValueError("Unterminated nested table")
                if (
                    self.index + 1 < len(self.tokens)
                    and self.tokens[self.index + 1] == "="
                ):
                    key = self._expect_identifier()
                    self._expect("=")
                    fields[key] = self._value()
                else:
                    values.append(self._value())
                self._separator()
            self._expect("}")
            return fields if fields else values
        if token is None:
            raise ValueError("Missing value")
        self.index += 1
        if token in ("true", "false", "nil"):
            return {"true": True, "false": False, "nil": None}[token]
        if token.startswith(("'", '"')):
            return bytes(token[1:-1], "utf-8").decode("unicode_escape")
        try:
            return float(token) if "." in token else int(token)
        except ValueError:
            try:
                return int(BeaconType[token])
            except KeyError as error:
                raise ValueError(f"Unsupported Lua expression: {token!r}") from error


def convert_lua_frequency(raw: Union[float, int]) -> int:
    if isinstance(raw, float):
        if not raw.is_integer():
            raise ValueError(f"Unexpected non-integer frequency: {raw}")
        return int(raw)
    return raw


def _finite_position(beacon: Any) -> tuple[Optional[float], Optional[float]]:
    """Return DCS map-plane x/z, never altitude or geographic degrees."""
    position = beacon.get("position")
    if position is None:
        return None, None
    try:
        if isinstance(position, (list, tuple)):
            x, z = float(position[0]), float(position[2])
        else:
            x, z = float(position[1]), float(position[3])
    except (IndexError, KeyError, TypeError, ValueError):
        return None, None
    if not math.isfinite(x) or not math.isfinite(z):
        return None, None
    return x, z


def beacons_from_terrain(dcs_path: Path, path: Path) -> TerrainBeaconImport:
    del dcs_path
    logging.info("Loading terrain data from %s", path)
    source = (path / "beacons.lua").read_bytes()
    source_values = _LuaTableParser(source.decode("utf-8")).parse()
    beacon_types_map = {int(item): item for item in BeaconType}
    records: list[tuple[str, Beacon]] = []
    unusable = 0
    seen_ids: set[str] = set()
    for index, raw in enumerate(source_values):
        source_id = str(
            raw.get("beaconId") or f"unidentified-source-record-{index + 1}"
        )
        beacon_id = source_id
        if beacon_id in seen_ids:
            unusable += 1
            beacon_id = f"{source_id}#source-record-{index + 1}"
        seen_ids.add(beacon_id)
        x, z = _finite_position(raw)
        name = str(raw.get("display_name") or "Unknown station")
        callsign = str(raw.get("callsign") or "")
        frequency_raw = raw.get("frequency")
        type_value = raw.get("type")
        if type_value not in beacon_types_map:
            unusable += 1
        if frequency_raw is None and raw.get("channel") is None:
            unusable += 1
        try:
            frequency = (
                convert_lua_frequency(frequency_raw)
                if frequency_raw is not None
                else None
            )
        except (TypeError, ValueError):
            unusable += 1
            frequency = None
        beacon_type = beacon_types_map.get(type_value, BeaconType.BEACON_TYPE_NULL)
        records.append(
            (
                beacon_id,
                Beacon(
                    name, callsign, beacon_type, frequency, raw.get("channel"), x, z
                ),
            )
        )
    return TerrainBeaconImport(
        records,
        len(source_values),
        unusable,
        hashlib.sha256(source).hexdigest(),
        os.environ.get("DCS_BUILD", "unspecified"),
    )


class Importer:
    """Import selected terrain sources into runtime catalogs."""

    def __init__(self, dcs_path: Path, export_dir: Path) -> None:
        self.dcs_path = dcs_path
        self.export_dir = export_dir

    def run(self) -> None:
        terrains_path = self.dcs_path / "Mods" / "terrains"
        self.export_dir.mkdir(parents=True, exist_ok=True)
        for terrain in terrains_path.iterdir():
            if (terrain / "beacons.lua").is_file():
                self.export_beacons(
                    terrain.name, beacons_from_terrain(self.dcs_path, terrain)
                )

    def export_beacons(
        self,
        terrain: str,
        imported: TerrainBeaconImport,
        source_path: str = "beacons.lua",
    ) -> None:
        self.export_dir.mkdir(parents=True, exist_ok=True)
        positioned = sum(
            b.position_x is not None and b.position_y is not None
            for _, b in imported.records
        )
        payload = {
            "metadata": {
                "terrain": terrain,
                "terrain_identity": (
                    "unconfirmed"
                    if terrain.lower() == "normandy"
                    else "source terrain name"
                ),
                "coordinate_system": "DCS world metres; x=northing, y=easting; beacons.lua position[1] and position[3]",
                "source_file": source_path,
                "source_origin": "user-supplied archive",
                "source_sha256": imported.source_sha256,
                "dcs_build": imported.dcs_build,
                "terrain_identity_status": (
                    "unconfirmed"
                    if terrain.lower() == "normandy"
                    else "source-name-only"
                ),
                "total_source_records": imported.source_record_count,
                "imported_records": len(imported.records),
                "unusable_records": imported.unusable_record_count,
                "positioned_records": positioned,
                "complete": imported.source_record_count == len(imported.records)
                and imported.unusable_record_count == 0
                and positioned == imported.source_record_count,
            },
            "beacons": {
                bid: dataclasses.asdict(beacon) for bid, beacon in imported.records
            },
        }
        (self.export_dir / f"{terrain.lower()}.json").write_text(
            json.dumps(payload, indent=True), encoding="utf-8"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import source terrain beacon Lua tables safely"
    )
    parser.add_argument(
        "source", type=Path, help="DCS terrain directory or supplied ZIP archive"
    )
    parser.add_argument("--export-to", type=Path, default=EXPORT_DIR)
    return parser.parse_args()


def main() -> None:
    import zipfile

    logging.basicConfig(level=logging.INFO)
    args = parse_args()
    if args.source.is_file() and zipfile.is_zipfile(args.source):
        with zipfile.ZipFile(args.source) as archive:
            names = {
                Path(name).name.lower(): name
                for name in archive.namelist()
                if name.lower().endswith(".lua")
            }
            for terrain in (
                "afghanistan",
                "caucasus",
                "falklands",
                "iraq",
                "marianas",
                "nevada",
                "normandy",
                "persiangulf",
                "sinai",
                "syria",
            ):
                member = names.get(f"{terrain}.lua")
                if member is None:
                    raise ValueError(f"Missing {terrain}.lua in supplied archive")
                source = archive.read(member)
                scratch = args.export_to.parent / ".beacon-source" / terrain
                scratch.mkdir(parents=True, exist_ok=True)
                (scratch / "beacons.lua").write_bytes(source)
                imported = beacons_from_terrain(Path(), scratch)
                Importer(Path(), args.export_to).export_beacons(
                    terrain, imported, f"{args.source.name}:{member}"
                )
    else:
        Importer(args.source, args.export_to).run()


if __name__ == "__main__":
    main()
