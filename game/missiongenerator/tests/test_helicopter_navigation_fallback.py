from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from dcs.mapping import Point
from unittest.mock import patch

from PIL import Image
from dcs.terrain.caucasus.caucasus import Caucasus

from game.missiongenerator.aircraft.flightdata import FlightData
from game.missiongenerator.helicopter_navigation import RouteLeg
from game.missiongenerator.helicopter_navigation_pages import (
    MAP_SIZE,
    NavigationMapPage,
)
from game.missiongenerator.kneeboard_recon import basemap
from game.missiongenerator.kneeboard_recon.extent import MapExtent


def test_navigation_page_keeps_real_offline_basemap_stamp(tmp_path: Path) -> None:
    terrain = Caucasus()
    extent = MapExtent(-10_000, 10_000, -10_000, 10_000, terrain)
    flight = cast(
        FlightData,
        SimpleNamespace(
            callsign="OFFLINE 1", aircraft_type=SimpleNamespace(variant_id="UH-1H")
        ),
    )
    leg = RouteLeg(
        1,
        "Start",
        "End",
        Point(0, 0, terrain),
        Point(1_000, 1_000, terrain),
    )
    page = NavigationMapPage(flight, [leg], [], terrain, False, 1, 1, {0: 0, 1: 1})

    with patch.object(basemap, "render_tiles", return_value=None), patch.object(
        basemap, "_load_gif", return_value=None
    ), patch(
        "game.missiongenerator.helicopter_navigation_pages.tile_cache_dir",
        return_value=tmp_path,
    ):
        page_path = tmp_path / "offline.png"
        page.write(page_path)

    image = Image.open(page_path)
    assert image.size == (960, 1080)
    # The real basemap fallback stamps a red OFFLINE banner across the top.
    red_pixels = sum(
        1
        for x in range(MAP_SIZE[0])
        if (pixel := image.getpixel((x, 6)))[0] > pixel[1] + 40
        and pixel[0] > pixel[2] + 40
    )
    assert red_pixels > 100
