from pathlib import Path
from typing import Any

from game.plugins.mooseautolase import (
    MooseAutolasePlugin,
    build_unit_class_table,
    corridor_depths_m,
)


def test_build_unit_class_table_maps_known_units() -> None:
    table = build_unit_class_table()
    assert table["ZSU-23-4 Shilka"] == "AAA"
    assert table["M-1 Abrams"] == "Tank"
    assert all(isinstance(v, str) and v for v in table.values())


def test_corridor_depths_match_deployment_table() -> None:
    default_m, artillery_m = corridor_depths_m()
    assert default_m == 8000
    assert artillery_m == 18000


class _FakeJtac:
    def __init__(self, name: str, seg: Any) -> None:
        self.group_name = name
        self.frontline_segment = seg


class _FakeMissionData:
    def __init__(self, jtacs: list[Any]) -> None:
        self.jtacs = jtacs


class _RecordingLuaGenerator:
    def __init__(self, jtacs: list[Any]) -> None:
        self.mission_data = _FakeMissionData(jtacs)
        self.triggers: list[str] = []

    def inject_lua_trigger(self, lua: str, comment: str) -> None:
        self.triggers.append(lua)


def test_subclass_injects_computed_tables_before_work_orders() -> None:
    from dcs.mapping import Point

    plugin = MooseAutolasePlugin.from_json(
        "MooseAutolase", Path("resources/plugins/MooseAutolase/plugin.json")
    )
    assert isinstance(plugin, MooseAutolasePlugin)
    seg = (Point(1.0, 2.0, None), Point(3.0, 4.0, None))  # type: ignore[arg-type]
    gen = _RecordingLuaGenerator([_FakeJtac("JTAC Alpha", seg)])

    plugin._inject_option_table(gen)  # type: ignore[arg-type]
    plugin._inject_computed_config(gen)  # type: ignore[arg-type]

    combined = "\n".join(gen.triggers)
    assert "dcsRetribution.plugins.MooseAutolase.UnitClasses" in combined
    assert "dcsRetribution.plugins.MooseAutolase.Frontlines" in combined
    assert '["JTAC Alpha"]' in combined
    assert "DefaultCorridorM = 8000" in combined
    assert "ArtilleryCorridorM = 18000" in combined


from game.plugins.manager import LuaPluginManager
from game.plugins.mooseautolase import MooseAutolasePlugin as _MAP


def test_manager_loads_mooseautolase_as_subclass() -> None:
    plugin = {p.identifier: p for p in LuaPluginManager.plugins()}.get("MooseAutolase")
    assert plugin is not None
    assert isinstance(plugin, _MAP)
