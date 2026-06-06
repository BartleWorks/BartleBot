"""Combat tab: edit Bot Combat Profiles, Duel Profiles, and player deck config."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.combat_profile import BotCombatProfile, DuelProfile, format_combat_order, parse_combat_order
from bartlebot.combat_profile_store import (
    list_bot_profiles,
    list_duel_profiles,
    load_bot_profile,
    load_duel_profile,
    save_bot_profile,
    save_duel_profile,
)
from bartlebot.gui.sidebar.sections.duel_profile_widget import DuelProfileWidget
from bartlebot.gui.sidebar.sidebar_section import SidebarSection

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_NO_BOT_LABEL = "(no bot loaded)"
_NO_PROFILES_LABEL = "(no profiles)"
_NUM_PLAYER_SLOTS = 4


# ---------------------------------------------------------------------------
# Player Decks section
# ---------------------------------------------------------------------------


class _PlayerDecksSection(SidebarSection):
    """Collapsible section showing one import row per player slot."""

    def __init__(self, parent=None):
        super().__init__("Player Decks", parent)

        layout = QVBoxLayout()
        layout.setSpacing(4)

        self._rows: list[QWidget] = []
        for i in range(1, _NUM_PLAYER_SLOTS + 1):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)

            lbl = QLabel(f"P{i}:")
            lbl.setFixedWidth(22)
            self._deck_summary = QLabel("No deck loaded")
            btn = QPushButton("Import from client")
            btn.setEnabled(False)
            btn.setToolTip("Deck import coming soon")

            row_layout.addWidget(lbl)
            row_layout.addWidget(self._deck_summary, 1)
            row_layout.addWidget(btn)
            layout.addWidget(row)
            self._rows.append(row)

        self.set_content_layout(layout)

    def set_editable(self, editable: bool) -> None:  # noqa: ARG002
        pass  # import buttons stay disabled until deck format is defined


# ---------------------------------------------------------------------------
# Duel Profiles section
# ---------------------------------------------------------------------------


class _DuelProfilesSection(SidebarSection):
    """Collapsible section listing all exposed DuelProfileWidgets."""

    duel_profiles_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Fight Profiles", parent)
        self._editable = True
        self._widgets: dict[str, DuelProfileWidget] = {}

        self._layout = QVBoxLayout()
        self._layout.setSpacing(8)
        self._no_profile_lbl = QLabel("(no combat profile selected)")
        self._layout.addWidget(self._no_profile_lbl)

        self.set_content_layout(self._layout)

    def rebuild(self, config: BotConfig | None, combat_profile: BotCombatProfile | None) -> None:
        """Rebuild widgets to match the bot's exposed fight names and active profile."""
        for w in self._widgets.values():
            self._layout.removeWidget(w)
            w.deleteLater()
        self._widgets.clear()

        if config is None or combat_profile is None:
            self._no_profile_lbl.show()
            return

        self._no_profile_lbl.hide()

        exposed_names = _collect_exposed_fight_names(config)
        # Always show "default" first, then any exposed names
        all_names = ["default"] + [n for n in exposed_names if n != "default"]

        for name in all_names:
            # Ensure the duel profile entry exists in the combat profile
            if name not in combat_profile.duel_profiles:
                combat_profile.duel_profiles[name] = DuelProfile(name=name)
            w = DuelProfileWidget(name, combat_profile, self)
            w.set_editable(self._editable)
            w.duel_profile_changed.connect(self.duel_profiles_changed)
            w.import_requested.connect(self._on_import_duel_profile)
            w.export_requested.connect(self._on_export_duel_profile)
            self._layout.addWidget(w)
            self._widgets[name] = w

    def update_combat_profile(self, combat_profile: BotCombatProfile | None) -> None:
        """Swap out the backing BotCombatProfile in all widgets."""
        for w in self._widgets.values():
            if combat_profile is not None:
                w.set_combat_profile(combat_profile)

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        for w in self._widgets.values():
            w.set_editable(editable)

    def _on_import_duel_profile(self, profile_name: str) -> None:
        names = list_duel_profiles()
        if not names:
            QMessageBox.information(self, "Import Duel Profile", "No saved duel profiles found.")
            return
        chosen, ok = QInputDialog.getItem(self, "Import Duel Profile", "Select a saved profile:", names, 0, False)
        if not ok or not chosen:
            return
        try:
            imported = load_duel_profile(chosen)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        # Rename to match the slot being imported into
        imported.name = profile_name
        widget = self._widgets.get(profile_name)
        if widget is None:
            return
        # Replace the duel profile in the backing object
        widget._combat_profile.duel_profiles[profile_name] = imported
        widget._load_profile()
        self.duel_profiles_changed.emit()

    def _on_export_duel_profile(self, profile_name: str) -> None:
        widget = self._widgets.get(profile_name)
        if widget is None:
            return
        profile = widget._combat_profile.get_duel_profile(profile_name)
        name, ok = QInputDialog.getText(self, "Export Duel Profile", "Save as name:", text=profile_name)
        if not ok or not name.strip():
            return
        try:
            to_save = profile.model_copy(update={"name": name.strip()})
            save_duel_profile(to_save)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))


