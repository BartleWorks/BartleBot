"""Combat tab: manage Bot Combat Strategy, state assignments, and player decks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DeckData,
    DuelCombatStrategy,
    PlayerCombatStrategy,
    StateAssignment,
)
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.sidebar.sidebar_section import SidebarSection
from bartlebot.gui.strategy_library.strategy_picker_dialog import StrategyPickerDialog
from bartlebot.gui.theme import THEME
from bartlebot.settings_manager import SettingsManager
from bartlebot.strategy_store import (
    delete_bot_strategy,
    list_bot_strategies,
    list_duel_strategies,
    list_player_strategies,
    save_bot_strategy,
    save_duel_strategy,
)

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig
    from bartlebot.gui.strategy_library.strategy_library_window import StrategyLibraryWindow

logger = logging.getLogger(__name__)

_DEBOUNCE_MS = 500
_NUM_PLAYER_SLOTS = 4
_BUILT_IN_LABEL = "BartleBot Default Combat Strategy"
_NO_STRATEGY_LABEL = "(no strategy set)"
_ICON_BTN_SIZE = 16
_DSL_PREVIEW_CHARS = 28


# ---------------------------------------------------------------------------
# Strategy chip button
# ---------------------------------------------------------------------------


class _StrategyChip(QPushButton):
    """Styled chip button showing a strategy name — click to open a picker."""

    _ICON_SIZE = 14

    def __init__(self, text: str = _NO_STRATEGY_LABEL, parent=None):
        super().__init__(text, parent)
        self._applying_style = False
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setIconSize(QSize(self._ICON_SIZE, self._ICON_SIZE))
        self._apply_style()

    def set_strategy_name(self, name: str) -> None:
        self.setText(name)

    def _apply_style(self) -> None:
        if self._applying_style:
            return
        self._applying_style = True
        try:
            self.setIcon(svg_icon("search", self._ICON_SIZE, tint=THEME.text_muted))
            self.setStyleSheet(
                f"QPushButton {{ text-align: left; padding: 3px 8px; "
                f"background: {THEME.surface}; color: {THEME.text}; "
                f"border: 1px solid {THEME.border}; border-radius: 3px; }}"
                f"QPushButton:hover {{ border-color: {THEME.accent}; }}"
                f"QPushButton:disabled {{ color: {THEME.text_muted}; }}"
            )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()


# ---------------------------------------------------------------------------
# Player Decks section
# ---------------------------------------------------------------------------


class _PlayerDecksSection(SidebarSection):
    """Collapsible section showing one import/apply row per player slot."""

    import_requested = pyqtSignal(int)  # 1-based slot number
    apply_requested = pyqtSignal(int)  # 1-based slot number
    view_requested = pyqtSignal(int)  # 1-based slot number
    save_to_library_requested = pyqtSignal(int)  # 1-based slot number
    load_from_library_requested = pyqtSignal(int)  # 1-based slot number
    clear_requested = pyqtSignal(int)  # 1-based slot number

    def __init__(self, parent=None):
        super().__init__("Player Decks", parent)

        layout = QVBoxLayout()
        layout.setSpacing(4)

        self._import_btns: list[QPushButton] = []
        self._apply_btns: list[QPushButton] = []
        self._menu_btns: list[QToolButton] = []
        self._deck_summary_labels: list[QLabel] = []
        self._decks: dict[int, DeckData] = {}
        self._editable: bool = True
        self._client_count: int = 0

        for i in range(1, _NUM_PLAYER_SLOTS + 1):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)

            lbl = QLabel(f"P{i}:")
            lbl.setFixedWidth(22)

            deck_summary = QLabel("No deck saved")
            deck_summary.setCursor(Qt.CursorShape.PointingHandCursor)
            deck_summary.setToolTip("Click to view deck contents")
            deck_summary.mousePressEvent = lambda _ev, slot=i: self._on_summary_clicked(slot)

            import_btn = QPushButton("Import")
            import_btn.setToolTip("Snapshot the deck from this game client")
            import_btn.clicked.connect(lambda _checked, slot=i: self.import_requested.emit(slot))

            apply_btn = QPushButton("Apply")
            apply_btn.setToolTip("Push the saved deck to this game client now")
            apply_btn.clicked.connect(lambda _checked, slot=i: self.apply_requested.emit(slot))

            menu_btn = QToolButton()
            menu_btn.setIcon(svg_icon("more_vert", _ICON_BTN_SIZE))
            menu_btn.setIconSize(QSize(_ICON_BTN_SIZE, _ICON_BTN_SIZE))
            menu_btn.setToolTip("More deck actions")
            menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            menu_btn.setStyleSheet("QToolButton::menu-indicator { image: none; }")
            slot_menu = QMenu(menu_btn)
            slot_menu.addAction("Save to Library", lambda _=False, slot=i: self.save_to_library_requested.emit(slot))
            slot_menu.addAction(
                "Load from Library", lambda _=False, slot=i: self.load_from_library_requested.emit(slot)
            )
            slot_menu.addSeparator()
            slot_menu.addAction("Clear Deck", lambda _=False, slot=i: self.clear_requested.emit(slot))
            menu_btn.setMenu(slot_menu)

            row_layout.addWidget(lbl)
            row_layout.addWidget(deck_summary, 1)
            row_layout.addWidget(import_btn)
            row_layout.addWidget(apply_btn)
            row_layout.addWidget(menu_btn)
            layout.addWidget(row)

            self._import_btns.append(import_btn)
            self._apply_btns.append(apply_btn)
            self._menu_btns.append(menu_btn)
            self._deck_summary_labels.append(deck_summary)

        self.set_content_layout(layout)

    def _on_summary_clicked(self, slot: int) -> None:
        deck = self._decks.get(slot)
        if deck is not None and not deck.is_empty():
            self.view_requested.emit(slot)

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._reapply_button_states()

    def set_client_count(self, count: int) -> None:
        self._client_count = count
        self._reapply_button_states()

    def _reapply_button_states(self) -> None:
        for i, (import_btn, apply_btn, menu_btn) in enumerate(
            zip(self._import_btns, self._apply_btns, self._menu_btns)
        ):
            slot = i + 1
            has_client = self._editable and slot <= self._client_count
            has_deck = self._decks.get(slot) is not None and not self._decks[slot].is_empty()
            import_btn.setEnabled(has_client)
            apply_btn.setEnabled(has_client and has_deck)
            menu_btn.setEnabled(self._editable)

    def refresh(self, player_decks: dict[int, DeckData]) -> None:
        self._decks = player_decks
        for i, lbl in enumerate(self._deck_summary_labels):
            slot = i + 1
            deck = player_decks.get(slot)
            has_deck = deck is not None and not deck.is_empty()
            if not has_deck:
                lbl.setText("No deck saved")
                lbl.setCursor(Qt.CursorShape.ArrowCursor)
                lbl.setToolTip("")
            else:
                parts = []
                if deck.normal:
                    parts.append(f"{sum(deck.normal.values())} normal")
                if deck.tc:
                    parts.append(f"{sum(deck.tc.values())} TC")
                if deck.item:
                    parts.append(f"{sum(deck.item.values())} item")
                lbl.setText(" · ".join(parts) if parts else "Empty deck")
                lbl.setCursor(Qt.CursorShape.PointingHandCursor)
                lbl.setToolTip("Click to view deck contents")
        self._reapply_button_states()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_exposed_states(config: BotConfig):
    """Return handle_combat states that do NOT use the default strategy."""
    return [
        s
        for s in config.states
        if s.node_type == "handle_combat"
        and not s.params.get("use_default_strategy", s.params.get("use_default_duel_profile", True))
    ]


def _state_dcs_map(bcs: BotCombatStrategy) -> dict[str, str]:
    """Flatten BCS state_assignments to {state_id: dcs_id}."""
    result: dict[str, str] = {}
    for assignment in bcs.state_assignments:
        for sid in assignment.state_ids:
            result[sid] = assignment.duel_combat_strategy_id
    return result


def _build_state_assignments(state_dcs: dict[str, str]) -> list[StateAssignment]:
    """Convert {state_id: dcs_id} back to list[StateAssignment]."""
    dcs_to_states: dict[str, list[str]] = {}
    for state_id, dcs_id in state_dcs.items():
        dcs_to_states.setdefault(dcs_id, []).append(state_id)
    return [StateAssignment(duel_combat_strategy_id=dcs_id, state_ids=sids) for dcs_id, sids in dcs_to_states.items()]


def _format_dcs_meta(dcs: DuelCombatStrategy) -> str:
    """One-line summary of a DCS: player range + combat order."""
    parts = []
    if dcs.min_players == 1 and dcs.max_players == 4:
        parts.append("Any players")
    elif dcs.min_players == dcs.max_players:
        parts.append(f"{dcs.min_players} player{'s' if dcs.min_players != 1 else ''}")
    else:
        parts.append(f"{dcs.min_players}–{dcs.max_players} players")
    if dcs.combat_order:
        parts.append(" → ".join(f"P{p}" for p in dcs.combat_order))
    else:
        parts.append("Simultaneous")
    return " · ".join(parts)


def _format_bcs_meta(bcs: BotCombatStrategy) -> str:
    """One-line summary of a BCS: number of state overrides."""
    n = sum(len(a.state_ids) for a in bcs.state_assignments)
    return f"{n} state override{'s' if n != 1 else ''}" if n else "Default strategy only"


def _summarize_assignment_dsl(assignment, pcs_by_id: dict[str, str]) -> str:
    """Label for one assignment: the linked preset's name, or its inline DSL truncated."""
    if assignment.is_linked:
        return pcs_by_id.get(assignment.player_combat_strategy_id, "(unknown)")
    dsl = " ".join(assignment.dsl.split())
    if not dsl:
        return "(default)"
    return dsl if len(dsl) <= _DSL_PREVIEW_CHARS else dsl[: _DSL_PREVIEW_CHARS - 1] + "…"


