"""Task 5 — Qt transfer model notifications and settings-driven visibility resets.

These tests verify the model/view contracts for ``TransferModel`` and the
settings signal wiring that re-syncs transfer visibility after a game replace
or settings change.
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QModelIndex, QObject, Signal
from PySide6.QtWidgets import QApplication, QGridLayout, QPushButton

from game.settings import Settings
from game.sim.gameupdateevents import GameUpdateEvents
from game.theater.base import Base
from game.theater.player import Player
from game.transfers import (
    PendingTransfers,
    TransferOrder,
)
from qt_ui.models import TransferModel
from qt_ui.windows.basemenu.NewUnitTransferDialog import NewUnitTransferDialog
from qt_ui.windows.basemenu.UnitTransactionFrame import UnitTransactionFrame
from qt_ui.windows.settings.QSettingsWindow import QSettingsWidget

# ---------------------------------------------------------------------------
# Qt application fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


@pytest.fixture(autouse=True)
def _seed_icons(app: QApplication) -> Any:
    """Populate uiconstants ICONS so QSettingsWidget can construct headless.

    ``QSettingsWidget.initUi`` reads several ``CONST.ICONS`` keys that are only
    populated by ``load_icons()`` (which needs the real resource files). Seed
    blank pixmaps for any missing keys so the settings widget builds under
    offscreen Qt. Depends on ``app`` so a QApplication exists first.
    """
    import qt_ui.uiconstants as CONST
    from PySide6.QtGui import QPixmap

    for key in [
        "Generator",
        "Cheat",
        "Plugins",
        "PluginsOptions",
        "Settings",
    ]:
        if key not in CONST.ICONS:
            CONST.ICONS[key] = QPixmap()
    yield


# ---------------------------------------------------------------------------
# Stub helpers (mirror the Task 4 test_transfer_ownership patterns)
# ---------------------------------------------------------------------------


class HashableCP:
    """A hashable control-point stand-in (usable as a ConvoyMap key)."""

    def __init__(self, name: str, captured: Player = Player.BLUE) -> None:
        self.name = name
        self.captured = captured
        self.base = Base()
        self.ground_objects: list[Any] = []
        self.position = object()

    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other


def _cp(name: str, captured: Player = Player.BLUE) -> HashableCP:
    return HashableCP(name, captured)


def _make_pending(player: Player) -> PendingTransfers:
    """A PendingTransfers with arrange_transport stubbed out."""
    game = MagicMock()
    game.transit_network_for = lambda _p: object()
    pending = PendingTransfers(cast(Any, game), player)
    cast(Any, pending).arrange_transport = lambda _transfer, _now, _events: None
    return pending


def _game_with_settings(
    blue_transfers: Any,
    red_transfers: Any,
    enemy_buy_sell: bool = False,
) -> Any:
    return MagicMock(
        settings=MagicMock(enable_enemy_buy_sell=enemy_buy_sell),
        coalition_for=lambda player: MagicMock(
            transfers=red_transfers if player is Player.RED else blue_transfers
        ),
    )


class SignalCounter(QObject):
    """Records how many times a Qt signal was emitted."""

    fired = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.count = 0
        self.fired.connect(self._inc)

    def _inc(self) -> None:
        self.count += 1


class FakeSimController(QObject):
    sim_update = Signal(GameUpdateEvents)


class FakePurchaseAdapter:
    def __init__(self, current: int) -> None:
        self.current = current
        self.coalition = SimpleNamespace(budget=100)

    def buy(self, _item: str, quantity: int) -> None:
        self.current += quantity

    def sell(self, _item: str, quantity: int) -> None:
        self.current -= quantity

    def current_quantity_of(self, _item: str) -> int:
        return self.current

    def pending_delivery_quantity(self, _item: str) -> int:
        return 0

    def expected_quantity_next_turn(self, item: str) -> int:
        return self.current_quantity_of(item)

    def name_of(self, item: str, multiline: bool = False) -> str:
        return item if not multiline else f"{item}<br />"

    def price_of(self, _item: str) -> int:
        return 1

    def can_buy(self, _item: str) -> bool:
        return True

    def can_sell_or_cancel(self, _item: str) -> bool:
        return self.current > 0

    def unit_type_of(self, _item: str) -> Any:
        return object()


def _game_model(game: Any, transfer_model: Any = None) -> Any:
    if transfer_model is None:
        transfer_model = MagicMock()
    return SimpleNamespace(game=game, transfer_model=transfer_model)


def _transfer(
    origin: HashableCP, destination: HashableCP, player: Player
) -> TransferOrder:
    return TransferOrder(cast(Any, origin), cast(Any, destination), {}, player=player)


# ---------------------------------------------------------------------------
# Inventory refresh
# ---------------------------------------------------------------------------


def test_unit_transaction_frame_refreshes_labels_after_transaction(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selling through the frame refreshes the visible current inventory label."""
    from qt_ui.windows.GameUpdateSignal import GameUpdateSignal

    monkeypatch.setattr(
        GameUpdateSignal,
        "get_instance",
        lambda: SimpleNamespace(updateBudget=lambda _game: None),
    )
    adapter = FakePurchaseAdapter(current=2)
    frame = UnitTransactionFrame(_game_model(SimpleNamespace()), cast(Any, adapter))
    layout = QGridLayout()
    frame.add_purchase_row("tank", layout, 0)

    assert frame.existing_units_labels["tank"].text() == "2"

    frame.sell("tank", 1)

    assert frame.existing_units_labels["tank"].text() == "1"