def _collect_exposed_fight_names(config: BotConfig) -> list[str]:
    """Return unique non-empty fight_profile_name values from all handle_combat nodes."""
    seen: list[str] = []
    for state in config.states:
        if state.node_type == "handle_combat":
            name = state.params.get("fight_profile_name", "").strip()
            if name and name not in seen:
                seen.append(name)
    return seen


# ---------------------------------------------------------------------------
# Combat Tab (main widget)
# ---------------------------------------------------------------------------


class CombatTab(QWidget):
    """Left-sidebar tab for combat configuration.

    Shows a profile selector (all BotCombatProfiles for the current bot),
    player deck import rows, and the list of exposed Duel Profiles.
    """

    combat_config_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._editable = True  # combat profiles are always editable; not gated by bot edit mode
        self._suppress = False

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(scroll_area.frameShape().NoFrame)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(4)

        # ── Profile selector bar ──────────────────────────────────────
        selector_section = SidebarSection("Bot Combat Profile", self)
        selector_layout = QVBoxLayout()
        selector_layout.setSpacing(4)

        self._profile_combo = QComboBox()
        self._profile_combo.currentTextChanged.connect(self._on_profile_selected)
        selector_layout.addWidget(self._profile_combo)

        btn_row = QHBoxLayout()
        self._new_btn = QPushButton("+ New")
        self._new_btn.setToolTip("Create a new blank Bot Combat Profile")
        self._new_btn.clicked.connect(self._on_new_profile)
        self._duplicate_btn = QPushButton("Duplicate")
        self._duplicate_btn.setToolTip("Copy the active profile with a new name")
        self._duplicate_btn.clicked.connect(self._on_duplicate_profile)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setToolTip("Delete the active profile")
        self._delete_btn.clicked.connect(self._on_delete_profile)
        btn_row.addWidget(self._new_btn)
        btn_row.addWidget(self._duplicate_btn)
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch()
        selector_layout.addLayout(btn_row)

        lib_row = QHBoxLayout()
        self._import_global_btn = QPushButton("Import from library")
        self._import_global_btn.setToolTip("Import a Bot Combat Profile from the global library")
        self._import_global_btn.clicked.connect(self._on_import_global)
        self._export_global_btn = QPushButton("Export to library")
        self._export_global_btn.setToolTip("Save the active profile to the global library")
        self._export_global_btn.clicked.connect(self._on_export_global)
        lib_row.addWidget(self._import_global_btn)
        lib_row.addWidget(self._export_global_btn)
        lib_row.addStretch()
        selector_layout.addLayout(lib_row)

        order_row = QHBoxLayout()
        order_row.addWidget(QLabel("Default combat order:"))
        self._combat_order_edit = QLineEdit()
        self._combat_order_edit.setPlaceholderText("(simultaneous)")
        self._combat_order_edit.setToolTip(
            "Default order clients enter combat for this profile.\n"
            "Enter player slots separated by commas, e.g. '2, 1'.\n"
            "Can be overridden per fight in the Fight Profiles section."
        )
        self._combat_order_edit.textEdited.connect(self._on_combat_order_changed)
        order_row.addWidget(self._combat_order_edit, 1)
        selector_layout.addLayout(order_row)

        selector_section.set_content_layout(selector_layout)
        content_layout.addWidget(selector_section)

        # ── Player Decks ─────────────────────────────────────────────
        self._decks_section = _PlayerDecksSection(self)
        content_layout.addWidget(self._decks_section)

        # ── Duel Profiles ─────────────────────────────────────────────
        self._duel_profiles_section = _DuelProfilesSection(self)
        self._duel_profiles_section.duel_profiles_changed.connect(self._on_content_changed)
        content_layout.addWidget(self._duel_profiles_section)

        content_layout.addStretch()

        scroll_area.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_area)

        self._refresh_controls()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._suppress = True
        self._rebuild_combo()
        self._rebuild_duel_profiles()
        self._refresh_controls()
        self._suppress = False

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._refresh_controls()
        self._decks_section.set_editable(editable)
        self._duel_profiles_section.set_editable(editable)

    def refresh_fight_profiles(self) -> None:
        """Re-scan exposed fight names and rebuild the duel profiles section.

        Called when the Combat tab becomes visible so it always reflects the
        current state of handle_combat nodes, even after in-session edits.
        """
        self._rebuild_duel_profiles()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _active_profile(self) -> BotCombatProfile | None:
        if self._config is None:
            return None
        return self._config.active_combat_profile

    def _rebuild_combo(self) -> None:
        self._suppress = True
        self._profile_combo.clear()
        if self._config is None or not self._config.combat_profiles:
            self._profile_combo.addItem(_NO_PROFILES_LABEL)
        else:
            for p in self._config.combat_profiles:
                self._profile_combo.addItem(p.name)
            active_name = self._config.active_combat_profile_name
            if active_name:
                idx = self._profile_combo.findText(active_name)
                if idx >= 0:
                    self._profile_combo.setCurrentIndex(idx)
        self._refresh_combat_order_field()
        self._suppress = False

    def _refresh_combat_order_field(self) -> None:
        profile = self._active_profile()
        self._combat_order_edit.setText(format_combat_order(profile.combat_order) if profile else "")

    def _rebuild_duel_profiles(self) -> None:
        self._duel_profiles_section.rebuild(self._config, self._active_profile())

    def _refresh_controls(self) -> None:
        has_config = self._config is not None
        has_profiles = has_config and bool(self._config.combat_profiles)
        editable = self._editable and has_config

        self._profile_combo.setEnabled(has_config and has_profiles)
        self._new_btn.setEnabled(editable)
        self._duplicate_btn.setEnabled(editable and has_profiles)
        self._delete_btn.setEnabled(editable and has_profiles and len(self._config.combat_profiles) > 1)
        self._import_global_btn.setEnabled(editable)
        self._export_global_btn.setEnabled(editable and has_profiles)
        self._combat_order_edit.setReadOnly(not (editable and has_profiles))

    def _on_profile_selected(self, name: str) -> None:
        if self._suppress or self._config is None or name == _NO_PROFILES_LABEL:
            return
        self._config.active_combat_profile_name = name
        self._duel_profiles_section.update_combat_profile(self._active_profile())
        self._refresh_combat_order_field()
        self._refresh_controls()
        self.combat_config_changed.emit()

    def _on_combat_order_changed(self, text: str) -> None:
        if self._suppress:
            return
        profile = self._active_profile()
        if profile is None:
            return
        profile.combat_order = parse_combat_order(text)
        self.combat_config_changed.emit()

    def _on_content_changed(self) -> None:
        self.combat_config_changed.emit()

    def _on_new_profile(self) -> None:
        if self._config is None:
            return
        name, ok = QInputDialog.getText(self, "New Profile", "Profile name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(p.name == name for p in self._config.combat_profiles):
            QMessageBox.warning(self, "Duplicate name", f"A profile named '{name}' already exists.")
            return
        new_profile = BotCombatProfile(name=name)
        self._config.combat_profiles.append(new_profile)
        self._config.active_combat_profile_name = name
        self._rebuild_combo()
        self._rebuild_duel_profiles()
        self._refresh_controls()
        self.combat_config_changed.emit()

    def _on_duplicate_profile(self) -> None:
        if self._config is None:
            return
        source = self._active_profile()
        if source is None:
            return
        name, ok = QInputDialog.getText(self, "Duplicate Profile", "New profile name:", text=f"{source.name} copy")
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(p.name == name for p in self._config.combat_profiles):
            QMessageBox.warning(self, "Duplicate name", f"A profile named '{name}' already exists.")
            return
        copy = source.model_copy(deep=True, update={"name": name})
        self._config.combat_profiles.append(copy)
        self._config.active_combat_profile_name = name
        self._rebuild_combo()
        self._rebuild_duel_profiles()
        self._refresh_controls()
        self.combat_config_changed.emit()

    def _on_delete_profile(self) -> None:
        if self._config is None or len(self._config.combat_profiles) <= 1:
            return
        active = self._active_profile()
        if active is None:
            return
        ans = QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete '{active.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._config.combat_profiles = [p for p in self._config.combat_profiles if p.name != active.name]
        self._config.active_combat_profile_name = (
            self._config.combat_profiles[0].name if self._config.combat_profiles else None
        )
        self._rebuild_combo()
        self._rebuild_duel_profiles()
        self._refresh_controls()
        self.combat_config_changed.emit()

    def _on_import_global(self) -> None:
        if self._config is None:
            return
        names = list_bot_profiles()
        if not names:
            QMessageBox.information(self, "Import Profile", "No saved profiles found in the global library.")
            return
        chosen, ok = QInputDialog.getItem(self, "Import Bot Combat Profile", "Select a profile:", names, 0, False)
        if not ok or not chosen:
            return
        try:
            imported = load_bot_profile(chosen)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        # Replace by name if it already exists, otherwise append
        existing_idx = next((i for i, p in enumerate(self._config.combat_profiles) if p.name == imported.name), None)
        if existing_idx is not None:
            self._config.combat_profiles[existing_idx] = imported
        else:
            self._config.combat_profiles.append(imported)
        self._config.active_combat_profile_name = imported.name
        self._rebuild_combo()
        self._rebuild_duel_profiles()
        self._refresh_controls()
        self.combat_config_changed.emit()

    def _on_export_global(self) -> None:
        if self._config is None:
            return
        profile = self._active_profile()
        if profile is None:
            return
        name, ok = QInputDialog.getText(self, "Export to Library", "Save as name:", text=profile.name)
        if not ok or not name.strip():
            return
        to_save = profile.model_copy(deep=True, update={"name": name.strip()})
        try:
            save_bot_profile(to_save)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Exported", f"Profile '{name.strip()}' saved to library.")
