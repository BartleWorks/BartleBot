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
the app that wrote a strategy file while the user was still typing into it. Until that
dialog exists, ``Edit in Library…`` is the way in -- which is the route a *linked*
binding already offered, so it is not a new path, only the only one.

No ``_GroupRail`` either. The rail was a vertical rule bracketing a panel whose fights
share one strategy, invented because several fight names stacked in a header read as a
list but not necessarily as a list that means "all of these use the strategy on the
right". A card frame already says that -- it is a closed shape around exactly the
fights it governs -- so the rule was a second, weaker drawing of the border sitting
four pixels inside it.

THE GUTTER IS THE NESTING INDENT
--------------------------------

``CardFrame`` puts its drag handle in a gutter outside the frame, and a card built
without a handle keeps the gutter so its frame lines up with the ones above it. Nothing
here is draggable -- the order of these cards is the order the fights group in, not a
user's -- so every card passes ``draggable=False`` and the gutter is empty. It is not
waste: ``GRIP_GUTTER`` is what indents these cards one level inside their
``SidebarSection``, the job ``NEST_INDENT_PX`` used to do, and the deck cards take the
same indent so both sections' frames start at one x.

VERBS
-----

Two on the face, everything in the right-click menu, per ``card_frame.py``:

- **Change…** is also the *body* verb. The card is the active configuration and the
  card is how you replace it, so the whole frame is the pick affordance rather than a
  chip inside it that had to be found first.
- **Edit in Library…** appears only on a linked binding, there being nothing in the
  library to open otherwise.
- **Applies to…** is in the menu, and is *also* the "Applies to" line in the card body:
  the label naming which fights a card governs and the control that edits them are one
  thing, and giving them one vocabulary is cheaper than an icon nobody can read.
- **Save as preset…** is menu-only. It can only apply to an inline config, which this
  surface can no longer create -- it is how a config from an older bot file gets into
  the library so that it can be picked like everything else.

None of them are implemented here. Each ``run`` is a signal the Combat tab wires, which
is what keeps *which dialog opens* out of the card -- the thing the edit-dialog work
will replace.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.card_frame import CardFrame, CardMode, CardVerb
from bartlebot.gui.widgets.elided_label import ElidedLabel
from bartlebot.gui.widgets.link_button import LinkButton
from bartlebot.strategy_summary import HealthStatus, summarize_duel

if TYPE_CHECKING:
    from bartlebot.bot.strategy_models import DuelCombatStrategy, PlayerCombatStrategy
    from bartlebot.strategy_summary import StrategySummary

#: What the *default* binding falls through to when it is unset.
BUILT_IN_LABEL = "BartleBot Default Combat Strategy"
NOT_CONFIGURED_LABEL = "Not configured — built-in default for every player"

# What an unset *state override* falls through to, which is not the same thing and the
# distinction is not cosmetic: an empty state override does not reach the built-in
# default, it reaches this bot strategy's own Default Combat Strategy -- see
# resolve_combat(), which consults ``bcs.default_strategy_id`` /
# ``bcs.default_duel_config`` first. Labelling both levels "BartleBot Default Combat
# Strategy" told the user the bot default would be skipped, and contradicted the
# pre-run warning text.
STATE_INHERIT_LABEL = "(bot default)"
STATE_INHERIT_META = "No override — uses the Default Duel Combat Strategy above"

MISSING_LABEL = "(missing strategy)"
CUSTOM_LABEL = "(custom)"

#: Why every verb on a card goes dead mid-run. One string, because the Combat tab's deck
#: cards say it too and two surfaces giving a user two different reasons for the same
#: lock is how a lock stops being believed.
LOCKED = "Locked while the bot is running"

#: Verb labels, so the tab and the tests name a verb by the same string the menu does.
CHANGE = "Change…"
EDIT_IN_LIBRARY = "Edit in Library…"
APPLIES_TO = "Applies to…"
SAVE_AS_PRESET = "Save as preset…"

#: How many player assignments a duel config can carry, and so how many caption lines a
#: card builds once and hides down to. Four, because a Wizard101 team is four wizards --
#: ``DuelConfig.max_players`` is bounded at 4 by the game, not by this widget.
_MAX_ASSIGNMENT_LINES = 4


