"""``StrategyPanel``: one duel-config binding on the Combat tab, drawn as a card.

Replaces the Combat tab's ``_DuelBindingPanel``, and the change is two things at once.

WHAT IT SAYS, WITHOUT BEING OPENED
----------------------------------

The old panel was a summary that had to be expanded to be believed: a chip naming the
strategy, and two muted lines under it -- the facets on one, every player assignment
joined by ``·`` and elided into the other. At a 300px sidebar the second line showed
about one and a half assignments of four. Everything else was behind a disclosure
arrow into a DSL editor.

Here the assignments are one line each, in the card, at rest. Four short lines beat one
long elided one, they are the same rows a player card uses for its live values, and
they are what makes the Combat tab answer *what will my wizards actually do* without
opening anything -- which is the whole point of a Run-mode surface.

A strategy whose DSL does not parse says so on its own name, in ``THEME.error``, the
same way ``StrategyCard`` does in the picker. Both render from one
:class:`~bartlebot.strategy_summary.StrategySummary`, so the card in the tab and the
card in the list cannot describe the same strategy differently.

WHAT IT NO LONGER CARRIES
-------------------------

No disclosure arrow, no inline ``DuelConfigEditor``. Editing combat is a user-owned
edit with an explicit Save/Cancel boundary, and it is moving into a dialog of its own;
a Run-mode surface that autosaved a DSL edit on every keystroke was the one place in
the app that wrote a strategy file while the user was still typing into it. The pencil
opens that dialog, ``CombatStrategyDialog``, as the combat profile's editor.

No ``_GroupRail`` either. The rail was a vertical rule bracketing a panel whose fights
share one strategy, invented because several fight names stacked in a header read as a
list but not necessarily as a list that means "all of these use the strategy on the
right". A card frame already says that -- it is a closed shape around exactly the
fights it governs -- so the rule was a second, weaker drawing of the border sitting
four pixels inside it.

MEMBERSHIP IS A PILL ROW, NOT A FOOTNOTE
----------------------------------------

Which fights a strategy covers is the second thing to know about it, and it used to be
last: an "Applies to" link under four assignment lines, at the foot of the card, with
one full-width row per fight name. Now it is a wrapped row of pills directly under the
title, and the button beside them opens a ``PickerPopup`` listing every exposed fight
with what that fight uses today in the right-hand slot.

A popup rather than the modal that used to do this, and the payload is the reason.
Browsing *fights* is one line each -- a name and a current value -- which is what
``POPUP_WIDTH`` was built for. Browsing *strategies* is seven to nine lines each and
does not fit in 400px at all, which is why picking one is still a dialog. The two are
not the same question and do not get the same widget.

The pills are inert on a strategy's own card. ``TagChip`` wears a ✕ because removing a
*filter* is the entire point of it being on screen; here membership is decided in the
popup, and a second removal route on the row would re-clutter exactly what the pills
declutter. The default card's pills are the exception -- see below.

ONE CARD SHAPE, TWO ROLES
-------------------------

Every card in the Duel Combat Strategies section is the same sentence: *this strategy,
these fights*. That includes the bot default, which is why it is this class in
``PanelRole.DEFAULT`` rather than a widget of its own, and why the section is one heading
rather than the three it used to take -- "Default Duel Combat Strategy" over one card,
then "Specialized Duel Combat Strategies" over the rest, then a plain-text "Using the bot
default" caption over one muted row per unassigned fight. All three said in headings what
the cards underneath them could say themselves.

The default card differs in three ways, each following from the same fact -- that its
membership is a *residue* rather than a choice:

- **A locked ``Default`` badge, first on the pill row.** Once a strategy is picked for it
  the card is titled with that strategy's name like every other one, so something has to
  say which card is the fallback. It also keeps the row from ever being empty: 49 of the
  68 shipped bots with combat expose no fight at all.
- **Its fight pills are live.** Each is an :class:`ActionPill` opening the strategy picker
  for its own fight -- the click the old one-row-per-fight layout took. Giving one fight a
  strategy is still one click on that fight's own name, and the fight leaves this card for
  one of its own the moment it lands. The add-card at the foot of the section is the other
  route in, and is the better one when several fights are to share a new strategy.
- **No "Fights…" button.** There is nowhere to move a fight *to*: unchecking one here
  would be an offer to send it nowhere.

The pills are also not the whole truth on that card, which is what the scope line under
them is for. Only 30 of the shipped library's 133 ``handle_combat`` states are exposed;
the other 103 run the bot default and appear nowhere in this sidebar. On a specialized
card the pills are exhaustive, and one muted line is cheaper than teaching the difference.

THE GUTTER IS THE NESTING INDENT
--------------------------------

``CardFrame`` puts its drag handle in a gutter outside the frame, and a card built
without a handle keeps the gutter so its frame lines up with the ones above it.
``GRIP_GUTTER`` is what indents these cards one level inside their ``SidebarSection``,
the job ``NEST_INDENT_PX`` used to do, and the deck cards take the same indent so both
sections' frames start at one x.

A specialized card has a grip in it, and dragging reorders the cards. The order is the
user's and purely cosmetic -- which fights a card covers decides what they fight with,
never where the card sits -- and it is kept as the order of the strategy's
``state_assignments``. The default card has no grip and takes no drops: it is first
because everything below it is an exception to it, so the tab's ``DragReorder`` is handed
the specialized cards alone and nothing can be dragged above it.

VERBS
-----

Two on the face, everything in the right-click menu, per ``card_frame.py``:

- **Change…** is also the *body* verb. The card is the active configuration and the
  card is how you replace it, so the whole frame is the pick affordance rather than a
  chip inside it that had to be found first.
- **Edit…** (the pencil) is on every card, the built-in default's included. It opens
  the profile editor on this card's strategy, and a strategy made there with New or
  Duplicate is linked to this card on Save -- so pencil, New, Save is the whole of
  giving a card a strategy of its own (#118).
- **Applies to…** is in the menu, and opens the same popup the pill row's own button
  does -- the menu is the keyboard route to a verb, not a second way of performing it.

None of them are implemented here. Each ``run`` is a signal the Combat tab wires, which
is what keeps *which dialog opens* out of the card -- the thing the edit-dialog work
will replace.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.card_frame import CardFrame, CardMode, CardVerb
from bartlebot.gui.widgets.elided_label import ElidedLabel
from bartlebot.gui.widgets.flow_layout import FlowLayout
from bartlebot.gui.widgets.picker_popup import PickerButton, Section
from bartlebot.gui.widgets.player_pills import PlayerBadgeRow
from bartlebot.gui.widgets.tag_pill import AccentPill, ActionPill, NeutralPill
from bartlebot.strategy_summary import HealthStatus, format_combat_order, summarize_duel

if TYPE_CHECKING:
    from bartlebot.bot.strategy_models import DuelCombatStrategy, PlayerCombatStrategy
    from bartlebot.strategy_summary import StrategySummary

#: What the *default* binding falls through to when it is unset.
BUILT_IN_LABEL = "BartleBot Default Combat Strategy"
NOT_CONFIGURED_LABEL = "Not configured — built-in default for every player"

# What an unset *state override* falls through to, which is not the same thing and the
# distinction is not cosmetic: an empty state override does not reach the built-in
# default, it reaches this bot strategy's own Default Combat Strategy -- see
# resolve_combat(), which consults ``bcs.default_strategy_id`` first. Labelling both
# levels "BartleBot Default Combat
# Strategy" told the user the bot default would be skipped, and contradicted the
# pre-run warning text.
STATE_INHERIT_LABEL = "(bot default)"
STATE_INHERIT_META = "No override — uses the default strategy above"

#: The badge on the default card's pill row, and the sentence under it.
#:
#: The badge is what tells the default card from a specialized one once a strategy has
#: been picked for it, both being titled with that strategy's name. It is also what keeps
#: the row from ever being empty: 49 of the 68 shipped bots with combat expose no fight
#: at all, so on those the card would otherwise open with a title and a blank row under
#: it, reading as a strategy that had lost its fights.
#:
#: The sentence is there because the badge cannot say the whole truth. Of 133
#: ``handle_combat`` states in the shipped library only 30 are exposed; the other 103 run
#: the bot default and appear nowhere in this sidebar, so on the four bots that carry both
#: kinds the fight pills are a *partial* list of what this card governs. Every other
#: card's pills are exhaustive, and one line is cheaper than teaching the difference.
DEFAULT_PILL = "Default"
DEFAULT_SCOPE_META = "Runs in every fight that has no strategy of its own"

MISSING_LABEL = "(missing strategy)"

#: Why every verb on a card goes dead mid-run. One string, because the Combat tab's deck
#: cards say it too and two surfaces giving a user two different reasons for the same
#: lock is how a lock stops being believed.
LOCKED = "Locked while the bot is running"

#: Verb labels, so the tab and the tests name a verb by the same string the menu does.
CHANGE = "Change…"
EDIT_STRATEGY = "Edit…"
APPLIES_TO = "Applies to…"

#: How many player assignments a duel config can carry, and so how many caption lines a
#: card builds once and hides down to. Four, because a Wizard101 team is four wizards --
#: ``DuelConfig.max_players`` is bounded at 4 by the game, not by this widget.
_MAX_ASSIGNMENT_LINES = 4

#: What the fights button says. Fixed, rather than ``PickerButton``'s default summary of
#: the selection, because the pills beside it already are that summary -- a button
#: reading "3 selected" next to three pills naming them is the same fact twice.
FIGHTS_BUTTON = "Fights…"


class PanelRole(Enum):
    """Which of the two jobs a card in the Duel Combat Strategies section is doing.

    They were two classes and two sections -- a ``StrategyPanel`` under "Default Duel
    Combat Strategy" and a ``BotDefaultPanel`` at the foot of "Specialized Duel Combat
    Strategies" -- which meant three headings for a list whose every entry is the same
    sentence: *this strategy, these fights*. The difference between them is real but
    small enough to be a field.
    """

    #: The bot default. Governs every fight no other card claimed, including the combat
    #: states this sidebar never shows, so its membership is a residue rather than a
    #: choice: it wears a locked ``Default`` badge, its fight pills are live shortcuts
    #: out of it, and there is no "Fights…" button because there is nowhere to move a
    #: fight *to* -- unchecking one here would be an offer to send it nowhere.
    DEFAULT = auto()

    #: A strategy someone picked for a named set of fights. Membership is the whole point
    #: of it, so it gets the "Fights…" popup and its pills are inert.
    SPECIALIZED = auto()


class _FightsButton(PickerButton):
    """The pill row's anchor: opens the list of fights this strategy could cover.

    A ``PickerPopup`` rather than a modal, and the payload is why. Browsing *fights*
    is one line each -- a name, and what that fight uses today in the right-hand slot
    -- which is what 400px was built for. Browsing *strategies* is seven to nine lines
    each and does not fit, which is why picking one is still a dialog.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Applies to", search_placeholder="Filter fights…", parent=parent)
        self.setToolTip("Choose which fights use this strategy")

    def format_text(self, labels: list[str]) -> str:
        return FIGHTS_BUTTON


