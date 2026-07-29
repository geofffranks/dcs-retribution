from game.settings.boundedintoption import BoundedIntOption
from game.settings.optiondescription import SETTING_DESCRIPTION_KEY
from game.settings.settings import MISSION_GENERATOR_PAGE, Settings

SETTING_NAME = "carrier_min_standoff_distance"


def _option() -> BoundedIntOption:
    field_info = Settings.__dataclass_fields__[SETTING_NAME]
    option = field_info.metadata[SETTING_DESCRIPTION_KEY]
    assert isinstance(option, BoundedIntOption)
    return option


def test_carrier_min_standoff_distance_defaults_to_60_nm() -> None:
    assert Settings().carrier_min_standoff_distance == 60


def test_carrier_min_standoff_distance_is_bounded_and_zero_disables() -> None:
    option = _option()
    assert option.min == 0
    assert option.max == 80
    assert Settings(carrier_min_standoff_distance=0).carrier_min_standoff_distance == 0


def test_carrier_min_standoff_distance_is_visible_with_agreed_description() -> None:
    option = _option()
    assert option.page == MISSION_GENERATOR_PAGE
    assert option.text == "Carrier minimum shore distance (NM)"
    assert option.detail is not None
    assert "0" in option.detail
    assert "disables" in option.detail


def test_carrier_min_standoff_distance_backfills_and_round_trips() -> None:
    settings = Settings.__new__(Settings)
    settings.__setstate__({})
    assert settings.carrier_min_standoff_distance == 60
    assert Settings.deserialize_state_dict({SETTING_NAME: 0})[SETTING_NAME] == 0
    assert Settings.deserialize_state_dict({SETTING_NAME: 75})[SETTING_NAME] == 75