class StrategyPanel(CardFrame):
    """One binding -- the bot default, or one group of fights -- as a card.

    Operates on any ``binding`` shaped like ``StateAssignment``: ``duel_config``,
    ``duel_combat_strategy_id`` (settable) and ``is_linked``. The Combat tab's
    ``_DefaultBinding`` adapts a ``BotCombatStrategy``'s own default fields to that
    shape, so one class draws both levels.
    """

    #: Its own name, so these rules cannot collide with ``#PlayerCard`` or ``#LaunchCard``.
    FRAME_NAME = "StrategyPanel"

    pick_requested = pyqtSignal()
    edit_in_library_requested = pyqtSignal(str)  # dcs_id
    membership_requested = pyqtSignal()
    promote_requested = pyqtSignal()

    def __init__(
        self,
        empty_label: str = BUILT_IN_LABEL,
        empty_meta: str = NOT_CONFIGURED_LABEL,
        parent: QWidget | None = None,
        supports_membership: bool = False,
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
        # False for the Default Duel Combat Strategy, which has no state_ids to edit: it
        # is the fallback for every fight no group claims.
        self._supports_membership = supports_membership
        # Whether the name currently reports a DSL that does not parse. Held rather than
        # re-derived because the theme pass needs it and does not have the summary.
        self._invalid = False
        self._titles: list[str] = []

        self._build()
        self.activated.connect(self.pick_requested)
        self.refresh_theme_styles()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        # draggable=False: these cards are ordered by how their fights group, which is
        # not a fact any drag could change. The gutter it keeps is what indents the card
        # one level inside its section -- see the module docstring.
        layout = self._build_frame("strategies", draggable=False)

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
        # list is not fixed: whether "Edit in Library…" applies depends on whether the
        # binding is linked, and the binding is replaced under the card on every pick. So
        # both buttons are made once and ``refresh`` shows or hides the second, while
        # ``_verbs()`` stays dynamic for the right-click menu -- which ``contextMenuEvent``
        # rebuilds per click and can therefore be honest about what applies right now.
        # Hiding costs no layout: a face button holds its space (``retainSizeWhenHidden``),
        # so the name does not lengthen and re-shorten as bindings change under it.
        for verb in self._face_verbs():
            header.addWidget(self._make_verb_button(verb), 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        # The facets -- "4 players · P1→P2→P3→P4" -- or, when nothing is configured, the
        # sentence naming what this binding falls through to instead.
        self._meta = self._sub_line(layout)

        # One line per player assignment, built to the maximum a duel config can hold and
        # then hidden down to what it has. Reused rather than created per refresh: the
        # binding changes under this card on every pick, and destroying widgets mid-
        # refresh is how the old per-state rebuild interrupted edits in progress.
        self._assignment_lbls = [self._sub_line(layout) for _ in range(_MAX_ASSIGNMENT_LINES)]

        # "Applies to  Defeat Balaur" -- the fights this card governs, and the way to
        # change them. Hidden entirely on the default panel, which governs whatever is
        # left over rather than a list anyone chose.
        self._applies_row = QWidget()
        applies_layout = QHBoxLayout(self._applies_row)
        applies_layout.setContentsMargins(0, 2, 0, 0)
        applies_layout.setSpacing(4)
        self._applies_btn = LinkButton("Applies to", font_size=THEME.font_card_sub_size)
        self._applies_btn.setToolTip("Choose which fights use this strategy")
        self._applies_btn.clicked.connect(self.membership_requested)
        applies_layout.addWidget(self._applies_btn, 0, Qt.AlignmentFlag.AlignTop)
        self._titles_layout = QVBoxLayout()
        self._titles_layout.setContentsMargins(0, 0, 0, 0)
        self._titles_layout.setSpacing(0)
        applies_layout.addLayout(self._titles_layout, 1)
        self._title_lbls: list[ElidedLabel] = []
        layout.addWidget(self._applies_row)
        self._applies_row.setVisible(False)

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

    def set_titles(self, titles: list[str]) -> None:
        """Name every fight this card governs, one per line beside "Applies to".

        Surplus labels are hidden rather than destroyed: membership changes while the
        card is on screen, and a card that rebuilds its own children during a refresh is
        a card that can delete the widget a click is still travelling to.
        """
        self._titles = list(titles)
        while len(self._title_lbls) < len(self._titles):
            label = ElidedLabel()
            self._titles_layout.addWidget(label)
            self._title_lbls.append(label)
        for label, text in zip(self._title_lbls, self._titles):
            label.setText(text)
            label.show()
        for label in self._title_lbls[len(self._titles) :]:
            label.hide()
        self._applies_row.setVisible(bool(self._titles) and self._supports_membership)
        self.refresh_theme_styles()

    def titles(self) -> list[str]:
        return list(self._titles)

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
        """The library DuelCombatStrategy this binding names, or None if it is inline."""
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
        self._applies_btn.setEnabled(editable and self._membership_allowed())
        self.setCursor(Qt.CursorShape.PointingHandCursor if editable else Qt.CursorShape.ArrowCursor)
        self.setToolTip("" if editable else LOCKED)

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
            config = self._binding.duel_config
            if config.is_empty():
                self._render_bare(self._empty_label, self._empty_meta)
            else:
                self._render_summary(CUSTOM_LABEL, summarize_duel(config, self._all_pcs))

        # Withdrawn rather than greyed. A disabled pencil on an inline config would be an
        # affordance offered and refused every time; there is simply nothing in the
        # library to open, and the button holds its space either way.
        edit_btn = self.action_button(EDIT_IN_LIBRARY)
        if edit_btn is not None:
            edit_btn.setVisible(self.linked_id is not None)
        self._applies_btn.setEnabled(self._editable and self._membership_allowed())
        self.refresh_theme_styles()

    def _render_bare(self, name: str, meta: str, invalid: bool = False) -> None:
        """A card with no configuration behind it: a name, one sentence, no assignments."""
        self._invalid = invalid
        self._name.setText(name)
        self._name.setToolTip(name)
        self._meta.setText(meta)
        self._meta.setVisible(True)
        for label in self._assignment_lbls:
            label.setVisible(False)

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

        self._meta.setText(" · ".join(summary.facets))
        self._meta.setVisible(bool(summary.facets))

        lines = summary.lines[:_MAX_ASSIGNMENT_LINES]
        for label, line in zip(self._assignment_lbls, lines):
            label.setText(f"{line.players}: {line.label}" if line.players else line.label)
            label.setVisible(True)
        for label in self._assignment_lbls[len(lines) :]:
            label.setVisible(False)
        if not lines:
            # Said out loud rather than left blank. A duel config with a player range and
            # a combat order but no assignments runs the built-in DSL for everyone, which
            # is a real configuration and not an empty card.
            self._meta.setText(" · ".join([*summary.facets, "no player assignments"]))
            self._meta.setVisible(True)

    def _membership_allowed(self) -> bool:
        """Whether "Applies to…" can act yet.

        Nothing to share until a card has a strategy: a group of fights holding no config
        is exactly what the grouping pulls back apart on the next rebuild, so offering it
        would look like an edit that quietly undid itself.
        """
        if not self._supports_membership or self._binding is None:
            return False
        return self._binding.is_linked or not self._binding.duel_config.is_empty()

    # ------------------------------------------------------------------
    # Verbs
    # ------------------------------------------------------------------

    def _face_verbs(self) -> list[CardVerb]:
        """The two verbs that get a button, in header order. Fixed for the card's life."""
        return [
            CardVerb(CHANGE, "search", "Pick a different Duel Combat Strategy", self.pick_requested.emit, on_face=True),
            CardVerb(
                EDIT_IN_LIBRARY,
                "pencil",
                "Open this strategy in the Strategy Library",
                self._emit_edit_in_library,
                on_face=True,
            ),
        ]

    def _verbs(self) -> list[CardVerb]:
        """Everything this card can do *right now* -- what the right-click menu offers.

        Rebuilt per click, so it names only what applies: no "Edit in Library…" for a
        config that is not in the library, and no "Save as preset…" for one that already
        is. The two face buttons are built from ``_face_verbs`` instead; see ``_build``.
        """
        verbs = [self._face_verbs()[0]]
        # Only on a linked binding: there is nothing in the library to open for a config
        # that lives inside this bot's own strategy file.
        if self.linked_id is not None:
            verbs.append(self._face_verbs()[1])
        if self._membership_allowed():
            verbs.append(
                CardVerb(APPLIES_TO, "attach", "Choose which fights use this strategy", self.membership_requested.emit)
            )
        if self._binding is not None and not self._binding.is_linked and not self._binding.duel_config.is_empty():
            verbs.append(
                CardVerb(
                    SAVE_AS_PRESET,
                    "save_done",
                    "Copy this configuration into the library",
                    self.promote_requested.emit,
                )
            )
        return verbs

    def _emit_edit_in_library(self) -> None:
        dcs_id = self.linked_id
        if dcs_id is not None:
            self.edit_in_library_requested.emit(dcs_id)

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
        """Recolour the name and every caption.

        ``ElidedLabel`` paints its own text, so a QSS ``color:`` rule never reaches it --
        the colour has to go through the palette. That is why this is a loop over
        palettes rather than the stylesheet write every other card does.
        """
        self._set_text_color(self._name, THEME.error if self._invalid else THEME.text)
        for label in (self._meta, *self._assignment_lbls, *self._title_lbls):
            self._set_text_color(label, THEME.text_muted)

    @staticmethod
    def _set_text_color(label: ElidedLabel, color: str) -> None:
        palette = label.palette()
        palette.setColor(label.foregroundRole(), QColor(color))
        label.setPalette(palette)
