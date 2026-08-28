"""Combat tab: manage Bot Combat Strategy, state assignments, and player decks."""

from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING
from uuid import uuid4

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QActionGroup, QColor, QFontMetrics, QPainter
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
from bartlebot.combat_grouping import (
    apply_membership,
    build_bindings,
    group_bindings,
    state_label,
)
from bartlebot.combat_warnings import exposed_combat_states
from bartlebot.gui.icon_utils import svg_icon
from bartlebot.gui.menu_utils import anchor_menu_to_window
from bartlebot.gui.sidebar.sidebar_section import (
    DISCLOSURE_PX,
    NEST_INDENT_PX,
    SidebarSection,
    disclosure_pixmap,
)
from bartlebot.gui.strategy_library.duel_strategy_editor import DuelConfigEditor
from bartlebot.gui.strategy_library.strategy_picker_dialog import StrategyPickerDialog
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.elided_label import ElidedLabel, apply_muted_color
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
_NOT_CONFIGURED_LABEL = "Not configured — built-in default for every player"
_ICON_BTN_SIZE = 16
_DSL_PREVIEW_CHARS = 28

# What an *unset* binding falls back to.  These differ by level and the
# distinction is not cosmetic: an empty state override does NOT fall through to
# the built-in default, it falls through to this bot strategy's own Default
# Combat Strategy — see resolve_combat(), which reaches for
# ``bcs.default_strategy_id`` / ``bcs.default_duel_config`` before the built-in.
# Labelling both levels "BartleBot Default Combat Strategy" told the user the
# bot default would be skipped, and contradicted the pre-run warning text.
_STATE_INHERIT_LABEL = "(bot default)"
_STATE_INHERIT_META = "No override — uses the Default Combat Strategy above"
_UNASSIGNED_DIVIDER = "Using the bot default"

_RAIL_WIDTH_PX = 2  # the drawn rule
_RAIL_SLOT_PX = 3  # column reserved for it, drawn or not
_RAIL_GAP_PX = 5  # breathing room between the rule and the content it brackets


# ---------------------------------------------------------------------------
# Strategy chip button
# ---------------------------------------------------------------------------


