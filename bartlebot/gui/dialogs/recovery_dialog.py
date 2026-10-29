"""Startup dialog offering to restore auto-saved bot files after a crash."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from bartleby.qt.widgets.buttons.action_button import ActionStyle
from bartleby.qt.widgets.layout.dialog_footer import dialog_footer
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class RecoveryDialog(QDialog):
    """Shown at startup when auto-save files are found that are newer than their originals.

    Each candidate is a dict:
        autosave_path: Path  — the .autosave.json file
        original_path: Path | None  — the original bot file (None for unsaved new bots)
        modified: datetime  — mtime of the autosave file

    The user checks which bots to restore and clicks OK, or clicks Discard All to
    delete every recovery file without restoring anything.
    """

    def __init__(self, candidates: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recover Unsaved Changes")
        self.setMinimumSize(560, 260)
        self._candidates = candidates
        self._checkboxes: list[QCheckBox] = []
        self._build(candidates)

    def _build(self, candidates: list[dict]) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        intro = QLabel(
            "BartleBot found auto-saved versions that are newer than the last saved files. "
            "Select the bots you want to restore."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        table = QTableWidget(len(candidates), 3)
        table.setHorizontalHeaderLabels(["Restore?", "Bot File", "Auto-saved At"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        for row, c in enumerate(candidates):
            # Centered checkbox
            cb = QCheckBox()
            cb.setChecked(True)
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.addWidget(cb)
            cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            table.setCellWidget(row, 0, cell)
            self._checkboxes.append(cb)

            # Bot file name
            original: Path | None = c.get("original_path")
            label = original.name if original else "(unsaved new bot)"
            table.setItem(row, 1, QTableWidgetItem(label))

            # Timestamp
            ts: datetime = c["modified"]
            table.setItem(row, 2, QTableWidgetItem(ts.strftime("%Y-%m-%d %H:%M:%S")))

        layout.addWidget(table)

        # Discard All takes the reject slot but not its weight: ``_discard_all`` unchecks
        # every candidate and rejects, which permanently drops every recovered unsaved bot
        # file. That is the most destructive button in the app, and it must not render as
        # the quietest thing on the screen.
        footer = dialog_footer(
            self,
            cancel_text="Discard All",
            cancel_icon="trash",
            cancel_style=ActionStyle.CAUTION,
            on_cancel=self._discard_all,
        )
        layout.addLayout(footer.layout)

    def selected_candidates(self) -> list[dict]:
        """Return only the candidates the user checked for restoration."""
        return [c for c, cb in zip(self._candidates, self._checkboxes) if cb.isChecked()]

    def _discard_all(self) -> None:
        for cb in self._checkboxes:
            cb.setChecked(False)
        self.reject()
