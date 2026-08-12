from __future__ import annotations

import importlib
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import ANY, MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from game.data.units import UnitClass
from game.dcs.groundunittype import GroundUnitType
from game.ground_forces.ai_ground_planner import (
    GroundPlanner,
    deployable_armor,
    reserve_armor_for,
)
from game.ground_forces.combat_stance import CombatStance
from game.purchaseadapter import GroundUnitPurchaseAdapter
from game.sim.gameupdateevents import GameUpdateEvents
from game.theater.base import Base
from game.theater.player import Player
from game.transfers import PendingTransfers, TransferOrder
from game.theater.transitnetwork import TransitConnection
from qt_ui.models import TransferModel
from qt_ui.windows.settings.QSettingsWindow import CheatSettingsBox

if TYPE_CHECKING:
    from game.theater import ControlPoint


def _unit(
    unit_class: UnitClass = UnitClass.TANK, variant_id: str = "Test unit"
) -> MagicMock:
    unit = MagicMock(spec=GroundUnitType)
    unit.unit_class = unit_class
    unit.variant_id = variant_id
    unit.price = 5
    return unit


def _cp(armor: dict[MagicMock, int], limit: int, has_enemy: bool) -> ControlPoint:
    # `captured` sentinels MUST be identity-distinct: SimpleNamespace() instances
    # compare EQUAL by content, which would make the `p.captured != cp.captured`
    # enemy gate vacuously false. Use object().
    own = object()
    enemy = SimpleNamespace(captured=object(), id=object(), name="Enemy")
    base = SimpleNamespace(armor=armor, total_armor=sum(armor.values()))
    return cast(
        "ControlPoint",
        SimpleNamespace(
            captured=own,
            connected_points=[enemy] if has_enemy else [],
            frontline_unit_count_limit=limit,
            base=base,
            name="Test CP",
            stances={enemy.id: CombatStance.DEFENSIVE},
        ),
    )


def test_rear_cp_reserves_full_pool() -> None:
    tank = _unit()
    cp = _cp({tank: 12}, limit=8, has_enemy=False)
    assert reserve_armor_for(cp) == {tank: 12}


def test_unknown_unit_class_stays_in_reserve() -> None:
    bogus = _unit()
    bogus.unit_class = object()
    cp = _cp({bogus: 7}, limit=10, has_enemy=True)
    assert deployable_armor(cp) == {}
    assert reserve_armor_for(cp) == {bogus: 7}


def test_reserve_matches_ground_planner_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alpha = _unit(variant_id="Alpha")
    bravo = _unit(variant_id="Bravo")
    charlie = _unit(variant_id="Charlie")
    armor = {charlie: 1, alpha: 4, bravo: 2}
    cp = _cp(armor, limit=4, has_enemy=True)
    planner = GroundPlanner(cp, cast(Any, SimpleNamespace()))
    monkeypatch.setattr(
        "game.ground_forces.ai_ground_planner.random.choice", lambda values: values[0]
    )

    planner.plan_groundwar()

    planned: dict[GroundUnitType, int] = {}
    for group in planner.tank_groups:
        planned[group.unit_type] = planned.get(group.unit_type, 0) + group.size
    reserve = reserve_armor_for(cp)
    assert planned == {
        unit_type: count - reserve.get(unit_type, 0)
        for unit_type, count in armor.items()
        if count - reserve.get(unit_type, 0) > 0
    }


def test_opfor_reserve_sale_requires_cheat_and_updates_inventory() -> None:
    unit = _unit()
    base = Base()
    base.commission_units({unit: 2})
    cp = SimpleNamespace(
        base=base,
        captured=Player.RED,
        ground_unit_orders=SimpleNamespace(pending_orders=lambda _item: 0),
    )
    coalition = SimpleNamespace(player=Player.RED, budget=10)
    coalition.adjust_budget = lambda amount: setattr(
        coalition, "budget", coalition.budget + amount
    )

    # Disabled: cheat setting off.
    game_off = SimpleNamespace(settings=SimpleNamespace(enable_enemy_buy_sell=False))
    disabled = GroundUnitPurchaseAdapter(
        cast(Any, cp), cast(Any, coalition), cast(Any, game_off)
    )
    assert disabled.can_sell(unit) is False

    # Enabled: cheat setting on. The dynamic check is used, not the
    # construction-time snapshot.
    game_on = SimpleNamespace(settings=SimpleNamespace(enable_enemy_buy_sell=True))
    enabled = GroundUnitPurchaseAdapter(
        cast(Any, cp), cast(Any, coalition), cast(Any, game_on)
    )
    assert enabled.can_sell(unit) is True
    enabled.sell(unit, 1)
    assert base.total_units_of_type(unit) == 1
    assert coalition.budget == 10 + unit.price


