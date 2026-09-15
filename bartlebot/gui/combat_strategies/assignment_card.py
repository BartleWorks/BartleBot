"""``AssignmentCard`` — one player assignment, drawn as a card rather than a form row.

WHAT CHANGED, AND WHY
---------------------

The row this replaces was a form: a grip, four checkboxes, an ``All`` and a ``None``
button, a school combo, a strategy combo, a cross, a note field underneath, and — when
the answer was custom DSL — a monospace box that grew to whatever height the DSL needed.
Nine controls across two-and-a-bit lines, of which the user reads *two* things when
scanning a duel: which players, and what they do.

So the card says those two things and folds the other seven away. What is left on the
face is what a card everywhere else in this app puts there — a name, a description, a
preview line, a row of pills, and the verbs — and it is the same ``CardFrame`` the
launch cards, the player cards and the Combat tab's own cards are built from, so this
list finally looks like the other card lists in the app instead of like a settings form
that wandered into a dialog.

THE THREE VERBS
---------------

* **search** — change what these players do. A themed popup menu over the same three
  answers the combo offered: the built-in default, any Player Strategy in the working
  set, or DSL of your own. A menu rather than ``PickerPopup`` because the payload is one
  line and single-valued, and rather than a nested dialog because a dialog stacked on
  this dialog is exactly what the combo was introduced to retire.
* **pencil** — the rest of the form, unfolded in place: the school, the note, and (only
  when the answer is custom DSL) the DSL box with its validator and its offer to be
  saved as a Player Strategy. Nothing was dropped; it stopped being in front of every
  user of every duel whether or not they had anything to say in it.
* **dismiss** — remove the assignment.

The expansion is per-card and not remembered: it is a detour, and a card that reopens
folded is a card whose height is a function of its content rather than of its history.

REORDERING
----------

The grip in the gutter is the only drag source, as on every other card in the app, and
this class installs its own event filter on it — ``GripHandle`` accepts the press and so
never passes the moves on to the card. What a drop then *does* is the still-list-with-a-
drop-line family rather than ``CardColumn``'s lift-and-slide, and deliberately: this list
lives inside the detail pane's single scroll area, and ``CardColumn`` brings a scroll area
of its own. A nested scrollable region here is the defect ``_KindPage`` and
``DuelConfigEditor`` both carry a comment about.
"""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, QEvent, QMimeData, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.strategy_models import PlayerAssignment, PlayerCombatStrategy
from bartlebot.dsl_syntax import DSL_HELP
from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.card_frame import CardFrame, CardMode, CardVerb
from bartlebot.gui.widgets.dsl_edit import DslGrowingTextEdit
from bartlebot.gui.widgets.player_pills import PlayerPillBar
from bartlebot.gui.widgets.rule_heading import RuleHeading
from bartlebot.gui.widgets.school_combo import SchoolCombo
from bartlebot.nodes.combat_runner import dsl_error, dsl_warning_text, dsl_warnings, validate_dsl

#: The two answers in the strategy menu that are not a Player Strategy, and the sentinel
#: each carries. Module level because the tests name them and because ``choose_strategy``
#: and :meth:`AssignmentCard.current_choice` have to agree exactly.
DEFAULT_CHOICE = "(built-in default)"
CUSTOM_CHOICE = "Custom DSL…"
DEFAULT_DATA = "\x00default"
CUSTOM_DATA = "\x00custom"

#: What the card offers once it holds DSL worth reusing. Named in full rather than
#: "preset": the library tab, the model and the menu all say *Player Strategy*.
PROMOTE_LABEL = "Save as Player Strategy…"

#: The verbs' labels, so a caller (and a test) can ask for a button by name.
CHANGE_VERB = "Change strategy"
EDIT_VERB = "Edit details"
REMOVE_VERB = "Remove"

#: What the card's title reads when the assignment names a strategy that is no longer in
#: the working set. Named rather than silently reset to the default — resetting would look
#: like the card was always configured that way, and Save would then make it true.
MISSING_TITLE = "(missing strategy)"

#: Private to this widget, so a drag from anywhere else is ignored outright.
_ROW_MIME = "application/x-bartlebot-assignment-card"
_DROP_INDICATOR_WIDTH = 2
#: Cap on the drag ghost's height. An expanded card holding a twenty-line DSL grabs as a
#: ghost the size of the panel otherwise.
_DRAG_PIXMAP_MAX_HEIGHT = 48