class _StrategyChip(QPushButton):
    """Styled chip button showing a strategy name — click to open a picker.

    Strategy names are user-supplied and routinely long ("Heretical Academy
    4-player single decks"), so the chip elides rather than letting a
    QPushButton's size hint — which is always its full text width — set the
    minimum width of the sidebar it sits in.  ``QSizePolicy.Policy.Ignored``
    drops the size hint out of the layout's minimum-width calculation entirely;
    eliding then keeps the visible text readable in whatever width remains.
    """

    _ICON_SIZE = 14
    # Room the icon and the horizontal padding take, subtracted before eliding.
    _CHROME_PX = 28

    def __init__(self, text: str = _NO_STRATEGY_LABEL, parent=None):
        super().__init__(text, parent)
        self._applying_style = False
        self._full_text = text
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setIconSize(QSize(self._ICON_SIZE, self._ICON_SIZE))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._apply_style()
        self._apply_elide()

    def set_strategy_name(self, name: str) -> None:
        self._full_text = name
        self._apply_elide()

    def strategy_name(self) -> str:
        """The untruncated name, regardless of what is currently visible."""
        return self._full_text

    def _apply_elide(self) -> None:
        avail = max(self.width() - self._CHROME_PX, 1)
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, avail)
        # QAbstractButton.setText short-circuits when the text is unchanged, so
        # the setText → relayout → resizeEvent → setText path converges.
        self.setText(elided)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_style(self) -> None:
        if self._applying_style:
            return
        self._applying_style = True
        try:
            self.setIcon(svg_icon("search", self._ICON_SIZE, tint=THEME.text_muted))
            # This widget is fully QSS-drawn already, so it needs its own
            # disabled rule — the palette's Disabled group can't reach through a
            # stylesheet that hard-codes background and border.
            self.setStyleSheet(
                f"QPushButton {{ text-align: left; padding: 3px 8px; "
                f"background: {THEME.surface}; color: {THEME.text}; "
                f"border: 1px solid {THEME.border}; border-radius: 3px; }}"
                f"QPushButton:hover {{ border-color: {THEME.accent}; }}"
                f"QPushButton:disabled {{ background: {THEME.surface_disabled}; "
                f"color: {THEME.text_disabled}; border-color: {THEME.surface_disabled}; }}"
            )
        finally:
            self._applying_style = False

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._apply_style()
            self._apply_elide()


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
    # (slot, True/False/None) — the deck's three-state archmastery override.
    archmastery_mode_changed = pyqtSignal(int, object)

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
            slot_menu = anchor_menu_to_window(QMenu(menu_btn))
            # Rebuilt on every show: the archmastery submenu's check marks have
            # to reflect the deck as it stands, and the deck changes underneath
            # this menu on every import, load and clear.
            slot_menu.aboutToShow.connect(lambda slot=i, m=slot_menu: self._rebuild_slot_menu(m, slot))
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

    #: Label, and the stored value it means, for the three-state override.
    _ARCHMASTERY_MODES = (
        ("Use the app setting", None),
        ("Always set it", True),
        ("Never set it", False),
    )

    def _rebuild_slot_menu(self, menu: QMenu, slot: int) -> None:
        menu.clear()
        menu.addAction("Save to Library", lambda _=False: self.save_to_library_requested.emit(slot))
        menu.addAction("Load from Library", lambda _=False: self.load_from_library_requested.emit(slot))

        deck = self._decks.get(slot)
        # A deck with no saved school has nothing to set, so the override would
        # be a choice with no effect either way.
        if deck is not None and deck.archmastery_school:
            sub = menu.addMenu(f"Archmastery ({deck.archmastery_school})")
            group = QActionGroup(sub)
            group.setExclusive(True)
            for label, value in self._ARCHMASTERY_MODES:
                action = sub.addAction(label)
                action.setCheckable(True)
                action.setChecked(deck.apply_archmastery is value)
                action.triggered.connect(lambda _checked=False, v=value: self.archmastery_mode_changed.emit(slot, v))
                group.addAction(action)

        menu.addSeparator()
        menu.addAction("Clear Deck", lambda _=False: self.clear_requested.emit(slot))

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
        """Enable/disable each row's buttons, and say *why* when disabled.

        A disabled control that keeps its enabled tooltip ("Push the saved deck
        to this game client now") tells the user what the button would do but
        never why it won't — which is the actual question. Every disabled state
        here has exactly one cause, so it can just be named.
        """
        for i, (import_btn, apply_btn, menu_btn) in enumerate(
            zip(self._import_btns, self._apply_btns, self._menu_btns)
        ):
            slot = i + 1
            has_client = self._editable and slot <= self._client_count
            has_deck = self._decks.get(slot) is not None and not self._decks[slot].is_empty()

            import_btn.setEnabled(has_client)
            if has_client:
                import_btn.setToolTip("Snapshot the deck from this game client")
            elif not self._editable:
                import_btn.setToolTip("Locked while the bot is running")
            else:
                import_btn.setToolTip(f"No game client connected for P{slot}")

            apply_btn.setEnabled(has_client and has_deck)
            if has_client and has_deck:
                apply_btn.setToolTip("Push the saved deck to this game client now")
            elif not self._editable:
                apply_btn.setToolTip("Locked while the bot is running")
            elif not has_client:
                apply_btn.setToolTip(f"No game client connected for P{slot}")
            else:
                apply_btn.setToolTip(f"No deck saved for P{slot} — use Import first")

            menu_btn.setEnabled(self._editable)
            menu_btn.setToolTip("More deck actions" if self._editable else "Locked while the bot is running")

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