def _format_dcs_assignments(dcs: DuelCombatStrategy, all_pcs: list[PlayerCombatStrategy]) -> str:
    """One-line summary of player assignments within a DCS."""
    if not dcs.player_assignments:
        return "No player assignments"
    pcs_by_id = {p.id: p.name for p in all_pcs}
    parts = []
    for assignment in dcs.player_assignments:
        players = sorted(assignment.players)
        if not players:
            continue
        name = _summarize_assignment_dsl(assignment, pcs_by_id)
        if len(players) == 1:
            player_str = f"P{players[0]}"
        elif players == list(range(players[0], players[-1] + 1)):
            player_str = f"P{players[0]}–P{players[-1]}"
        else:
            player_str = ", ".join(f"P{p}" for p in players)
        parts.append(f"{player_str}: {name}")
    return " · ".join(parts)


def _unique_dcs_name(base: str, existing: list[DuelCombatStrategy]) -> str:
    """Return base if unused, otherwise base (2), (3), etc."""
    names = {d.name for d in existing}
    if base not in names:
        return base
    i = 2
    while f"{base} ({i})" in names:
        i += 1
    return f"{base} ({i})"


def _make_icon_btn(icon_name: str, tooltip: str) -> QToolButton:
    """Small flat icon-only QToolButton."""
    btn = QToolButton()
    btn.setIcon(svg_icon(icon_name, _ICON_BTN_SIZE))
    btn.setIconSize(QSize(_ICON_BTN_SIZE, _ICON_BTN_SIZE))
    btn.setToolTip(tooltip)
    btn.setAutoRaise(True)
    return btn