def test_blue_ground_unit_sale_remains_unavailable() -> None:
    unit = _unit()
    base = Base()
    base.commission_units({unit: 1})
    cp = SimpleNamespace(base=base, captured=Player.BLUE)
    coalition = SimpleNamespace(player=Player.BLUE, budget=10)
    adapter = GroundUnitPurchaseAdapter(
        cast(Any, cp),
        cast(Any, coalition),
        cast(
            Any, SimpleNamespace(settings=SimpleNamespace(enable_enemy_buy_sell=True))
        ),
    )

    assert adapter.can_sell(unit) is False
    assert base.total_units_of_type(unit) == 1


def test_empty_reserve_has_no_sellable_units() -> None:
    unit = _unit()
    base = Base()
    cp = SimpleNamespace(base=base, captured=Player.RED)
    coalition = SimpleNamespace(player=Player.RED, budget=10)
    adapter = GroundUnitPurchaseAdapter(
        cast(Any, cp),
        cast(Any, coalition),
        cast(
            Any, SimpleNamespace(settings=SimpleNamespace(enable_enemy_buy_sell=True))
        ),
    )

    assert adapter.current_quantity_of(unit) == 0
    assert adapter.can_sell(unit) is False


@pytest.fixture
def app() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def test_cheat_menu_uses_exact_opfor_transfer_label(app: QApplication) -> None:
    settings = SimpleNamespace(
        enable_frontline_cheats=False,
        enable_base_capture_cheat=False,
        enable_runway_state_cheat=False,
        enable_transfer_cheat=False,
        enable_air_wing_adjustments=False,
        enable_enemy_buy_sell=False,
    )
    container = SimpleNamespace(settings=settings)
    box = CheatSettingsBox(cast(Any, container), lambda: None)

    label = box.redfor_buysell_cheat.itemAt(0).widget()
    assert cast(Any, label).text() == "Enable OPFOR Buy/Sell/Transfer Cheat"


def test_dialog_submit_handler_registers_transfer_and_closes() -> None:
    unit = _unit()

    class ControlPointStub:
        def __init__(self, name: str) -> None:
            self.name = name
            self.captured = Player.RED
            self.base = Base()
            self.ground_objects: list[object] = []
            self.position = object()

    origin = ControlPointStub("Red Origin")
    destination = ControlPointStub("Red Destination")
    origin.base.commission_units({unit: 2})

    class Network:
        def shortest_path_between(
            self, _origin: object, _destination: object
        ) -> list[object]:
            return [destination]

        def link_type(self, _origin: object, _destination: object) -> TransitConnection:
            return TransitConnection.Road

    red_pending = PendingTransfers(
        cast(Any, SimpleNamespace(transit_network_for=lambda _player: Network())),
        Player.RED,
    )
    cast(Any, red_pending).arrange_transport = lambda _transfer, _now, _events: None
    submitted_times: list[datetime] = []

    def new_transfer(transfer: TransferOrder, now: datetime) -> None:
        submitted_times.append(now)
        red_pending.new_transfer(transfer, now, GameUpdateEvents())

    now = datetime(2024, 1, 1, 12, 0)
    game_model = SimpleNamespace(
        game=SimpleNamespace(
            coalition_for=lambda player: SimpleNamespace(
                transfers=red_pending if player is Player.RED else None
            )
        ),
        sim_controller=SimpleNamespace(current_time_in_sim=now),
        transfer_model=SimpleNamespace(new_transfer=new_transfer),
    )
    selected_units = {unit: 1, _unit(UnitClass.APC): 0}
    close_calls: list[None] = []
    dialog = SimpleNamespace(
        game_model=game_model,
        origin=origin,
        dest_panel=SimpleNamespace(current=destination, request_airlift=True),
        transfer_panel=SimpleNamespace(transfers=selected_units),
        close=lambda: close_calls.append(None),
    )

    dialog_module = importlib.import_module(
        "qt_ui.windows.basemenu.NewUnitTransferDialog"
    )
    dialog_module.NewUnitTransferDialog.on_submit(dialog)

    transfer = red_pending.pending_transfers[0]
    assert transfer.destination is destination
    assert transfer.units == {unit: 1}
    assert transfer.request_airflift is True
    assert submitted_times == [now]
    assert origin.base.total_units_of_type(unit) == 1
    assert transfer.position is origin
    assert origin.base.total_units_of_type(unit) + transfer.units[unit] == 2
    assert close_calls == [None]

    red_pending.cancel_transfer(transfer, GameUpdateEvents())
    assert red_pending.pending_transfers == []
    assert origin.base.total_units_of_type(unit) == 2


