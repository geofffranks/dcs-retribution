from dcs.mapping import Point

from game.theater.controlpoint import Lha, OffMapSpawn, Player
from game.theater.presetlocation import PresetLocation
from game.theater.theatergroundobject import ShipGroundObject
from types import SimpleNamespace
from game.utils import Heading, nautical_miles


def _ship() -> ShipGroundObject:
    location = PresetLocation(
        name="loc", position=Point(0, 0, None), heading=Heading(0)  # type: ignore[arg-type]
    )
    cp = OffMapSpawn(
        name="cp",
        position=Point(0, 0, None),  # type: ignore[arg-type]
        theater=None,  # type: ignore[arg-type]
        starts_blue=Player.BLUE,
    )
    return ShipGroundObject(name="ship", location=location, control_point=cp)


def test_ship_is_moveable_with_80nm_cap() -> None:
    ship = _ship()
    assert ship.moveable is True
    assert ship.max_move_distance == nautical_miles(80)


def test_ship_target_position_defaults_none() -> None:
    assert _ship().target_position is None


def test_destination_in_range_boundary() -> None:
    ship = _ship()  # ship at (0, 0)
    # 80nm in meters; just inside vs just outside.
    inside = Point(nautical_miles(80).meters - 1.0, 0, None)  # type: ignore[arg-type]
    outside = Point(nautical_miles(80).meters + 1.0, 0, None)  # type: ignore[arg-type]
    assert ship.destination_in_range(inside) is True
    assert ship.destination_in_range(outside) is False


def test_ordinary_ship_ignores_carrier_standoff() -> None:
    ship = _ship()
    ship.control_point.theater = SimpleNamespace(  # type: ignore[assignment]
        landmap=SimpleNamespace(distance_to_land=lambda point: 1.0),
    )
    ship.control_point._coalition = SimpleNamespace(  # type: ignore[assignment]
        game=SimpleNamespace(settings=SimpleNamespace(carrier_min_standoff_distance=60))
    )
    destination = Point(nautical_miles(10).meters, 0, None)  # type: ignore[arg-type]
    assert ship.destination_in_range(destination) is True


def test_ship_attached_to_lha_inherits_carrier_standoff() -> None:
    lha = _lha()
    location = PresetLocation(
        name="escort", position=Point(0, 0, None), heading=Heading(0)  # type: ignore[arg-type]
    )
    ship = ShipGroundObject(name="escort", location=location, control_point=lha)
    destination = Point(nautical_miles(10).meters, 0, None)  # type: ignore[arg-type]

    assert ship.destination_within_carrier_standoff(destination) is False


def _lha(shore_distance_nm: int = 10) -> Lha:
    lha = Lha(
        name="lha",
        at=Point(0, 0, None),  # type: ignore[arg-type]
        theater=None,  # type: ignore[arg-type]
        starts_blue=Player.BLUE,
    )
    lha.theater = SimpleNamespace(  # type: ignore[assignment]
        landmap=SimpleNamespace(
            distance_to_land=lambda point: nautical_miles(shore_distance_nm).meters
        ),
    )
    lha._coalition = SimpleNamespace(  # type: ignore[assignment]
        game=SimpleNamespace(settings=SimpleNamespace(carrier_min_standoff_distance=60))
    )
    return lha


def test_lha_rejects_destination_inside_carrier_standoff() -> None:
    destination = Point(nautical_miles(10).meters, 0, None)  # type: ignore[arg-type]
    assert _lha().destination_in_range(destination) is False


def test_lha_accepts_destination_outside_carrier_standoff() -> None:
    lha = _lha(shore_distance_nm=70)
    destination = Point(nautical_miles(10).meters, 0, None)  # type: ignore[arg-type]
    assert lha.destination_in_range(destination) is True
