"""Settings tab: Keybindings (click-to-rebind table)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from bartlebot.settings_manager import SettingsManager

# Human-readable action labels
_ACTION_LABELS: dict[str, str] = {
    "toggle_embed":     "Toggle game embed",
    "next_client":      "Switch to next client",
    "run_bot":          "Run bot",
    "pause_bot":        "Pause bot",
    "stop_bot":         "Stop bot",
    "launch_profile_0": "Launch profile 1",
    "launch_profile_1": "Launch profile 2",
}


class _ShortcutCell(QWidget):
    """Inline widget for a keybinding cell: shows current shortcut + Clear button."""

    def __init__(self, action: str, shortcut: str | None, parent=None) -> None:
        super().__init__(parent)
        self._action = action
        self._capturing = False

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(4)

        self._edit = QLineEdit(shortcut or "")
        self._edit.setReadOnly(True)
        self._edit.setPlaceholderText("Click to set…")
        self._edit.mousePressEvent = self._start_capture  # type: ignore[assignment]

        self._clear_btn = QPushButton("✕")
        self._clear_btn.setFixedWidth(28)
        self._clear_btn.clicked.connect(self._clear)

        row.addWidget(self._edit, 1)
        row.addWidget(self._clear_btn)

    def _start_capture(self, _event) -> None:
        self._capturing = True
        self._edit.setText("")
        self._edit.setPlaceholderText("Press keys…")
        self._edit.setFocus()
        # Install key filter on the edit
        self._edit.keyPressEvent = self._capture_key  # type: ignore[method-assign]

    def _capture_key(self, event) -> None:
        key = event.key()
        mods = event.modifiers()
        # Ignore bare modifier keys
        if key in (
            Qt.Key.Key_Control, Qt.Key.Key_Shift,
            Qt.Key.Key_Alt, Qt.Key.Key_Meta,
        ):
            return
        seq = QKeySequence(int(mods) | key)
        text = seq.toString(QKeySequence.SequenceFormat.NativeText)
        self._edit.setText(text)
        self._capturing = False
        # Restore default keyPressEvent
        self._edit.keyPressEvent = QLineEdit.keyPressEvent  # type: ignore[method-assign]

    def _clear(self) -> None:
        self._edit.setText("")
        self._capturing = False

    @property
    def shortcut(self) -> str | None:
        text = self._edit.text().strip()
        return text if text else None


class KeybindingsTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Click a shortcut field and press keys to rebind:"))

        self._table = QTableWidget(len(_ACTION_LABELS), 2)
        self._table.setHorizontalHeaderLabels(["Action", "Shortcut"])
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        bindings = SettingsManager.instance().keybindings
        self._cells: dict[str, _ShortcutCell] = {}

        for row, (action, label) in enumerate(_ACTION_LABELS.items()):
            action_item = QTableWidgetItem(label)
            action_item.setFlags(action_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, action_item)

            cell = _ShortcutCell(action, bindings.get(action))
            self._table.setCellWidget(row, 1, cell)
            self._cells[action] = cell

        self._table.resizeColumnsToContents()
        layout.addWidget(self._table, 1)

    def apply(self) -> None:
        """Persist all keybinding changes to SettingsManager."""
        settings = SettingsManager.instance()
        for action, cell in self._cells.items():
            settings.set_keybinding(action, cell.shortcut)
