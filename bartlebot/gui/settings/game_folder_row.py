"""Settings › General › Game folder: where Wizard101 is installed (epic #215, Stage 2).

The same key the setup page writes, :data:`~bartleby.shared.game_install.INSTALL_DIR_KEY`
in the vendor-wide ``preferences.json``, so BartleBuddy launches from the folder chosen
here. The row's value is the folder chosen, or None for "detected": Detect, and the
row's reset, go back to detection, which is the default. Like every row it is pending
until OK, and OK writes it only when it changed since the tab opened, so a folder the
other product chose meanwhile stands.
"""

from __future__ import annotations

import logging
from pathlib import Path

from bartleby.qt.widgets.buttons.action_button import ActionButton
from bartleby.shared.game_install import (
    SOURCE_NAMES,
    clear_install_dir,
    detect_install,
    is_install,
    set_install_dir,
    stored_install_dir,
)
from bartleby.shared.preferences import PreferenceStore
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from bartlebot.clients.install_location import apply_install
from bartlebot.gui.settings.rows import Binding, SettingRow
from bartlebot.gui.widgets.inputs.game_folder_input import FOLDER_MISSING, GameFolderInput, folder_status

logger = logging.getLogger(__name__)

TITLE = "Game folder"
DETECT = "Detect"
#: The row's line: what it is, then the state of the folder shown.
SUMMARY = "Where Wizard101 is installed. {status}"
NOT_FOUND = "Not found. Choose the folder to launch the game."
CHOSEN = "Chosen by you."
DETAILS = (
    "BartleBot looks in the KingsIsle launcher's folder, your Steam libraries and the running game. "
    "Detect clears your choice and looks again."
)


class GameFolderControl(QWidget):
    """The field and Browse…, with Detect beside them. Its value is a folder, or None for detected."""

    def __init__(self, prefs: PreferenceStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._prefs = prefs
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.input = GameFolderInput()
        row.addWidget(self.input, 1)
        self.detect_button = ActionButton("search", DETECT, fill=False)
        self.detect_button.clicked.connect(lambda _checked: self.set_value(None))
        row.addWidget(self.detect_button)
        self._detected = None
        self._is_detected = False
        self._writing = False
        self.input.changed.connect(self._on_edited)

    def value(self) -> str | None:
        return None if self._is_detected else self.input.path()

    def set_value(self, value: str | None) -> None:
        self._writing = True
        try:
            if value is None:
                self._detected = detect_install()
                self._is_detected = True
                self.input.set_path(self._detected.path if self._detected else None)
            else:
                self._is_detected = False
                self.input.set_path(value)
            # Said once the field has settled, and still as a write rather than an edit:
            # the text may not have changed, but whether it is detected may have.
            self.input.changed.emit()
        finally:
            self._writing = False

    def status(self) -> tuple[str, bool]:
        """The state of the folder shown, and whether it is a problem."""
        if self._is_detected:
            if self._detected is None:
                return NOT_FOUND, True
            return folder_status(self.input.path(), self._detected.path, SOURCE_NAMES[self._detected.source])
        path = self.input.path()
        if not path or not is_install(path):
            return FOLDER_MISSING if path else NOT_FOUND, True
        return CHOSEN, False

    def _on_edited(self) -> None:
        if not self._writing:
            self._is_detected = False


def game_folder_row(prefs: PreferenceStore) -> tuple[SettingRow, GameFolderControl]:
    """The row and its control, opened on the folder in use."""
    control = GameFolderControl(prefs)
    stored = stored_install_dir(prefs)
    control.set_value(str(stored) if stored is not None and is_install(stored) else None)
    row = SettingRow(
        TITLE,
        "",
        control,
        default=None,
        details=DETAILS,
        wide=True,
        binding=Binding(
            read=control.value,
            write=control.set_value,
            signals=(control.input.changed,),
            describe=lambda v: "detected" if v is None else v,
        ),
    )

    def show_status() -> None:
        text, problem = control.status()
        row.summary.setText(SUMMARY.format(status=text))
        row.summary.set_color("error" if problem else "text_muted")

    control.input.changed.connect(show_status)
    show_status()
    return row, control


def apply_game_folder(prefs: PreferenceStore, opened: str | None, value: str | None) -> bool:
    """Write the row's *value* when it differs from the one the tab *opened* on. Whether anything was written.

    A folder that does not hold the game is not written: the row says so, and the folder
    stored before stands. Left on detected, a folder found only from the game running now
    is stored all the same, as setup's page stores it, so the game closed still finds it.
    """
    if value == opened:
        if value is None:
            apply_install(prefs)
        return False
    if value is None:
        written = clear_install_dir(prefs)
    elif not is_install(value):
        logger.warning("Not storing %s as the game folder: it does not hold Wizard101", value)
        return False
    else:
        written = set_install_dir(prefs, Path(value))
    if not written:
        logger.warning("Could not store the game folder; the one before stands")
        return False
    apply_install(prefs)
    return True
