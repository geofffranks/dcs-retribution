from typing import Any, Callable, cast

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from game.missiongenerator.carrierstandoff import CarrierStandoffFinding
from qt_ui.widgets.carrierstandoffwarning import (
    confirm_carrier_standoff,
    guarded_mission_generation,
)


@pytest.fixture
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


FINDINGS = [
    CarrierStandoffFinding(display_name="Carrier Alpha", shore_distance_nm=12.34),
    CarrierStandoffFinding(display_name="LHA Bravo", shore_distance_nm=4.5),
]


def _clicking(button_text: str) -> Callable[[QMessageBox], int]:
    """Build a fake QMessageBox.exec_ that clicks the button with the given text."""

    def fake_exec(self: QMessageBox) -> int:
        for button in self.buttons():
            if button.text() == button_text:
                button.click()
                break
        return 0

    return fake_exec


def test_no_findings_confirms_without_showing_a_dialog(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = []
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: shown.append(True))

    assert confirm_carrier_standoff(None, []) is True
    assert shown == []


def test_dialog_content_lists_every_affected_name_and_distance(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def fake_exec(self: QMessageBox) -> int:
        captured["text"] = self.text()
        for button in self.buttons():
            if button.text() == "Cancel":
                button.click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec_", fake_exec)

    confirm_carrier_standoff(None, FINDINGS)

    assert "Carrier Alpha" in captured["text"]
    assert "12.3" in captured["text"]
    assert "LHA Bravo" in captured["text"]
    assert "4.5" in captured["text"]


def test_cancel_returns_false(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QMessageBox, "exec_", _clicking("Cancel"))

    assert confirm_carrier_standoff(None, FINDINGS) is False


def test_continue_returns_true(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QMessageBox, "exec_", _clicking("Continue"))

    assert confirm_carrier_standoff(None, FINDINGS) is True


def test_guarded_generation_with_no_findings_invokes_callback_without_dialog(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "qt_ui.widgets.carrierstandoffwarning.carrier_standoff_preflight",
        lambda game: [],
    )
    shown = []
    monkeypatch.setattr(QMessageBox, "exec_", lambda self: shown.append(True))
    calls = []

    guarded_mission_generation(
        None, cast(Any, object()), lambda findings: calls.append(True)
    )

    assert calls == [True]
    assert shown == []


def test_guarded_generation_cancel_prevents_the_generation_callback(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "qt_ui.widgets.carrierstandoffwarning.carrier_standoff_preflight",
        lambda game: FINDINGS,
    )
    monkeypatch.setattr(QMessageBox, "exec_", _clicking("Cancel"))
    calls = []

    guarded_mission_generation(
        None, cast(Any, object()), lambda findings: calls.append(True)
    )

    assert calls == []


def test_guarded_generation_continue_invokes_the_generation_callback_exactly_once(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "qt_ui.widgets.carrierstandoffwarning.carrier_standoff_preflight",
        lambda game: FINDINGS,
    )
    monkeypatch.setattr(QMessageBox, "exec_", _clicking("Continue"))
    calls = []

    guarded_mission_generation(
        None, cast(Any, object()), lambda findings: calls.append(True)
    )

    assert calls == [True]


def test_guarded_generation_shows_the_dialog_at_most_once(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "qt_ui.widgets.carrierstandoffwarning.carrier_standoff_preflight",
        lambda game: FINDINGS,
    )
    exec_calls = []

    def fake_exec(self: QMessageBox) -> int:
        exec_calls.append(True)
        for button in self.buttons():
            if button.text() == "Continue":
                button.click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec_", fake_exec)

    guarded_mission_generation(None, cast(Any, object()), lambda findings: None)

    assert len(exec_calls) == 1


def test_guarded_generation_passes_confirmed_findings_to_callback(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "qt_ui.widgets.carrierstandoffwarning.carrier_standoff_preflight",
        lambda game: FINDINGS,
    )
    monkeypatch.setattr(QMessageBox, "exec_", _clicking("Continue"))
    callback_findings: list[CarrierStandoffFinding] = []

    def generate(findings: list[CarrierStandoffFinding]) -> None:
        callback_findings.extend(findings)

    guarded_mission_generation(None, cast(Any, object()), generate)

    assert callback_findings == FINDINGS