def _make_meta_label(text: str = "") -> QLabel:
    lbl = QLabel(text)
    font = lbl.font()
    font.setPointSize(max(font.pointSize() - 1, 7))
    lbl.setFont(font)
    lbl.setStyleSheet(f"color: {THEME.text_muted};")
    lbl.setContentsMargins(2, 0, 0, 2)
    return lbl


# ---------------------------------------------------------------------------
# Combat Tab
# ---------------------------------------------------------------------------


class CombatTab(QWidget):
    """Left-sidebar tab for combat configuration using the three-level strategy system."""

    combat_config_changed = pyqtSignal()
    import_deck_requested = pyqtSignal(int)  # 1-based slot number
    apply_deck_requested = pyqtSignal(int)  # 1-based slot number

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: BotConfig | None = None
        self._bcs: BotCombatStrategy | None = None
        self._all_bcs: list[BotCombatStrategy] = []
        self._all_dcs: list[DuelCombatStrategy] = []
        self._all_pcs: list[PlayerCombatStrategy] = []
        # state_id → dcs_id (working copy, flushed to BCS before save)
        self._state_dcs: dict[str, str] = {}
        self._suppress = False
        self._closed = False
        self._editable = True
        self._library_window: StrategyLibraryWindow | None = None

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._save_bcs)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(8, 8, 8, 8)
        self._content_layout.setSpacing(4)

        # ── Bot Combat Strategy section ───────────────────────────────
        bcs_section = SidebarSection("Bot Combat Strategy", self)
        bcs_layout = QVBoxLayout()
        bcs_layout.setSpacing(2)

        bcs_row = QHBoxLayout()
        self._bcs_chip = _StrategyChip(_NO_STRATEGY_LABEL)
        self._bcs_chip.setToolTip("Click to pick a Bot Combat Strategy for this bot")
        self._bcs_chip.clicked.connect(self._on_pick_bcs)
        bcs_row.addWidget(self._bcs_chip, 1)

        self._bcs_menu_btn = QToolButton()
        self._bcs_menu_btn.setIcon(svg_icon("more_vert", _ICON_BTN_SIZE))
        self._bcs_menu_btn.setIconSize(QSize(_ICON_BTN_SIZE, _ICON_BTN_SIZE))
        self._bcs_menu_btn.setToolTip("Manage Bot Combat Strategies (New, Rename, Duplicate, Delete)")
        self._bcs_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._bcs_menu_btn.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        bcs_menu = QMenu(self._bcs_menu_btn)
        bcs_menu.addAction("New", self._on_new_bcs)
        bcs_menu.addAction("Rename", self._on_rename_bcs)
        bcs_menu.addAction("Duplicate", self._on_duplicate_bcs)
        bcs_menu.addSeparator()
        bcs_menu.addAction("Delete", self._on_delete_bcs)
        self._bcs_menu_btn.setMenu(bcs_menu)
        bcs_row.addWidget(self._bcs_menu_btn)

        bcs_layout.addLayout(bcs_row)
        self._bcs_meta_lbl = _make_meta_label()
        self._bcs_meta_lbl.hide()
        bcs_layout.addWidget(self._bcs_meta_lbl)
        bcs_section.set_content_layout(bcs_layout)
        self._content_layout.addWidget(bcs_section)

        # ── Default Combat Strategy section ───────────────────────────
        default_section = SidebarSection("Default Combat Strategy", self)
        default_layout = QVBoxLayout()
        default_layout.setSpacing(2)

        default_row = QHBoxLayout()
        self._default_chip = _StrategyChip(_BUILT_IN_LABEL)
        self._default_chip.setToolTip("Click to set the default Duel Strategy for unassigned states")
        self._default_chip.clicked.connect(self._on_pick_default_dcs)
        default_row.addWidget(self._default_chip, 1)

        self._default_edit_btn = _make_icon_btn("pencil", "Open this Duel Strategy in the Strategy Library")
        self._default_edit_btn.clicked.connect(self._on_edit_default_dcs)
        default_row.addWidget(self._default_edit_btn)

        self._default_new_btn = _make_icon_btn("plus", "Create a new Duel Strategy and open it in the Strategy Library")
        self._default_new_btn.clicked.connect(self._on_new_default_dcs_quick)
        default_row.addWidget(self._default_new_btn)

        default_layout.addLayout(default_row)
        self._default_meta_lbl = _make_meta_label()
        self._default_meta_lbl.hide()
        default_layout.addWidget(self._default_meta_lbl)
        self._default_assignments_lbl = _make_meta_label()
        self._default_assignments_lbl.hide()
        default_layout.addWidget(self._default_assignments_lbl)
        default_section.set_content_layout(default_layout)
        self._content_layout.addWidget(default_section)

        # ── Duel Combat Strategies section ───────────────────────────
        self._assignments_section = SidebarSection("Duel Combat Strategies", self)
        self._assignments_layout = QVBoxLayout()
        self._assignments_layout.setSpacing(4)
        self._no_assignments_lbl = QLabel("No exposed combat states")
        self._no_assignments_lbl.setStyleSheet(f"color: {THEME.text_muted};")
        self._assignments_layout.addWidget(self._no_assignments_lbl)
        self._assignments_section.set_content_layout(self._assignments_layout)
        self._content_layout.addWidget(self._assignments_section)

        # ── Player Decks ─────────────────────────────────────────────
        self._decks_section = _PlayerDecksSection(self)
        self._decks_section.import_requested.connect(self.import_deck_requested)
        self._decks_section.apply_requested.connect(self.apply_deck_requested)
        self._decks_section.view_requested.connect(self._on_view_deck)
        self._decks_section.save_to_library_requested.connect(self._on_save_deck_to_library)
        self._decks_section.load_from_library_requested.connect(self._on_load_deck_from_library)
        self._decks_section.clear_requested.connect(self._on_clear_deck)
        self._content_layout.addWidget(self._decks_section)

        self._content_layout.addStretch()

        scroll_area.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll_area)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._suppress = True
        self._load_bcs()
        self._rebuild_state_assignments()
        self._refresh_deck_summaries()
        self._refresh_bcs_chip()
        self._refresh_default_chip()
        self._suppress = False

    def refresh_deck_summaries(self) -> None:
        self._refresh_deck_summaries()

    def update_deck_slot(self, slot: int, deck: DeckData) -> None:
        """Update the in-memory BCS deck for one slot and refresh the display."""
        if self._bcs is not None:
            self._bcs.player_decks[slot] = deck
        self._refresh_deck_summaries()

    def refresh_duel_profiles(self) -> None:
        """Compatibility alias — refreshes state assignments when combat tab becomes visible."""
        self._rebuild_state_assignments()

    def refresh_strategy_data(self) -> None:
        """Reload DCS/BCS/PCS data from disk. Called when StrategyLibraryWindow signals changes."""
        self._all_dcs = list_duel_strategies()
        self._all_pcs = list_player_strategies()
        if self._config is not None:
            self._all_bcs = list_bot_strategies(self._config.id)
        self._refresh_bcs_chip()
        self._refresh_default_chip()
        self._rebuild_state_assignments()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._bcs_chip.setEnabled(editable)
        self._bcs_menu_btn.setEnabled(editable)
        self._default_chip.setEnabled(editable)
        self._default_edit_btn.setEnabled(editable)
        self._default_new_btn.setEnabled(editable)
        self._decks_section.set_editable(editable)
        self._rebuild_state_assignments()

    def set_client_count(self, count: int) -> None:
        self._decks_section.set_client_count(count)

    # ------------------------------------------------------------------
    # BCS loading / saving
    # ------------------------------------------------------------------

    def _load_bcs(self) -> None:
        """Load BCS from disk based on active strategy setting."""
        self._bcs = None
        self._state_dcs = {}
        self._all_bcs = []
        self._all_dcs = list_duel_strategies()
        self._all_pcs = list_player_strategies()
        if self._config is None:
            return
        self._all_bcs = list_bot_strategies(self._config.id)
        active_id = SettingsManager.instance().get_active_strategy(self._config.id)
        if active_id:
            self._bcs = next((b for b in self._all_bcs if b.id == active_id), None)
        if self._bcs is None and self._all_bcs:
            # Fall back to first available BCS
            self._bcs = self._all_bcs[0]
            if active_id != self._bcs.id:
                SettingsManager.instance().set_active_strategy(self._config.id, self._bcs.id)
        if self._bcs is not None:
            self._state_dcs = _state_dcs_map(self._bcs)

    def _schedule_save(self) -> None:
        if not self._suppress:
            self._save_timer.start()

    def _save_bcs(self) -> None:
        if self._bcs is None:
            return
        # Write _state_dcs back into BCS before saving
        self._bcs.state_assignments = _build_state_assignments(self._state_dcs)
        try:
            save_bot_strategy(self._bcs)
        except Exception:
            logger.exception("Failed to save BotCombatStrategy %s", self._bcs.id)
        self.combat_config_changed.emit()

    def _save_bcs_immediate(self) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
        self._save_bcs()

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------

    def _refresh_bcs_chip(self) -> None:
        if self._bcs is None:
            self._bcs_chip.set_strategy_name(_NO_STRATEGY_LABEL)
            self._bcs_meta_lbl.hide()
        else:
            self._bcs_chip.set_strategy_name(self._bcs.name)
            self._bcs_meta_lbl.setText(_format_bcs_meta(self._bcs))
            self._bcs_meta_lbl.show()

    def _refresh_default_chip(self) -> None:
        if self._bcs is None or self._bcs.default_strategy_id is None:
            self._default_chip.set_strategy_name(_BUILT_IN_LABEL)
            self._default_meta_lbl.hide()
            self._default_edit_btn.setEnabled(False)
            return
        dcs = next((d for d in self._all_dcs if d.id == self._bcs.default_strategy_id), None)
        self._default_edit_btn.setEnabled(self._editable)
        if dcs:
            self._default_chip.set_strategy_name(dcs.name)
            self._default_meta_lbl.setText(_format_dcs_meta(dcs))
            self._default_meta_lbl.show()
            assignments_text = _format_dcs_assignments(dcs, self._all_pcs)
            self._default_assignments_lbl.setText(assignments_text)
            self._default_assignments_lbl.show()
        else:
            self._default_chip.set_strategy_name("(missing strategy)")
            self._default_meta_lbl.hide()
            self._default_assignments_lbl.hide()

    def _refresh_deck_summaries(self) -> None:
        decks = self._bcs.player_decks if self._bcs else {}
        self._decks_section.refresh(decks)

    def _on_view_deck(self, slot: int) -> None:
        from bartlebot.gui.deck_view_dialog import DeckViewDialog  # noqa: PLC0415

        if self._bcs is None:
            return
        deck = self._bcs.player_decks.get(slot)
        if deck is None or deck.is_empty():
            return
        dlg = DeckViewDialog(slot, deck, parent=self)
        dlg.exec()

    def _on_save_deck_to_library(self, slot: int) -> None:
        from bartlebot.bot.strategy_models import DeckPreset  # noqa: PLC0415
        from bartlebot.strategy_store import save_deck_preset  # noqa: PLC0415

        if self._bcs is None:
            return
        deck = self._bcs.player_decks.get(slot)
        if deck is None or deck.is_empty():
            QMessageBox.information(self, "No deck", f"No deck is saved for P{slot}.")
            return
        name, ok = QInputDialog.getText(self, "Save to Deck Library", "Preset name:")
        if not ok or not name.strip():
            return
        preset = DeckPreset(name=name.strip(), deck=deck.model_copy(deep=True))
        save_deck_preset(preset)
        if self._library_window is not None:
            self._library_window.reload_decks()
            self._library_window.focus_deck_preset(preset.id)

    def _on_load_deck_from_library(self, slot: int) -> None:
        from bartlebot.gui.strategy_library.deck_preset_picker_dialog import DeckPresetPickerDialog  # noqa: PLC0415
        from bartlebot.strategy_store import list_deck_presets  # noqa: PLC0415

        presets = list_deck_presets()
        if not presets:
            QMessageBox.information(
                self, "Deck Library empty", "No deck presets saved yet. Use Import and Save to Library first."
            )
            return
        dlg = DeckPresetPickerDialog(presets, parent=self)
        if not dlg.exec():
            return
        chosen_id = dlg.selected_id()
        if chosen_id is None:
            return
        chosen = next((p for p in presets if p.id == chosen_id), None)
        if chosen is None:
            return
        if self._bcs is None:
            return
        self._bcs.player_decks[slot] = chosen.deck.model_copy(deep=True)
        save_bot_strategy(self._bcs)
        self._refresh_deck_summaries()

    def _on_clear_deck(self, slot: int) -> None:
        if self._bcs is None:
            return
        deck = self._bcs.player_decks.get(slot)
        if deck is None or deck.is_empty():
            return
        ans = QMessageBox.question(
            self,
            "Clear Deck",
            f"Clear the saved deck for P{slot}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._bcs.player_decks.pop(slot, None)
        save_bot_strategy(self._bcs)
        self._refresh_deck_summaries()

    def _rebuild_state_assignments(self) -> None:
        # Remove all dynamic rows (everything except no_assignments_lbl)
        while self._assignments_layout.count() > 1:
            item = self._assignments_layout.takeAt(1)
            if item and item.widget():
                item.widget().deleteLater()

        if self._config is None:
            self._no_assignments_lbl.show()
            return

        exposed = _collect_exposed_states(self._config)
        if not exposed:
            self._no_assignments_lbl.show()
            return

        self._no_assignments_lbl.hide()
        for state in exposed:
            row = self._build_assignment_row(state)
            self._assignments_layout.addWidget(row)

    def _build_assignment_row(self, state) -> QWidget:
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(1)

        # Top row: state label | chip | edit | new
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)

        display = state.label or state.id
        lbl = QLabel(display)
        lbl.setToolTip(f"State ID: {state.id}")
        row_layout.addWidget(lbl, 1)

        dcs_id = self._state_dcs.get(state.id)
        dcs = next((d for d in self._all_dcs if d.id == dcs_id), None) if dcs_id else None
        dcs_name = dcs.name if dcs else (_BUILT_IN_LABEL if not dcs_id else "(missing)")

        chip = _StrategyChip(dcs_name)
        chip.setMaximumWidth(160)
        chip.setEnabled(self._editable)
        chip.clicked.connect(lambda _checked=False, sid=state.id: self._on_pick_state_dcs(sid))
        row_layout.addWidget(chip)

        edit_btn = _make_icon_btn("pencil", "Open this Duel Strategy in the Strategy Library")
        edit_btn.setEnabled(self._editable and dcs is not None)
        edit_btn.clicked.connect(lambda _checked=False, sid=state.id: self._on_edit_state_dcs(sid))
        row_layout.addWidget(edit_btn)

        new_btn = _make_icon_btn("plus", "Create a new Duel Strategy and assign it to this state")
        new_btn.setEnabled(self._editable)
        new_btn.clicked.connect(lambda _checked=False, sid=state.id: self._on_new_state_dcs_quick(sid))
        row_layout.addWidget(new_btn)

        outer_layout.addWidget(row)

        # Meta lines (player range + order, then PCS assignments)
        if dcs is not None:
            outer_layout.addWidget(_make_meta_label(_format_dcs_meta(dcs)))
            outer_layout.addWidget(_make_meta_label(_format_dcs_assignments(dcs, self._all_pcs)))

        return outer

    # ------------------------------------------------------------------
    # BCS picker / menu actions
    # ------------------------------------------------------------------

    def _on_pick_bcs(self) -> None:
        if self._config is None:
            return
        dlg = StrategyPickerDialog(
            title="Pick Bot Combat Strategy",
            mode="bot",
            strategies=self._all_bcs,
            current_id=self._bcs.id if self._bcs else None,
            allow_none=False,
            parent=self,
        )
        if dlg.exec():
            chosen = dlg.selected_id()
            if chosen:
                self._bcs = next((b for b in self._all_bcs if b.id == chosen), None)
                self._state_dcs = _state_dcs_map(self._bcs) if self._bcs else {}
                SettingsManager.instance().set_active_strategy(self._config.id, chosen)
                self._rebuild_state_assignments()
                self._refresh_bcs_chip()
                self._refresh_default_chip()
                self._refresh_deck_summaries()

    def _on_new_bcs(self) -> None:
        if self._config is None:
            return
        name, ok = QInputDialog.getText(self, "New Bot Strategy", "Name:")
        if not ok or not name.strip():
            return
        bcs = BotCombatStrategy(name=name.strip(), bot_id=self._config.id)
        save_bot_strategy(bcs)
        self._all_bcs.append(bcs)
        self._bcs = bcs
        self._state_dcs = {}
        SettingsManager.instance().set_active_strategy(self._config.id, bcs.id)
        self._rebuild_state_assignments()
        self._refresh_bcs_chip()
        self._refresh_default_chip()
        self._refresh_deck_summaries()

    def _on_rename_bcs(self) -> None:
        if self._bcs is None:
            return
        name, ok = QInputDialog.getText(self, "Rename Strategy", "Name:", text=self._bcs.name)
        if not ok or not name.strip():
            return
        self._bcs.name = name.strip()
        self._save_bcs_immediate()
        self._refresh_bcs_chip()

    def _on_duplicate_bcs(self) -> None:
        if self._bcs is None or self._config is None:
            return
        name, ok = QInputDialog.getText(self, "Duplicate Strategy", "New name:", text=f"{self._bcs.name} copy")
        if not ok or not name.strip():
            return
        new_bcs = self._bcs.model_copy(deep=True, update={"id": str(uuid4()), "name": name.strip()})
        save_bot_strategy(new_bcs)
        self._all_bcs.append(new_bcs)
        self._bcs = new_bcs
        self._state_dcs = _state_dcs_map(new_bcs)
        SettingsManager.instance().set_active_strategy(self._config.id, new_bcs.id)
        self._refresh_bcs_chip()
        self._refresh_default_chip()

    def _on_delete_bcs(self) -> None:
        if self._bcs is None or self._config is None:
            return
        ans = QMessageBox.question(
            self,
            "Delete Bot Strategy",
            f"Delete '{self._bcs.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        to_delete = self._bcs
        self._all_bcs = [b for b in self._all_bcs if b.id != to_delete.id]
        delete_bot_strategy(to_delete.id)
        self._bcs = self._all_bcs[0] if self._all_bcs else None
        self._state_dcs = _state_dcs_map(self._bcs) if self._bcs else {}
        new_active = self._bcs.id if self._bcs else None
        SettingsManager.instance().set_active_strategy(self._config.id, new_active)
        self._rebuild_state_assignments()
        self._refresh_bcs_chip()
        self._refresh_default_chip()
        self._refresh_deck_summaries()

    def _on_pick_default_dcs(self) -> None:
        if self._bcs is None:
            return
        from bartlebot.gui.strategy_library.strategy_library_window import (  # noqa: PLC0415
            StrategyLibraryWindow as _Lib,
        )

        dlg = _Lib(
            mode="pick",
            current_id=self._bcs.default_strategy_id,
            allow_none=True,
            none_label=_BUILT_IN_LABEL,
            parent=self,
        )
        dlg.strategies_changed.connect(self.refresh_strategy_data)
        if dlg.exec():
            self._bcs.default_strategy_id = dlg.selected_id()
            self._schedule_save()
        self.refresh_strategy_data()

    def _on_pick_state_dcs(self, state_id: str) -> None:
        if self._bcs is None:
            return
        from bartlebot.gui.strategy_library.strategy_library_window import (  # noqa: PLC0415
            StrategyLibraryWindow as _Lib,
        )

        dlg = _Lib(
            mode="pick",
            current_id=self._state_dcs.get(state_id),
            allow_none=True,
            none_label=_BUILT_IN_LABEL,
            parent=self,
        )
        dlg.strategies_changed.connect(self.refresh_strategy_data)
        if dlg.exec():
            chosen = dlg.selected_id()
            if chosen is None:
                self._state_dcs.pop(state_id, None)
            else:
                self._state_dcs[state_id] = chosen
            self._schedule_save()
        self.refresh_strategy_data()

    # ------------------------------------------------------------------
    # Strategy Library access
    # ------------------------------------------------------------------

    def _ensure_library(self) -> StrategyLibraryWindow:
        from bartlebot.gui.strategy_library.strategy_library_window import (
            StrategyLibraryWindow as _Lib,  # noqa: PLC0415
        )

        if self._library_window is None or not self._library_window.isVisible():
            self._library_window = _Lib()
            self._library_window.strategies_changed.connect(self.refresh_strategy_data)
        self._library_window.show()
        self._library_window.raise_()
        self._library_window.activateWindow()
        return self._library_window

    def _on_edit_default_dcs(self) -> None:
        """Open the Strategy Library focused on the current default DCS."""
        lib = self._ensure_library()
        if self._bcs and self._bcs.default_strategy_id:
            lib.focus_duel_strategy(self._bcs.default_strategy_id)

    def _on_new_default_dcs_quick(self) -> None:
        """Create a new DCS with a default name, assign it as default, open it in the library."""
        if self._bcs is None:
            return
        dcs = DuelCombatStrategy(name=_unique_dcs_name("New Duel Strategy", self._all_dcs))
        save_duel_strategy(dcs)
        self._all_dcs.append(dcs)
        self._bcs.default_strategy_id = dcs.id
        self._refresh_default_chip()
        self._schedule_save()
        lib = self._ensure_library()
        lib.refresh()
        lib.focus_duel_strategy(dcs.id)

    def _on_edit_state_dcs(self, state_id: str) -> None:
        """Open the Strategy Library focused on the DCS assigned to this state."""
        lib = self._ensure_library()
        dcs_id = self._state_dcs.get(state_id)
        if dcs_id:
            lib.focus_duel_strategy(dcs_id)

    def _on_new_state_dcs_quick(self, state_id: str) -> None:
        """Create a new DCS, assign it to this state, and open it in the library."""
        if self._bcs is None:
            return
        dcs = DuelCombatStrategy(name=_unique_dcs_name("New Duel Strategy", self._all_dcs))
        save_duel_strategy(dcs)
        self._all_dcs.append(dcs)
        self._state_dcs[state_id] = dcs.id
        self._rebuild_state_assignments()
        self._schedule_save()
        lib = self._ensure_library()
        lib.refresh()
        lib.focus_duel_strategy(dcs.id)

    # ------------------------------------------------------------------
    # Theme / lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._closed = True
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_bcs()
        super().closeEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._bcs_menu_btn.setIcon(svg_icon("more_vert", _ICON_BTN_SIZE))
            self._default_edit_btn.setIcon(svg_icon("pencil", _ICON_BTN_SIZE))
            self._default_new_btn.setIcon(svg_icon("plus", _ICON_BTN_SIZE))
            self._bcs_meta_lbl.setStyleSheet(f"color: {THEME.text_muted};")
            self._default_meta_lbl.setStyleSheet(f"color: {THEME.text_muted};")
            self._default_assignments_lbl.setStyleSheet(f"color: {THEME.text_muted};")
            self._no_assignments_lbl.setStyleSheet(f"color: {THEME.text_muted};")
            self._rebuild_state_assignments()
