"""Combat tab: edit Bot Combat Profiles, Duel Profiles, and player deck config."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.combat_profile import (
    BotCombatProfile,
    DuelProfile,
    PlayerDeck,
)
from bartlebot.bot.schema import StateModel
from bartlebot.combat_profile_store import (
    list_bot_profiles,
    list_duel_profiles,
    load_bot_profile,
    load_duel_profile,
    save_bot_profile,
    save_duel_profile,
)
from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget
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

    import_requested = pyqtSignal(int)  # 1-based slot number

    def __init__(self, parent=None):
        super().__init__("Player Decks", parent)

        layout = QVBoxLayout()
        layout.setSpacing(4)

        self._import_btns: list[QPushButton] = []
        self._deck_summary_labels: list[QLabel] = []
        for i in range(1, _NUM_PLAYER_SLOTS + 1):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)

            lbl = QLabel(f"P{i}:")
            lbl.setFixedWidth(22)
            deck_summary = QLabel("No deck saved")
            btn = QPushButton("Import from client")
            btn.setToolTip("Open this client's deck window and snapshot the current deck")
            btn.clicked.connect(lambda _checked, slot=i: self.import_requested.emit(slot))

            row_layout.addWidget(lbl)
            row_layout.addWidget(deck_summary, 1)
            row_layout.addWidget(btn)
            layout.addWidget(row)
            self._import_btns.append(btn)
            self._deck_summary_labels.append(deck_summary)

        self.set_content_layout(layout)

    def set_editable(self, editable: bool) -> None:
        for btn in self._import_btns:
            btn.setEnabled(editable)

    def refresh(self, player_decks: dict[int, PlayerDeck]) -> None:
        """Update summary labels from the active combat profile's player_decks."""
        for i, lbl in enumerate(self._deck_summary_labels):
            slot = i + 1
            deck = player_decks.get(slot)
            if deck is None or deck.preset is None or deck.preset.is_empty():
                lbl.setText("No deck saved")
            else:
                preset = deck.preset
                parts = []
                if preset.normal:
                    total_normal = sum(preset.normal.values())
                    parts.append(f"{total_normal} normal")
                if preset.tc:
                    total_tc = sum(preset.tc.values())
                    parts.append(f"{total_tc} TC")
                if preset.item:
                    total_item = sum(preset.item.values())
                    parts.append(f"{total_item} item")
                lbl.setText(" · ".join(parts) if parts else "Empty deck")


# ---------------------------------------------------------------------------
# Duel Profiles section
# ---------------------------------------------------------------------------