def strategy_menu_entries(all_pcs: list[PlayerCombatStrategy]) -> list[tuple[str, str]]:
    """``(label, data)`` for every answer the change-strategy menu offers, in order.

    Pure and module level so the menu's contents can be asserted without a
    ``QApplication``, and so the card and its tests cannot drift on what the menu holds.
    """
    entries = [(DEFAULT_CHOICE, DEFAULT_DATA)]
    entries += [(pcs.name, pcs.id) for pcs in all_pcs]
    entries.append((CUSTOM_CHOICE, CUSTOM_DATA))
    return entries


def dsl_preview(text: str, limit: int = 120) -> str:
    """One line of *text*, collapsed and truncated — what the card's third line shows."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


class AssignmentCard(CardFrame):
    """One player-assignment card: which players, what they do, and the rest folded away.

    ``CardMode.MEMBER``: the body is not a button. Its verbs are the grip and the three
    icons, and a body click that also did something would fight the pills sitting on it.
    """

    FRAME_NAME = "AssignmentCard"

    remove_requested = pyqtSignal(object)  # emits self
    #: (source_card, self, before) when another card is dropped on this one. ``before``
    #: says which of this card's edges the indicator was showing.
    reorder_requested = pyqtSignal(object, object, bool)
    changed = pyqtSignal()
    #: (self, newly_added_set) after a user-driven player change. Only newly added players
    #: are reported, so exclusivity only evicts what was just claimed.
    players_changed = pyqtSignal(object, object)
    #: This card's custom DSL is worth keeping — emits self, for the dialog to turn into a
    #: Player Strategy in its working set.
    promote_requested = pyqtSignal(object)

    def __init__(
        self,
        assignment: PlayerAssignment,
        all_pcs: list[PlayerCombatStrategy],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, mode=CardMode.MEMBER)
        self._assignment = assignment
        self._all_pcs = all_pcs
        self._updating = False  # suppresses re-entrant enforcement
        self._suppress = False
        self._editable = True
        self._expanded = False
        # The title without its warning mark. Set before ``_build`` because a theme pass
        # can arrive during construction and ``_restyle_content`` reads it.
        self._title = ""
        self._drag_start_pos: QPoint | None = None
        self._drop_edge: str | None = None  # "above" / "below" while a drop hovers
        # Whether the user asked for the DSL box on a card that has no DSL yet. Without it,
        # choosing "Custom DSL…" on an empty assignment would resolve straight back to the
        # built-in default on the next refresh and the box would never appear.
        self._custom = bool(assignment.dsl.strip()) and not assignment.is_linked
        self.setAcceptDrops(True)

        self._build()
        # Validation piggybacks on its own debounce rather than every keystroke — a
        # half-typed DSL is invalid by definition and must not nag.
        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.setInterval(500)
        self._validate_timer.timeout.connect(self._update_dsl_validation)
        self._refresh()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        inner = self._build_frame("player assignments")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self._title_lbl = QLabel(self)
        font = self._title_lbl.font()
        font.setBold(True)
        self._title_lbl.setFont(font)
        header.addWidget(self._title_lbl, 0)

        self._pills = PlayerPillBar(parent=self)
        self._pills.set_value(self._assignment.players)
        self._pills.changed.connect(self._on_players_changed)
        header.addWidget(self._pills, 0)

        header.addStretch(1)
        self._add_face_verbs(header)
        inner.addLayout(header)

        self._desc_lbl = QLabel(self)
        self._desc_lbl.setWordWrap(True)
        inner.addWidget(self._desc_lbl)

        self._dsl_lbl = QLabel(self)
        self._dsl_lbl.setWordWrap(True)
        inner.addWidget(self._dsl_lbl)

        # Everything the face no longer carries. Built once and hidden, rather than
        # constructed on first expand: the school and the note are already live on the
        # assignment, and a widget built late is a widget whose value has to be pushed
        # into it from somewhere that would otherwise have nothing to say.
        self._details = QWidget(self)
        details = QVBoxLayout(self._details)
        details.setContentsMargins(0, 6, 0, 0)
        details.setSpacing(4)
        details.addWidget(RuleHeading("Details", self._details))

        school_row = QHBoxLayout()
        school_row.setContentsMargins(0, 0, 0, 0)
        school_row.setSpacing(6)
        self._school_label = QLabel("School:", self._details)
        school_row.addWidget(self._school_label, 0)
        self._school_combo = SchoolCombo()
        self._school_combo.setToolTip("What this assignment needs, if any — descriptive only, never enforced")
        self._school_combo.set_value(self._assignment.school)
        self._school_combo.currentIndexChanged.connect(self._on_school_changed)
        school_row.addWidget(self._school_combo, 0)
        school_row.addStretch(1)
        details.addLayout(school_row)

        self._note_edit = QLineEdit(self._details)
        self._note_edit.setPlaceholderText('Note (optional) — e.g. "P2 passes to bank pips for round 3"')
        self._note_edit.setText(self._assignment.note)
        self._note_edit.textChanged.connect(self._on_note_changed)
        details.addWidget(self._note_edit)

        self._dsl_edit = DslGrowingTextEdit(parent=self._details)
        # A QPlainTextEdit accepts drops by default, and leaving that on would carve this
        # card's drop target down to a strip along the top. Dropping text *into* a DSL box
        # is far rarer than dropping a card onto its neighbour.
        self._dsl_edit.setAcceptDrops(False)
        self._dsl_edit.setPlaceholderText("Combat DSL for these players — blank uses the built-in default")
        self._dsl_edit.setToolTip(DSL_HELP)
        self._dsl_edit.textChanged.connect(self._on_dsl_changed)
        details.addWidget(self._dsl_edit)

        self._dsl_error_lbl = QLabel(self._details)
        self._dsl_error_lbl.setWordWrap(True)
        self._dsl_error_lbl.hide()
        details.addWidget(self._dsl_error_lbl)

        self._promote_btn = ActionButton("save_done", PROMOTE_LABEL, style=ActionStyle.SECONDARY)
        self._promote_btn.setToolTip("Add this DSL to the Player Strategies tab so other assignments can use it")
        self._promote_btn.clicked.connect(lambda: self.promote_requested.emit(self))
        details.addWidget(self._promote_btn)

        self._details.setVisible(False)
        inner.addWidget(self._details)

    def _verbs(self) -> list[CardVerb]:
        return [
            CardVerb(CHANGE_VERB, "search", "Change Player Combat Strategy", self._open_strategy_menu, on_face=True),
            CardVerb(EDIT_VERB, "pencil", "Edit required school, note, and custom DSL", self.toggle_expanded, True),
            CardVerb(REMOVE_VERB, "dismiss", "Remove this assignment", lambda: self.remove_requested.emit(self), True),
        ]

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def assignment(self) -> PlayerAssignment:
        return self._assignment

    @property
    def pills(self) -> PlayerPillBar:
        """The row of slot pills. Public because which players an assignment covers is
        read and set from outside — the editor enforces exclusivity across cards.
        """
        return self._pills

    def is_custom(self) -> bool:
        """Whether this card is answering with DSL of its own rather than a name."""
        return self._custom and not self._assignment.is_linked

    def is_expanded(self) -> bool:
        return self._expanded

    def current_choice(self) -> str:
        """The menu datum matching what the assignment actually holds."""
        if self._assignment.is_linked:
            return self._assignment.player_combat_strategy_id
        return CUSTOM_DATA if self._custom else DEFAULT_DATA

    def title_text(self) -> str:
        """What the card's first line reads. Public so a test can name what it drew."""
        return self._title_lbl.text()

    def _linked_pcs(self) -> PlayerCombatStrategy | None:
        pcs_id = self._assignment.player_combat_strategy_id
        if pcs_id is None:
            return None
        return next((p for p in self._all_pcs if p.id == pcs_id), None)

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def set_pcs_list(self, all_pcs: list[PlayerCombatStrategy]) -> None:
        self._all_pcs = all_pcs
        self._refresh()

    def set_editable(self, editable: bool) -> None:
        """Lock or unlock every control (used while a bot is running)."""
        self._editable = editable
        if self.grip is not None:
            self.grip.setCursor(Qt.CursorShape.OpenHandCursor if editable else Qt.CursorShape.ArrowCursor)
            self.grip.setToolTip(
                "Drag to reorder player assignments" if editable else "Locked while the bot is running"
            )
        self._pills.set_editable(editable)
        self._school_combo.setEnabled(editable)
        self._note_edit.setEnabled(editable)
        for button, verb in ((self.action_button(v.label), v) for v in self._verbs()):
            if button is not None:
                # The pencil stays live while locked: reading the note and the DSL of a
                # running bot is exactly what someone watching it fight wants to do, and
                # everything the fold reveals is already read-only by then.
                button.setEnabled(editable or verb.label == EDIT_VERB)
        self._refresh()

    def set_max_players(self, n: int) -> None:
        """Hide slots beyond *n* and clamp ``assignment.players`` to 1..n."""
        self._pills.set_max_players(n)
        filtered = [p for p in self._assignment.players if p <= n]
        if filtered != self._assignment.players:
            self._assignment.players = filtered
            self.changed.emit()

    def set_player_color_keys(self, keys: list[int | None], labels: list[str] | None = None) -> None:
        """Colour the pills by the account in each slot — see gui/widgets/player_pills.py."""
        self._pills.set_color_keys(keys)
        self._pills.set_account_labels(labels or [])

    def update_players_silently(self, players: list[int]) -> None:
        """Apply an externally-enforced player list without re-running exclusivity."""
        self._updating = True
        self._assignment.players = players
        self._pills.set_value(players)
        self._updating = False
        self.changed.emit()

    def link_to(self, pcs_id: str) -> None:
        """Point this card at a Player Strategy — how the dialog answers a promotion."""
        self._assignment.player_combat_strategy_id = pcs_id
        self._assignment.dsl = ""
        self._custom = False
        self._refresh()
        self.changed.emit()

    def toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._refresh()

    # ------------------------------------------------------------------
    # The strategy menu
    # ------------------------------------------------------------------

    def _open_strategy_menu(self) -> None:
        if not self._editable:
            return
        menu = popup_menu(self)
        current = self.current_choice()
        for label, data in strategy_menu_entries(self._all_pcs):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(data == current)
            action.triggered.connect(lambda _checked=False, d=data: self.choose_strategy(d))
        button = self.action_button(CHANGE_VERB)
        anchor = button if button is not None else self
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def choose_strategy(self, data: str) -> None:
        """Apply one of the three answers — the whole of the Assemble tier.

        Public because it is the one path a choice takes: the menu calls it, and so does
        a test, so there is no route into this state a test cannot also take.
        """
        if data == DEFAULT_DATA:
            self._assignment.player_combat_strategy_id = None
            self._assignment.dsl = ""
            self._custom = False
        elif data == CUSTOM_DATA:
            # Seeded from the strategy being left behind, so "this one, but with a tweak"
            # starts from that one rather than from an empty box — but only when there is
            # nothing already typed, since a stashed draft outranks the seed.
            pcs = self._linked_pcs()
            if pcs is not None and not self._assignment.dsl.strip():
                self._assignment.dsl = pcs.dsl
            # The assignment's own school already won over the linked one, so only fill in
            # where nothing was authored here: forking a Storm strategy into custom DSL
            # must not quietly drop the Storm.
            if pcs is not None and self._assignment.school is None:
                self._assignment.school = pcs.school
                self._school_combo.set_value(self._assignment.school)
            self._assignment.player_combat_strategy_id = None
            self._custom = True
            # Custom DSL is the one answer with nowhere to be typed on the face, so the
            # fold opens itself: choosing it and then having to find the pencil is two
            # gestures for one intent.
            self._expanded = True
        else:
            self._assignment.player_combat_strategy_id = data
            self._custom = False
        self._refresh()
        if self.is_custom() and self._editable:
            self._dsl_edit.setFocus()
        self.changed.emit()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """Sync every label, the fold, the DSL box's presence, and each read-only state."""
        self._suppress = True
        try:
            custom = self.is_custom()
            pcs = self._linked_pcs()
            if self._assignment.is_linked and pcs is None:
                title, description, preview = MISSING_TITLE, "", ""
            elif pcs is not None:
                title, description, preview = pcs.name, pcs.description, dsl_preview(pcs.dsl)
            elif custom:
                title, description, preview = CUSTOM_CHOICE, "", dsl_preview(self._assignment.dsl)
            else:
                title = DEFAULT_CHOICE
                description = "BartleBot's built-in DSL fights for these players."
                preview = ""
            # Held, because ``_update_dsl_validation`` appends a warning mark to it and
            # has to be able to take it back off without re-deriving which of the four
            # titles this card has.
            self._title = title
            self._title_lbl.setText(title)
            self._desc_lbl.setText(description)
            self._desc_lbl.setVisible(bool(description))
            # The note rides on the face when there is one: it is the assignment's own
            # words about this fight, which is exactly what a reader scanning the list is
            # looking for, and burying it in the fold hid it from everyone but its author.
            note = self._assignment.note.strip()
            self._dsl_lbl.setText(f"{preview}  ·  {note}" if preview and note else (preview or note))
            self._dsl_lbl.setVisible(bool(self._dsl_lbl.text()))

            self._pills.set_value(self._assignment.players)
            self._school_combo.set_value(self._assignment.school)
            self._dsl_edit.setPlainText(self._assignment.dsl if custom else "")
            self._dsl_edit.setReadOnly(not self._editable)
            self._dsl_edit.setVisible(custom)
            self._dsl_error_lbl.setVisible(custom and bool(self._dsl_error_lbl.text()))
            self._promote_btn.setVisible(custom and self._editable and bool(self._assignment.dsl.strip()))
            self._details.setVisible(self._expanded)
            button = self.action_button(EDIT_VERB)
            if button is not None:
                button.setToolTip(
                    "Hide this assignment's details" if self._expanded else "Edit required school, note, and custom DSL"
                )
        finally:
            self._suppress = False
        self._update_dsl_validation()
        self.refresh_theme_styles()

    def _restyle_content(self) -> None:
        error = validate_dsl(self._dsl_edit.toPlainText()) if self.is_custom() else None
        title_color = THEME.error if error else THEME.text
        self._title_lbl.setStyleSheet(f"color: {title_color}; background: transparent;")
        for label in (self._desc_lbl, self._dsl_lbl):
            label.setStyleSheet(
                f"color: {THEME.text_muted}; font-size: {THEME.font_card_sub_size}px; background: transparent;"
            )
        self._school_label.setStyleSheet(f"color: {THEME.text_muted}; background: transparent;")
        label_color = THEME.error if error else THEME.syntax_warning
        self._dsl_error_lbl.setStyleSheet(f"color: {label_color}; background: transparent;")
        self._dsl_edit.setFont(THEME.font_mono())
        self._dsl_edit.setStyleSheet(
            f"QTextEdit {{ background: {THEME.surface if self._dsl_edit.isReadOnly() else THEME.bg};"
            f" color: {THEME.text}; border: 1px solid {THEME.border};"
            f" border-left: 3px solid {THEME.error if error else THEME.border}; }}"
        )

    # ------------------------------------------------------------------
    # Edits
    # ------------------------------------------------------------------

    def _on_players_changed(self) -> None:
        if self._updating or self._suppress:
            return
        old = set(self._assignment.players)
        self._assignment.players = self._pills.get_value()
        self.players_changed.emit(self, set(self._assignment.players) - old)
        self.changed.emit()

    def _on_school_changed(self) -> None:
        if self._suppress:
            return
        self._assignment.school = self._school_combo.value()
        self.changed.emit()

    def _on_note_changed(self) -> None:
        if self._suppress:
            return
        self._assignment.note = self._note_edit.text()
        note = self._assignment.note.strip()
        preview = dsl_preview(self._assignment.dsl) if self.is_custom() else self._dsl_lbl.text().split("  ·  ")[0]
        self._dsl_lbl.setText(f"{preview}  ·  {note}" if preview and note else (preview or note))
        self._dsl_lbl.setVisible(bool(self._dsl_lbl.text()))
        self.changed.emit()

    def _on_dsl_changed(self) -> None:
        if self._suppress or not self.is_custom():
            return
        # The underlines mark columns in the text as it was validated; after an edit they
        # point at the wrong characters until the debounce re-checks.
        self._dsl_edit.set_dsl_error(None)
        self._dsl_edit.set_dsl_warnings(())
        self._assignment.dsl = self._dsl_edit.toPlainText()
        # Follows the text rather than waiting for the next refresh: the first character
        # typed into an empty box is exactly when the offer to name it becomes meaningful.
        self._promote_btn.setVisible(self._editable and bool(self._assignment.dsl.strip()))
        self._dsl_lbl.setText(dsl_preview(self._assignment.dsl))
        self._dsl_lbl.setVisible(bool(self._dsl_lbl.text()))
        self._validate_timer.stop()
        self._validate_timer.start()
        self.changed.emit()

    def _update_dsl_validation(self) -> None:
        """Re-run ``validate_dsl`` and reflect it on the card's face and in the fold.

        Only for a card answering with DSL of its own. A card naming a Player Strategy
        does not mirror an error in DSL it does not own — the duel's own summary carries
        ``INVALID_DSL`` and its card in the list draws the warning, which is where a
        problem with a *shared* strategy belongs.

        The warning mark on the title is what makes the fold safe. Everything else the
        pencil hides is optional; a DSL that does not parse is not, and a card that only
        admitted to it once expanded would hide the one thing the user has to act on.
        Same mark and same tooltip as ``StrategyCard``, so a broken thing looks broken
        wherever it is drawn.
        """
        dsl = self._dsl_edit.toPlainText()
        error = dsl_error(dsl) if self.is_custom() else None
        # Spell names no card has share the line, but not the title's mark: the move still
        # runs, and the snapshot they are checked against can be older than the game.
        warnings = dsl_warnings(dsl) if self.is_custom() else ()
        self._dsl_error_lbl.setText(error.message if error else dsl_warning_text(warnings))
        self._dsl_error_lbl.setVisible(bool(error or warnings))
        self._title_lbl.setText(f"{self._title}  ⚠" if error else self._title)
        self._title_lbl.setToolTip(f"Does not parse: {error.message}" if error else "")
        self._dsl_edit.set_dsl_error(error)
        self._dsl_edit.set_dsl_warnings(warnings)
        self._restyle_content()

    # ------------------------------------------------------------------
    # Drag to reorder
    # ------------------------------------------------------------------

    def _make_grip(self, what: str):
        """The base class's grip, plus the event filter that turns it into a drag source.

        ``GripHandle`` accepts its own presses and so never passes the moves on — which is
        what makes it a reliable handle, and what means the thing being dragged has to
        watch it from outside. ``CardColumn`` does the same from the column; here the card
        watches its own, there being no column.
        """
        grip = super()._make_grip(what)
        grip.installEventFilter(self)
        return grip

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        if obj is not self.grip or not self._editable:
            return super().eventFilter(obj, event)
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
        elif event.type() == QEvent.Type.MouseMove and self._drag_start_pos is not None:
            if (event.pos() - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance():
                self._start_drag()
        elif event.type() == QEvent.Type.MouseButtonRelease:
            self._drag_start_pos = None
        return super().eventFilter(obj, event)

    def _start_drag(self) -> None:
        self._drag_start_pos = None
        mime = QMimeData()
        # The payload carries nothing: event.source() identifies the card, and unlike a
        # recorded index it cannot go stale across a move. The format is only there to make
        # a foreign drag distinguishable.
        mime.setData(_ROW_MIME, QByteArray(b"1"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = self.grab()
        if pixmap.height() > _DRAG_PIXMAP_MAX_HEIGHT:
            pixmap = pixmap.copy(0, 0, pixmap.width(), _DRAG_PIXMAP_MAX_HEIGHT)
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.DropAction.MoveAction)

    def accepts_drop_from(self, source) -> bool:
        """Whether *source* is a sibling card this one could accept a drop from."""
        return self._editable and isinstance(source, AssignmentCard) and source is not self

    def _accepts(self, event) -> bool:  # noqa: ANN001
        return event.mimeData().hasFormat(_ROW_MIME) and self.accepts_drop_from(event.source())

    def drop_edge(self) -> str | None:
        """Which edge the drop indicator is currently drawn on, if any."""
        return self._drop_edge

    def _set_drop_edge(self, edge: str | None) -> None:
        if edge != self._drop_edge:
            self._drop_edge = edge
            self.update()

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001
        if self._accepts(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: ANN001
        if not self._accepts(event):
            event.ignore()
            return
        # Cards are variable height — an expanded one holding a long DSL is many times the
        # height of a folded neighbour — so which edge is meant has to come from the cursor
        # against this card's own midpoint, not a fixed band.
        self._set_drop_edge("above" if event.position().y() < self.height() / 2 else "below")
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001, ARG002
        self._set_drop_edge(None)

    def dropEvent(self, event) -> None:  # noqa: ANN001
        edge = self._drop_edge
        self._set_drop_edge(None)
        if not self._accepts(event):
            event.ignore()
            return
        self.reorder_requested.emit(event.source(), self, edge != "below")
        event.acceptProposedAction()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        super().paintEvent(event)
        if self._drop_edge is None:
            return
        # Read at paint time, so a theme switch needs no changeEvent here.
        painter = QPainter(self)
        painter.setPen(QPen(QColor(THEME.accent), _DROP_INDICATOR_WIDTH))
        y = 1 if self._drop_edge == "above" else self.height() - 1
        painter.drawLine(0, y, self.width(), y)
        painter.end()
