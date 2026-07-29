from math import isfinite

from PySide6.QtWidgets import QComboBox, QGroupBox, QHBoxLayout, QLabel, QSpinBox

from game.ato import FlightType
from game.ato.flight import Flight
from game.ato.flightplans.tankerorbitspeed import (
    TANKER_ORBIT_SPEED_KIAS_PROP,
    TANKER_ORBIT_SPEED_MODE_PROP,
    MAX_TANKER_ORBIT_SPEED_KIAS,
    MIN_TANKER_ORBIT_SPEED_KIAS,
)


class QTankerOrbitSpeed(QGroupBox):
    def __init__(self, flight: Flight) -> None:
        super().__init__("Tanker orbit speed")
        self.flight = flight
        self.mode = QComboBox()
        self.mode.addItem("Auto", "auto")
        self.mode.addItem("Manual", "manual")
        mode = flight.props.get(TANKER_ORBIT_SPEED_MODE_PROP, "auto")
        self.mode.setCurrentIndex(max(0, self.mode.findData(mode)))
        self.mode.currentIndexChanged.connect(self._on_mode_changed)

        self.speed = QSpinBox()
        self.speed.setRange(MIN_TANKER_ORBIT_SPEED_KIAS, MAX_TANKER_ORBIT_SPEED_KIAS)
        speed = flight.props.get(TANKER_ORBIT_SPEED_KIAS_PROP, 280)
        if (
            isinstance(speed, (int, float))
            and not isinstance(speed, bool)
            and (isinstance(speed, int) or isfinite(speed))
        ):
            speed_value = int(speed)
        else:
            speed_value = 280
        self.speed.setValue(
            min(
                MAX_TANKER_ORBIT_SPEED_KIAS,
                max(MIN_TANKER_ORBIT_SPEED_KIAS, speed_value),
            )
        )
        if self.mode.currentData() == "manual":
            self.flight.props[TANKER_ORBIT_SPEED_KIAS_PROP] = self.speed.value()
        self.speed.valueChanged.connect(self._on_speed_changed)

        layout = QHBoxLayout()
        layout.addWidget(QLabel("Mode:"))
        layout.addWidget(self.mode)
        layout.addWidget(QLabel("Manual speed (KIAS):"))
        layout.addWidget(self.speed)
        self.setLayout(layout)
        self._update_speed_enabled()
        self.setVisible(
            flight.flight_type in (FlightType.REFUELING, FlightType.RECOVERY)
        )

    def _on_mode_changed(self, _index: int) -> None:
        mode = self.mode.currentData()
        self.flight.props[TANKER_ORBIT_SPEED_MODE_PROP] = mode
        if mode == "manual":
            self.flight.props[TANKER_ORBIT_SPEED_KIAS_PROP] = self.speed.value()
        self._update_speed_enabled()

    def _on_speed_changed(self, value: int) -> None:
        self.flight.props[TANKER_ORBIT_SPEED_KIAS_PROP] = value

    def _update_speed_enabled(self) -> None:
        self.speed.setEnabled(self.mode.currentData() == "manual")