class StrategyPanel(CardFrame):
    """One binding -- the bot default, or one group of fights -- as a card.

    Operates on any ``binding`` shaped like ``StateAssignment``:
    ``duel_combat_strategy_id`` (settable) and ``is_linked``. The Combat tab's
    ``_DefaultBinding`` adapts a ``BotCombatStrategy``'s own default fields to that
    shape, so one class draws both levels.

    :class:`PanelRole` is the only thing that differs between the two, and it decides
    three things: whether the pill row opens with a locked ``Default`` badge, whether its
    fight pills are live or inert, and whether there is a "Fights…" button at the end of
    it. See the module docstring for why each follows from the other.
    """

    #: Its own name, so these rules cannot collide with ``#PlayerCard`` or ``#LaunchCard``.
    FRAME_NAME = "StrategyPanel"

    pick_requested = pyqtSignal()
    #: The pencil: open this card's strategy in the profile editor, or — on a card with
    #: none, the built-in default included — open the editor to make one for it (#118).
    edit_requested = pyqtSignal()
    #: The complete desired membership, once the popup has closed -- not per toggle.
    #: Acting on each toggle would rebuild this card's own section while its popup is
    #: still open on top of it; see ``PickerButton.committed``.
    membership_changed = pyqtSignal(list)  # list[str] of state_ids
    #: ``DEFAULT`` role only: one of this card's fight pills was clicked, so that fight
    #: wants a Duel Combat Strategy of its own.
    fight_pick_requested = pyqtSignal(str)  # state_id

    def __init__(
        self,
        empty_label: str = BUILT_IN_LABEL,
        empty_meta: str = NOT_CONFIGURED_LABEL,
        parent: QWidget | None = None,
        role: PanelRole = PanelRole.SPECIALIZED,
    ) -> None:
        # MANAGE, not MEMBER: the body *is* a verb here -- clicking the card changes the
        # strategy -- so it takes the pointing-hand cursor and the hover highlight that
        # promise it. MANAGE is also what pins the face buttons open, which is right on a
        # sidebar that exists to be read: hiding the verbs behind hover on a surface whose
        # job is to run them is backwards (see ``pins_chrome``).
        super().__init__(parent, mode=CardMode.MANAGE)
        self._binding = None
        self._all_dcs: list[DuelCombatStrategy] = []
        self._all_pcs: list[PlayerCombatStrategy] = []
        self._editable = True
        self._empty_label = empty_label
        self._empty_meta = empty_meta
        self._role = role
        # False on the default card, which has no state_ids to edit: it holds whatever no
        # other card claimed, so there is nothing for a picker to choose between.
        self._supports_membership = role is PanelRole.SPECIALIZED
        # Whether the name currently reports a DSL that does not parse. Held rather than
        # re-derived because the theme pass needs it and does not have the summary.
        self._invalid = False
        self._fights: list[tuple[str, str]] = []

        self._build()
        self.activated.connect(self.pick_requested)
        self.refresh_theme_styles()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        # The default card is locked to the top, so it alone has no grip. The gutter is
        # kept either way, and is what indents the card inside its section -- see the
        # module docstring.
        layout = self._build_frame("strategies", draggable=self._role is PanelRole.SPECIALIZED)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        # Elided, and not a plain QLabel: strategy names are user-supplied and routinely
        # long ("Heretical Academy 4-player single decks"), and a QLabel reports its full
        # text width as its minimum, which a widgetResizable QScrollArea honours -- one
        # long name would put a horizontal scrollbar across the whole sidebar.
        self._name = ElidedLabel()
        font = self._name.font()
        font.setBold(True)
        self._name.setFont(font)
        header.addWidget(self._name, 1)
        # Built here rather than through ``_add_face_verbs``, which reads ``_verbs()``.
        # ``CardFrame`` builds a card's face buttons exactly once, and this card's verb
        # list is not fixed -- "Applies to…" depends on the binding, which is replaced
        # under the card on every pick -- so the two face buttons are made once from
        # ``_face_verbs`` and ``_verbs()`` stays dynamic for the right-click menu, which
        # ``contextMenuEvent`` rebuilds per click. ``refresh`` retunes the pencil's
        # tooltip, which says something different on a card with no strategy.
        for verb in self._face_verbs():
            header.addWidget(self._make_verb_button(verb), 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        # Directly under the title, because which fights a strategy covers is the
        # second thing to know about it and the old arrangement put it last -- a
        # "Applies to" link under four assignment lines, at the bottom of the card,
        # where it read as a footnote rather than as the scope of everything above it.
        self._pill_row = QWidget()
        self._pill_layout = FlowLayout(self._pill_row, spacing=4)
        self._pills: list[NeutralPill | ActionPill] = []
        self._default_pill: AccentPill | None = None
        self._fights_btn: _FightsButton | None = None

        if self._role is PanelRole.DEFAULT:
            # First, and never removed. It is what identifies this card once it is titled
            # with a strategy name like every other one, and it is what keeps the row from
            # being empty on a bot that exposes no fights -- which is most of them.
            self._default_pill = AccentPill(DEFAULT_PILL, parent=self._pill_row)
            self._default_pill.setToolTip(DEFAULT_SCOPE_META)
            self._pill_layout.addWidget(self._default_pill)
        else:
            # Inert pills, not chips with a ✕. ``TagChip`` wears one because removing a
            # *filter* is the whole point of it being on screen; here membership is decided
            # in the popup, and a second removal route on the row would re-clutter exactly
            # what the pills are here to declutter.
            self._fights_btn = _FightsButton(parent=self._pill_row)
            self._fights_btn.committed.connect(self.membership_changed)
            self._pill_layout.addWidget(self._fights_btn)
        layout.addWidget(self._pill_row)
        self._pill_row.setVisible(self._role is PanelRole.DEFAULT)

        # What the badge cannot fit: that this card also governs every combat state the
        # sidebar never lists. Only on the default card, and always -- see
        # ``DEFAULT_SCOPE_META``.
        self._scope = self._sub_line(layout)
        if self._role is PanelRole.DEFAULT:
            self._scope.setText(DEFAULT_SCOPE_META)
            self._scope.setToolTip(DEFAULT_SCOPE_META)
            self._scope.setVisible(True)

        # The facets -- "4 players ·" then the combat order as P4 ← P3 ← P2 ← P1 pills -- or,
        # when nothing is configured, the sentence naming what this binding falls through
        # to instead. The order is pills rather than text so each slot wears its account's
        # colour (#40); "Simultaneous" has no slots and stays a word.
        #
        # ``duel_order`` mirrors the row and gems each position, so this summary and the
        # editor that sets the value are laid out the same way round (#29). The arrow turns
        # with the row and keeps meaning "then": it runs from each player to the one who
        # enters after them, which once the row is mirrored is leftwards.
        self._meta_row, self._meta, self._order_badges = self._badge_line(
            layout, separator="←", badges_first=False, duel_order=True
        )

        # One line per player assignment, built to the maximum a duel config can hold and
        # then hidden down to what it has. Reused rather than created per refresh: the
        # binding changes under this card on every pick, and destroying widgets mid-
        # refresh is how the old per-state rebuild interrupted edits in progress. The
        # players lead each line as pills, for the same reason the order is pills.
        self._assignment_rows: list[QWidget] = []
        self._assignment_lbls: list[ElidedLabel] = []
        self._assignment_badges: list[PlayerBadgeRow] = []
        for _ in range(_MAX_ASSIGNMENT_LINES):
            row, label, badges = self._badge_line(layout)
            self._assignment_rows.append(row)
            self._assignment_lbls.append(label)
            self._assignment_badges.append(badges)
        # Per player slot, index 0 being P1. Held so a refresh re-colours what it redraws.
        self._color_keys: list[int | None] = []
        self._account_labels: list[str] = []

    def _sub_line(self, layout: QVBoxLayout) -> ElidedLabel:
        """One muted caption inside the frame, elided to whatever width the sidebar has.

        Elided rather than wrapped: these lines summarise DSL that runs to well over a
        hundred characters, and wrapping would trade the horizontal scrollbar for three
        or four stacked lines per card -- the opposite of what a summary is for. The full
        text is always in the tooltip.
        """
        label = ElidedLabel()
        layout.addWidget(label)
        label.setVisible(False)
        return label

    def _badge_line(
        self, layout: QVBoxLayout, *, separator: str = "", badges_first: bool = True, duel_order: bool = False
    ) -> tuple[QWidget, ElidedLabel, PlayerBadgeRow]:
        """A caption line with a row of player pills in it, hidden until it is filled.

        The row, not the label, is what is shown and hidden, so a line with nothing in it
        takes no spacing in the card. The label is elided exactly as ``_sub_line``'s is;
        where the pills come after it, a stretch keeps them beside the text rather than
        pushed to the card's far edge.
        """
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)
        label = ElidedLabel()
        badges = PlayerBadgeRow(separator, duel_order=duel_order)
        if badges_first:
            row_layout.addWidget(badges, 0, Qt.AlignmentFlag.AlignVCenter)
            row_layout.addWidget(label, 1)
        else:
            row_layout.addWidget(label, 0)
            row_layout.addWidget(badges, 0, Qt.AlignmentFlag.AlignVCenter)
            row_layout.addStretch(1)
        layout.addWidget(row)
        row.setVisible(False)
        return row, label, badges

    def set_player_colors(self, keys: list[int | None], labels: list[str]) -> None:
        """Colour every player pill on the card by the account in that slot right now.

        ``keys`` and ``labels`` are per slot, index 0 being P1; shorter lists leave the
        rest uncoloured. Held on the card, so a pick that redraws the lines keeps them.
        """
        self._color_keys = list(keys)
        self._account_labels = list(labels)
        for badges in (self._order_badges, *self._assignment_badges):
            badges.set_player_colors(self._color_keys, self._account_labels)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def load(self, binding, all_dcs: list[DuelCombatStrategy], all_pcs: list[PlayerCombatStrategy]) -> None:
        self._binding = binding
        self._all_dcs = all_dcs
        self._all_pcs = all_pcs
        self.refresh()

    def set_libraries(self, all_dcs: list[DuelCombatStrategy], all_pcs: list[PlayerCombatStrategy]) -> None:
        """Update the reference lists without replacing this widget."""
        self._all_dcs = all_dcs
        self._all_pcs = all_pcs
        self.refresh()

    def set_fights(self, fights: list[tuple[str, str]]) -> None:
        """Name every fight this card governs, as a pill each, ``(state_id, label)``.

        Pills rather than the stacked lines this used to draw: a group of four fights
        cost four full-width rows at the bottom of the card, and the four names are
        short enough to sit on one wrapped row. ``FlowLayout`` is what makes that safe
        at a 300px sidebar -- they wrap rather than widening the panel.

        On the default card the pills are live: each opens the strategy picker for its
        own fight, which is the click the old one-row-per-unassigned-fight layout took.
        Nothing is lost by condensing them -- giving one fight a strategy is still one
        click on that fight's own name, and the fight leaves this card for one of its own
        the moment it lands. On a specialized card they are inert, membership there being
        the "Fights…" popup's question.

        Surplus pills are hidden rather than destroyed, for the reason the assignment
        lines are: membership changes while the card is on screen, and a card that
        rebuilds its own children during a refresh can delete the widget a click is
        still travelling to.
        """
        self._fights = list(fights)
        while len(self._pills) < len(self._fights):
            self._pills.append(self._add_pill())
        for pill, (state_id, label) in zip(self._pills, self._fights):
            if isinstance(pill, ActionPill):
                pill.set_pill(label, state_id)
                pill.setToolTip(f"{label} — {DEFAULT_SCOPE_META.lower()}. Click to give it one of its own.")
                pill.setEnabled(self._editable)
            else:
                pill.setText(label)
            pill.show()
        for pill in self._pills[len(self._fights) :]:
            pill.hide()
        self._sync_pill_row()
        self.refresh_theme_styles()

    def _add_pill(self) -> NeutralPill | ActionPill:
        """One more pill, of whichever kind this card's role calls for.

        Inserted after the badge and before the "Fights…" button -- neither of which any
        card has both of. The button stays last so it does not move as pills come and go:
        a control that walks along the row is one you have to find again after every
        change. The badge stays first for the same reason.
        """
        if self._role is PanelRole.DEFAULT:
            pill: NeutralPill | ActionPill = ActionPill()
            pill.activated.connect(self.fight_pick_requested)
        else:
            pill = NeutralPill("")
        self._pill_layout.insertWidget(len(self._pills) + (1 if self._default_pill is not None else 0), pill)
        return pill

    def fights(self) -> list[tuple[str, str]]:
        """What this card governs, as ``(state_id, label)``. Public for the tab and tests."""
        return list(self._fights)

    def titles(self) -> list[str]:
        """Just the labels, in order -- what a reader sees on the pill row."""
        return [label for _state_id, label in self._fights]

    def open_fights_popup(self) -> None:
        """Open the membership popup as a click on the "Fights…" button would.

        The add flow's second half: a card created for a freshly picked strategy has no
        fights yet, and this is what asks for them without the user having to find the
        button on a card that appeared under the pointer a moment ago.
        """
        if self._fights_btn is not None:
            self._fights_btn.click()

    def set_fight_options(self, sections: list[Section], selected: list[str]) -> None:
        """The fights this strategy *could* cover, and the ones it does.

        Built by the tab rather than here: the vocabulary is every exposed fight in the
        bot plus what each one uses today, and only the tab knows either. The card just
        shows them.

        A no-op on the default card, which has no button to hang them on.
        """
        if self._fights_btn is None:
            return
        self._fights_btn.set_sections(sections)
        self._fights_btn.set_selection(selected)

    @property
    def binding(self):
        """The ``StateAssignment``-shaped object this card draws, or None.

        Public because the tab acts on it -- committing a pick, editing membership --
        and reaching into a card for it would be exactly the private-attribute access
        the root CLAUDE.md forbids across class boundaries.
        """
        return self._binding

    @property
    def empty_label(self) -> str:
        """What this card falls through to when nothing is picked.

        The picker's "none" row is labelled with it, so choosing nothing in the dialog
        and reading the card afterwards say the same words -- and the two levels say
        *different* words, which is the distinction ``STATE_INHERIT_LABEL`` exists for.
        """
        return self._empty_label

    @property
    def linked_id(self) -> str | None:
        """The DuelCombatStrategy this binding names, or None when it names none."""
        if self._binding is None or not self._binding.is_linked:
            return None
        return self._binding.duel_combat_strategy_id

    def set_editable(self, editable: bool) -> None:
        """Lock or unlock every way this card can change the configuration.

        The body verb goes with them. A card that still highlights on hover and still
        takes a click while the bot is running is a card promising a pick it will then
        drop on the floor.
        """
        self._editable = editable
        for button in self.action_buttons:
            button.setEnabled(editable)
        if self._fights_btn is not None:
            self._fights_btn.setEnabled(editable)
        # The default card's pills are its only verb, so they lock with the rest.
        for pill in self._pills:
            if isinstance(pill, ActionPill):
                pill.setEnabled(editable)
        self.setCursor(Qt.CursorShape.PointingHandCursor if editable else Qt.CursorShape.ArrowCursor)
        self.setToolTip("" if editable else LOCKED)
        if self.grip is not None:
            self.grip.set_locked(not editable, LOCKED)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Redraw every line from the binding as it currently stands."""
        if self._binding is None:
            self.setVisible(False)
            return
        self.setVisible(True)

        if self._binding.is_linked:
            dcs = next((d for d in self._all_dcs if d.id == self.linked_id), None)
            if dcs is None:
                # A reference to a file that is gone. Named rather than blanked: the
                # binding still resolves to nothing at run time and the pre-run warning
                # will say so, but the card is where a user can act on it.
                self._render_bare(MISSING_LABEL, "This strategy is no longer in the library", invalid=True)
            else:
                self._render_summary(dcs.name, summarize_duel(dcs, self._all_pcs))
        else:
            self._render_bare(self._empty_label, self._empty_meta)

        # Always shown, even on a card running the built-in default: the profile editor
        # is where a card gets a strategy made for it, and New there, then Save, is the
        # whole of that (#118). What the tooltip promises is what differs.
        edit_btn = self.action_button(EDIT_STRATEGY)
        if edit_btn is not None:
            edit_btn.setToolTip(self._edit_tooltip())
        self._sync_pill_row()
        self.refresh_theme_styles()

    def _render_bare(self, name: str, meta: str, invalid: bool = False) -> None:
        """A card with no configuration behind it: a name, one sentence, no assignments."""
        self._invalid = invalid
        self._name.setText(name)
        self._name.setToolTip(name)
        self._meta.setText(meta)
        self._order_badges.set_players([])
        self._meta_row.setVisible(True)
        for row in self._assignment_rows:
            row.setVisible(False)

    def _sync_pill_row(self) -> None:
        """Show the pill row wherever it has something to say.

        Always on the default card: the badge is permanent, so the row is never empty
        even on the 49 shipped bots that expose no fight for it to list. On a specialized
        card, only once it holds a strategy -- a group of fights sharing nothing is what
        the next rebuild pulls apart, and its fights belong on the default card rather
        than on one of their own.
        """
        self._pill_row.setVisible(self._role is PanelRole.DEFAULT or self._membership_allowed())

    def _render_summary(self, name: str, summary: StrategySummary) -> None:
        """Name, facets and one line per player assignment, all from one summary."""
        self._invalid = summary.health.status is HealthStatus.INVALID_DSL
        if self._invalid:
            # The same mark and the same reason ``StrategyCard`` uses, so a strategy that
            # does not parse looks identical wherever the user meets it.
            self._name.setText(f"{name}  ⚠")
            self._name.setToolTip(f"Does not parse: {summary.health.reason}")
        else:
            self._name.setText(name)
            self._name.setToolTip(summary.description or name)

        lines = summary.lines[:_MAX_ASSIGNMENT_LINES]
        # The order facet is drawn as pills after the text, so it leaves the text; the
        # words stay in the tooltip. "Simultaneous" has no slots and is kept as a word.
        order_text = format_combat_order(summary.combat_order) if summary.combat_order else None
        parts = [facet for facet in summary.facets if facet != order_text]
        if not lines:
            # Said out loud rather than left blank. A duel config with a player range and
            # a combat order but no assignments runs the built-in DSL for everyone, which
            # is a real configuration and not an empty card.
            parts.append("no player assignments")
        text = " · ".join(parts)
        self._meta.setText(f"{text} ·" if text and summary.combat_order else text)
        self._meta.setToolTip(" · ".join([*summary.facets, *([] if lines else ["no player assignments"])]))
        self._order_badges.set_players(summary.combat_order)
        self._meta_row.setVisible(bool(parts) or bool(summary.combat_order))

        for row, label, badges, line in zip(
            self._assignment_rows, self._assignment_lbls, self._assignment_badges, lines
        ):
            badges.set_players(line.slots)
            label.setText(line.label)
            row.setVisible(True)
        for row in self._assignment_rows[len(lines) :]:
            row.setVisible(False)

    def _membership_allowed(self) -> bool:
        """Whether "Applies to…" can act yet.

        Nothing to share until a card has a strategy: a group of fights holding no config
        is exactly what the grouping pulls back apart on the next rebuild, so offering it
        would look like an edit that quietly undid itself.
        """
        if not self._supports_membership or self._binding is None:
            return False
        return self._binding.is_linked

    # ------------------------------------------------------------------
    # Verbs
    # ------------------------------------------------------------------

    def _face_verbs(self) -> list[CardVerb]:
        """The two verbs that get a button, in header order. Fixed for the card's life."""
        return [
            CardVerb(CHANGE, "search", "Pick a different Duel Combat Strategy", self.pick_requested.emit, on_face=True),
            CardVerb(EDIT_STRATEGY, "pencil", self._edit_tooltip(), self.edit_requested.emit, on_face=True),
        ]

    def _edit_tooltip(self) -> str:
        if self.linked_id is not None:
            return "Edit this strategy, or make one of this profile's own for these fights"
        return "Make a strategy of this profile's own for these fights"

    def _verbs(self) -> list[CardVerb]:
        """Everything this card can do *right now* -- what the right-click menu offers.

        Rebuilt per click, so it names only what applies. The two face buttons are built
        from ``_face_verbs`` instead; see ``_build``.
        """
        verbs = self._face_verbs()
        if self._membership_allowed() and self._fights_btn is not None:
            # Opens the same popup the pill row's button does, rather than a second
            # membership surface of its own -- the menu is the keyboard route to a verb,
            # not an alternative way of performing it.
            verbs.append(
                CardVerb(APPLIES_TO, "attach", "Choose which fights use this strategy", self._fights_btn.click)
            )
        return verbs

    def _refuses_activation(self) -> bool:
        """Swallow a body click while the bot is running.

        The whole frame is the pick affordance, so there is no ``QPushButton`` here whose
        disabled state would do it -- the card has to refuse for itself, the same way a
        launch card that will not fit does.
        """
        return not self._editable

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _restyle_content(self) -> None:
        """Recolour the name and every caption -- see :func:`_set_text_color`."""
        _set_text_color(self._name, THEME.error if self._invalid else THEME.text)
        for label in (self._scope, self._meta, *self._assignment_lbls):
            _set_text_color(label, THEME.text_muted)


def _set_text_color(label: ElidedLabel, color: str) -> None:
    """Recolour an ``ElidedLabel``, which paints its own text.

    A QSS ``color:`` rule never reaches one, so the colour has to go through the
    palette -- which is why the cards here restyle by looping over palettes rather than
    by the single stylesheet write every other card does.
    """
    palette = label.palette()
    palette.setColor(label.foregroundRole(), QColor(color))
    label.setPalette(palette)
