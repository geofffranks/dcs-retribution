from __future__ import annotations

from dcs.mapping import Point

from game.missiongenerator.flotgenerator import frontline_segment_from_bounds
from game.missiongenerator.missiondata import JtacInfo
from game.radio.radios import MHz
from game.theater.player import Player


def test_jtacinfo_carries_optional_frontline_segment() -> None:
    point_a = Point(10.0, 20.0, None)  # type: ignore[arg-type]
    point_b = Point(30.0, 40.0, None)  # type: ignore[arg-type]
    info = JtacInfo(
        group_name="JTAC GROUP",
        unit_name="JTAC UNIT",
        callsign="Axeman 1",
        region="Frontline Alpha/Bravo",
        code="1688",
        blue=Player.BLUE,
        freq=MHz(30),
        frontline_segment=(point_a, point_b),
    )
    assert info.frontline_segment == (point_a, point_b)


def test_frontline_segment_from_bounds_returns_left_right() -> None:
    class FakeBounds:
        left_position = Point(1.0, 2.0, None)  # type: ignore[arg-type]
        right_position = Point(3.0, 4.0, None)  # type: ignore[arg-type]

    seg = frontline_segment_from_bounds(FakeBounds())  # type: ignore[arg-type]
    assert seg == (FakeBounds.left_position, FakeBounds.right_position)


def test_jtacinfo_frontline_segment_defaults_none() -> None:
    info = JtacInfo(
        group_name="JTAC GROUP",
        unit_name="JTAC UNIT",
        callsign="Axeman 1",
        region="Frontline Alpha/Bravo",
        code="1688",
        blue=Player.BLUE,
        freq=MHz(30),
    )
    assert info.frontline_segment is None
