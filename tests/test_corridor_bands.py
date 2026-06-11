# tests/test_corridor_bands.py
"""Tests for front-line corridor band geometry + rendering."""

from __future__ import annotations

import math

from PIL import Image

from PIL import ImageDraw

from game.missiongenerator.kneeboard_recon.corridor_bands import (
    CapsuleGeometry,
    capsule_geometry,
    dashed_line,
    draw_corridor_bands,
)


def test_capsule_geometry_horizontal_segment() -> None:
    geom = capsule_geometry((100.0, 200.0), (300.0, 200.0), 40.0)
    assert isinstance(geom, CapsuleGeometry)
    assert geom.corners == [
        (100.0, 240.0),
        (300.0, 240.0),
        (300.0, 160.0),
        (100.0, 160.0),
    ]
    assert geom.cap_a == (60.0, 160.0, 140.0, 240.0)
    assert geom.cap_b == (260.0, 160.0, 340.0, 240.0)
    assert geom.cap_angle_deg == 90.0


def test_capsule_geometry_vertical_segment() -> None:
    geom = capsule_geometry((200.0, 100.0), (200.0, 300.0), 40.0)
    assert geom is not None
    assert geom.corners == [
        (160.0, 100.0),
        (160.0, 300.0),
        (240.0, 300.0),
        (240.0, 100.0),
    ]
    assert geom.cap_angle_deg == 180.0


def test_capsule_geometry_diagonal_radius_offset_is_perpendicular() -> None:
    a, b, r = (0.0, 0.0), (100.0, 100.0), 20.0
    geom = capsule_geometry(a, b, r)
    assert geom is not None
    off = math.hypot(geom.corners[0][0] - a[0], geom.corners[0][1] - a[1])
    assert math.isclose(off, r, abs_tol=1e-6)


def test_capsule_geometry_degenerate_returns_none() -> None:
    assert capsule_geometry((50.0, 50.0), (50.0, 50.0), 40.0) is None  # a == b
    assert capsule_geometry((0.0, 0.0), (100.0, 0.0), 0.0) is None  # r <= 0


_YELLOW = (255, 210, 40)


def _solid(size: tuple[int, int] = (400, 400)) -> Image.Image:
    return Image.new("RGB", size, (40, 40, 40))


def test_draw_corridor_bands_fills_inner_only_and_dots_outer() -> None:
    img = _solid()
    draw_corridor_bands(
        img,
        (100.0, 200.0),
        (300.0, 200.0),
        inner_r_px=30.0,
        outer_r_px=70.0,
        color=_YELLOW,
        inner_alpha=26,
    )
    base = 40
    # On the segment (inside inner pill): tinted toward yellow, R lifted most.
    inner_px = img.getpixel((200, 200))
    assert inner_px[0] > base and inner_px[2] <= base + 2
    # The outer band has NO fill: a point 50 px off the segment (between the
    # inner and outer radius, but clear of the outer dotted perimeter at 70 px)
    # is untouched.
    assert img.getpixel((200, 200 + 50)) == (base, base, base)
    # The outer perimeter is dotted, not solid: scanning along the top straight
    # edge (y = 200 - 70) we should hit both painted dots and untouched gaps.
    edge_y = 200 - 70
    edge_pixels = [img.getpixel((x, edge_y)) for x in range(110, 290)]
    painted = [px for px in edge_pixels if px != (base, base, base)]
    gaps = [px for px in edge_pixels if px == (base, base, base)]
    assert painted, "outer perimeter should paint some dots"
    assert gaps, "outer perimeter should leave gaps between dots (dotted, not solid)"
    # Well outside the outer pill: untouched.
    assert img.getpixel((200, 200 + 120)) == (base, base, base)


def test_dashed_line_alternates_paint_and_gaps() -> None:
    img = _solid((200, 20))
    dashed_line(ImageDraw.Draw(img), (5.0, 10.0), (195.0, 10.0), _YELLOW, width=1)
    base = 40
    row = [img.getpixel((x, 10)) for x in range(5, 195)]
    assert any(px != (base, base, base) for px in row), "expected painted dots"
    assert any(px == (base, base, base) for px in row), "expected gaps between dots"


def test_dashed_line_zero_length_is_noop() -> None:
    img = _solid((50, 50))
    dashed_line(ImageDraw.Draw(img), (25.0, 25.0), (25.0, 25.0), _YELLOW)
    assert img.getcolors() == [(50 * 50, (40, 40, 40))]


def test_draw_corridor_bands_degenerate_is_noop() -> None:
    img = _solid()
    draw_corridor_bands(
        img,
        (200.0, 200.0),
        (200.0, 200.0),  # coincident -> no capsule
        inner_r_px=30.0,
        outer_r_px=70.0,
        color=_YELLOW,
        inner_alpha=26,
    )
    assert img.getcolors() == [(400 * 400, (40, 40, 40))]