def test_opfor_transfer_model_registers_and_cancels_in_red_collection() -> None:
    red_transfers = MagicMock()
    red_transfers.pending_transfer_count = 0
    blue_transfers = MagicMock()
    blue_transfers.pending_transfer_count = 0
    game = SimpleNamespace(
        settings=SimpleNamespace(enable_enemy_buy_sell=True),
        coalition_for=lambda player: SimpleNamespace(
            transfers=red_transfers if player is Player.RED else blue_transfers
        ),
    )
    model = TransferModel(cast(Any, SimpleNamespace(game=game)))
    transfer = cast(
        Any,
        SimpleNamespace(origin=SimpleNamespace(captured=Player.RED), player=Player.RED),
    )

    model.new_transfer(transfer, SimpleNamespace())

    red_transfers.new_transfer.assert_called_once_with(transfer, SimpleNamespace(), ANY)
    blue_transfers.new_transfer.assert_not_called()


def test_opfor_transfer_model_can_cancel_transfer_without_player_owned() -> None:
    red_transfers = MagicMock()
    red_transfers.pending_transfers = []
    blue_transfers = MagicMock()
    blue_transfers.pending_transfers = []
    game = SimpleNamespace(
        settings=SimpleNamespace(enable_enemy_buy_sell=True),
        coalition_for=lambda player: SimpleNamespace(
            transfers=red_transfers if player is Player.RED else blue_transfers
        ),
    )
    model = TransferModel(cast(Any, SimpleNamespace(game=game)))
    transfer = cast(
        Any,
        SimpleNamespace(
            origin=SimpleNamespace(captured=Player.RED),
            player=Player.RED,
        ),
    )
    red_transfers.pending_transfers = [transfer]

    model.cancel_transfer(transfer)

    red_transfers.cancel_transfer.assert_called_once_with(transfer, ANY)
    blue_transfers.cancel_transfer.assert_not_called()


def test_opfor_pending_transfer_dialog_can_cancel_transfer_order() -> None:
    from qt_ui.windows.PendingTransfersDialog import PendingTransfersDialog

    dialog = PendingTransfersDialog.__new__(PendingTransfersDialog)
    cast(Any, dialog).transfer_model = SimpleNamespace(
        transfer_at_index=lambda _index: SimpleNamespace(
            origin=SimpleNamespace(captured=Player.RED)
        ),
        # can_cancel now delegates to the model authorization method.
        can_manage=lambda _transfer: True,
    )
    index = cast(Any, SimpleNamespace(isValid=lambda: True))

    assert cast(Any, dialog).can_cancel(index) is True


def test_opfor_pending_transfer_dialog_cannot_cancel_when_enemy_buy_sell_disabled() -> (
    None
):
    from qt_ui.windows.PendingTransfersDialog import PendingTransfersDialog

    dialog = PendingTransfersDialog.__new__(PendingTransfersDialog)
    cast(Any, dialog).transfer_model = SimpleNamespace(
        transfer_at_index=lambda _index: SimpleNamespace(
            origin=SimpleNamespace(captured=Player.RED)
        ),
        # can_cancel now delegates to the model authorization method.
        can_manage=lambda _transfer: False,
    )
    index = cast(Any, SimpleNamespace(isValid=lambda: True))

    assert cast(Any, dialog).can_cancel(index) is False