class _DuelProfilesSection(SidebarSection):
    """Collapsible section with duel assignments and the duel profile library."""

    duel_profiles_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Duel Profiles", parent)
        self._editable = True
        self._config: BotConfig | None = None
        self._combat_profile: BotCombatProfile | None = None
        self._profile_widgets: dict[str, DuelProfileWidget] = {}
        # ComboBoxes keyed by state_id for duel assignment rows
        self._assignment_combos: dict[str, QComboBox] = {}

        self._layout = QVBoxLayout()
        self._layout.setSpacing(8)
        self._no_profile_lbl = QLabel("(no combat profile selected)")
        self._layout.addWidget(self._no_profile_lbl)

        self.set_content_layout(self._layout)

    def rebuild(self, config: BotConfig | None, combat_profile: BotCombatProfile | None) -> None:
        """Rebuild duel assignments and duel profile library widgets."""
        self._config = config
        self._combat_profile = combat_profile

        # Clear all dynamic widgets
        for w in self._profile_widgets.values():
            self._layout.removeWidget(w)
            w.deleteLater()
        self._profile_widgets.clear()
        self._assignment_combos.clear()

        # Remove duel-assignment rows (everything except the no_profile_lbl)
        while self._layout.count() > 1:
            item = self._layout.takeAt(1)
            if item and item.widget():
                item.widget().deleteLater()

        if config is None or combat_profile is None:
            self._no_profile_lbl.show()
            return

        self._no_profile_lbl.hide()

        all_states = _collect_all_handle_combat_states(config)
        exposed_states = _collect_exposed_states(config)

        # ── Part A: Duel Assignments ──────────────────────────────────
        if exposed_states:
            assignments_lbl = QLabel("Duel Assignments")
            font = assignments_lbl.font()
            font.setBold(True)
            assignments_lbl.setFont(font)
            self._layout.addWidget(assignments_lbl)

            for state in exposed_states:
                row = self._build_assignment_row(state, combat_profile)
                self._layout.addWidget(row)

        # ── Part B: Duel Profiles Library ─────────────────────────────
        library_lbl = QLabel("Duel Profiles")
        font = library_lbl.font()
        font.setBold(True)
        library_lbl.setFont(font)
        self._layout.addWidget(library_lbl)

        # Ensure "default" always exists
        if "default" not in combat_profile.duel_profiles:
            combat_profile.duel_profiles["default"] = DuelProfile(name="default")

        profile_names = ["default"] + [n for n in combat_profile.duel_profiles if n != "default"]
        for name in profile_names:
            self._add_profile_widget(name, combat_profile, all_states)

        new_btn = QPushButton("+ New Profile")
        new_btn.setToolTip("Create a new reusable Duel Profile")
        new_btn.clicked.connect(self._on_new_profile)
        self._layout.addWidget(new_btn)

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        for w in self._profile_widgets.values():
            w.set_editable(editable)

    def _build_assignment_row(self, state: StateModel, combat_profile: BotCombatProfile) -> QWidget:
        """Build one duel-assignment row: label + profile ComboBox."""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        display = f"{state.label or state.id} ({state.id})"
        lbl = QLabel(display)
        lbl.setToolTip(f"State ID: {state.id}")
        row_layout.addWidget(lbl, 1)

        combo = QComboBox()
        self._populate_combo(combo, combat_profile)
        current = state.params.get("duel_profile_name", "").strip() or "default"
        idx = combo.findText(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            # Backward compat: name in params not yet in profile dict — add as option
            combo.addItem(current)
            combo.setCurrentText(current)

        combo.currentTextChanged.connect(lambda name, sid=state.id: self._on_assignment_changed(sid, name))
        row_layout.addWidget(combo)
        self._assignment_combos[state.id] = combo
        return row

    def _populate_combo(self, combo: QComboBox, combat_profile: BotCombatProfile) -> None:
        combo.clear()
        combo.addItem("default")
        for name in combat_profile.duel_profiles:
            if name != "default":
                combo.addItem(name)

    def _add_profile_widget(
        self,
        name: str,
        combat_profile: BotCombatProfile,
        exposed_states: list[StateModel],
    ) -> None:
        w = DuelProfileWidget(name, combat_profile, exposed_states, parent=self)
        w.set_editable(self._editable)
        w.duel_profile_changed.connect(self.duel_profiles_changed)
        w.import_requested.connect(self._on_import_duel_profile)
        w.export_requested.connect(self._on_export_duel_profile)
        w.rename_requested.connect(self._on_rename_profile)
        w.delete_requested.connect(self._on_delete_profile)
        self._layout.addWidget(w)
        self._profile_widgets[name] = w

    def _on_assignment_changed(self, state_id: str, profile_name: str) -> None:
        """Update the state's duel_profile_name param when the ComboBox changes."""
        if self._config is None:
            return
        for state in self._config.states:
            if state.id == state_id:
                state.params["duel_profile_name"] = profile_name if profile_name != "default" else ""
                break
        # Refresh Used-by labels in all profile widgets
        all_states = _collect_all_handle_combat_states(self._config)
        for w in self._profile_widgets.values():
            w.set_exposed_states(all_states)
        self.duel_profiles_changed.emit()

    def _on_new_profile(self) -> None:
        if self._combat_profile is None:
            return
        name, ok = QInputDialog.getText(self, "New Duel Profile", "Profile name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self._combat_profile.duel_profiles:
            QMessageBox.warning(self, "Duplicate name", f"A profile named '{name}' already exists.")
            return
        self._combat_profile.duel_profiles[name] = DuelProfile(name=name)
        self.rebuild(self._config, self._combat_profile)
        self.duel_profiles_changed.emit()

    def _on_rename_profile(self, old_name: str, new_name: str) -> None:
        if self._combat_profile is None:
            return
        if new_name in self._combat_profile.duel_profiles:
            QMessageBox.warning(self, "Duplicate name", f"A profile named '{new_name}' already exists.")
            # Reset the name field in the widget
            widget = self._profile_widgets.get(old_name)
            if widget:
                widget.reset_name(old_name)
            return
        # Rename the dict entry
        profile = self._combat_profile.duel_profiles.pop(old_name)
        profile.name = new_name
        self._combat_profile.duel_profiles[new_name] = profile
        # Update all exposed states that referenced the old name
        if self._config:
            for state in self._config.states:
                if state.node_type == "handle_combat" and state.params.get("duel_profile_name") == old_name:
                    state.params["duel_profile_name"] = new_name
        self.rebuild(self._config, self._combat_profile)
        self.duel_profiles_changed.emit()

    def _on_delete_profile(self, profile_name: str) -> None:
        if self._combat_profile is None or profile_name == "default":
            return
        self._combat_profile.duel_profiles.pop(profile_name, None)
        self.rebuild(self._config, self._combat_profile)
        self.duel_profiles_changed.emit()

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
        widget = self._profile_widgets.get(profile_name)
        if widget is None or self._combat_profile is None:
            return
        self._combat_profile.duel_profiles[profile_name] = imported
        widget.reload()
        self.duel_profiles_changed.emit()

    def _on_export_duel_profile(self, profile_name: str) -> None:
        if self._combat_profile is None:
            return
        profile = self._combat_profile.get_duel_profile(profile_name)
        name, ok = QInputDialog.getText(self, "Export Duel Profile", "Save as name:", text=profile_name)
        if not ok or not name.strip():
            return
        try:
            to_save = profile.model_copy(update={"name": name.strip()})
            save_duel_profile(to_save)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))


