"""Widget for editing a single DuelProfile within a BotCombatProfile."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.combat_profile import CombatBehavior, DuelProfile
from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget
from bartlebot.gui.theme import THEME

if TYPE_CHECKING:
    from bartlebot.bot.combat_profile import BotCombatProfile
    from bartlebot.bot.schema import StateModel

_NUM_PLAYER_SLOTS = 4


class _CombatBehaviorWidget(QWidget):
    """Row for one CombatBehavior: player checkboxes + DSL editor + delete button."""

    changed = pyqtSignal()
    players_changed = pyqtSignal(object)  # emits self after a player toggle (for exclusivity enforcement)
    delete_requested = pyqtSignal(object)  # emits self

    def __init__(self, behavior: CombatBehavior, parent=None):
        super().__init__(parent)
        self._behavior = behavior

        # Horizontal divider above each behavior row
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {THEME.border};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(3)
        outer.addWidget(divider)

        # ── Player checkboxes ──────────────────────────────────────────
        player_row = QWidget()
        player_layout = QHBoxLayout(player_row)
        player_layout.setContentsMargins(0, 0, 0, 0)
        player_layout.setSpacing(6)

        self._player_chks: list[QCheckBox] = []
        for i in range(1, _NUM_PLAYER_SLOTS + 1):
            chk = QCheckBox(f"P{i}")
            chk.setChecked(i in behavior.players)
            chk.toggled.connect(self._on_players_changed)
            player_layout.addWidget(chk)
            self._player_chks.append(chk)

        player_layout.addStretch()

        self._delete_btn = QPushButton("✕")
        self._delete_btn.setFixedSize(20, 20)
        self._delete_btn.setToolTip("Remove this behavior")
        self._delete_btn.clicked.connect(lambda: self.delete_requested.emit(self))
        player_layout.addWidget(self._delete_btn)

        outer.addWidget(player_row)

        # ── DSL editor ────────────────────────────────────────────────
        self._dsl_edit = QPlainTextEdit()
        self._dsl_edit.setPlainText(behavior.dsl)
        self._dsl_edit.setPlaceholderText("Enter combat DSL…")
        self._dsl_edit.setFixedHeight(56)  # ~2-3 lines; scroll for longer configs
        self._dsl_edit.textChanged.connect(self._on_dsl_changed)
        outer.addWidget(self._dsl_edit)

    @property
    def players(self) -> list[int]:
        """Currently checked player slots (1-based) for this behavior."""
        return self._behavior.players

    def set_editable(self, editable: bool) -> None:
        for chk in self._player_chks:
            chk.setEnabled(editable)
        self._dsl_edit.setReadOnly(not editable)
        self._delete_btn.setEnabled(editable)

    def force_uncheck_player(self, player_1based: int) -> None:
        """Silently uncheck a player checkbox and update the backing model (no signals)."""
        idx = player_1based - 1
        if 0 <= idx < len(self._player_chks):
            chk = self._player_chks[idx]
            if chk.isChecked():
                chk.blockSignals(True)
                chk.setChecked(False)
                chk.blockSignals(False)
                if player_1based in self._behavior.players:
                    self._behavior.players.remove(player_1based)

    def _on_players_changed(self) -> None:
        self._behavior.players = [i + 1 for i, chk in enumerate(self._player_chks) if chk.isChecked()]
        self.changed.emit()
        self.players_changed.emit(self)

    def _on_dsl_changed(self) -> None:
        self._behavior.dsl = self._dsl_edit.toPlainText()
        self.changed.emit()


class DuelProfileWidget(QWidget):
    """Collapsible card for editing one DuelProfile within a BotCombatProfile.

    Emits ``duel_profile_changed`` whenever the profile data is mutated.
    Emits ``import_requested`` / ``export_requested`` for the Combat tab to handle.
    Emits ``rename_requested(old_name, new_name)`` when the user renames the profile.
    Emits ``delete_requested(profile_name)`` when the user deletes the profile.
    """

    duel_profile_changed = pyqtSignal()
    import_requested = pyqtSignal(str)  # profile_name
    export_requested = pyqtSignal(str)  # profile_name
    rename_requested = pyqtSignal(str, str)  # old_name, new_name
    delete_requested = pyqtSignal(str)  # profile_name

    def __init__(
        self,
        profile_name: str,
        combat_profile: BotCombatProfile,
        exposed_states: list[StateModel],
        *,
        parent=None,
    ):
        super().__init__(parent)
        self._profile_name = profile_name
        self._combat_profile = combat_profile
        self._exposed_states = exposed_states
        self._editable = False
        self._suppress = False

        # Outer layout on self — no stylesheet on self to avoid StyleChange recursion.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.setSpacing(0)

        # Card frame provides the visible border without using self.setStyleSheet().
        self._card = QFrame()
        self._card.setFrameShape(QFrame.Shape.StyledPanel)
        self._card.setStyleSheet(
            f"QFrame {{ border: 1px solid {THEME.border}; border-radius: 4px; background: {THEME.bg}; padding: 6px; }}"
        )
        outer.addWidget(self._card)

        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(6, 6, 6, 6)
        card_layout.setSpacing(4)

        # ── "Used by" label ───────────────────────────────────────────
        self._used_by_lbl = QLabel()
        self._used_by_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px;")
        self._used_by_lbl.setWordWrap(True)
        card_layout.addWidget(self._used_by_lbl)

        # ── Header row ────────────────────────────────────────────────
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)

        self._name_edit = QLineEdit(profile_name)
        font = self._name_edit.font()
        font.setBold(True)
        self._name_edit.setFont(font)
        self._name_edit.setFrame(False)
        self._name_edit.setToolTip("Profile name — press Enter or click away to rename")
        self._name_edit.setReadOnly(profile_name == "default")
        self._name_edit.editingFinished.connect(self._on_name_editing_finished)
        header_layout.addWidget(self._name_edit, 1)

        self._import_btn = QPushButton("Import")
        self._import_btn.setToolTip("Import a saved Duel Profile from the global library")
        self._import_btn.clicked.connect(lambda: self.import_requested.emit(self._profile_name))
        self._export_btn = QPushButton("Export")
        self._export_btn.setToolTip("Export this Duel Profile to the global library")
        self._export_btn.clicked.connect(lambda: self.export_requested.emit(self._profile_name))
        self._delete_profile_btn = QPushButton("Delete")
        self._delete_profile_btn.setToolTip("Delete this Duel Profile (only available when unused)")
        self._delete_profile_btn.clicked.connect(lambda: self.delete_requested.emit(self._profile_name))
        header_layout.addWidget(self._import_btn)
        header_layout.addWidget(self._export_btn)
        header_layout.addWidget(self._delete_profile_btn)

        card_layout.addWidget(header)
        self._refresh_used_by()

        # ── Use BartleBot default checkbox ────────────────────────────
        self._use_default_chk = QCheckBox("Use BartleBot default")
        self._use_default_chk.toggled.connect(self._on_use_default_changed)
        card_layout.addWidget(self._use_default_chk)

        # ── Combat order override ─────────────────────────────────────
        card_layout.addWidget(QLabel("Combat order override:"))
        self._combat_order_widget = CombatOrderWidget(empty_label="Inherit from profile")
        self._combat_order_widget.setToolTip(
            "Override the combat entry order for this fight only.\n"
            "Drag chips to reorder. Check 'Inherit from profile' to use the profile default."
        )
        self._combat_order_widget.changed.connect(self._on_combat_order_changed)
        card_layout.addWidget(self._combat_order_widget)

        # ── Behaviors container ───────────────────────────────────────
        self._behaviors_container = QWidget()
        self._behaviors_layout = QVBoxLayout(self._behaviors_container)
        self._behaviors_layout.setContentsMargins(4, 4, 4, 4)
        self._behaviors_layout.setSpacing(0)

        self._add_btn = QPushButton("+ Add behavior")
        self._add_btn.clicked.connect(self._on_add_behavior)
        self._behaviors_layout.addWidget(self._add_btn)

        card_layout.addWidget(self._behaviors_container)

        self._behavior_widgets: list[_CombatBehaviorWidget] = []

        self._load_profile()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_combat_profile(self, combat_profile: BotCombatProfile) -> None:
        """Replace the backing BotCombatProfile and reload the displayed profile."""
        self._combat_profile = combat_profile
        self._load_profile()

    def reload(self) -> None:
        """Reload all fields from the backing DuelProfile (e.g. after an import)."""
        self._load_profile()

    def reset_name(self, name: str) -> None:
        """Reset the name field to *name* (e.g. after a rejected rename)."""
        self._name_edit.setText(name)

    def set_exposed_states(self, handle_combat_states: list[StateModel]) -> None:
        """Update the list of handle_combat states and refresh the Used-by label."""
        self._exposed_states = handle_combat_states
        self._refresh_used_by()
        self._refresh_delete_btn()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._use_default_chk.setEnabled(editable)
        self._combat_order_widget.set_editable(editable)
        self._add_btn.setVisible(editable)
        self._import_btn.setEnabled(editable)
        if self._profile_name != "default":
            self._name_edit.setReadOnly(not editable)
        self._refresh_delete_btn()
        for w in self._behavior_widgets:
            w.set_editable(editable)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._card.setStyleSheet(
                f"QFrame {{ border: 1px solid {THEME.border}; border-radius: 4px; background: {THEME.bg}; padding: 6px; }}"
            )
            self._used_by_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px;")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _duel_profile(self) -> DuelProfile:
        return self._combat_profile.get_duel_profile(self._profile_name)

    def _refresh_used_by(self) -> None:
        if self._profile_name == "default":
            # Default profile is used by all states that have use_default_duel_profile=True (or unset)
            matching = [s for s in self._exposed_states if s.params.get("use_default_duel_profile", True)]
        else:
            # Named profile is used by states explicitly assigned to it
            matching = [
                s
                for s in self._exposed_states
                if not s.params.get("use_default_duel_profile", True)
                and s.params.get("duel_profile_name", "") == self._profile_name
            ]
        if matching:
            parts = [f"{s.label or s.id} ({s.id})" for s in matching]
            self._used_by_lbl.setText("Used by: " + ", ".join(parts))
        else:
            self._used_by_lbl.setText("Unused")
        self._refresh_delete_btn()

    def _refresh_delete_btn(self) -> None:
        if self._profile_name == "default":
            matching = [s for s in self._exposed_states if s.params.get("use_default_duel_profile", True)]
        else:
            matching = [
                s
                for s in self._exposed_states
                if not s.params.get("use_default_duel_profile", True)
                and s.params.get("duel_profile_name", "") == self._profile_name
            ]
        can_delete = self._editable and self._profile_name != "default" and not matching
        self._delete_profile_btn.setVisible(can_delete)

    def _load_profile(self) -> None:
        self._suppress = True
        profile = self._duel_profile
        self._use_default_chk.setChecked(profile.use_bartlebot_default)
        order = profile.combat_order_override
        self._combat_order_widget.set_value(order if order is not None else [])
        self._behaviors_container.setVisible(not profile.use_bartlebot_default)
        self._rebuild_behaviors()
        self._suppress = False

    def _rebuild_behaviors(self) -> None:
        for w in self._behavior_widgets:
            self._behaviors_layout.removeWidget(w)
            w.deleteLater()
        self._behavior_widgets.clear()

        profile = self._duel_profile
        for behavior in profile.behaviors:
            self._append_behavior_widget(behavior)

    def _append_behavior_widget(self, behavior: CombatBehavior) -> None:
        w = _CombatBehaviorWidget(behavior, self)
        w.set_editable(self._editable)
        w.changed.connect(self.duel_profile_changed)
        w.players_changed.connect(self._on_behavior_players_changed)
        w.delete_requested.connect(self._on_delete_behavior)
        # Insert before the "+ Add behavior" button
        idx = self._behaviors_layout.indexOf(self._add_btn)
        self._behaviors_layout.insertWidget(idx, w)
        self._behavior_widgets.append(w)

    def _on_name_editing_finished(self) -> None:
        new_name = self._name_edit.text().strip()
        if not new_name or new_name == self._profile_name:
            self._name_edit.setText(self._profile_name)
            return
        self.rename_requested.emit(self._profile_name, new_name)

    def _on_behavior_players_changed(self, source: _CombatBehaviorWidget) -> None:
        """Enforce mutual exclusivity: each player slot appears in at most one behavior."""
        claimed = set(source.players)
        any_changed = False
        for w in self._behavior_widgets:
            if w is source:
                continue
            for player in list(w.players):
                if player in claimed:
                    w.force_uncheck_player(player)
                    any_changed = True
                else:
                    claimed.add(player)
        if any_changed:
            self.duel_profile_changed.emit()

    def _on_combat_order_changed(self) -> None:
        if self._suppress:
            return
        value = self._combat_order_widget.get_value()
        self._duel_profile.combat_order_override = value if value else None
        self.duel_profile_changed.emit()

    def _on_use_default_changed(self, checked: bool) -> None:
        if self._suppress:
            return
        self._duel_profile.use_bartlebot_default = checked
        self._behaviors_container.setVisible(not checked)
        self.duel_profile_changed.emit()

    def _on_add_behavior(self) -> None:
        behavior = CombatBehavior()
        self._duel_profile.behaviors.append(behavior)
        self._append_behavior_widget(behavior)
        self.duel_profile_changed.emit()

    def _on_delete_behavior(self, widget: _CombatBehaviorWidget) -> None:
        idx = self._behavior_widgets.index(widget)
        self._duel_profile.behaviors.pop(idx)
        self._behaviors_layout.removeWidget(widget)
        widget.deleteLater()
        self._behavior_widgets.remove(widget)
        self.duel_profile_changed.emit()
