import os
import pytest

from shapely.geometry import MultiPolygon, Polygon

from dcs.mapping import Point
from dcs.terrain.caucasus.caucasus import Caucasus
from game.theater import landmap


def test_miz() -> None:
    """
    Test miz generation and loading
    """
    test_map = landmap.Landmap(
        inclusion_zones=MultiPolygon([Polygon([(0, 0), (0, 1), (1, 0)])]),
        exclusion_zones=MultiPolygon([Polygon([(1, 1), (0, 1), (1, 0)])]),
        sea_zones=MultiPolygon([Polygon([(0, 0), (0, 2), (1, 0)])]),
    )
    test_filename = "test.miz"
    landmap.to_miz(test_map, Caucasus(), test_filename)
    assert os.path.isfile("test.miz")
    loaded_map = landmap.from_miz("test.miz")
    assert test_map.inclusion_zones.equals_exact(
        loaded_map.inclusion_zones, tolerance=1e-6
    )
    assert test_map.sea_zones.equals_exact(loaded_map.sea_zones, tolerance=1e-6)
    assert test_map.exclusion_zones.equals_exact(
        loaded_map.exclusion_zones, tolerance=1e-6
    )

    if os.path.isfile(test_filename):
        os.remove(test_filename)


def _landmap() -> landmap.Landmap:
    return landmap.Landmap(
        inclusion_zones=MultiPolygon(
            [Polygon([(0, 0), (0, 100), (100, 100), (100, 0)])]
        ),
        exclusion_zones=MultiPolygon(),
        sea_zones=MultiPolygon(),
    )


def test_distance_to_land_returns_nearest_boundary_distance() -> None:
    assert landmap.distance_to_land(Point(150, 50, None), _landmap()) == 50


def test_distance_to_land_returns_zero_for_land_point() -> None:
    assert landmap.distance_to_land(Point(50, 50, None), _landmap()) == 0


def test_distance_to_land_uses_land_after_exclusions_and_sea_zones() -> None:
    zones = landmap.Landmap(
        inclusion_zones=MultiPolygon(
            [Polygon([(0, 0), (0, 100), (100, 100), (100, 0)])]
        ),
        exclusion_zones=MultiPolygon(
            [Polygon([(40, 40), (40, 60), (60, 60), (60, 40)])]
        ),
        sea_zones=MultiPolygon(),
    )

    assert landmap.distance_to_land(Point(50, 50, None), zones) == 10


def test_distance_to_land_returns_none_without_landmap() -> None:
    assert landmap.distance_to_land(Point(0, 0, None), None) is None


def test_distance_to_land_returns_none_for_empty_land_geometry() -> None:
    empty_landmap = landmap.Landmap(
        inclusion_zones=MultiPolygon(),
        exclusion_zones=MultiPolygon(),
        sea_zones=MultiPolygon(),
    )

    assert landmap.distance_to_land(Point(0, 0, None), empty_landmap) is None


def test_distance_to_land_does_not_mutate_landmap() -> None:
    zones = _landmap()
    inclusion_zones_before = zones.inclusion_zones.wkb
    exclusion_zones_before = zones.exclusion_zones.wkb
    sea_zones_before = zones.sea_zones.wkb

    landmap.distance_to_land(Point(150, 50, None), zones)

    assert zones.inclusion_zones.wkb == inclusion_zones_before
    assert zones.exclusion_zones.wkb == exclusion_zones_before
    assert zones.sea_zones.wkb == sea_zones_before