def _collect_all_handle_combat_states(config: BotConfig) -> list[StateModel]:
    """Return all handle_combat states in the config."""
    return [s for s in config.states if s.node_type == "handle_combat"]


def _collect_exposed_states(config: BotConfig) -> list[StateModel]:
    """Return handle_combat states that are not using the default duel profile."""
    return [
        s
        for s in config.states
        if s.node_type == "handle_combat" and not s.params.get("use_default_duel_profile", True)
    ]


# ---------------------------------------------------------------------------
# Combat Tab (main widget)
# ---------------------------------------------------------------------------


class CombatTab(QWidget):
    """Left-sidebar tab for combat configuration.

    Shows a profile selector (all BotCombatProfiles for the current bot),
    player deck import rows, and the list of exposed Duel Profiles.
    """

    combat_config_changed = pyqtSignal()
    import_deck_requested = pyqtSignal(int)  # 1-based slot number

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

        selector_layout.addWidget(QLabel("Default combat order:"))
        self._combat_order_widget = CombatOrderWidget(empty_label="Random (enter combat simultaneously)")
        self._combat_order_widget.setToolTip(
            "Default order clients enter combat for this profile.\n"
            "Drag chips to reorder. Check the box for random simultaneous entry.\n"
            "Can be overridden per duel in the Duel Profiles section."
        )
        self._combat_order_widget.changed.connect(self._on_combat_order_changed)
        selector_layout.addWidget(self._combat_order_widget)

        selector_section.set_content_layout(selector_layout)
        content_layout.addWidget(selector_section)

        # ── Player Decks ─────────────────────────────────────────────
        self._decks_section = _PlayerDecksSection(self)
        self._decks_section.import_requested.connect(self.import_deck_requested)
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
        self._refresh_deck_summaries()
        self._refresh_controls()
        self._suppress = False

    def refresh_deck_summaries(self) -> None:
        """Re-read player_decks from the active profile and update summary labels."""
        self._refresh_deck_summaries()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._refresh_controls()
        self._decks_section.set_editable(editable)
        self._duel_profiles_section.set_editable(editable)

    def refresh_duel_profiles(self) -> None:
        """Re-scan exposed duel names and rebuild the duel profiles section.

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
        self._combat_order_widget.set_value(profile.combat_order if profile else [])

    def _rebuild_duel_profiles(self) -> None:
        self._duel_profiles_section.rebuild(self._config, self._active_profile())

    def _refresh_deck_summaries(self) -> None:
        profile = self._active_profile()
        self._decks_section.refresh(profile.player_decks if profile else {})

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
        self._combat_order_widget.set_editable(editable and has_profiles)

    def _on_profile_selected(self, name: str) -> None:
        if self._suppress or self._config is None or name == _NO_PROFILES_LABEL:
            return
        self._config.active_combat_profile_name = name
        self._rebuild_duel_profiles()
        self._refresh_combat_order_field()
        self._refresh_deck_summaries()
        self._refresh_controls()
        self.combat_config_changed.emit()

    def _on_combat_order_changed(self) -> None:
        if self._suppress:
            return
        profile = self._active_profile()
        if profile is None:
            return
        profile.combat_order = self._combat_order_widget.get_value()
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
