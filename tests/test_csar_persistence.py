"""Regression tests for CSAR persistence across save/reload.

Downed pilots are campaign-persistent state added by the seven-PR integration.
These tests pin the persistence contract: a downed pilot survives a pickle
round-trip with its identity intact, old-save DownedPilot/Coalition/Pilot
states gain the CSAR fields with safe defaults, and Game load rebuilds the
DownedPilot index from the per-coalition lists so the two cannot disagree.
"""

from __future__ import annotations

import pickle
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from dcs.mapping import Point
from dcs.terrain import Caucasus

from game.coalition import Coalition
from game.game import Game
from game.squadrons.downedpilot import DownedPilot
from game.squadrons.pilot import Pilot, PilotStatus
from game.squadrons.squadron import Squadron
from game.theater.player import Player


def _downed_pilot() -> tuple[Pilot, DownedPilot]:
    pilot = Pilot(name="Testy McTestFace", status=PilotStatus.Downed)
    squadron = Squadron.__new__(Squadron)
    squadron.id = uuid4()
    squadron.name = "010"
    position = Point(0.0, 0.0, Caucasus())
    downed = DownedPilot(
        pilot=pilot,
        squadron=squadron,
        _position=position,
        player=Player.BLUE,
        turn_downed=3,
        turns_remaining=2,
        was_player=False,
        aircraft_name="F/A-18C Hornet (Lot 20)",
        in_water=True,
    )
    return pilot, downed


def test_downed_pilot_survives_pickle_round_trip() -> None:
    """A downed pilot and its pilot roster entry keep id, position, fate and
    the in-water flag through save/reload."""
    pilot, downed = _downed_pilot()

    restored = pickle.loads(pickle.dumps(downed))

    assert restored.id == downed.id
    assert restored.pilot.name == pilot.name
    assert restored.pilot.status is PilotStatus.Downed
    assert restored.position.x == 0.0
    assert restored.position.y == 0.0
    assert restored.in_water is True
    assert restored.turns_remaining == 2
    assert restored == downed  # identity is the UUID, not object identity


def test_pre_csar_pilot_and_coalition_states_gain_defaults() -> None:
    """Saves from before CSAR lack the new fields; loading them must not crash
    and must produce usable defaults."""
    pilot = Pilot(name="Old Save Pilot")
    pilot.__setstate__({"name": "Old Save Pilot", "status": PilotStatus.Active})
    assert pilot.turns_until_available == 0
    assert pilot.held_at is None

    squadron = Squadron.__new__(Squadron)
    squadron.id = uuid4()
    squadron.name = "010"
    downed = DownedPilot.__new__(DownedPilot)
    downed.__setstate__(
        {
            "pilot": pilot,
            "squadron": squadron,
            "_position": Point(0.0, 0.0, Caucasus()),
            "player": Player.BLUE,
            "turn_downed": 1,
            "turns_remaining": 1,
            "was_player": False,
            "aircraft_name": "MiG-19P",
            "id": uuid4(),
            # No "in_water": simulates the older save format.
        }
    )
    assert downed.in_water is False

    coalition = cast(Coalition, SimpleNamespace())
    Coalition.__setstate__(coalition, {"player": Player.BLUE})
    assert coalition.downed_pilots == []


def test_rebuild_downed_pilot_index_is_authoritative() -> None:
    """On load the db index is rebuilt from the per-coalition lists, dropping
    stale entries so rescue lookups cannot hit a pilot no longer on the map."""
    _, blue_downed = _downed_pilot()
    _, red_downed = _downed_pilot()

    blue = cast(Any, SimpleNamespace(downed_pilots=[blue_downed]))
    red = cast(Any, SimpleNamespace(downed_pilots=[red_downed]))
    stale = SimpleNamespace(id=uuid4())

    db = SimpleNamespace(downed_pilots=SimpleNamespace(objects={stale.id: stale}))
    game = Game.__new__(Game)
    game.blue = blue
    game.red = red
    game.db = cast(Any, db)

    game._rebuild_downed_pilot_index()

    assert set(db.downed_pilots.objects) == {blue_downed.id, red_downed.id}
    assert db.downed_pilots.objects[blue_downed.id] is blue_downed


def test_pilot_rescue_cycle_returns_to_active() -> None:
    """The rescue half of the lifecycle: down -> recovering -> active."""
    pilot, _ = _downed_pilot()

    pilot.begin_recovery(2)
    assert pilot.status is PilotStatus.Recovering
    assert pilot.turns_until_available == 2
    pilot.advance_recovery()
    pilot.advance_recovery()
    assert pilot.status is PilotStatus.Active


def test_pilot_mia_cycle_frees_prisoner_into_recovery() -> None:
    """The missing half of the lifecycle: a pilot held as missing in action
    (the state a save persists) is freed by retaking their camp and lands in
    recovery with the captor cleared."""
    captor = uuid4()
    pilot = Pilot(
        name="Testy McTestFace",
        status=PilotStatus.MissingInAction,
        held_at=captor,
    )

    pilot.release_from_captivity(1)
    assert pilot.status is PilotStatus.Recovering
    assert pilot.held_at is None
