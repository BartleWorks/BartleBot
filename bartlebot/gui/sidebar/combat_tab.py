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
    DuelConfig,
    PlayerCombatStrategy,
    StateAssignment,
)
from bartlebot.combat_warnings import exposed_combat_states
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.sidebar.sidebar_section import SidebarSection
from bartlebot.gui.strategy_library.duel_strategy_editor import DuelConfigEditor
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
_NOT_CONFIGURED_LABEL = "Not configured"
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
                # Treasure cards are never captured or applied, so the summary
                # reports only what a deck apply will actually restore.
                parts = []
                if deck.normal:
                    parts.append(f"{sum(deck.normal.values())} normal")
                if deck.item:
                    parts.append(f"{sum(deck.item.values())} item")
                lbl.setText(" · ".join(parts) if parts else "Empty deck")
                lbl.setCursor(Qt.CursorShape.PointingHandCursor)
                lbl.setToolTip("Click to view deck contents")
        self._reapply_button_states()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_dcs_meta(config: DuelConfig) -> str:
    """One-line summary of a duel config: player range + combat order.

    Takes ``DuelConfig`` so the same helper covers both a linked library
    DuelCombatStrategy (which IS-A DuelConfig) and an inline one.
    """
    parts = []
    if config.min_players == 1 and config.max_players == 4:
        parts.append("Any players")
    elif config.min_players == config.max_players:
        parts.append(f"{config.min_players} player{'s' if config.min_players != 1 else ''}")
    else:
        parts.append(f"{config.min_players}–{config.max_players} players")
    if config.combat_order:
        parts.append(" → ".join(f"P{p}" for p in config.combat_order))
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


def _format_dcs_assignments(config: DuelConfig, all_pcs: list[PlayerCombatStrategy]) -> str:
    """One-line summary of player assignments within a duel config (linked or inline)."""
    if not config.player_assignments:
        return "No player assignments"
    pcs_by_id = {p.id: p.name for p in all_pcs}
    parts = []
    for assignment in config.player_assignments:
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


class _DefaultBinding:
    """Adapts BotCombatStrategy's ``default_strategy_id`` / ``default_duel_config``
    fields to the same shape as ``StateAssignment`` — ``duel_config``,
    ``duel_combat_strategy_id`` (settable), ``is_linked`` — so ``_DuelBindingPanel``
    can edit either through one interface.
    """

    def __init__(self, bcs: BotCombatStrategy):
        self._bcs = bcs

    @property
    def duel_config(self) -> DuelConfig:
        return self._bcs.default_duel_config

    @duel_config.setter
    def duel_config(self, value: DuelConfig) -> None:
        self._bcs.default_duel_config = value

    @property
    def duel_combat_strategy_id(self) -> str | None:
        return self._bcs.default_strategy_id

    @duel_combat_strategy_id.setter
    def duel_combat_strategy_id(self, value: str | None) -> None:
        self._bcs.default_strategy_id = value

    @property
    def is_linked(self) -> bool:
        return self._bcs.default_strategy_id is not None


# ---------------------------------------------------------------------------
# Duel binding panel — one state override, or the BCS default
# ---------------------------------------------------------------------------


