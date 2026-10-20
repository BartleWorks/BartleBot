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

THE VERBS
---------

* **search** — change what these players do. A themed popup menu over the same three
  answers the combo offered: the built-in default, any Player Strategy in the working
  set, or DSL of your own. A menu rather than ``PickerPopup`` because the payload is one
  line and single-valued, and rather than a nested dialog because a dialog stacked on
  this dialog is exactly what the combo was introduced to retire.
* **pencil** — edit the linked Player Strategy: the dialog switches to its Player tab
  with that strategy selected. Only on a card that links one. A pencil that merely
  unfolded the card read as "edit this strategy's DSL" and did nothing of the kind
  (#112), and nothing else here led to the DSL a linked card runs. Default, custom and
  missing cards have nothing to open, so the button is withdrawn rather than grayed —
  the same call ``StrategyBindingCard`` makes for an inline config.
* **dismiss** — remove the assignment.
* **chevron** — the rest of the form, unfolded in place: the school, the note, and (only
  when the answer is custom DSL) the DSL box with its validator and its offer to be
  saved as a Player Strategy. Nothing was dropped; it stopped being in front of every
  user of every duel whether or not they had anything to say in it. A disclosure
  chevron at the header's right edge, where Material, Fluent and macOS all put one, so
  it never moves as the card grows. Not a verb button, because its glyph and tooltip
  follow the fold; the right-click menu still offers the fold as a verb.

The expansion is per-card and not remembered: it is a detour, and a card that reopens
folded is a card whose height is a function of its content rather than of its history.

REORDERING
----------

The grip in the gutter is the only drag source, as on every other card in the app, and
the drag is the same lift-and-slide every other card list uses: ``DuelConfigEditor``
hosts a ``DragReorder`` over its cards. This card's part is only the grip, and its lock
while the bot is running.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from bartlebot.combat.dsl_syntax import DSL_HELP
from bartlebot.gui.combat_strategies.dsl_checks import checker
from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.theming.themed import set_style_sheet
from bartlebot.gui.widgets.buttons.action_button import ActionButton, ActionStyle
from bartlebot.gui.widgets.buttons.icon_button import IconButton
from bartlebot.gui.widgets.cards.card_frame import CardFrame, CardMode, CardVerb
from bartlebot.gui.widgets.inputs.dsl_edit import DslGrowingTextEdit
from bartlebot.gui.widgets.labels.elided_label import WrappingElidedLabel
from bartlebot.gui.widgets.labels.rule_heading import RuleHeading
from bartlebot.gui.widgets.pickers.school_picker import SchoolPickerButton
from bartlebot.gui.widgets.pills.player_pills import PlayerPillBar
from bartlebot.nodes.combat_runner import DslError, dsl_error, dsl_warning_text, dsl_warnings
from bartlebot.shared.strategy_models import PlayerAssignment, PlayerCombatStrategy

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
EDIT_VERB = "Edit this Player Strategy"
REMOVE_VERB = "Remove"
#: The fold's menu entry and the chevron's tooltip, each naming what a press does next.
SHOW_DETAILS = "Show details"
HIDE_DETAILS = "Hide details"

#: What the card's title reads when the assignment names a strategy that is no longer in
#: the working set. Named rather than silently reset to the default — resetting would look
#: like the card was always configured that way, and Save would then make it true.
MISSING_TITLE = "(missing strategy)"


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

    ``CardMode.MEMBER``: the body is not a button. Its verbs are the grip and the header
    icons, and a body click that also did something would fight the pills sitting on it.
    """

    FRAME_NAME = "AssignmentCard"

    remove_requested = pyqtSignal(object)  # emits self
    changed = pyqtSignal()
    #: (self, newly_added_set) after a user-driven player change. Only newly added players
    #: are reported, so exclusivity only evicts what was just claimed.
    players_changed = pyqtSignal(object, object)
    #: This card's custom DSL is worth keeping — emits self, for the dialog to turn into a
    #: Player Strategy in its working set.
    promote_requested = pyqtSignal(object)
    #: The pencil: open the linked Player Strategy, by id, wherever it is edited.
    open_pcs_requested = pyqtSignal(str)

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
        # Whether anything is listening for the pencil with somewhere to take it. Pick
        # has no Player tab, so there the pencil is grayed rather than a dead button.
        self._can_open_pcs = True
        self._expanded = False
        # The title without its warning mark. Set before ``_build`` because a theme pass
        # can arrive during construction and ``_restyle_content`` reads it.
        self._title = ""
        #: The parse error last shown, which the title and border follow; see ``_restyle_content``.
        self._shown_error: DslError | None = None
        # Whether the user asked for the DSL box on a card that has no DSL yet. Without it,
        # choosing "Custom DSL…" on an empty assignment would resolve straight back to the
        # built-in default on the next refresh and the box would never appear.
        self._custom = bool(assignment.dsl.strip()) and not assignment.is_linked

        self._build()
        # Validation piggybacks on its own debounce rather than every keystroke — a
        # half-typed DSL is invalid by definition and must not nag.
        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.setInterval(500)
        self._validate_timer.timeout.connect(self._check_in_background)
        checker().checked.connect(self._on_dsl_checked)
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
        self._add_face_verbs(header, self._all_verbs())
        self._chevron = self._make_face_icon_button("chevron_down", SHOW_DETAILS)
        self._chevron.clicked.connect(self.toggle_expanded)
        header.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        inner.addLayout(header)

        # Wrapping and elided, not plain word-wrapped: a DSL token has no spaces to wrap
        # at, and one longer than the line made itself the card's minimum width (#118).
        self._desc_lbl = WrappingElidedLabel(parent=self)
        inner.addWidget(self._desc_lbl)

        self._dsl_lbl = WrappingElidedLabel(parent=self)
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
        self._school_label = QLabel("Schools:", self._details)
        school_row.addWidget(self._school_label, 0)
        self._school_picker = SchoolPickerButton(parent=self._details)
        self._school_picker.setToolTip(
            "The schools these players can be, if it matters — descriptive only, never enforced"
        )
        self._school_picker.set_value(self._assignment.schools)
        self._school_picker.selection_changed.connect(self._on_school_changed)
        school_row.addWidget(self._school_picker, 0)
        school_row.addStretch(1)
        details.addLayout(school_row)

        self._note_edit = QLineEdit(self._details)
        self._note_edit.setPlaceholderText('Note (optional) — e.g. "Player 2 passes to bank pips for round 3"')
        self._note_edit.setText(self._assignment.note)
        self._note_edit.textChanged.connect(self._on_note_changed)
        details.addWidget(self._note_edit)

        self._dsl_edit = DslGrowingTextEdit(parent=self._details)
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

    def _all_verbs(self) -> list[CardVerb]:
        """Every verb this card can ever offer — what its face buttons are built from."""
        fold = HIDE_DETAILS if self._expanded else SHOW_DETAILS
        return [
            CardVerb(CHANGE_VERB, "search", "Change Player Combat Strategy", self._open_strategy_menu, on_face=True),
            CardVerb(EDIT_VERB, "pencil", "Open this Player Strategy in the Player tab", self._open_linked_pcs, True),
            CardVerb(REMOVE_VERB, "dismiss", "Remove this assignment", lambda: self.remove_requested.emit(self), True),
            CardVerb(fold, "chevron_up" if self._expanded else "chevron_down", fold, self.toggle_expanded),
        ]

    def _verbs(self) -> list[CardVerb]:
        """What the right-click menu offers now: only the verbs whose face button is live.

        The pencil needs a strategy to open and somewhere to open it; Change and Remove
        need the card to be editable. The menu bypasses the buttons' enabled state, so
        without this a read-only card's right-click could still remove an assignment.
        """
        return [v for v in self._all_verbs() if self._verb_enabled(v.label)]

    def _verb_enabled(self, label: str) -> bool:
        if label == EDIT_VERB:
            return self._can_open_pcs and self._linked_pcs() is not None
        return self._editable or label not in (CHANGE_VERB, REMOVE_VERB)

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

    @property
    def school_picker(self) -> SchoolPickerButton:
        """The schools control in the fold. Public so a test can pick a school as a user would."""
        return self._school_picker

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
            self.grip.set_locked(not editable, "Locked while the bot is running")
        self._pills.set_editable(editable)
        self._school_picker.setEnabled(editable)
        self._note_edit.setEnabled(editable)
        self._sync_verb_buttons()
        self._refresh()

    def set_can_open_pcs(self, can_open: bool) -> None:
        """Gray the pencil when nothing can take it anywhere (Pick has no Player tab)."""
        self._can_open_pcs = can_open
        self._sync_verb_buttons()

    def _sync_verb_buttons(self) -> None:
        # The pencil does not follow ``_editable``: it only navigates, so a locked card
        # still opens its strategy wherever there is a Player tab to open it on. The
        # chevron is never locked: everything the fold reveals is already read-only.
        for verb in self._all_verbs():
            button = self.action_button(verb.label)
            if button is not None:
                if verb.label == EDIT_VERB:
                    button.setEnabled(self._can_open_pcs)
                else:
                    button.setEnabled(self._editable or verb.label not in (CHANGE_VERB, REMOVE_VERB))

    def set_max_players(self, n: int) -> None:
        """Hide slots beyond *n* and clamp ``assignment.players`` to 1..n."""
        self._pills.set_max_players(n)
        filtered = [p for p in self._assignment.players if p <= n]
        if filtered != self._assignment.players:
            self._assignment.players = filtered
            self.changed.emit()

    def set_player_color_keys(self, keys: list[int | None], labels: list[str] | None = None) -> None:
        """Color the pills by the account in each slot — see gui/widgets/pills/player_pills.py."""
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

    @property
    def chevron(self) -> IconButton:
        """The fold's disclosure button. Public so a test can press it and read its glyph."""
        return self._chevron

    def _open_linked_pcs(self) -> None:
        pcs = self._linked_pcs()
        if pcs is not None:
            self.open_pcs_requested.emit(pcs.id)

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
            if pcs is not None and not self._assignment.schools:
                self._assignment.schools = list(pcs.schools)
                self._school_picker.set_value(self._assignment.schools)
            self._assignment.player_combat_strategy_id = None
            self._custom = True
            # Custom DSL is the one answer with nowhere to be typed on the face, so the
            # fold opens itself: choosing it and then having to find the chevron is two
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
            self._school_picker.set_value(self._assignment.schools)
            self._dsl_edit.setPlainText(self._assignment.dsl if custom else "")
            self._dsl_edit.setReadOnly(not self._editable)
            self._dsl_edit.setVisible(custom)
            self._dsl_error_lbl.setVisible(custom and bool(self._dsl_error_lbl.text()))
            self._promote_btn.setVisible(custom and self._editable and bool(self._assignment.dsl.strip()))
            self._details.setVisible(self._expanded)
            self._chevron.set_icon_name("chevron_up" if self._expanded else "chevron_down")
            self._chevron.setToolTip(HIDE_DETAILS if self._expanded else SHOW_DETAILS)
            # Withdrawn rather than grayed, as on ``StrategyBindingCard``: with no linked
            # strategy there is nothing to open, and the button holds its space either way.
            pencil = self.action_button(EDIT_VERB)
            if pencil is not None:
                pencil.setVisible(pcs is not None)
        finally:
            self._suppress = False
        self._update_dsl_validation()
        self.refresh_theme_styles()

    def _restyle_content(self) -> None:
        # The error last shown, not the text's own: a restyle can land mid-edit, and
        # checking the text here parsed it on the GUI thread.
        error = self._shown_error if self.is_custom() else None
        title_color = THEME.error if error else THEME.text
        set_style_sheet(self._title_lbl, f"color: {title_color}; background: transparent;")
        for label in (self._desc_lbl, self._dsl_lbl):
            set_style_sheet(
                label, f"color: {THEME.text_muted}; font-size: {THEME.font_subtext_px}px; background: transparent;"
            )
        set_style_sheet(self._school_label, f"color: {THEME.text_muted}; background: transparent;")
        label_color = THEME.error if error else THEME.syntax_warning
        set_style_sheet(self._dsl_error_lbl, f"color: {label_color}; background: transparent;")
        self._dsl_edit.setFont(THEME.font_mono())
        set_style_sheet(
            self._dsl_edit,
            f"QTextEdit {{ background: {THEME.surface if self._dsl_edit.isReadOnly() else THEME.bg};"
            f" color: {THEME.text}; border: 1px solid {THEME.border};"
            f" border-left: 3px solid {THEME.error if error else THEME.border}; }}",
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
        self._assignment.schools = self._school_picker.value()
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

    def _check_in_background(self) -> None:
        """The debounce's end: check the text off the GUI thread, and show it once known.

        A long DSL took up to 800 ms to parse, and the window froze for it at every pause
        in typing. Known already -- a strategy's saved text, or text typed before -- it is
        shown at once.
        """
        if not self.is_custom() or checker().request(self._dsl_edit.toPlainText()) is not None:
            self._update_dsl_validation()

    def _on_dsl_checked(self, dsl: str) -> None:
        # Only the text still in the box: one typed over since has its own check coming.
        if dsl == self._dsl_edit.toPlainText() and not self._validate_timer.isActive():
            self._update_dsl_validation()

    def _update_dsl_validation(self) -> None:
        """Re-run ``validate_dsl`` and reflect it on the card's face and in the fold.

        Only for a card answering with DSL of its own. A card naming a Player Strategy
        does not mirror an error in DSL it does not own — the duel's own summary carries
        ``INVALID_DSL`` and its card in the list draws the warning, which is where a
        problem with a *shared* strategy belongs.

        The warning mark on the title is what makes the fold safe. Everything else the
        chevron hides is optional; a DSL that does not parse is not, and a card that only
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
        self._shown_error = error
        self._restyle_content()
