"""Sidebar section: bot metadata fields (name, version, description, …)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QVBoxLayout, QWidget

from bartlebot.gui.sidebar.sidebar_section import (
    SidebarField,
    SidebarSection,
    value_edit,
    value_edit_multi,
    value_label,
)

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_PLACEHOLDER = "—"


def _swappable(label_w: QWidget, edit_w: QWidget) -> QWidget:
    """Container that shows either *label_w* or *edit_w* (not both).

    The label is shown by default.  Call ``setVisible`` on each child to
    switch modes; the layout collapses the hidden widget to zero height.
    """
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(label_w)
    lay.addWidget(edit_w)
    edit_w.setVisible(False)
    return w


class BotInfoSection(SidebarSection):
    """Collapsible 'Bot Info' section: file, name, version, description, etc."""

    metadata_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Bot Info", parent)
        self._config: BotConfig | None = None
        self._suppress: bool = False

        # File field — always a label, never editable
        self._file_lbl = value_label(wrap=True)

        # Switchable fields: each has a display label and an edit widget.
        # Labels are shown in Run mode (or when no config is loaded).
        # Edit widgets are shown in Edit mode.
        self._name_lbl = value_label()
        self._name_edit = value_edit()
        self._name_edit.setReadOnly(False)

        self._version_lbl = value_label()
        self._version_edit = value_edit()
        self._version_edit.setReadOnly(False)

        self._desc_lbl = value_label(wrap=True)
        self._desc_edit = value_edit_multi(lines=3)
        self._desc_edit.setReadOnly(False)

        self._start_lbl = value_label()
        self._start_edit = value_edit()
        self._start_edit.setReadOnly(False)

        self._authors_lbl = value_label()
        self._authors_edit = value_edit()
        self._authors_edit.setReadOnly(False)

        self._drops_lbl = value_label()
        self._drops_edit = value_edit()
        self._drops_edit.setReadOnly(False)
        self._drops_edit.setToolTip("Comma-separated list of expected drop items")

        self._initial_lbl = value_label()
        self._initial_combo = QComboBox()

        self._recovery_lbl = value_label()
        self._recovery_combo = QComboBox()

        # Ordered list of (label, edit) pairs for bulk visibility switching
        self._switchable_pairs: list[tuple[QWidget, QWidget]] = [
            (self._name_lbl, self._name_edit),
            (self._version_lbl, self._version_edit),
            (self._desc_lbl, self._desc_edit),
            (self._start_lbl, self._start_edit),
            (self._authors_lbl, self._authors_edit),
            (self._drops_lbl, self._drops_edit),
            (self._initial_lbl, self._initial_combo),
            (self._recovery_lbl, self._recovery_combo),
        ]

        vbox = QVBoxLayout()
        vbox.setSpacing(0)
        vbox.addWidget(SidebarField("File", self._file_lbl))
        vbox.addWidget(SidebarField("Name", _swappable(self._name_lbl, self._name_edit)))
        vbox.addWidget(SidebarField("Version", _swappable(self._version_lbl, self._version_edit)))
        vbox.addWidget(SidebarField("Description", _swappable(self._desc_lbl, self._desc_edit)))
        vbox.addWidget(SidebarField("Start", _swappable(self._start_lbl, self._start_edit)))
        vbox.addWidget(SidebarField("Author(s)", _swappable(self._authors_lbl, self._authors_edit)))
        vbox.addWidget(SidebarField("Drops", _swappable(self._drops_lbl, self._drops_edit)))
        vbox.addWidget(SidebarField("Initial State", _swappable(self._initial_lbl, self._initial_combo)))
        vbox.addWidget(SidebarField("Recovery State", _swappable(self._recovery_lbl, self._recovery_combo)))

        self.set_content_layout(vbox)

        self._name_edit.textChanged.connect(self._on_name_changed)
        self._version_edit.textChanged.connect(self._on_version_changed)
        self._desc_edit.textChanged.connect(self._on_desc_changed)
        self._start_edit.textChanged.connect(self._on_start_changed)
        self._authors_edit.textChanged.connect(self._on_authors_changed)
        self._drops_edit.textChanged.connect(self._on_drops_changed)
        self._initial_combo.currentTextChanged.connect(self._on_initial_changed)
        self._recovery_combo.currentTextChanged.connect(self._on_recovery_changed)

        self.set_config(None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_file(self, filename: str | None) -> None:
        self._file_lbl.setText(filename or _PLACEHOLDER)

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._suppress = True
        if config is None:
            for lbl in (
                self._name_lbl,
                self._version_lbl,
                self._desc_lbl,
                self._start_lbl,
                self._authors_lbl,
                self._drops_lbl,
                self._initial_lbl,
            ):
                lbl.setText(_PLACEHOLDER)
            for w in (
                self._name_edit,
                self._version_edit,
                self._start_edit,
                self._authors_edit,
                self._drops_edit,
            ):
                w.setText("")
            self._desc_edit.setPlainText("")
            self._initial_combo.clear()
            self._recovery_combo.clear()
            self._recovery_lbl.setText(_PLACEHOLDER)
            self._set_label_mode(True)
        else:
            drops_str = ", ".join(config.drops) if config.drops else ""
            # Populate display labels
            self._name_lbl.setText(config.name or _PLACEHOLDER)
            self._version_lbl.setText(config.version or _PLACEHOLDER)
            self._desc_lbl.setText(config.description or _PLACEHOLDER)
            self._start_lbl.setText(config.start_location or _PLACEHOLDER)
            self._authors_lbl.setText(config.authors or _PLACEHOLDER)
            self._drops_lbl.setText(drops_str or _PLACEHOLDER)
            # Populate edit widgets
            self._name_edit.setText(config.name or "")
            self._version_edit.setText(config.version or "")
            self._desc_edit.setPlainText(config.description or "")
            self._start_edit.setText(config.start_location or "")
            self._authors_edit.setText(config.authors or "")
            self._drops_edit.setText(drops_str)
            self._populate_initial_combo(config.states, config.initial)
            self._populate_recovery_combo(config.states, config.recovery_state)
        self._suppress = False

    def refresh_states(self, states: list) -> None:
        if self._config is None:
            return
        self._populate_initial_combo(states, self._config.initial)
        self._populate_recovery_combo(states, self._config.recovery_state)

    def set_editable(self, editable: bool) -> None:
        if self._config is None:
            return
        self._set_label_mode(not editable)

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    def _on_name_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.name = text
        self._name_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_version_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.version = text
        self._version_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_desc_changed(self) -> None:
        if self._suppress or self._config is None:
            return
        text = self._desc_edit.toPlainText()
        self._config.description = text or None
        self._desc_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_start_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.start_location = text or None
        self._start_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_authors_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.authors = text or None
        self._authors_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_drops_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.drops = [x.strip() for x in text.split(",") if x.strip()]
        self._drops_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    def _on_initial_changed(self, text: str) -> None:
        if self._suppress or self._config is None or not text:
            return
        self._config.initial = text
        self.metadata_changed.emit()

    def _on_recovery_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.recovery_state = text if text and text != _PLACEHOLDER else None
        self._recovery_lbl.setText(text or _PLACEHOLDER)
        self.metadata_changed.emit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_label_mode(self, label_mode: bool) -> None:
        """Switch all switchable fields between display labels and edit widgets."""
        for lbl, edit in self._switchable_pairs:
            lbl.setVisible(label_mode)
            edit.setVisible(not label_mode)

    def _populate_initial_combo(self, states: list, current_initial: str) -> None:
        self._suppress = True
        self._initial_combo.clear()
        for state in states:
            self._initial_combo.addItem(state.id)
        idx = self._initial_combo.findText(current_initial)
        if idx >= 0:
            self._initial_combo.setCurrentIndex(idx)
        self._initial_lbl.setText(current_initial or _PLACEHOLDER)
        self._suppress = False

    def _populate_recovery_combo(self, states: list, current_recovery: str | None) -> None:
        self._suppress = True
        self._recovery_combo.clear()
        self._recovery_combo.addItem(_PLACEHOLDER)  # "(none)" option
        for state in states:
            self._recovery_combo.addItem(state.id)
        target = current_recovery or _PLACEHOLDER
        idx = self._recovery_combo.findText(target)
        if idx >= 0:
            self._recovery_combo.setCurrentIndex(idx)
        self._recovery_lbl.setText(current_recovery or _PLACEHOLDER)
        self._suppress = False