def test_unit_transaction_frame_refreshes_labels_after_external_inventory_change(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An external inventory signal refreshes an already-open frame label."""
    from qt_ui.windows.GameUpdateSignal import GameUpdateSignal

    monkeypatch.setattr(
        GameUpdateSignal,
        "get_instance",
        lambda: SimpleNamespace(updateBudget=lambda _game: None),
    )
    transfer_model = TransferModel(
        _game_model(
            _game_with_settings(_make_pending(Player.BLUE), _make_pending(Player.RED))
        )
    )
    adapter = FakePurchaseAdapter(current=2)
    frame = UnitTransactionFrame(
        _game_model(SimpleNamespace(), transfer_model), cast(Any, adapter)
    )
    layout = QGridLayout()
    frame.add_purchase_row("tank", layout, 0)

    adapter.current = 7
    transfer_model.inventory_changed.emit()

    assert frame.existing_units_labels["tank"].text() == "7"


# ---------------------------------------------------------------------------
# Row ordering and insertion
# ---------------------------------------------------------------------------


def test_transfer_create_emits_inventory_changed(app: QApplication) -> None:
    """Creating a transfer emits the Qt inventory refresh notification."""
    blue = _make_pending(Player.BLUE)
    game = _game_with_settings(blue, _make_pending(Player.RED))
    model = TransferModel(_game_model(game))
    fired: list[int] = []
    model.inventory_changed.connect(lambda: fired.append(1))

    origin = _cp("Alpha", captured=Player.BLUE)
    destination = _cp("Bravo", captured=Player.BLUE)
    model.new_transfer(_transfer(origin, destination, Player.BLUE), datetime.now())

    assert fired == [1]


def test_transfer_cancel_emits_inventory_changed(app: QApplication) -> None:
    """Cancelling a transfer emits the Qt inventory refresh notification."""
    blue = _make_pending(Player.BLUE)
    transfer = _transfer(_cp("Alpha"), _cp("Bravo"), Player.BLUE)
    blue.new_transfer(transfer, datetime.now(), GameUpdateEvents())
    game = _game_with_settings(blue, _make_pending(Player.RED))
    model = TransferModel(_game_model(game))
    fired: list[int] = []
    model.inventory_changed.connect(lambda: fired.append(1))

    model.cancel_transfer(transfer)

    assert fired == [1]


def test_sim_update_resets_rows_when_pending_transfers_are_replaced(
    app: QApplication,
) -> None:
    """Turn processing replaces the collection, so the open view must reset."""
    blue = _make_pending(Player.BLUE)
    transfer = _transfer(_cp("Alpha"), _cp("Bravo"), Player.BLUE)
    blue.new_transfer(transfer, datetime.now(), GameUpdateEvents())
    sim_controller = FakeSimController()
    game = _game_with_settings(blue, _make_pending(Player.RED))
    game_model = _game_model(game)
    game_model.sim_controller = sim_controller
    model = TransferModel(game_model)
    resets: list[int] = []
    inventory_refreshes: list[int] = []
    model.modelReset.connect(lambda: resets.append(1))
    model.inventory_changed.connect(lambda: inventory_refreshes.append(1))

    blue.pending_transfers = []
    sim_controller.sim_update.emit(GameUpdateEvents())

    assert model.rowCount() == 0
    assert resets == [1]
    assert inventory_refreshes == [1]


def test_sim_update_resets_rows_when_pending_transfer_is_removed(
    app: QApplication,
) -> None:
    """Turn processing removal is visible even when the list object is retained."""
    blue = _make_pending(Player.BLUE)
    transfer = _transfer(_cp("Alpha"), _cp("Bravo"), Player.BLUE)
    blue.new_transfer(transfer, datetime.now(), GameUpdateEvents())
    sim_controller = FakeSimController()
    game = _game_with_settings(blue, _make_pending(Player.RED))
    game_model = _game_model(game)
    game_model.sim_controller = sim_controller
    model = TransferModel(game_model)
    resets: list[int] = []
    model.modelReset.connect(lambda: resets.append(1))

    blue.pending_transfers.remove(transfer)
    sim_controller.sim_update.emit(GameUpdateEvents())

    assert model.rowCount() == 0
    assert resets == [1]


def test_blue_insert_precedes_visible_red_rows(app: QApplication) -> None:
    """A BLUE insertion announces the pre-insert BLUE count.

    With enemy management enabled and one RED row present, inserting a BLUE
    transfer must announce row 0 (the pre-insert BLUE count), placing it before
    the visible RED row.
    """
    blue = _make_pending(Player.BLUE)
    red = _make_pending(Player.RED)
    red_origin = _cp("Red Origin", captured=Player.RED)
    red_dest = _cp("Red Dest", captured=Player.RED)
    red.new_transfer(
        _transfer(red_origin, red_dest, Player.RED), datetime.now(), GameUpdateEvents()
    )

    game = _game_with_settings(blue, red, enemy_buy_sell=True)
    model = TransferModel(_game_model(game))

    inserts: list[tuple[int, int]] = []
    cast(Any, model).rowsInserted.connect(
        lambda parent, first, last: inserts.append((first, last))
    )

    origin = _cp("Alpha", captured=Player.BLUE)
    destination = _cp("Bravo", captured=Player.BLUE)
    model.new_transfer(_transfer(origin, destination, Player.BLUE), datetime.now())

    # The BLUE row is announced at the pre-insert BLUE count (0), before RED.
    assert inserts == [(0, 0)]
    assert model.rowCount() == 2
    assert (
        model.transfer_at_index(model.index(0, 0, QModelIndex())).player is Player.BLUE
    )
    assert (
        model.transfer_at_index(model.index(1, 0, QModelIndex())).player is Player.RED
    )


def test_red_insert_appends(app: QApplication) -> None:
    """A RED insertion (enemy management enabled) announces the aggregate tail.

    With one BLUE row present, inserting a RED transfer must announce the row
    after the BLUE count.
    """
    blue = _make_pending(Player.BLUE)
    red = _make_pending(Player.RED)
    blue_origin = _cp("Blue Origin", captured=Player.BLUE)
    blue_dest = _cp("Blue Dest", captured=Player.BLUE)
    blue.new_transfer(
        _transfer(blue_origin, blue_dest, Player.BLUE),
        datetime.now(),
        GameUpdateEvents(),
    )

    game = _game_with_settings(blue, red, enemy_buy_sell=True)
    model = TransferModel(_game_model(game))

    inserts: list[tuple[int, int]] = []
    cast(Any, model).rowsInserted.connect(
        lambda parent, first, last: inserts.append((first, last))
    )

    origin = _cp("Red Origin", captured=Player.RED)
    destination = _cp("Red Dest", captured=Player.RED)
    model.new_transfer(_transfer(origin, destination, Player.RED), datetime.now())

    # RED appends after the single BLUE row.
    assert inserts == [(1, 1)]
    assert model.rowCount() == 2


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------


def test_remove_uses_visible_row(app: QApplication) -> None:
    """Removal authorizes first, then brackets the visible pre-mutation row.

    With one BLUE and one RED row (enemy management enabled), removing the RED
    row must announce removal of the visible row 1.
    """
    blue = _make_pending(Player.BLUE)
    red = _make_pending(Player.RED)
    blue_origin = _cp("Blue Origin", captured=Player.BLUE)
    blue_dest = _cp("Blue Dest", captured=Player.BLUE)
    blue_transfer = _transfer(blue_origin, blue_dest, Player.BLUE)
    blue.new_transfer(blue_transfer, datetime.now(), GameUpdateEvents())

    red_origin = _cp("Red Origin", captured=Player.RED)
    red_dest = _cp("Red Dest", captured=Player.RED)
    red_transfer = _transfer(red_origin, red_dest, Player.RED)
    red.new_transfer(red_transfer, datetime.now(), GameUpdateEvents())

    game = _game_with_settings(blue, red, enemy_buy_sell=True)
    model = TransferModel(_game_model(game))

    removals: list[tuple[int, int]] = []
    cast(Any, model).rowsRemoved.connect(
        lambda parent, first, last: removals.append((first, last))
    )

    model.cancel_transfer(red_transfer)

    # The visible RED row was at index 1.
    assert removals == [(1, 1)]
    assert model.rowCount() == 1
    assert model.transfer_at_index(model.index(0, 0, QModelIndex())) is blue_transfer


# ---------------------------------------------------------------------------
# Visibility sync
# ---------------------------------------------------------------------------


def test_visibility_change_resets_model(app: QApplication) -> None:
    """A visibility change resets the model strictly inside begin/endResetModel.

    With one BLUE and one RED row, toggling enemy management off via
    ``sync_game_and_visibility`` must emit exactly one modelReset and drop the
    RED row from the visible count.
    """
    blue = _make_pending(Player.BLUE)
    red = _make_pending(Player.RED)
    blue_origin = _cp("Blue Origin", captured=Player.BLUE)
    blue_dest = _cp("Blue Dest", captured=Player.BLUE)
    blue.new_transfer(
        _transfer(blue_origin, blue_dest, Player.BLUE),
        datetime.now(),
        GameUpdateEvents(),
    )
    red_origin = _cp("Red Origin", captured=Player.RED)
    red_dest = _cp("Red Dest", captured=Player.RED)
    red.new_transfer(
        _transfer(red_origin, red_dest, Player.RED), datetime.now(), GameUpdateEvents()
    )

    game = _game_with_settings(blue, red, enemy_buy_sell=True)
    model = TransferModel(_game_model(game))
    # Seed the snapshot while RED is visible.
    model.sync_game_and_visibility()
    assert model.rowCount() == 2

    resets: list[int] = []
    cast(Any, model).modelReset.connect(lambda: resets.append(1))

    # Disable enemy management — RED rows become hidden.
    game.settings.enable_enemy_buy_sell = False
    model.sync_game_and_visibility()

    assert resets == [1]
    assert model.rowCount() == 1


def test_game_replacement_syncs_transfer_visibility(app: QApplication) -> None:
    """``GameModel.set()`` calls ``sync_game_and_visibility`` after game replace."""
    from qt_ui.models import GameModel
    from qt_ui.simcontroller import SimController

    blue = _make_pending(Player.BLUE)
    red = _make_pending(Player.RED)
    red_origin = _cp("Red Origin", captured=Player.RED)
    red_dest = _cp("Red Dest", captured=Player.RED)
    red.new_transfer(
        _transfer(red_origin, red_dest, Player.RED), datetime.now(), GameUpdateEvents()
    )

    game = _game_with_settings(blue, red, enemy_buy_sell=True)
    game.blue = MagicMock(ato=MagicMock())
    game.red = MagicMock(ato=MagicMock())
    game.air_wing_for = MagicMock()
    game.theater = MagicMock(control_points_for=lambda _x: [])

    sim_controller = MagicMock(spec=SimController)
    model = GameModel(None, cast(Any, sim_controller))
    sync_calls: list[int] = []
    model.transfer_model.sync_game_and_visibility = (  # type: ignore[method-assign]
        lambda: sync_calls.append(1)
    )

    model.set(game)

    assert sync_calls == [1]


def test_game_replacement_resets_same_visibility_transfer_model(
    app: QApplication,
) -> None:
    """Replacing a same-visibility game still notifies views of new transfer rows."""
    first_blue = _make_pending(Player.BLUE)
    first_transfer = _transfer(_cp("First Origin"), _cp("First Dest"), Player.BLUE)
    first_blue.new_transfer(first_transfer, datetime.now(), GameUpdateEvents())
    first_game = _game_with_settings(
        first_blue, _make_pending(Player.RED), enemy_buy_sell=False
    )

    second_blue = _make_pending(Player.BLUE)
    second_transfer = _transfer(_cp("Second Origin"), _cp("Second Dest"), Player.BLUE)
    second_blue.new_transfer(second_transfer, datetime.now(), GameUpdateEvents())
    second_game = _game_with_settings(
        second_blue, _make_pending(Player.RED), enemy_buy_sell=False
    )

    game_model = _game_model(first_game)
    model = TransferModel(game_model)
    resets: list[int] = []
    cast(Any, model).modelReset.connect(lambda: resets.append(1))

    game_model.game = second_game
    model.sync_game_and_visibility()

    assert resets == [1]
    assert model.transfer_at_index(model.index(0, 0, QModelIndex())) is second_transfer


# ---------------------------------------------------------------------------
# Unloaded safety
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Unloaded safety
# ---------------------------------------------------------------------------


def test_unloaded_game_has_no_rows(app: QApplication) -> None:
    """An unloaded game (no game set) must produce no rows and no signals."""
    game_model = MagicMock()
    game_model.game = None
    model = TransferModel(game_model)

    inserts: list[tuple[int, int]] = []
    cast(Any, model).rowsInserted.connect(
        lambda parent, first, last: inserts.append((first, last))
    )

    assert model.rowCount() == 0
    assert inserts == []


# ---------------------------------------------------------------------------
# Settings signal wiring
# ---------------------------------------------------------------------------


def _settings_widget() -> QSettingsWidget:
    return QSettingsWidget(cast(Any, Settings()), None)


def test_settings_apply_emits_once_and_syncs_transfer_visibility(
    app: QApplication,
) -> None:
    """``applySettings`` emits the completion signal exactly once."""
    widget = _settings_widget()

    fired: list[int] = []
    widget.settings_applied.connect(lambda: fired.append(1))

    widget.applySettings()

    assert fired == [1]


def test_settings_apply_enqueues_all_motorpool_control_points(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settings changes publish every CP for final motorpool reconciliation."""
    widget = _settings_widget()
    first = _cp("first")
    second = _cp("second")
    published: list[GameUpdateEvents] = []
    updated_at: list[tuple[GameUpdateEvents, tuple[Any, ...]]] = []
    game = SimpleNamespace(
        theater=SimpleNamespace(controlpoints=[first, second]),
        compute_unculled_zones=lambda _events: None,
    )
    widget.game = cast(Any, game)

    def record_motorpool_update(
        events: GameUpdateEvents, *control_points: Any
    ) -> GameUpdateEvents:
        updated_at.append((events, control_points))
        return events

    monkeypatch.setattr(
        GameUpdateEvents, "update_motorpools_at", record_motorpool_update
    )
    monkeypatch.setattr(
        "qt_ui.windows.settings.QSettingsWindow.EventStream.put_nowait",
        lambda events: published.append(events),
    )
    monkeypatch.setattr(
        "qt_ui.windows.settings.QSettingsWindow.GameUpdateSignal.get_instance",
        lambda: SimpleNamespace(updateGame=lambda _game: None),
    )

    widget.applySettings()

    assert len(published) == 1
    assert updated_at == [(published[0], (first, second))]


def _write_settings_zip(path: Any, settings: Settings) -> None:
    # The inner json name must match load_settings' derivation:
    #   zipfilename.split("/")[-1].replace(".zip", ".json")
    inner = path.name.replace(".zip", ".json")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            inner,
            json.dumps(settings.__dict__, indent=2, default=settings.default_json),
            zipfile.ZIP_DEFLATED,
        )


