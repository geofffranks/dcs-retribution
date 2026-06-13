from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from dcs.task import Modulation

from game.missiongenerator.atisgenerator import AtisGenerator
from game.radio.radios import MHz, RadioRegistry
from game.theater.controlpoint import Airfield


def _blue_airfield(name: str) -> MagicMock:
    cp = MagicMock(spec=Airfield)
    cp.full_name = name
    cp.is_friendly.return_value = True
    return cp


def _make_theater(*airfields: MagicMock) -> MagicMock:
    theater = MagicMock()
    theater.controlpoints = list(airfields)
    return theater


def test_allocates_unique_vhf_am_per_blue_airfield() -> None:
    registry = RadioRegistry()
    theater = _make_theater(_blue_airfield("Batumi"), _blue_airfield("Kobuleti"))
    gen = AtisGenerator(theater, registry, friendly=MagicMock())
    result = gen.generate()
    assert len(result) == 2
    freqs = {info.frequency.hertz for info in result}
    assert len(freqs) == 2  # unique
    for info in result:
        assert info.frequency.modulation == Modulation.AM
        assert 130_000_000 <= info.frequency.hertz < 140_000_000


def test_deterministic_order_by_airfield_name() -> None:
    theater = _make_theater(_blue_airfield("Zugdidi"), _blue_airfield("Anapa"))
    gen = AtisGenerator(theater, RadioRegistry(), friendly=MagicMock())
    names = [info.airfield_name for info in gen.generate()]
    assert names == ["Anapa", "Zugdidi"]


def test_skips_frequency_already_reserved() -> None:
    registry = RadioRegistry()
    registry.reserve(MHz(131))  # base slot taken by something else
    gen = AtisGenerator(
        _make_theater(_blue_airfield("Batumi")), registry, friendly=MagicMock()
    )
    info = gen.generate()[0]
    assert info.frequency.hertz != MHz(131).hertz  # skipped to next slot


def test_band_exhaustion_logs_and_skips_without_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # 2 slots wide -> 3 fields can't all fit; the 3rd is skipped with a warning.
    fields = [_blue_airfield(n) for n in ("A", "B", "C")]
    gen = AtisGenerator(
        _make_theater(*fields),
        RadioRegistry(),
        friendly=MagicMock(),
        base_mhz=131.0,
        spacing_khz=500,
        window_max_mhz=132.0,  # slots: 131.0, 131.5 -> only 2
    )
    with caplog.at_level(logging.WARNING):
        result = gen.generate()
    assert len(result) == 2
    assert any("exhaust" in r.message.lower() for r in caplog.records)


def test_ignores_non_airfield_and_enemy_control_points() -> None:
    blue = _blue_airfield("Batumi")
    carrier = MagicMock()  # not an Airfield instance
    enemy = _blue_airfield("Mozdok")
    enemy.is_friendly.return_value = False
    gen = AtisGenerator(
        _make_theater(blue, carrier, enemy), RadioRegistry(), friendly=MagicMock()
    )
    result = gen.generate()
    assert [i.airfield_name for i in result] == ["Batumi"]
