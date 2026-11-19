"""Regression tests for CSAR migration of pre-CSAR saves.

The seven-PR integration adds campaign-persistent downed pilots on top of
geoff-main's existing squadron mission-type restrictions and QRA handling.
These tests pin the integration contract of Migrator._update_squadrons:
existing restrictions are preserved, CSAR is opted into exactly once for
capable squadrons, and a player who later removes CSAR keeps that choice.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import uuid4

from dcs.countries import countries_by_name

from game.ato.flighttype import FlightType
from game.migrator import Migrator
from game.squadrons.squadron import Squadron


def _old_save_squadron(
    auto_assignable: set[FlightType],
    capable: set[FlightType],
    csar_auto_assign_seeded: bool | None,
) -> Squadron:
    """A minimal squadron as it would load from a pre-CSAR save.

    Built without __init__ -- the migration path under test only touches the
    auto-assignable mission types, the one-time CSAR opt-in flag, and the
    parking-corruption check (which is deflected via a mock location).
    """
    squadron = Squadron.__new__(Squadron)
    squadron.id = uuid4()
    squadron.name = "010"
    squadron.auto_assignable_mission_types = set(auto_assignable)
    squadron.csar_auto_assign_seeded = csar_auto_assign_seeded  # type: ignore[assignment]

    aircraft = MagicMock()
    aircraft.task_priorities = {FlightType.BARCAP: 10}
    aircraft.helicopter = False
    aircraft.lha_capable = False
    aircraft.flyable = True
    aircraft.capable_of.side_effect = lambda task: task in capable
    squadron.aircraft = cast(Any, aircraft)

    squadron.country = countries_by_name["USA"]()
    squadron.location = MagicMock()
    squadron.location.unclaimed_parking.return_value = 12
    squadron.coalition = cast(
        Any,
        SimpleNamespace(
            game=SimpleNamespace(settings=SimpleNamespace(ground_start_ai_planes=True))
        ),
    )
    return squadron


def _migrator_over(squadron: Squadron) -> Migrator:
    cp = SimpleNamespace(squadrons=[squadron])
    migrator = Migrator.__new__(Migrator)
    migrator.game = cast(
        Any,
        SimpleNamespace(theater=SimpleNamespace(controlpoints=[cp]), coalitions=[]),
    )
    migrator.is_liberation = True
    return migrator


def test_migration_adds_csar_once_and_keeps_restrictions() -> None:
    """A pre-CSAR save with restricted mission types keeps its restrictions and
    gains CSAR exactly once, for a CSAR-capable squadron."""
    squadron = _old_save_squadron(
        auto_assignable={FlightType.BARCAP},
        capable={FlightType.BARCAP, FlightType.CSAR},
        csar_auto_assign_seeded=None,
    )

    _migrator_over(squadron)._update_squadrons()

    assert squadron.auto_assignable_mission_types == {
        FlightType.BARCAP,
        FlightType.CSAR,
    }
    assert squadron.csar_auto_assign_seeded is True


def test_migration_does_not_re_add_csar_player_turned_off() -> None:
    """A player who removed CSAR from a previously seeded squadron keeps that
    choice on the next load."""
    squadron = _old_save_squadron(
        auto_assignable={FlightType.BARCAP},
        capable={FlightType.BARCAP, FlightType.CSAR},
        csar_auto_assign_seeded=True,
    )

    _migrator_over(squadron)._update_squadrons()

    assert squadron.auto_assignable_mission_types == {FlightType.BARCAP}
    assert squadron.csar_auto_assign_seeded is True


def test_migration_leaves_csar_incapable_squadron_alone() -> None:
    """Squadrons whose airframe cannot fly CSAR never gain it."""
    squadron = _old_save_squadron(
        auto_assignable={FlightType.BARCAP},
        capable={FlightType.BARCAP},
        csar_auto_assign_seeded=None,
    )

    _migrator_over(squadron)._update_squadrons()

    assert squadron.auto_assignable_mission_types == {FlightType.BARCAP}
    assert squadron.csar_auto_assign_seeded is True