def _make_meta_label(text: str = "") -> ElidedLabel:
    """One-line muted summary line, elided to whatever width the sidebar has.

    Elided rather than wrapped on purpose: these lines summarise a duel config
    whose DSL previews run to well over a hundred characters, and wrapping them
    would trade the horizontal scrollbar for three or four stacked lines per
    collapsed panel — the opposite of what a collapsed summary is for.  The full
    text is always in the tooltip.
    """
    lbl = ElidedLabel(text)
    font = lbl.font()
    font.setPointSize(max(font.pointSize() - 1, 7))
    lbl.setFont(font)
    apply_muted_color(lbl)
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


class _GroupRail(QWidget):
    """Vertical rule bracketing a panel whose fights share one Duel Strategy.

    Several fight names stacked in a header read as a list, but not necessarily
    as a list that means "all of these use the strategy on the right".  The rail
    is what says so, and it spans the panel's whole height — the fights, the
    summary and the inline editor — so the claim is "this entire block is one
    group", not merely "these names belong together".

    The column is reserved whether or not the rule is drawn, so a one-fight
    panel and a four-fight panel keep their contents on the same left edge.
    Same reasoning as the disclosure arrow's slot, which survives being
    withdrawn precisely so the titles stay aligned.

    THEME is read at paint time, so this needs no ``changeEvent`` hook to
    survive a theme switch.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self.setFixedWidth(_RAIL_SLOT_PX)

    def is_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.update()

    def paintEvent(self, event) -> None:  # noqa: ARG002, N802 — Qt naming
        if not self._active:
            return
        painter = QPainter(self)
        # THEME.border, not an accent: the rail is structure, and it must not
        # compete with the fight names it is grouping.
        painter.fillRect(0, 0, _RAIL_WIDTH_PX, self.height(), QColor(THEME.border))
        painter.end()


class _DisclosureRow(QWidget):
    """Arrow + one title line per fight, behaving as one click target.

    Deliberately not a QToolButton: an auto-raise button brings its own hover
    frame, pressed state and checked highlight, which made a nested duel panel
    look like a different kind of control from the section header directly above
    it rather than one level down from it.  Same glyph, same size, same
    treatment at every depth — only the indent and the tint change.

    A panel now covers a *group* of fights, so the title is a list.  The fights
    are stacked vertically rather than joined into one elided string: a joined
    title truncates, and a membership list that ends early hides which fights an
    edit is about to reach.  One line each also lets a long name elide against
    its own width, with its own tooltip, instead of eating the space its
    neighbours needed.
    """

    clicked = pyqtSignal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Boxed to one line's height and centred within it, so the glyph lines up
        # with the *first* fight rather than floating against the whole stack.
        self._arrow = QLabel()
        self._arrow.setFixedSize(DISCLOSURE_PX, QFontMetrics(self.font()).height())
        self._arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._arrow, 0, Qt.AlignmentFlag.AlignTop)

        # Elided per line: these are FSM state labels, which the bot author
        # writes freely ("Defeat Preceptor of Order").  They are the only part of
        # a collapsed panel whose width scales with user data.
        self._titles_layout = QVBoxLayout()
        self._titles_layout.setContentsMargins(0, 0, 0, 0)
        self._titles_layout.setSpacing(0)
        layout.addLayout(self._titles_layout, 1)

        self._title_lbls: list[ElidedLabel] = []
        self._titles: list[str] = []
        self.set_title(title)

        self._interactive = True
        self.set_expanded(False)

    def set_title(self, title: str) -> None:
        self.set_titles([title])

    def set_titles(self, titles: list[str]) -> None:
        """Show one line per fight, reusing label widgets across refreshes.

        Surplus labels are hidden rather than destroyed: membership changes
        while the panel is on screen, and deleting widgets mid-refresh is how
        the old per-state rebuild interrupted in-progress edits.
        """
        self._titles = list(titles)
        while len(self._title_lbls) < len(self._titles):
            lbl = ElidedLabel()
            self._titles_layout.addWidget(lbl)
            self._title_lbls.append(lbl)
        for lbl, text in zip(self._title_lbls, self._titles):
            lbl.setText(text)
            lbl.show()
        for lbl in self._title_lbls[len(self._titles) :]:
            lbl.hide()

    def title(self) -> str:
        """The first fight's label — what a single-fight panel shows."""
        return self._titles[0] if self._titles else ""

    def titles(self) -> list[str]:
        return list(self._titles)

    def set_expanded(self, expanded: bool) -> None:
        # Nested level: muted tint against the section header's icon_tint.
        self._arrow.setPixmap(disclosure_pixmap(expanded, tint=THEME.text_muted))

    def set_interactive(self, interactive: bool, reason: str = "") -> None:
        """Show or withdraw the expand affordance entirely.

        When there is nothing to expand (the binding is linked to a library
        entry) the arrow is *hidden* rather than shown greyed out — an
        affordance that is never offered needs no disabled styling, and the
        empty slot keeps the titles aligned.
        """
        self._interactive = interactive
        self._arrow.setVisible(interactive)
        self.setCursor(Qt.CursorShape.PointingHandCursor if interactive else Qt.CursorShape.ArrowCursor)
        self.setToolTip("Expand to edit this duel config inline" if interactive else reason)

    def mousePressEvent(self, event) -> None:  # noqa: ARG002
        if self._interactive:
            self.clicked.emit()


