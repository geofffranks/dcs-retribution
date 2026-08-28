from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from game.purchaseadapter import GroundUnitPurchaseAdapter
from game.theater.base import Base
from game.theater.player import Player
from qt_ui.models import TransferModel
from qt_ui.windows.settings.QSettingsWindow import CheatSettingsBox


@pytest.fixture
def app() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def test_cheat_menu_uses_explicit_opfor_transfer_label(app: QApplication) -> None:
    settings = SimpleNamespace(
        enable_frontline_cheats=False,
        enable_base_capture_cheat=False,
        enable_runway_state_cheat=False,
        enable_transfer_cheat=False,
        enable_air_wing_adjustments=False,
        enable_enemy_buy_sell=False,
    )
    box = CheatSettingsBox(
        cast(Any, SimpleNamespace(settings=settings)), cast(Any, lambda: None)
    )

    label = cast(Any, box.redfor_buysell_cheat.itemAt(0).widget())

    assert label.text() == "Enable OPFOR Buy/Sell/Transfer Cheat"


def test_red_ground_sale_is_available_only_when_cheat_is_enabled() -> None:
    unit_type = cast(Any, MagicMock())
    unit_type.price = 5
    base = Base()
    base.commission_units({unit_type: 2})
    control_point = SimpleNamespace(base=base, captured=Player.RED)
    coalition = SimpleNamespace(player=Player.RED, budget=10)
    game = SimpleNamespace(settings=SimpleNamespace(enable_enemy_buy_sell=False))

    disabled = cast(
        Any,
        GroundUnitPurchaseAdapter(
            cast(Any, control_point), cast(Any, coalition), cast(Any, game)
        ),
    )
    assert disabled.can_sell(unit_type) is False

    game.settings.enable_enemy_buy_sell = True
    enabled = cast(
        Any,
        GroundUnitPurchaseAdapter(
            cast(Any, control_point), cast(Any, coalition), cast(Any, game)
        ),
    )

    assert enabled.can_sell(unit_type) is True


def test_red_transfer_model_routes_transfer_to_red_collection_when_enabled() -> None:
    red_transfers = MagicMock(pending_transfer_count=0)
    blue_transfers = MagicMock(pending_transfer_count=0)
    game = SimpleNamespace(
        settings=SimpleNamespace(enable_enemy_buy_sell=True),
        coalition_for=lambda player: SimpleNamespace(
            transfers=red_transfers if player is Player.RED else blue_transfers
        ),
    )
    model = TransferModel(cast(Any, SimpleNamespace(game=game)))
    transfer = cast(Any, SimpleNamespace(player=Player.RED))

    model.new_transfer(transfer, cast(Any, SimpleNamespace()))

    red_transfers.new_transfer.assert_called_once_with(transfer, SimpleNamespace())
    blue_transfers.new_transfer.assert_not_called()


def test_red_transfer_model_rejects_mutation_when_cheat_is_disabled() -> None:
    game = SimpleNamespace(
        settings=SimpleNamespace(enable_enemy_buy_sell=False),
        coalition_for=lambda _player: SimpleNamespace(
            transfers=MagicMock(pending_transfer_count=0)
        ),
    )
    model = TransferModel(cast(Any, SimpleNamespace(game=game)))
    transfer = cast(Any, SimpleNamespace(player=Player.RED))

    with pytest.raises(PermissionError, match="OPFOR buy/sell/transfer is disabled"):
        model.new_transfer(transfer, cast(Any, SimpleNamespace()))