def test_settings_load_emits_once_and_syncs_transfer_visibility(
    app: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """``load_settings`` emits exactly once after an accepted, decoded archive."""
    import qt_ui.windows.settings.QSettingsWindow as qsw

    widget = _settings_widget()

    fired: list[int] = []
    widget.settings_applied.connect(lambda: fired.append(1))

    archive = tmp_path / "archive.zip"
    _write_settings_zip(archive, Settings())

    fd = MagicMock()
    fd.exec_.return_value = True
    fd.selectedFiles.return_value = [str(archive)]
    monkeypatch.setattr(qsw, "settings_dir", lambda: tmp_path)
    monkeypatch.setattr(qsw, "QFileDialog", lambda *a, **k: fd)
    widget.load_settings()

    assert fired == [1]


def test_settings_default_load_emits_once_and_syncs_transfer_visibility(
    app: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """``load_default_settings`` emits exactly once after loading defaults."""
    import qt_ui.windows.settings.QSettingsWindow as qsw

    widget = _settings_widget()

    fired: list[int] = []
    widget.settings_applied.connect(lambda: fired.append(1))

    monkeypatch.setattr(qsw, "settings_dir", lambda: tmp_path)
    widget.load_default_settings()

    assert fired == [1]


@pytest.mark.parametrize("loader", ["archive", "default"])
def test_settings_load_enqueues_all_motorpool_control_points(
    app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    loader: str,
) -> None:
    """Loaded settings publish every CP for final motorpool reconciliation."""
    import qt_ui.windows.settings.QSettingsWindow as qsw

    widget = _settings_widget()
    first = _cp("first")
    second = _cp("second")
    published: list[GameUpdateEvents] = []
    updated_at: list[tuple[GameUpdateEvents, tuple[Any, ...]]] = []
    widget.game = cast(
        Any,
        SimpleNamespace(
            theater=SimpleNamespace(controlpoints=[first, second]),
            compute_unculled_zones=lambda _events: None,
        ),
    )

    def record_motorpool_update(
        events: GameUpdateEvents, *control_points: Any
    ) -> GameUpdateEvents:
        updated_at.append((events, control_points))
        return events

    monkeypatch.setattr(
        GameUpdateEvents, "update_motorpools_at", record_motorpool_update
    )
    monkeypatch.setattr(
        qsw.EventStream, "put_nowait", lambda events: published.append(events)
    )
    monkeypatch.setattr(
        qsw.GameUpdateSignal,
        "get_instance",
        lambda: SimpleNamespace(updateGame=lambda _game: None),
    )
    monkeypatch.setattr(qsw, "settings_dir", lambda: tmp_path)

    if loader == "archive":
        archive = tmp_path / "archive.zip"
        _write_settings_zip(archive, Settings())
        fd = MagicMock()
        fd.exec_.return_value = True
        fd.selectedFiles.return_value = [str(archive)]
        monkeypatch.setattr(qsw, "QFileDialog", lambda *a, **k: fd)
        widget.load_settings()
    else:
        widget.load_default_settings()

    assert len(published) == 1
    assert updated_at == [(published[0], (first, second))]


def test_transfer_submit_requires_eligible_destination(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selected units cannot be submitted when no destination is available."""
    submitted: list[Any] = []
    monkeypatch.setattr(
        "qt_ui.windows.basemenu.NewUnitTransferDialog.submit_transfer",
        lambda *args, **kwargs: submitted.append((args, kwargs)),
    )
    monkeypatch.setattr(NewUnitTransferDialog, "close", lambda _self: None)
    dialog = NewUnitTransferDialog.__new__(NewUnitTransferDialog)
    cast(Any, dialog).submit_button = QPushButton()
    cast(Any, dialog).transfer_panel = SimpleNamespace(transfers={"tank": 1})
    cast(Any, dialog).dest_panel = SimpleNamespace(current=None, request_airlift=False)
    cast(Any, dialog).game_model = SimpleNamespace(
        sim_controller=SimpleNamespace(current_time_in_sim=0)
    )
    cast(Any, dialog).origin = object()

    dialog.on_transfer_quantity_changed()
    dialog.on_submit()

    assert dialog.submit_button.isEnabled() is False
    assert submitted == []


def test_cancelled_settings_load_emits_no_settings_applied(
    app: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A cancelled load dialog emits zero completion signals."""
    import qt_ui.windows.settings.QSettingsWindow as qsw

    widget = _settings_widget()

    fired: list[int] = []
    widget.settings_applied.connect(lambda: fired.append(1))

    archive = tmp_path / "archive.zip"
    _write_settings_zip(archive, Settings())

    fd = MagicMock()
    fd.exec_.return_value = False  # cancelled
    fd.selectedFiles.return_value = [str(archive)]
    monkeypatch.setattr(qsw, "settings_dir", lambda: tmp_path)
    monkeypatch.setattr(qsw, "QFileDialog", lambda *a, **k: fd)
    widget.load_settings()

    assert fired == []
