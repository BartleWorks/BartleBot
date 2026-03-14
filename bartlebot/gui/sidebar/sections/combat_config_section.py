"""Sidebar section: bot-level combat config (use-default toggle + DSL editor)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PyQt6.QtWidgets import QCheckBox, QVBoxLayout

from bartlebot.gui.sidebar.sidebar_section import SidebarSection, value_edit_multi

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig


class CombatConfigSection(SidebarSection):
    """Collapsible 'Combat Config' section."""

    def __init__(self, parent=None):
        super().__init__("Combat Config", parent)
        self._config: BotConfig | None = None
        self._suppress: bool = False
        self.on_config_changed: Callable[[], None] = lambda: None

        layout = QVBoxLayout()
        layout.setSpacing(4)

        self._use_default_chk = QCheckBox("Use BartleBot default combat config")
        self._use_default_chk.setChecked(True)
        layout.addWidget(self._use_default_chk)

        self._dsl_edit = value_edit_multi(lines=8)
        self._dsl_edit.setPlaceholderText("Enter WizSprinter combat DSL…")
        layout.addWidget(self._dsl_edit)

        self.set_content_layout(layout)

        self._use_default_chk.toggled.connect(self._on_use_default_changed)
        self._use_default_chk.toggled.connect(
            lambda checked: self._dsl_edit.setReadOnly(checked)
        )
        self._dsl_edit.textChanged.connect(self._on_dsl_changed)

        self.set_config(None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._suppress = True
        if config is None:
            self._use_default_chk.setChecked(True)
            self._use_default_chk.setEnabled(False)
            self._dsl_edit.setPlainText("")
            self._dsl_edit.setReadOnly(True)
        else:
            self._use_default_chk.setChecked(config.use_bartlebot_default_combat_config)
            self._dsl_edit.setPlainText(config.default_combat_config or "")
        self._suppress = False

    def set_editable(self, editable: bool) -> None:
        if self._config is None:
            return
        self._use_default_chk.setEnabled(editable)
        use_default = self._use_default_chk.isChecked()
        self._dsl_edit.setReadOnly(not editable or use_default)

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    def _on_use_default_changed(self, checked: bool) -> None:
        if self._suppress or self._config is None:
            return
        self._config.use_bartlebot_default_combat_config = checked
        self.on_config_changed()

    def _on_dsl_changed(self) -> None:
        if self._suppress or self._config is None:
            return
        self._config.default_combat_config = self._dsl_edit.toPlainText() or None
        self.on_config_changed()
