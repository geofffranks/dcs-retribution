from pathlib import Path

from game.plugins.luaplugin import LuaPluginDefinition


def test_mooseautolase_defines_six_tier_spinboxes() -> None:
    definition = LuaPluginDefinition.from_json(
        "MooseAutolase", Path("resources/plugins/MooseAutolase/plugin.json")
    )
    by_id = {opt.identifier: opt for opt in definition.options}
    expected = {
        "MooseAutolase.TierSamThreats": 1,
        "MooseAutolase.TierGuidedAaa": 2,
        "MooseAutolase.TierArtillery": 3,
        "MooseAutolase.TierUnguidedAaa": 3,
        "MooseAutolase.TierArmorLaunchers": 4,
        "MooseAutolase.TierOther": 5,
    }
    for identifier, default in expected.items():
        assert identifier in by_id, f"missing {identifier}"
        opt = by_id[identifier]
        assert opt.get_value == default
        assert opt.min == 0
        assert opt.max == 6
