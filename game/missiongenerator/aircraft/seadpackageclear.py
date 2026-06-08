from __future__ import annotations

from dcs.action import SetFlag
from dcs.condition import (
    AllOfGroupOutsideZone,
    FlagIsTrue,
    GroupDead,
    PartOfGroupInZone,
)
from dcs.mission import Mission
from dcs.triggers import Event, TriggerOnce

from game.ato.flight import Flight
from game.ato.flighttype import FlightType
from game.ato.package import Package
from game.theater import TheaterGroundObject

#: WEZ-radius multiplier for the package-clear trigger zone. The zone is the target's
#: weapon-engagement range plus a 25% buffer, so strikers that skirt the envelope edge
#: still register as having transited it. Deliberately a constant, not a user setting
#: (see spec 2026-06-06-sead-loiter-and-react-design.md sec 4.2/4.4).
PACKAGE_CLEAR_ZONE_BUFFER = 1.25


def package_clear_zone_radius_meters(threat_range_m: float) -> float | None:
    """Radius (m) of the package-clear zone for a target with the given threat range.

    Returns ``None`` when the target has no live threat range (``<= 0``): there is no
    meaningful WEZ to transit, so package-clear is skipped and the loiter falls back to
    its backstop timer.
    """
    if threat_range_m <= 0:
        return None
    return threat_range_m * PACKAGE_CLEAR_ZONE_BUFFER


def is_gating_flight(flight_type: FlightType) -> bool:
    """Whether a flight of this type gates the SEAD loiter's package-clear break-off.

    A gating flight is one whose transit of the target zone the loiter waits on before
    going home. Only plain ``FlightType.SEAD`` is excluded -- those flights *are* the
    loiter(s) doing the waiting, so a SEAD flight must never gate itself or a sibling
    SEAD loiter (which would deadlock). Every other type gates, including ``SEAD_SWEEP``
    and ``SEAD_ESCORT``: they ingress and egress the target area like the strikers, so
    the loiter should remain on station until they too have cleared.
    """
    return flight_type is not FlightType.SEAD


def generate_sead_package_clear_triggers(
    package: Package,
    spawned_flights: list[Flight],
    mission: Mission,
) -> None:
    """Emit triggers that RTB a plain-SEAD loiter once the package has cleared the WEZ.

    The loiter already stops when the package flag ``id(package)`` is set (phase 1 sets
    it from a backstop timer). This adds, for each non-SEAD flight that spawned, an
    "entered" flag (set when the flight enters the target WEZ+buffer zone) and a
    "cleared" flag (set when it has entered and then left, or has died). When every
    gating flight is cleared, the package flag is set and the loiter breaks off.

    No-ops (leaving the backstop as the sole terminator) when: the target is not a
    ground object, the package has no plain-SEAD flight to break off, the target has no
    threat range, or there are no other (gating) flights.

    Call once per package: trigger and zone names are keyed by ``id(package)`` /
    ``id(flight)`` and are not de-duplicated.
    """
    target = package.target
    if not isinstance(target, TheaterGroundObject):
        return
    if not any(f.flight_type is FlightType.SEAD for f in spawned_flights):
        return
    radius = package_clear_zone_radius_meters(target.max_threat_range().meters)
    if radius is None:
        return
    # Skip flights whose group was not actually spawned (group_id stays 0 until
    # spawn); referencing group 0 in a trigger would match the wrong group.
    gating = [
        f
        for f in spawned_flights
        if is_gating_flight(f.flight_type) and f.group_id != 0
    ]
    if not gating:
        return

    zone = mission.triggers.add_triggerzone(
        target.position, radius=radius, hidden=True, name=f"SeadClear{id(package)}"
    )

    cleared_flags: list[str] = []
    for flight in gating:
        entered_flag = f"sead-clear-entered-{id(flight)}"
        cleared_flag = f"sead-clear-cleared-{id(flight)}"
        cleared_flags.append(cleared_flag)

        entered = TriggerOnce(Event.NoEvent, f"SeadClearEntered{id(flight)}")
        entered.add_condition(PartOfGroupInZone(flight.group_id, zone.id))
        entered.add_action(SetFlag(entered_flag))
        mission.triggerrules.triggers.append(entered)

        # A flight clears in either of two independent ways, so each sets the cleared
        # flag from its own trigger (an unambiguous OR): it entered then left, or its
        # group died -- GroupDead stands alone, since a dead group can't exit a zone.
        exited = TriggerOnce(Event.NoEvent, f"SeadClearExited{id(flight)}")
        exited.add_condition(FlagIsTrue(flag=entered_flag))
        exited.add_condition(AllOfGroupOutsideZone(flight.group_id, zone.id))
        exited.add_action(SetFlag(cleared_flag))
        mission.triggerrules.triggers.append(exited)

        died = TriggerOnce(Event.NoEvent, f"SeadClearDied{id(flight)}")
        died.add_condition(GroupDead(flight.group_id))
        died.add_action(SetFlag(cleared_flag))
        mission.triggerrules.triggers.append(died)

    final = TriggerOnce(Event.NoEvent, f"SeadClearAll{id(package)}")
    for cleared_flag in cleared_flags:
        final.add_condition(FlagIsTrue(flag=cleared_flag))
    final.add_action(SetFlag(id(package)))
    mission.triggerrules.triggers.append(final)