class _DuelBindingPanel(QWidget):
    """One duel-config binding: inline (expand to edit directly) or linked to a
    library DuelCombatStrategy (compact summary + edit-in-library).

    Used for both the Default Combat Strategy and every per-state override —
    same actions, same layout, in both places.  Operates on any ``binding``
    object shaped like ``StateAssignment``: ``duel_config``,
    ``duel_combat_strategy_id`` (settable), ``is_linked``.
    """

    changed = pyqtSignal()
    presets_changed = pyqtSignal()  # a new library DCS or PCS was created
    edit_in_library_requested = pyqtSignal(str)  # dcs_id

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._binding = None
        self._all_dcs: list[DuelCombatStrategy] = []
        self._all_pcs: list[PlayerCombatStrategy] = []
        self._expanded = False
        self._editable = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(1)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)

        self._toggle_btn = _make_icon_btn("arrow_right", "Expand to edit inline")
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.clicked.connect(self._on_toggle)
        header.addWidget(self._toggle_btn)

        self._title_lbl = QLabel(title)
        header.addWidget(self._title_lbl)

        self._chip = _StrategyChip(_BUILT_IN_LABEL)
        self._chip.setMaximumWidth(160)
        self._chip.setToolTip("Click to link a Duel Strategy from the library")
        self._chip.clicked.connect(self._on_pick_link)
        header.addWidget(self._chip, 1)

        self._menu_btn = QToolButton()
        self._menu_btn.setIcon(svg_icon("more_vert", _ICON_BTN_SIZE))
        self._menu_btn.setIconSize(QSize(_ICON_BTN_SIZE, _ICON_BTN_SIZE))
        self._menu_btn.setToolTip("Strategy actions")
        self._menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu_btn.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        self._menu = QMenu(self._menu_btn)
        self._menu.aboutToShow.connect(self._rebuild_menu)
        self._menu_btn.setMenu(self._menu)
        header.addWidget(self._menu_btn)

        outer.addLayout(header)

        self._meta_lbl = _make_meta_label()
        outer.addWidget(self._meta_lbl)
        self._assignments_lbl = _make_meta_label()
        outer.addWidget(self._assignments_lbl)

        self._config_editor = DuelConfigEditor()
        self._config_editor.changed.connect(self.changed)
        self._config_editor.presets_changed.connect(self.presets_changed)
        outer.addWidget(self._config_editor)
        self._config_editor.hide()

    # -- data -----------------------------------------------------------

    def load(self, binding, all_dcs: list[DuelCombatStrategy], all_pcs: list[PlayerCombatStrategy]) -> None:
        self._binding = binding
        self._all_dcs = all_dcs
        self._all_pcs = all_pcs
        self._expanded = False
        self._refresh()

    def set_libraries(self, all_dcs: list[DuelCombatStrategy], all_pcs: list[PlayerCombatStrategy]) -> None:
        """Update the reference lists without disturbing expand state or in-progress edits."""
        self._all_dcs = all_dcs
        self._all_pcs = all_pcs
        self._refresh()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._chip.setEnabled(editable)
        self._menu_btn.setEnabled(editable)
        self._toggle_btn.setEnabled(editable and self._binding is not None and not self._binding.is_linked)

    def _linked_dcs(self):
        if self._binding is None or not self._binding.is_linked:
            return None
        return next((d for d in self._all_dcs if d.id == self._binding.duel_combat_strategy_id), None)

    def _refresh(self) -> None:
        if self._binding is None:
            self.setVisible(False)
            return
        self.setVisible(True)

        if self._binding.is_linked:
            dcs = self._linked_dcs()
            self._toggle_btn.setEnabled(False)
            self._toggle_btn.setChecked(False)
            self._config_editor.hide()
            if dcs is not None:
                self._chip.set_strategy_name(dcs.name)
                self._meta_lbl.setText(_format_dcs_meta(dcs))
                self._meta_lbl.show()
                self._assignments_lbl.setText(_format_dcs_assignments(dcs, self._all_pcs))
                self._assignments_lbl.show()
            else:
                self._chip.set_strategy_name("(missing strategy)")
                self._meta_lbl.hide()
                self._assignments_lbl.hide()
            return

        # Inline
        self._toggle_btn.setEnabled(self._editable)
        self._toggle_btn.setChecked(self._expanded)
        self._toggle_btn.setIcon(svg_icon("arrow_down" if self._expanded else "arrow_right", _ICON_BTN_SIZE))
        config = self._binding.duel_config
        empty = config.is_empty()
        self._chip.set_strategy_name(_BUILT_IN_LABEL if empty else "(custom)")
        if self._expanded:
            self._meta_lbl.hide()
            self._assignments_lbl.hide()
            self._config_editor.load(config, self._all_pcs)
            self._config_editor.show()
        else:
            self._config_editor.hide()
            if empty:
                self._meta_lbl.setText(_NOT_CONFIGURED_LABEL)
                self._meta_lbl.show()
                self._assignments_lbl.hide()
            else:
                self._meta_lbl.setText(_format_dcs_meta(config))
                self._meta_lbl.show()
                self._assignments_lbl.setText(_format_dcs_assignments(config, self._all_pcs))
                self._assignments_lbl.show()

    # -- actions ----------------------------------------------------------

    def _on_toggle(self) -> None:
        self._expanded = self._toggle_btn.isChecked()
        self._refresh()

    def _on_pick_link(self) -> None:
        if self._binding is None:
            return
        dlg = StrategyPickerDialog(
            title="Pick Duel Strategy",
            mode="duel",
            strategies=self._all_dcs,
            current_id=self._binding.duel_combat_strategy_id,
            allow_none=True,
            none_label=_BUILT_IN_LABEL,
            parent=self,
        )
        if dlg.exec():
            self._binding.duel_combat_strategy_id = dlg.selected_id()
            self._expanded = False
            self._refresh()
            self.changed.emit()

    def _rebuild_menu(self) -> None:
        self._menu.clear()
        if self._binding is None:
            return
        if self._binding.is_linked:
            self._menu.addAction("Unlink (edit a private copy)", self._on_unlink)
            self._menu.addAction("Edit in library…", self._on_edit_in_library)
        else:
            action = self._menu.addAction("Save as preset…", self._on_promote)
            action.setEnabled(not self._binding.duel_config.is_empty())

    def _on_unlink(self) -> None:
        """Fork the linked strategy's fields back into this binding as an inline copy."""
        dcs = self._linked_dcs()
        if dcs is not None:
            self._binding.duel_config = DuelConfig(
                min_players=dcs.min_players,
                max_players=dcs.max_players,
                combat_order=dcs.combat_order,
                player_assignments=[a.model_copy(deep=True) for a in dcs.player_assignments],
            )
        self._binding.duel_combat_strategy_id = None
        self._refresh()
        self.changed.emit()

    def _on_edit_in_library(self) -> None:
        if self._binding is not None and self._binding.duel_combat_strategy_id:
            self.edit_in_library_requested.emit(self._binding.duel_combat_strategy_id)

    def _on_promote(self) -> None:
        """Copy this binding's inline config into a new library entry and link to it."""
        config = self._binding.duel_config
        if config.is_empty():
            QMessageBox.information(self, "Nothing to save", "Add a player assignment before saving it as a preset.")
            return
        name, ok = QInputDialog.getText(self, "Save as Preset", "Duel Strategy name:")
        if not ok or not name.strip():
            return
        dcs = DuelCombatStrategy(
            name=name.strip(),
            min_players=config.min_players,
            max_players=config.max_players,
            combat_order=config.combat_order,
            player_assignments=[a.model_copy(deep=True) for a in config.player_assignments],
        )
        save_duel_strategy(dcs)
        self._all_dcs.append(dcs)
        self._binding.duel_combat_strategy_id = dcs.id
        self._expanded = False
        self._refresh()
        self.changed.emit()
        self.presets_changed.emit()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._toggle_btn.setIcon(svg_icon("arrow_down" if self._expanded else "arrow_right", _ICON_BTN_SIZE))
            self._menu_btn.setIcon(svg_icon("more_vert", _ICON_BTN_SIZE))
            self._meta_lbl.setStyleSheet(f"color: {THEME.text_muted};")
            self._assignments_lbl.setStyleSheet(f"color: {THEME.text_muted};")


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
        # state_id → its StateAssignment (owned in-memory; flushed to BCS on save,
        # dropped if it never ends up linked or holding real inline content)
        self._state_bindings: dict[str, StateAssignment] = {}
        self._state_panels: dict[str, _DuelBindingPanel] = {}
        self._suppress = False
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
        self._default_panel = _DuelBindingPanel("Default")
        self._default_panel.changed.connect(self._schedule_save)
        self._default_panel.presets_changed.connect(self._on_presets_changed)
        self._default_panel.edit_in_library_requested.connect(self._on_edit_dcs_in_library)
        self._default_panel.setVisible(False)
        default_layout.addWidget(self._default_panel)
        default_section.set_content_layout(default_layout)
        self._content_layout.addWidget(default_section)

        # ── Duel Combat Strategies section ───────────────────────────
        self._assignments_section = SidebarSection("Duel Combat Strategies", self)
        self._assignments_layout = QVBoxLayout()
        self._assignments_layout.setSpacing(6)
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
        # Commit the outgoing bot's pending BCS edit before _bcs is replaced —
        # otherwise the timer fires against the newly loaded bot's strategy.
        self.flush_pending_save()
        self._config = config
        self._suppress = True
        self._load_bcs()
        self._rebuild_state_assignments()
        self._refresh_deck_summaries()
        self._refresh_bcs_chip()
        self._refresh_default_panel()
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
        """Reload DCS/BCS/PCS data from disk. Called when StrategyLibraryWindow signals changes.

        Pushes the refreshed lists into the existing panel widgets rather than
        rebuilding them, so an in-progress inline edit elsewhere on the tab is
        not interrupted.
        """
        self._all_dcs = list_duel_strategies()
        self._all_pcs = list_player_strategies()
        if self._config is not None:
            self._all_bcs = list_bot_strategies(self._config.id)
        self._refresh_bcs_chip()
        self._default_panel.set_libraries(self._all_dcs, self._all_pcs)
        for panel in self._state_panels.values():
            panel.set_libraries(self._all_dcs, self._all_pcs)

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._bcs_chip.setEnabled(editable)
        self._bcs_menu_btn.setEnabled(editable)
        self._decks_section.set_editable(editable)
        self._default_panel.set_editable(editable)
        for panel in self._state_panels.values():
            panel.set_editable(editable)

    def set_client_count(self, count: int) -> None:
        self._decks_section.set_client_count(count)

    # ------------------------------------------------------------------
    # BCS loading / saving
    # ------------------------------------------------------------------

    def _load_bcs(self) -> None:
        """Load BCS from disk based on active strategy setting."""
        self._bcs = None
        self._state_bindings = {}
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
            for assignment in self._bcs.state_assignments:
                for sid in assignment.state_ids:
                    self._state_bindings[sid] = assignment

    def _ensure_state_binding(self, state_id: str) -> StateAssignment:
        """Return this state's binding, creating an empty in-memory one if absent.

        Never persisted until it actually links a strategy or holds a non-empty
        inline config — see ``_save_bcs``.
        """
        existing = self._state_bindings.get(state_id)
        if existing is not None:
            return existing
        fresh = StateAssignment(state_ids=[state_id])
        self._state_bindings[state_id] = fresh
        return fresh

    def _schedule_save(self) -> None:
        if not self._suppress:
            self._save_timer.start()

    def _save_bcs(self) -> None:
        if self._bcs is None:
            return
        # Only persist bindings that actually say something — an untouched
        # placeholder (unlinked, empty) is indistinguishable from no binding at
        # all, so writing it out would just be silent clutter in the file.
        # dict keyed by id() in case a loaded file grouped several state_ids
        # under one shared StateAssignment object.
        kept = {id(a): a for a in self._state_bindings.values() if a.is_linked or not a.duel_config.is_empty()}
        self._bcs.state_assignments = list(kept.values())
        try:
            save_bot_strategy(self._bcs)
        except Exception:
            logger.exception("Failed to save BotCombatStrategy %s", self._bcs.id)
        self.combat_config_changed.emit()

    def _save_bcs_immediate(self) -> None:
        """Save unconditionally — for mutations that bypass the debounce timer."""
        if self._save_timer.isActive():
            self._save_timer.stop()
        self._save_bcs()

    def flush_pending_save(self) -> None:
        """Commit any debounced edit — this tab's BCS and any open Strategy Library.

        A no-op when nothing is pending, so it is safe to call on every config
        load and before every strategy switch.
        """
        if self._library_window is not None and self._library_window.isVisible():
            self._library_window.flush_editors()
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

    def _refresh_default_panel(self) -> None:
        if self._bcs is None:
            self._default_panel.setVisible(False)
            return
        self._default_panel.load(_DefaultBinding(self._bcs), self._all_dcs, self._all_pcs)
        self._default_panel.set_editable(self._editable)

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
        """Full rebuild: destroys and recreates one panel per exposed state.

        Only call this when the *set* of exposed states may have changed
        (config load, or the FSM canvas edited states while this tab was
        hidden) — it destroys existing panel widgets, which would interrupt an
        in-progress inline edit.  Data-only refreshes go through
        ``refresh_strategy_data`` / panel.set_libraries instead.
        """
        while self._assignments_layout.count() > 1:
            item = self._assignments_layout.takeAt(1)
            if item and item.widget():
                item.widget().deleteLater()
        self._state_panels.clear()

        if self._config is None:
            self._no_assignments_lbl.show()
            return

        exposed = exposed_combat_states(self._config)
        if not exposed:
            self._no_assignments_lbl.show()
            return

        self._no_assignments_lbl.hide()
        for state in exposed:
            binding = self._ensure_state_binding(state.id)
            panel = _DuelBindingPanel(state.label or state.id, parent=self._assignments_section)
            panel.load(binding, self._all_dcs, self._all_pcs)
            panel.set_editable(self._editable)
            panel.changed.connect(self._schedule_save)
            panel.presets_changed.connect(self._on_presets_changed)
            panel.edit_in_library_requested.connect(self._on_edit_dcs_in_library)
            self._assignments_layout.addWidget(panel)
            self._state_panels[state.id] = panel

    def _on_presets_changed(self) -> None:
        """A panel promoted its inline config to a new library DuelCombatStrategy."""
        self.refresh_strategy_data()

    def _on_edit_dcs_in_library(self, dcs_id: str) -> None:
        lib = self._ensure_library()
        lib.focus_duel_strategy(dcs_id)

    # ------------------------------------------------------------------
    # BCS picker / menu actions
    # ------------------------------------------------------------------

    def _on_pick_bcs(self) -> None:
        if self._config is None:
            return
        # Pending edits belong to the strategy being replaced, not the new one.
        self.flush_pending_save()
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
                self._state_bindings = {}
                if self._bcs is not None:
                    for assignment in self._bcs.state_assignments:
                        for sid in assignment.state_ids:
                            self._state_bindings[sid] = assignment
                SettingsManager.instance().set_active_strategy(self._config.id, chosen)
                self._rebuild_state_assignments()
                self._refresh_bcs_chip()
                self._refresh_default_panel()
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
        self._state_bindings = {}
        SettingsManager.instance().set_active_strategy(self._config.id, bcs.id)
        self._rebuild_state_assignments()
        self._refresh_bcs_chip()
        self._refresh_default_panel()
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
        self._state_bindings = {}
        for assignment in new_bcs.state_assignments:
            for sid in assignment.state_ids:
                self._state_bindings[sid] = assignment
        SettingsManager.instance().set_active_strategy(self._config.id, new_bcs.id)
        self._rebuild_state_assignments()
        self._refresh_bcs_chip()
        self._refresh_default_panel()

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
        # Drop rather than flush any pending edit: it belongs to the strategy
        # being deleted, and letting the timer survive would apply it to whichever
        # strategy we fall back to below.
        self._save_timer.stop()
        to_delete = self._bcs
        self._all_bcs = [b for b in self._all_bcs if b.id != to_delete.id]
        delete_bot_strategy(to_delete.id)
        self._bcs = self._all_bcs[0] if self._all_bcs else None
        self._state_bindings = {}
        if self._bcs is not None:
            for assignment in self._bcs.state_assignments:
                for sid in assignment.state_ids:
                    self._state_bindings[sid] = assignment
        new_active = self._bcs.id if self._bcs else None
        SettingsManager.instance().set_active_strategy(self._config.id, new_active)
        self._rebuild_state_assignments()
        self._refresh_bcs_chip()
        self._refresh_default_panel()
        self._refresh_deck_summaries()

    # ------------------------------------------------------------------
    # Strategy Library access
    # ------------------------------------------------------------------

    def _ensure_library(self) -> StrategyLibraryWindow:
        from bartlebot.gui.strategy_library.strategy_library_window import (
            StrategyLibraryWindow as _Lib,  # noqa: PLC0415
        )

        if self._library_window is None or not self._library_window.isVisible():
            # Parented so it is destroyed with the main window and does not keep
            # the app alive after everything else has closed.
            self._library_window = _Lib(parent=self)
            self._library_window.strategies_changed.connect(self.refresh_strategy_data)
        self._library_window.show()
        self._library_window.raise_()
        self._library_window.activateWindow()
        return self._library_window

    # ------------------------------------------------------------------
    # Theme / lifecycle
    # ------------------------------------------------------------------

    # No closeEvent: this tab lives inside a QTabWidget and is never closed as a
    # window, so one would never fire.  Pending edits are flushed via
    # flush_pending_save(), which AppController calls on aboutToQuit.

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._bcs_menu_btn.setIcon(svg_icon("more_vert", _ICON_BTN_SIZE))
            self._bcs_meta_lbl.setStyleSheet(f"color: {THEME.text_muted};")
            self._no_assignments_lbl.setStyleSheet(f"color: {THEME.text_muted};")
