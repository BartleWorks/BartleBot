"""Sidebar section: bot metadata fields (name, version, description, …)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QFormLayout, QSizePolicy, QVBoxLayout

from bartlebot.gui.sidebar.sidebar_section import SidebarSection, field_label, value_edit, value_edit_multi, value_label

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_PLACEHOLDER = "—"


class BotInfoSection(SidebarSection):
    """Collapsible 'Bot Info' section: file, name, version, description, etc."""

    def __init__(self, parent=None):
        super().__init__("Bot Info", parent)
        self._config: BotConfig | None = None
        self._suppress: bool = False
        self.on_config_changed: Callable[[], None] = lambda: None

        form = QFormLayout()
        form.setSpacing(2)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._file_lbl = value_label(wrap=True)
        self._file_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        self._name_edit = value_edit()
        self._version_edit = value_edit()
        self._desc_edit = value_edit_multi(lines=3)
        self._start_edit = value_edit()
        self._authors_edit = value_edit()
        self._drops_edit = value_edit()
        self._drops_edit.setToolTip("Comma-separated list of expected drop items")

        self._initial_combo = QComboBox()
        self._initial_combo.setEnabled(False)

        form.addRow(field_label("File"), self._file_lbl)
        form.addRow(field_label("Name"), self._name_edit)
        form.addRow(field_label("Version"), self._version_edit)
        form.addRow(field_label("Description"), self._desc_edit)
        form.addRow(field_label("Start"), self._start_edit)
        form.addRow(field_label("Author(s)"), self._authors_edit)
        form.addRow(field_label("Drops"), self._drops_edit)
        form.addRow(field_label("Initial State"), self._initial_combo)

        self.set_content_layout(form)

        self._name_edit.textChanged.connect(self._on_name_changed)
        self._version_edit.textChanged.connect(self._on_version_changed)
        self._desc_edit.textChanged.connect(self._on_desc_changed)
        self._start_edit.textChanged.connect(self._on_start_changed)
        self._authors_edit.textChanged.connect(self._on_authors_changed)
        self._drops_edit.textChanged.connect(self._on_drops_changed)
        self._initial_combo.currentTextChanged.connect(self._on_initial_changed)

        self.set_config(None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_file(self, filename: str | None) -> None:
        self._file_lbl.setText(filename or _PLACEHOLDER)

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._suppress = True
        line_edits = (
            self._name_edit, self._version_edit,
            self._start_edit, self._authors_edit, self._drops_edit,
        )
        if config is None:
            for w in line_edits:
                w.setText("")
                w.setReadOnly(True)
            self._desc_edit.setPlainText("")
            self._desc_edit.setReadOnly(True)
            self._initial_combo.clear()
            self._initial_combo.setEnabled(False)
        else:
            self._name_edit.setText(config.name or "")
            self._version_edit.setText(config.version or "")
            self._start_edit.setText(config.start_location or "")
            self._authors_edit.setText(config.authors or "")
            self._drops_edit.setText(", ".join(config.drops) if config.drops else "")
            self._desc_edit.setPlainText(config.description or "")
            self._populate_initial_combo(config.states, config.initial)
        self._suppress = False

    def refresh_states(self, states: list) -> None:
        if self._config is None:
            return
        self._populate_initial_combo(states, self._config.initial)

    def set_editable(self, editable: bool) -> None:
        if self._config is None:
            return
        line_edits = (
            self._name_edit, self._version_edit,
            self._start_edit, self._authors_edit, self._drops_edit,
        )
        for w in line_edits:
            w.setReadOnly(not editable)
        self._desc_edit.setReadOnly(not editable)
        self._initial_combo.setEnabled(editable)

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    def _on_name_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.name = text
        self.on_config_changed()

    def _on_version_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.version = text
        self.on_config_changed()

    def _on_desc_changed(self) -> None:
        if self._suppress or self._config is None:
            return
        self._config.description = self._desc_edit.toPlainText() or None
        self.on_config_changed()

    def _on_start_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.start_location = text or None
        self.on_config_changed()

    def _on_authors_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.authors = text or None
        self.on_config_changed()

    def _on_drops_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.drops = [x.strip() for x in text.split(",") if x.strip()]
        self.on_config_changed()

    def _on_initial_changed(self, text: str) -> None:
        if self._suppress or self._config is None or not text:
            return
        self._config.initial = text
        self.on_config_changed()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _populate_initial_combo(self, states: list, current_initial: str) -> None:
        self._suppress = True
        self._initial_combo.clear()
        for state in states:
            self._initial_combo.addItem(state.id)
        idx = self._initial_combo.findText(current_initial)
        if idx >= 0:
            self._initial_combo.setCurrentIndex(idx)
        self._suppress = False
