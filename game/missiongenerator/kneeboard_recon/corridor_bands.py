# game/missiongenerator/kneeboard_recon/corridor_bands.py
"""Front-line corridor highlight bands for the CAS recon kneeboard.

Draws the JTAC autolase boxing corridor as two nested translucent "pill"
(capsule / stadium) bands hugging the front-line segment: an inner combat
box and a wider artillery box. A capsule is the locus "distance to the
segment <= r", matching the autolase distance-to-frontline box filter.

Geometry is pure pixel space; the caller pre-converts metres to pixels via
``Projector.meters_to_px`` (uniform under ``square_extent``, so pills stay
true).
"""

from __future__ import annotations

import math
from typing import NamedTuple, Optional

from PIL import Image, ImageDraw

# Page-axis pixel coordinate (page x grows right, page y grows down).
PixelPoint = tuple[float, float]


class CapsuleGeometry(NamedTuple):
    """Drawable parts of a capsule of radius r around a segment a -> b."""

    corners: list[PixelPoint]  # rect: a+perp, b+perp, b-perp, a-perp
    cap_a: tuple[float, float, float, float]  # ellipse bbox at endpoint a
    cap_b: tuple[float, float, float, float]  # ellipse bbox at endpoint b
    cap_angle_deg: float  # perpendicular angle, for the rounded-cap arcs


def capsule_geometry(
    a_px: PixelPoint, b_px: PixelPoint, r_px: float
) -> Optional[CapsuleGeometry]:
    """Return the rect corners + endpoint-circle bboxes for a capsule of radius
    ``r_px`` around segment ``a_px`` -> ``b_px``.

    Returns ``None`` for a degenerate capsule (coincident endpoints or
    non-positive radius) so callers can no-op cleanly.
    """
    ax, ay = a_px
    bx, by = b_px
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length <= 0.0 or r_px <= 0.0:
        return None
    ux, uy = dx / length, dy / length
    px, py = -uy, ux  # unit perpendicular
    ox, oy = px * r_px, py * r_px
    corners: list[PixelPoint] = [
        (ax + ox, ay + oy),
        (bx + ox, by + oy),
        (bx - ox, by - oy),
        (ax - ox, ay - oy),
    ]
    cap_a = (ax - r_px, ay - r_px, ax + r_px, ay + r_px)
    cap_b = (bx - r_px, by - r_px, bx + r_px, by + r_px)
    cap_angle_deg = math.degrees(math.atan2(py, px))
    return CapsuleGeometry(corners, cap_a, cap_b, cap_angle_deg)


Color = tuple[int, int, int]


def _paste_capsule(
    basemap: Image.Image, geom: CapsuleGeometry, color: Color, alpha: int
) -> None:
    """Blend ``color`` at ``alpha`` over the capsule via an L-mask.

    One paste per zone (mask filled at ``alpha``, single RGB layer pasted
    through it) so overlapping rect+cap regions never double-darken.
    """
    mask = Image.new("L", basemap.size, 0)
    md = ImageDraw.Draw(mask)
    md.polygon(geom.corners, fill=alpha)
    md.ellipse(geom.cap_a, fill=alpha)
    md.ellipse(geom.cap_b, fill=alpha)
    basemap.paste(Image.new("RGB", basemap.size, color), (0, 0), mask)


def _outline_capsule(
    draw: ImageDraw.ImageDraw, geom: CapsuleGeometry, color: Color
) -> None:
    """Draw the two offset edges + the two rounded end-caps at full opacity."""
    c = geom.corners
    draw.line([c[0], c[1]], fill=color, width=2)
    draw.line([c[3], c[2]], fill=color, width=2)
    a = geom.cap_angle_deg
    draw.arc(geom.cap_a, a, a + 180, fill=color, width=2)
    draw.arc(geom.cap_b, a - 180, a, fill=color, width=2)


# Dotted-perimeter dash geometry (pixels). A short dash with a wider gap reads
# as a dotted line at the kneeboard's render scale; reused by the page legend
# swatch so the legend matches the map exactly.
DOT_PX = 2.0
DOT_GAP_PX = 5.0


def dashed_line(
    draw: ImageDraw.ImageDraw,
    p0: PixelPoint,
    p1: PixelPoint,
    color: Color,
    width: int = 2,
    *,
    dash_px: float = DOT_PX,
    gap_px: float = DOT_GAP_PX,
) -> None:
    """Draw a dotted straight segment p0 -> p1 by stepping dash/gap along it."""
    x0, y0 = p0
    x1, y1 = p1
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= 0.0:
        return
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    period = dash_px + gap_px
    dist = 0.0
    while dist < length:
        end = min(dist + dash_px, length)
        draw.line(
            [(x0 + ux * dist, y0 + uy * dist), (x0 + ux * end, y0 + uy * end)],
            fill=color,
            width=width,
        )
        dist += period


def _dashed_arc(
    draw: ImageDraw.ImageDraw,
    bbox: tuple[float, float, float, float],
    start_deg: float,
    end_deg: float,
    color: Color,
    width: int = 2,
    *,
    dash_px: float = DOT_PX,
    gap_px: float = DOT_GAP_PX,
) -> None:
    """Dotted arc over [start_deg, end_deg], dash count scaled by arc length so
    the dot spacing matches the straight edges regardless of cap radius."""
    r_px = (bbox[2] - bbox[0]) / 2.0
    span = end_deg - start_deg
    arc_len = abs(math.radians(span)) * r_px
    period = dash_px + gap_px
    if arc_len <= 0.0 or period <= 0.0:
        return
    n = max(1, int(round(arc_len / period)))
    dash_frac = dash_px / period
    step = span / n
    for i in range(n):
        a0 = start_deg + i * step
        draw.arc(bbox, start=a0, end=a0 + step * dash_frac, fill=color, width=width)


def _dotted_outline_capsule(
    draw: ImageDraw.ImageDraw, geom: CapsuleGeometry, color: Color
) -> None:
    """Draw the capsule perimeter (two edges + two end-cap arcs) as a dotted
    line at full opacity."""
    c = geom.corners
    dashed_line(draw, c[0], c[1], color)
    dashed_line(draw, c[3], c[2], color)
    a = geom.cap_angle_deg
    _dashed_arc(draw, geom.cap_a, a, a + 180, color)
    _dashed_arc(draw, geom.cap_b, a - 180, a, color)


def draw_corridor_bands(
    basemap: Image.Image,
    a_px: PixelPoint,
    b_px: PixelPoint,
    *,
    inner_r_px: float,
    outer_r_px: float,
    color: Color,
    inner_alpha: int,
) -> None:
    """Render the corridor as an inner translucent pill (combat box) plus an
    outer dotted-perimeter pill (artillery box) around segment a -> b.

    The inner pill keeps its translucent fill + solid outline; the outer pill
    is a dotted perimeter with no fill, so it frames the artillery box without
    dimming the map. Drawn inner-fill first, then both outlines (outer dotted,
    inner solid). Any degenerate capsule (coincident endpoints / non-positive
    radius) is skipped, so a degenerate front line renders the page unchanged.
    """
    outer = capsule_geometry(a_px, b_px, outer_r_px)
    inner = capsule_geometry(a_px, b_px, inner_r_px)
    if inner is not None:
        _paste_capsule(basemap, inner, color, inner_alpha)
    draw = ImageDraw.Draw(basemap)
    if outer is not None:
        _dotted_outline_capsule(draw, outer, color)
    if inner is not None:
        _outline_capsule(draw, inner, color)
