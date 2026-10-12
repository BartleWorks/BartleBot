"""The pre-run dialog for combat faults: shown only when a choice the user made cannot work.

Faults and notices are told apart in :mod:`bartlebot.combat.combat_warnings`. Only faults
reach here; a fallback in use is a notice, which goes to the log and never to a dialog
(``docs/COMBAT_CONFIG.md``, "Before a run"). So this dialog is rare, and when it opens it
is worth reading: **Go back** is the default, so the press a frequent dialog trains is
the one that keeps a broken setup from running.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox, QWidget

TITLE = "Combat Strategy Problems"
GO_BACK = "Go back"
RUN_ANYWAY = "Run anyway"


def fault_text(faults: list[str]) -> str:
    """The dialog's text: what is broken, and what running anyway does."""
    listed = "\n".join(f"• {fault}" for fault in faults)
    return (
        f"Something in this bot's combat setup can't work as chosen:\n\n{listed}\n\n"
        "Go back to fix it, or Run anyway to start the bot as it is."
    )


def fault_box(parent: QWidget | None, faults: list[str]) -> tuple[QMessageBox, object]:
    """The dialog, unopened, and its Run anyway button. Go back is its default and its Escape."""
    box = QMessageBox(QMessageBox.Icon.Warning, TITLE, fault_text(faults), QMessageBox.StandardButton.NoButton, parent)
    run_anyway = box.addButton(RUN_ANYWAY, QMessageBox.ButtonRole.AcceptRole)
    go_back = box.addButton(GO_BACK, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(go_back)
    box.setEscapeButton(go_back)
    return box, run_anyway


def confirm_run(parent: QWidget | None, faults: list[str]) -> bool:
    """Show the faults and return whether the user chose Run anyway."""
    box, run_anyway = fault_box(parent, faults)
    box.exec()
    return box.clickedButton() is run_anyway
