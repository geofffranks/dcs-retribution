from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from dcs.task import Modulation

from game.missiongenerator.missiondata import AtisInfo
from game.radio.radios import RadioFrequency
from game.theater.controlpoint import Airfield

if TYPE_CHECKING:
    from game.radio.radios import RadioRegistry
    from game.theater.conflicttheater import ConflictTheater
    from game.theater.player import Player

logger = logging.getLogger(__name__)


class AtisGenerator:
    """Allocates one unique VHF-AM ATIS frequency per blue airfield.

    Frequencies are reserved on the shared ``RadioRegistry`` so they cannot
    collide with package / intra-flight / AWACS / tanker frequencies already
    allocated. Allocation order is deterministic (airfield-name sort) so a
    field keeps the same ATIS frequency across regenerated turns where the
    blue-field set is unchanged.
    """

    def __init__(
        self,
        theater: "ConflictTheater",
        radio_registry: "RadioRegistry",
        friendly: "Player",
        *,
        base_mhz: float = 131.0,
        spacing_khz: int = 500,
        window_max_mhz: float = 140.0,
    ) -> None:
        self.theater = theater
        self.radio_registry = radio_registry
        self.friendly = friendly
        self.base_mhz = base_mhz
        self.spacing_khz = spacing_khz
        self.window_max_mhz = window_max_mhz

    def _blue_airfields(self) -> list[Airfield]:
        airfields = [
            cp
            for cp in self.theater.controlpoints
            if isinstance(cp, Airfield) and cp.is_friendly(self.friendly)
        ]
        return sorted(airfields, key=lambda cp: cp.full_name)

    def _next_free_frequency(self, start_slot: int) -> tuple[RadioFrequency, int]:
        """Return the next unreserved VHF-AM frequency at/after ``start_slot``.

        Raises ``StopIteration`` when the window is exhausted.
        """
        slot = start_slot
        window_max_hz = int(round(self.window_max_mhz * 1_000_000))
        base_hz = int(round(self.base_mhz * 1_000_000))
        step_hz = self.spacing_khz * 1_000
        while True:
            hertz = base_hz + slot * step_hz
            if hertz >= window_max_hz:
                raise StopIteration
            freq = RadioFrequency(hertz, Modulation.AM)
            slot += 1
            if freq not in self.radio_registry.allocated_channels:
                self.radio_registry.reserve(freq)
                return freq, slot

    def generate(self) -> list[AtisInfo]:
        result: list[AtisInfo] = []
        slot = 0
        for airfield in self._blue_airfields():
            try:
                freq, slot = self._next_free_frequency(slot)
            except StopIteration:
                logger.warning(
                    "ATIS frequency band exhausted (base %.3f MHz, %d kHz spacing, "
                    "max %.3f MHz); skipping ATIS for %s and any remaining fields.",
                    self.base_mhz,
                    self.spacing_khz,
                    self.window_max_mhz,
                    airfield.full_name,
                )
                break
            result.append(AtisInfo(airfield_name=airfield.full_name, frequency=freq))
        return result
