from types import SimpleNamespace
from typing import cast

import pytest
from PySide6.QtWidgets import QApplication

from game.ato import FlightType
from game.ato.flight import Flight
from game.ato.flightplans.tankerorbitspeed import (
    TANKER_ORBIT_SPEED_KIAS_PROP,
    TANKER_ORBIT_SPEED_MODE_PROP,
)
from qt_ui.windows.mission.flight.settings.QTankerOrbitSpeed import (
    QTankerOrbitSpeed,
)


@pytest.fixture
def app() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def flight(flight_type: FlightType, props: dict[str, object] | None = None) -> Flight:
    # QTankerOrbitSpeed only reads flight_type/props, so a SimpleNamespace test
    # double is sufficient at runtime; the cast satisfies mypy's Flight typing.
    return cast(Flight, SimpleNamespace(flight_type=flight_type, props=props or {}))


def test_tanker_types_show_controls_and_non_tankers_do_not(app: QApplication) -> None:
    assert QTankerOrbitSpeed(flight(FlightType.REFUELING)).isVisible() is True
    assert QTankerOrbitSpeed(flight(FlightType.RECOVERY)).isVisible() is True
    assert QTankerOrbitSpeed(flight(FlightType.CAS)).isVisible() is False


def test_auto_is_default_and_disables_manual_speed(app: QApplication) -> None:
    widget = QTankerOrbitSpeed(flight(FlightType.REFUELING))

    assert widget.mode.currentData() == "auto"
    assert widget.speed.isEnabled() is False
    assert TANKER_ORBIT_SPEED_MODE_PROP not in widget.flight.props


def test_switching_to_manual_persists_displayed_default_speed(
    app: QApplication,
) -> None:
    model = flight(FlightType.RECOVERY)
    widget = QTankerOrbitSpeed(model)

    widget.mode.setCurrentIndex(widget.mode.findData("manual"))

    assert model.props == {
        TANKER_ORBIT_SPEED_MODE_PROP: "manual",
        TANKER_ORBIT_SPEED_KIAS_PROP: widget.speed.value(),
    }


def test_manual_mode_persists_valid_speed_and_enforces_bounds(
    app: QApplication,
) -> None:
    model = flight(FlightType.RECOVERY)
    widget = QTankerOrbitSpeed(model)

    widget.mode.setCurrentIndex(widget.mode.findData("manual"))
    assert widget.speed.isEnabled() is True
    assert widget.speed.minimum() == 220
    assert widget.speed.maximum() == 350
    widget.speed.setValue(350)
    assert model.props == {
        TANKER_ORBIT_SPEED_MODE_PROP: "manual",
        TANKER_ORBIT_SPEED_KIAS_PROP: 350,
    }
    widget.speed.setValue(180)
    assert widget.speed.value() == 220
    assert model.props[TANKER_ORBIT_SPEED_KIAS_PROP] == 220
    widget.speed.setValue(400)
    assert widget.speed.value() == 350
    assert model.props[TANKER_ORBIT_SPEED_KIAS_PROP] == 350


def test_existing_manual_props_are_loaded(app: QApplication) -> None:
    widget = QTankerOrbitSpeed(
        flight(
            FlightType.REFUELING,
            {
                TANKER_ORBIT_SPEED_MODE_PROP: "manual",
                TANKER_ORBIT_SPEED_KIAS_PROP: 275,
            },
        )
    )

    assert widget.mode.currentData() == "manual"
    assert widget.speed.value() == 275
    assert widget.speed.isEnabled() is True


@pytest.mark.parametrize("value", [None, "fast", True, False, float("nan"), object()])
def test_malformed_stored_speed_uses_safe_default(
    app: QApplication, value: object
) -> None:
    widget = QTankerOrbitSpeed(
        flight(
            FlightType.REFUELING,
            {
                TANKER_ORBIT_SPEED_MODE_PROP: "manual",
                TANKER_ORBIT_SPEED_KIAS_PROP: value,
            },
        )
    )

    assert widget.speed.value() == 280


@pytest.mark.parametrize("value, expected", [(200, 220), (400, 350), (275.5, 275)])
def test_numeric_stored_speed_is_clamped_and_persisted(
    app: QApplication, value: float, expected: int
) -> None:
    model = flight(
        FlightType.REFUELING,
        {
            TANKER_ORBIT_SPEED_MODE_PROP: "manual",
            TANKER_ORBIT_SPEED_KIAS_PROP: value,
        },
    )
    widget = QTankerOrbitSpeed(model)

    assert widget.speed.value() == expected
    assert model.props[TANKER_ORBIT_SPEED_KIAS_PROP] == expected
