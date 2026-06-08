from game.ato.flighttype import FlightType
from game.missiongenerator.aircraft.seadpackageclear import (
    PACKAGE_CLEAR_ZONE_BUFFER,
    is_gating_flight,
    package_clear_zone_radius_meters,
)


def test_buffer_constant_is_25_percent() -> None:
    assert PACKAGE_CLEAR_ZONE_BUFFER == 1.25


def test_zone_radius_applies_buffer_to_threat_range() -> None:
    # 40 km WEZ + 25% buffer -> 50 km zone.
    assert package_clear_zone_radius_meters(40000.0) == 50000.0


def test_zone_radius_none_when_target_has_no_threat() -> None:
    # A target with no live threat range yields no zone (package-clear is skipped
    # and the backstop timer remains the sole terminator).
    assert package_clear_zone_radius_meters(0.0) is None


def test_plain_sead_does_not_gate_itself() -> None:
    # The loiter flight (and any other plain-SEAD flight) must not gate break-off.
    assert is_gating_flight(FlightType.SEAD) is False


def test_strikers_and_dead_are_gating_flights() -> None:
    # The flights SEAD covers through the WEZ are what gate its departure.
    assert is_gating_flight(FlightType.STRIKE) is True
    assert is_gating_flight(FlightType.DEAD) is True


def test_other_sead_role_flights_still_gate() -> None:
    # SEAD_SWEEP / SEAD_ESCORT transit the target area like strikers, so they gate the
    # loiter's break-off; only plain SEAD (the loiter itself) is excluded.
    assert is_gating_flight(FlightType.SEAD_SWEEP) is True
    assert is_gating_flight(FlightType.SEAD_ESCORT) is True


def test_zone_radius_none_for_negative_threat_range() -> None:
    # The guard is `<= 0`, so a negative range (not just zero) yields no zone.
    assert package_clear_zone_radius_meters(-1.0) is None
