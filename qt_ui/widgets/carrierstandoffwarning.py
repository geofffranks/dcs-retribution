"""Qt Continue/Cancel warning for carrier/LHA shore-standoff findings.

Wraps the Qt-independent `carrier_standoff_preflight` result with the
interactive confirmation used by normal and Pretense mission generation.
Headless/server generation has no interactive channel and instead logs the
same structured warning via `log_carrier_standoff_warning` from the domain
module — see `game/missiongenerator/carrierstandoff.py`.
"""

from typing import Callable, Optional, TYPE_CHECKING

from PySide6.QtWidgets import QMessageBox, QWidget

from game.missiongenerator.carrierstandoff import (
    CarrierStandoffFinding,
    carrier_standoff_preflight,
    format_standoff_warning,
)

if TYPE_CHECKING:
    from game import Game


def confirm_carrier_standoff(
    parent: Optional[QWidget], findings: list[CarrierStandoffFinding]
) -> bool:
    """Show a single Continue/Cancel warning for the given findings.

    With no findings, no dialog is shown and this returns True immediately.
    Returns True (proceed) when the user chooses Continue, and False (abort)
    when the user chooses Cancel.
    """
    if not findings:
        return True

    mbox = QMessageBox(
        QMessageBox.Icon.Warning,
        "Carrier shore-standoff warning",
        format_standoff_warning(findings),
        parent=parent,
    )
    continue_button = mbox.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)
    cancel_button = mbox.addButton(QMessageBox.StandardButton.Cancel)
    mbox.setDefaultButton(cancel_button)
    mbox.setEscapeButton(cancel_button)
    mbox.exec_()
    return mbox.clickedButton() is continue_button


def guarded_mission_generation(
    parent: Optional[QWidget],
    game: "Game",
    generate: Callable[[list[CarrierStandoffFinding]], None],
) -> None:
    """Run `generate` unless standoff findings exist and the user cancels.

    Requests the preflight result before any mission output is written. With
    no findings, `generate` runs immediately, preserving the existing
    generation path unchanged. With findings, shows the Continue/Cancel
    warning (at most once per generation attempt); Cancel returns without
    invoking `generate`, so no mission output is written, while Continue
    invokes `generate` exactly once.
    """
    findings = carrier_standoff_preflight(game)
    if not confirm_carrier_standoff(parent, findings):
        return
    generate(findings)