class _DuelBindingPanel(QWidget):
    """One duel-config binding: inline (expand to edit directly) or linked to a
    library DuelCombatStrategy (compact summary + edit-in-library).

    Used for both the Default Combat Strategy and every per-state override —
    same actions, same layout, in both places.  Operates on any ``binding``
    object shaped like ``StateAssignment``: ``duel_config``,
    ``duel_combat_strategy_id`` (settable), ``is_linked``.

    ``empty_label`` / ``empty_meta`` differ between the two uses because the
    fallback genuinely differs: an unset *default* drops to the built-in
    strategy, while an unset *state override* drops to the bot's default first.
    """

    changed = pyqtSignal()
    presets_changed = pyqtSignal()  # a new library DCS or PCS was created
    edit_in_library_requested = pyqtSignal(str)  # dcs_id
    membership_requested = pyqtSignal()  # user asked to edit which fights this group covers
    # Linking, unlinking or promoting can change how panels *group*: picking a
    # strategy another panel already uses merges the two.  Kept separate from
    # ``changed``, which fires on every keystroke in the inline DSL editor and
    # must never trigger a rebuild that would destroy the editor being typed in.
    linkage_changed = pyqtSignal()

    def __init__(
        self,
        title: str,
        empty_label: str = _BUILT_IN_LABEL,
        empty_meta: str = _NOT_CONFIGURED_LABEL,
        parent=None,
        supports_membership: bool = False,
    ):
        super().__init__(parent)
        self._binding = None
        self._all_dcs: list[DuelCombatStrategy] = []
        self._all_pcs: list[PlayerCombatStrategy] = []
        self._expanded = False
        self._editable = True
        self._empty_label = empty_label
        self._empty_meta = empty_meta
        # False for the Default Combat Strategy, which has no state_ids to edit —
        # it is the fallback for every fight that no group claims.
        self._supports_membership = supports_membership

        # Indented one level: this panel is a child of the enclosing
        # SidebarSection, and the inset is what makes that relationship visible.
        spine = QHBoxLayout(self)
        spine.setContentsMargins(NEST_INDENT_PX, 0, 0, 0)
        spine.setSpacing(_RAIL_GAP_PX)
        self._rail = _GroupRail()
        spine.addWidget(self._rail)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(1)
        spine.addLayout(outer, 1)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)

        self._disclosure = _DisclosureRow(title)
        self._disclosure.clicked.connect(self._on_toggle)
        header.addWidget(self._disclosure)

        self._chip = _StrategyChip(empty_label)
        self._chip.setMaximumWidth(160)
        self._chip.setToolTip("Click to link a Duel Strategy from the library")
        self._chip.clicked.connect(self._on_pick_link)
        # Top-aligned against the first fight: the chip describes the whole
        # group, so centring it against a four-line title would leave it
        # floating beside no particular fight.
        header.addWidget(self._chip, 1, Qt.AlignmentFlag.AlignTop)

        self._menu_btn = QToolButton()
        self._menu_btn.setIcon(svg_icon("more_vert", _ICON_BTN_SIZE))
        self._menu_btn.setIconSize(QSize(_ICON_BTN_SIZE, _ICON_BTN_SIZE))
        self._menu_btn.setToolTip("Strategy actions")
        self._menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu_btn.setStyleSheet("QToolButton::menu-indicator { image: none; }")
        self._menu = anchor_menu_to_window(QMenu(self._menu_btn))
        # A disabled action has to be able to say why — see _rebuild_menu.
        self._menu.setToolTipsVisible(True)
        self._menu.aboutToShow.connect(self._rebuild_menu)
        self._menu_btn.setMenu(self._menu)
        header.addWidget(self._menu_btn, 0, Qt.AlignmentFlag.AlignTop)

        outer.addLayout(header)

        self._meta_lbl = _make_meta_label()
        outer.addWidget(self._meta_lbl)
        self._assignments_lbl = _make_meta_label()
        outer.addWidget(self._assignments_lbl)

        self._config_editor = DuelConfigEditor()
        self._config_editor.changed.connect(self.changed)
        self._config_editor.presets_changed.connect(self.presets_changed)
        # Indented one level below this panel's own header, continuing the
        # "deeper == further right" rule the section headers establish.
        editor_row = QHBoxLayout()
        editor_row.setContentsMargins(0, 0, 0, 0)
        editor_row.setSpacing(0)
        editor_row.addSpacing(NEST_INDENT_PX)
        editor_row.addWidget(self._config_editor, 1)
        outer.addLayout(editor_row)
        self._config_editor.hide()

    # -- data -----------------------------------------------------------

    def load(self, binding, all_dcs: list[DuelCombatStrategy], all_pcs: list[PlayerCombatStrategy]) -> None:
        self._binding = binding
        self._all_dcs = all_dcs
        self._all_pcs = all_pcs
        self._expanded = False
        self._refresh()

    def set_titles(self, titles: list[str]) -> None:
        """Name every fight this panel governs, one per line."""
        self._disclosure.set_titles(titles)
        # A rail beside a single fight would claim a group that does not exist.
        self._rail.set_active(len(titles) > 1)

    def titles(self) -> list[str]:
        return self._disclosure.titles()

    def set_libraries(self, all_dcs: list[DuelCombatStrategy], all_pcs: list[PlayerCombatStrategy]) -> None:
        """Update the reference lists without disturbing expand state or in-progress edits."""
        self._all_dcs = all_dcs
        self._all_pcs = all_pcs
        self._refresh()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._chip.setEnabled(editable)
        self._menu_btn.setEnabled(editable)
        self._menu_btn.setToolTip("Strategy actions" if editable else "Locked while the bot is running")
        # The inline editor has to be locked too.  Disabling only the chip, menu
        # and disclosure left an already-expanded panel fully editable mid-run —
        # min/max players, combat order drag, DSL text and add/remove assignment
        # all stayed live, and the debounce timer happily persisted them.
        self._config_editor.set_editable(editable)
        self._refresh_disclosure()

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        """Expand or collapse the inline editor. No-op when there is nothing to expand."""
        if self._binding is None or self._binding.is_linked:
            return
        self._expanded = expanded
        self._refresh()

    def _refresh_disclosure(self) -> None:
        """Sync the expand affordance to whether expanding is currently possible."""
        if self._binding is None:
            self._disclosure.set_interactive(False)
        elif self._binding.is_linked:
            self._disclosure.set_interactive(False, "Linked to a Duel Strategy in the library — unlink to edit it here")
        elif not self._editable:
            self._disclosure.set_interactive(False, "Locked while the bot is running")
        else:
            self._disclosure.set_interactive(True)
        self._disclosure.set_expanded(self._expanded)

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
            self._expanded = False
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
            self._refresh_disclosure()
            return

        # Inline
        config = self._binding.duel_config
        empty = config.is_empty()
        self._chip.set_strategy_name(self._empty_label if empty else "(custom)")
        if self._expanded:
            self._meta_lbl.hide()
            self._assignments_lbl.hide()
            self._config_editor.load(config, self._all_pcs)
            self._config_editor.set_editable(self._editable)
            self._config_editor.show()
        else:
            self._config_editor.hide()
            if empty:
                self._meta_lbl.setText(self._empty_meta)
                self._meta_lbl.show()
                self._assignments_lbl.hide()
            else:
                # The meta line carries min/max players *and* the combat order,
                # so a fight whose order differs from its neighbours (Balaur
                # entering P1→P2→P4→P3) is visible without expanding anything.
                self._meta_lbl.setText(_format_dcs_meta(config))
                self._meta_lbl.show()
                self._assignments_lbl.setText(_format_dcs_assignments(config, self._all_pcs))
                self._assignments_lbl.show()
        self._refresh_disclosure()

    # -- actions ----------------------------------------------------------

    def _on_toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def _on_pick_link(self) -> None:
        if self._binding is None:
            return
        dlg = StrategyPickerDialog(
            title="Pick Duel Strategy",
            mode="duel",
            strategies=self._all_dcs,
            current_id=self._binding.duel_combat_strategy_id,
            allow_none=True,
            none_label=self._empty_label,
            parent=self,
        )
        if dlg.exec():
            self._binding.duel_combat_strategy_id = dlg.selected_id()
            self._expanded = False
            self._refresh()
            self.changed.emit()
            self.linkage_changed.emit()

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
        if self._supports_membership:
            # Separated: everything above changes *what* this group does, this
            # changes *which* fights it does it to.
            self._menu.addSeparator()
            action = self._menu.addAction("Applies to…", self.membership_requested.emit)
            # Nothing to share yet, and a group of fights holding no config is
            # exactly what split_unconfigured pulls back apart — so offering it
            # here would look like an edit that quietly undid itself.
            configured = self._binding.is_linked or not self._binding.duel_config.is_empty()
            action.setEnabled(configured)
            if not configured:
                action.setToolTip("Give this fight a strategy first — there is nothing to share yet")

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
        self.linkage_changed.emit()

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
        self.linkage_changed.emit()
        self.presets_changed.emit()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._disclosure.set_expanded(self._expanded)
            self._menu_btn.setIcon(svg_icon("more_vert", _ICON_BTN_SIZE))
            apply_muted_color(self._meta_lbl)
            apply_muted_color(self._assignments_lbl)


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
        bcs_menu = anchor_menu_to_window(QMenu(self._bcs_menu_btn))
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
        # The bot default is the last stop before the built-in strategy, so its
        # empty label names the built-in one.  State panels below say something
        # different — see _STATE_INHERIT_LABEL.
        self._default_panel = _DuelBindingPanel("Default", _BUILT_IN_LABEL, _NOT_CONFIGURED_LABEL)
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
        # Separates configured groups from the fights that fall through to the
        # bot default.  Persistent rather than rebuilt so its themed stylesheet
        # survives a rebuild; ``_rebuild_state_assignments`` re-places it.
        self._group_divider = QLabel(_UNASSIGNED_DIVIDER)
        self._group_divider.setStyleSheet(f"color: {THEME.text_muted};")
        self._group_divider.hide()
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
        self._decks_section.archmastery_mode_changed.connect(self._on_archmastery_mode_changed)
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
        for panel in self._group_panels():
            panel.set_libraries(self._all_dcs, self._all_pcs)

    def _group_panels(self) -> list[_DuelBindingPanel]:
        """Each panel once — ``_state_panels`` maps every fight in a group to the
        same widget, so its values repeat."""
        return list(dict.fromkeys(self._state_panels.values()))

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._bcs_chip.setEnabled(editable)
        self._bcs_menu_btn.setEnabled(editable)
        self._decks_section.set_editable(editable)
        self._default_panel.set_editable(editable)
        for panel in self._group_panels():
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
            self._state_bindings = build_bindings(self._bcs.state_assignments)

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
        self._bcs.state_assignments = self._prune_orphans(list(kept.values()))
        try:
            save_bot_strategy(self._bcs)
        except Exception:
            logger.exception("Failed to save BotCombatStrategy %s", self._bcs.id)
        self.combat_config_changed.emit()

    def _prune_orphans(self, assignments: list[StateAssignment]) -> list[StateAssignment]:
        """Drop state ids that no longer exist in the bot, and any assignment left empty.

        Deleting a node used to leave its assignment behind forever, because
        bindings are seeded from the strategy file and never checked against the
        bot's actual states.  Those orphans are not merely clutter: state ids are
        recycled, so a stale assignment lies in wait for whatever node is created
        next and hands it a duel strategy nobody asked for.  ``_unique_id`` no
        longer recycles ids, which closes that door for new bots; this closes it
        for strategy files written before that change.

        Only runs when a config is loaded — with no bot to compare against,
        every assignment would look orphaned.
        """
        if self._config is None:
            return assignments
        live_ids = {s.id for s in self._config.states}
        pruned: list[StateAssignment] = []
        for assignment in assignments:
            kept_ids = [sid for sid in assignment.state_ids if sid in live_ids]
            if not kept_ids:
                logger.info(
                    "Dropping combat assignment for deleted state(s) %s from strategy %s",
                    ", ".join(assignment.state_ids),
                    self._bcs.id if self._bcs else "?",
                )
                continue
            if kept_ids != assignment.state_ids:
                logger.info(
                    "Dropping deleted state(s) %s from a combat assignment in strategy %s",
                    ", ".join(sid for sid in assignment.state_ids if sid not in live_ids),
                    self._bcs.id if self._bcs else "?",
                )
                assignment.state_ids = kept_ids
            pruned.append(assignment)
        return pruned

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

    def _on_archmastery_mode_changed(self, slot: int, mode: object) -> None:
        """Pin (or unpin) this deck's answer to "set the archmastery school".

        ``mode`` is True, False, or None for "follow the app setting". Saved
        immediately rather than debounced: it is a single discrete choice made
        from a menu, not a field someone is still typing into.
        """
        if self._bcs is None:
            return
        deck = self._bcs.player_decks.get(slot)
        if deck is None or deck.apply_archmastery is mode:
            return
        deck.apply_archmastery = mode
        save_bot_strategy(self._bcs)
        self._refresh_deck_summaries()

    def _rebuild_state_assignments(self) -> None:
        """Full rebuild: destroys and recreates one panel per *group* of fights.

        A Duel Strategy is drawn once however many fights use it — several
        panels backed by one ``StateAssignment`` was the old arrangement, and it
        meant editing any of them silently edited all of them while the others
        went on showing stale summaries.

        Only call this when the set of exposed states, or the way they group, may
        have changed (config load, a strategy relinked, membership edited) — it
        destroys existing panel widgets, which would interrupt an in-progress
        inline edit.  Data-only refreshes go through ``refresh_strategy_data`` /
        panel.set_libraries instead.
        """
        while self._assignments_layout.count() > 1:
            item = self._assignments_layout.takeAt(1)
            widget = item.widget() if item else None
            # The divider outlives the rebuild — it is re-placed below.
            if widget is not None and widget is not self._group_divider:
                widget.deleteLater()
        self._state_panels.clear()
        self._group_divider.hide()

        if self._config is None:
            self._no_assignments_lbl.show()
            return

        exposed = exposed_combat_states(self._config)
        if not exposed:
            self._no_assignments_lbl.show()
            return

        self._no_assignments_lbl.hide()
        for state in exposed:
            self._ensure_state_binding(state.id)
        groups = group_bindings(self._state_bindings, exposed)

        # Only worth a divider when there is something on both sides of it.
        show_divider = any(g.is_configured for g in groups) and any(not g.is_configured for g in groups)
        divider_placed = False

        for group in groups:
            if show_divider and not divider_placed and not group.is_configured:
                self._assignments_layout.addWidget(self._group_divider)
                self._group_divider.show()
                divider_placed = True

            panel = _DuelBindingPanel(
                "",
                _STATE_INHERIT_LABEL,
                _STATE_INHERIT_META,
                parent=self._assignments_section,
                supports_membership=True,
            )
            panel.set_titles([state_label(s) for s in group.states])
            panel.load(group.assignment, self._all_dcs, self._all_pcs)
            panel.set_editable(self._editable)
            panel.changed.connect(self._schedule_save)
            panel.presets_changed.connect(self._on_presets_changed)
            panel.edit_in_library_requested.connect(self._on_edit_dcs_in_library)
            panel.linkage_changed.connect(self._on_linkage_changed)
            panel.membership_requested.connect(partial(self._on_edit_membership, group.assignment))
            self._assignments_layout.addWidget(panel)
            # Keyed per state, so every fight in a group resolves to its panel.
            for state in group.states:
                self._state_panels[state.id] = panel

    def _on_linkage_changed(self) -> None:
        """A panel linked, unlinked or promoted — groups may have merged or split."""
        self._regroup_bindings()
        self._rebuild_state_assignments()
        self._save_bcs_immediate()

    def _regroup_bindings(self) -> None:
        """Re-merge bindings that now share a Duel Strategy.

        Linking a fight to a strategy another panel already uses leaves two
        objects naming one strategy; ``build_bindings`` folds them together so
        the next rebuild draws a single row, which is the whole point.
        """
        distinct = list({id(a): a for a in self._state_bindings.values()}.values())
        self._state_bindings = build_bindings(distinct)

    def _on_edit_membership(self, assignment: StateAssignment) -> None:
        """Show "Applies to…" for one group and commit the result."""
        if self._config is None:
            return
        from bartlebot.gui.sidebar.applies_to_dialog import AppliesToDialog  # noqa: PLC0415

        exposed = exposed_combat_states(self._config)
        dlg = AppliesToDialog(
            strategy_name=self._describe_binding_of(assignment),
            exposed=exposed,
            chosen_ids=list(assignment.state_ids),
            describe=self._describe_state_binding,
            parent=self,
        )
        if not dlg.exec():
            return
        apply_membership(self._state_bindings, assignment, dlg.selected_ids())
        # Re-asserts both invariants before anything is drawn from the result.
        self._regroup_bindings()
        self._rebuild_state_assignments()
        # Structural, so it bypasses the debounce: a half-applied membership is
        # not something to leave sitting on a timer.
        self._save_bcs_immediate()

    def _describe_state_binding(self, state_id: str) -> str:
        binding = self._state_bindings.get(state_id)
        if binding is None:
            return _STATE_INHERIT_LABEL
        return self._describe_binding_of(binding)

    def _describe_binding_of(self, binding: StateAssignment) -> str:
        """The same words the panel's chip uses, so the dialog reads as the same thing."""
        if binding.is_linked:
            dcs = next((d for d in self._all_dcs if d.id == binding.duel_combat_strategy_id), None)
            return dcs.name if dcs is not None else "(missing strategy)"
        if binding.duel_config.is_empty():
            return _STATE_INHERIT_LABEL
        return "(custom)"

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
                self._state_bindings = build_bindings(self._bcs.state_assignments) if self._bcs else {}
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
        self._state_bindings = build_bindings(new_bcs.state_assignments)
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
        self._state_bindings = build_bindings(self._bcs.state_assignments) if self._bcs else {}
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
            self._group_divider.setStyleSheet(f"color: {THEME.text_muted};")
