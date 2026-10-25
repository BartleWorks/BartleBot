"""A folder field with Browse…, for where Wizard101 is installed (epic #215, Stage 2).

The setup page and the Settings row both draw it. It is only the control: whether the
folder holds the game, and where the answer is stored, are theirs (``shared.game_install``).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QWidget

from bartlebot.gui.widgets.buttons.action_button import ActionButton
from bartlebot.shared.game_install import is_install

BROWSE = "Browse…"
BROWSE_TITLE = "Choose Wizard101 Folder"
PLACEHOLDER = "The folder holding Wizard101's Bin folder"

#: Under the field, for each state of the folder in it.
FOLDER_EMPTY = "Choose the Wizard101 folder."
FOLDER_FOUND = "Found in {source}."
FOLDER_OK = "Wizard101 is installed here."
FOLDER_MISSING = "Wizard101 isn't in this folder. Choose the one that holds the Bin folder."


def folder_status(path: str, found_path: Path | None, found_source: str = "") -> tuple[str, bool]:
    """The line under a game folder field, and whether it reports a problem."""
    if not path:
        return FOLDER_EMPTY, False
    if not is_install(path):
        return FOLDER_MISSING, True
    if found_path is not None and Path(path) == found_path:
        return FOLDER_FOUND.format(source=found_source), False
    return FOLDER_OK, False


def _choose_directory(parent: QWidget, start: str) -> str:
    return QFileDialog.getExistingDirectory(parent, BROWSE_TITLE, start)


class GameFolderInput(QWidget):
    """A path field and Browse…. ``changed`` fires on every edit and every folder chosen."""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._edit = QLineEdit()
        self._edit.setPlaceholderText(PLACEHOLDER)
        self._edit.textChanged.connect(lambda _text: self.changed.emit())
        row.addWidget(self._edit, 1)
        self._browse = ActionButton("folder", BROWSE, fill=False)
        self._browse.clicked.connect(lambda _checked: self.browse())
        row.addWidget(self._browse)
        #: Asks for a folder; a test swaps it for a stand-in. An empty answer is a cancel.
        self.choose_directory: Callable[[QWidget, str], str] = _choose_directory

    @property
    def edit(self) -> QLineEdit:
        return self._edit

    @property
    def browse_button(self) -> ActionButton:
        return self._browse

    def path(self) -> str:
        return self._edit.text().strip()

    def set_path(self, path: Path | str | None) -> None:
        self._edit.setText("" if path is None else str(path))

    def browse(self) -> None:
        chosen = self.choose_directory(self, self.path())
        if chosen:
            self.set_path(Path(chosen))
