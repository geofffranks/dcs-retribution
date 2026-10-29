from __future__ import annotations

import json
from pathlib import Path

from resources.tools.import_beacons import Importer, beacons_from_terrain


def test_importer_preserves_source_count_positions_and_invalid_rows(
    tmp_path: Path,
) -> None:
    terrain = tmp_path / "Mods" / "terrains" / "FixtureMap"
    terrain.mkdir(parents=True)
    lua_source = """
beacons = {
  [1] = { beaconId = "GOOD", display_name = "Fixture VOR", callsign = "FIX", type = BEACON_TYPE_VOR, frequency = 110000000, position = { 1200, 50, 3400 } },
  [2] = { beaconId = "NO_POSITION", display_name = "Positionless", callsign = "NOP", type = BEACON_TYPE_HOMER, frequency = 350000 },
  [3] = { beaconId = "BAD_FREQUENCY", display_name = "Bad Frequency", callsign = "BAD", type = BEACON_TYPE_VOR, frequency = 110000000.5, position = { 2000, 10, 4000 } },
  [4] = { beaconId = "GOOD", display_name = "Duplicate", callsign = "DUP", type = BEACON_TYPE_VOR, frequency = 110000000, position = { 3000, 10, 5000 } },
  [5] = { display_name = "Unknown type", callsign = "UNK", type = 999, frequency = 110000000, position = { 4000, 10, 6000 } },
}
"""
    (terrain / "beacons.lua").write_text(lua_source, encoding="utf-8")

    imported = beacons_from_terrain(tmp_path, terrain)

    assert imported.source_record_count == 5
    assert len(imported.records) == 5
    assert imported.unusable_record_count == 3
    assert imported.records[0][1].position_x == 1200
    assert imported.records[0][1].position_y == 3400
    assert imported.records[1][1].position_x is None
    assert imported.records[2][1].hertz is None
    assert imported.records[4][1].beacon_type.name == "BEACON_TYPE_NULL"
    assert imported.records[3][0] == "GOOD#source-record-4"

    exporter = Importer(tmp_path, tmp_path / "out")
    exporter.export_beacons("FixtureMap", imported)
    result = json.loads(
        (tmp_path / "out" / "fixturemap.json").read_text(encoding="utf-8")
    )
    metadata = result["metadata"]
    assert metadata["total_source_records"] == 5
    assert metadata["imported_records"] == 5
    assert metadata["unusable_records"] == 3
    assert metadata["positioned_records"] == 4
    assert metadata["complete"] is False


def test_importer_rejects_executable_lua_instead_of_running_it(tmp_path: Path) -> None:
    import pytest

    from resources.tools.import_beacons import _LuaTableParser

    malicious = "os.execute('touch /tmp/beacon-import-was-executed')\nbeacons = {}"
    with pytest.raises(ValueError, match="Unsupported Lua syntax"):
        _LuaTableParser(malicious)


def test_python_position_fixture_uses_northing_and_easting_not_altitude() -> None:
    from resources.tools.import_beacons import _finite_position

    assert _finite_position({"position": [12, 999, 34]}) == (12.0, 34.0)
    assert _finite_position({"position": [12, float("nan"), 34]}) == (12.0, 34.0)
    assert _finite_position({"position": [12, 999, float("inf")]}) == (None, None)
