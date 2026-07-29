"""Tests for the optional per-aircraft AAR (receiver) speed metadata.

This metadata is consumed by the shared tanker orbit-speed selection policy
(``game.ato.flightplans.tankerorbitspeed``) to pick the slowest known receiver
speed in Auto mode. It is loaded from the ``aar_receiver_speed_kias`` key in an
aircraft's ``resources/units/aircraft/<type>.yaml`` data file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dcs.planes import F_16C_50

from game import persistency
from game.dcs.aircrafttype import AircraftType


@pytest.fixture(autouse=True)
def _persistency(tmp_path: Path) -> None:
    # AircraftType._variant_from_dict reaches for the user weapon-injection
    # directory via persistency; point it at a throwaway dir.
    persistency.setup(str(tmp_path), prefer_liberation_payloads=False, port=16885)


def _variant(data: dict[str, object]) -> AircraftType:
    full_data: dict[str, object] = {"price": 1, **data}
    return AircraftType._variant_from_dict(F_16C_50, "test-variant", full_data)


def test_aar_receiver_speed_absent_by_default() -> None:
    variant = _variant({})
    assert variant.aar_receiver_speed is None


@pytest.mark.parametrize("value", [220, 235, 350])
def test_aar_receiver_speed_parses_values_in_the_expanded_envelope(
    value: int,
) -> None:
    variant = _variant({"aar_receiver_speed_kias": value})
    assert variant.aar_receiver_speed is not None
    assert variant.aar_receiver_speed.knots == pytest.approx(value)


def test_aar_receiver_speed_parses_valid_value() -> None:
    variant = _variant({"aar_receiver_speed_kias": 300})
    assert variant.aar_receiver_speed is not None
    assert variant.aar_receiver_speed.knots == pytest.approx(300)


@pytest.mark.parametrize(
    "value",
    ["300", True, False, 219, 351],
)
def test_aar_receiver_speed_ignores_malformed_values(value: object) -> None:
    variant = _variant({"aar_receiver_speed_kias": value})
    assert variant.aar_receiver_speed is None
