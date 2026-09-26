"""Editor for a DuelConfig — the Assemble tier, with the DSL two levels deeper.

WHAT AN ASSIGNMENT ASKS
-----------------------

*What should these players do?* — and the honest answer for most users is the name of a
Player Strategy, not a line of WizSprinter DSL. So an assignment's primary control is a
choice between three answers:

    (built-in default)  ·  a named Player Strategy  ·  Custom DSL…

Choosing a name is the whole interaction for the Assemble tier: no DSL box is built,
shown or scrolled past. Choosing ``Custom DSL…`` is the Author tier, and unfolds the
editor in place. That replaced a layout where every row put a monospace box in front of
the user whether or not they could write in one.

Where that choice *lives* moved once more, in the panel rework: it was a combo box on
the row, and it is now a themed menu behind the card's search verb. See
:mod:`bartlebot.gui.combat_strategies.assignment_card` for the card and its three verbs;
what is left here is the page around them.

WHAT THIS PAGE IS
-----------------

Two sections under headings — Combat Order and Player Strategies — and nothing else. The
name, the description and the usage line moved up into the detail pane's *About* section,
which is shared by all three tabs (see :mod:`bartlebot.gui.combat_strategies.dialog`);
they are facts about the library entry rather than about the duel's shape, and every tab
was otherwise saying them in a different arrangement.

WHAT LIVES ELSEWHERE
--------------------

``DuelStrategyEditor``, the debounce wrapper that autosaved a named library entry 500ms
after every keystroke, is gone. This editor still mutates the config it is handed
directly — but what it is handed is a working copy, so writing through is safe, and when
the write happens is the dialog's Save button's business. See
:mod:`bartlebot.strategy_workset`.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.strategy_models import (
    DuelConfig,
    PlayerAssignment,
    PlayerCombatStrategy,
)
from bartlebot.gui.combat_strategies.assignment_card import AssignmentCard
from bartlebot.gui.sidebar.sections.combat_order_widget import CombatOrderWidget
from bartlebot.gui.widgets.add_card import AddCard
from bartlebot.gui.widgets.drag_reorder import DragReorder
from bartlebot.gui.widgets.elided_label import apply_muted_color
from bartlebot.gui.widgets.rule_heading import RuleHeading

#: The two section headings, and what the Add card at the foot of the second one reads.
#: "Player Strategies" rather than "Player assignments": an assignment *is* the pairing
#: of players with a strategy, and the tab that holds the strategies themselves is called
#: the same thing — one vocabulary across the dialog, as the mockup in issue #45 has it.
ORDER_HEADING = "Combat Order"
ASSIGNMENTS_HEADING = "Player Strategies"
ADD_ASSIGNMENT_TEXT = "Add player strategy"


def move_item(items: list, src: int, dst: int) -> list:
    """``items`` with the element at ``src`` reinserted at position ``dst``.

    ``dst`` is interpreted against the list *as the caller sees it now* — the positions
    before the move. Removing the source first shifts everything after it down one, so a
    downward move has to compensate; that off-by-one is the whole function.

    Out-of-range or no-op moves return an unchanged copy rather than raising.
    """
    if not (0 <= src < len(items)) or not (0 <= dst <= len(items)) or dst in (src, src + 1):
        return list(items)
    out = list(items)
    item = out.pop(src)
    out.insert(dst - 1 if dst > src else dst, item)
    return out


class DuelConfigEditor(QWidget):
    """Edit a DuelConfig: min/max players, combat order, player assignments.

    Mutates the loaded ``DuelConfig`` directly as the user edits — there is no separate
    "apply" step, and no autosave of its own. What it is handed is a working copy out of
    :class:`~bartlebot.strategy_workset.StrategyWorkingSet`, so writing through is safe
    and *when* it is written is the dialog's Save button's business. ``changed`` is still
    emitted, but only so an embedder can refresh what it draws — nothing downstream of it
    touches disk.
    """

    changed = pyqtSignal()
    #: A card wants its custom DSL kept as a named Player Strategy. Emits the card, for
    #: whoever owns the working set to name it, create it and call
    #: :meth:`AssignmentCard.link_to`.
    promote_requested = pyqtSignal(object)
    #: A card's pencil: open this Player Strategy (by id) wherever Player Strategies are
    #: edited. Re-emitted from :attr:`AssignmentCard.open_pcs_requested`.
    open_pcs_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config: DuelConfig | None = None
        self._all_pcs: list[PlayerCombatStrategy] = []
        self._suppress = False
        self._cards: list[AssignmentCard] = []
        self._editable = True
        self._color_keys: list[int | None] = []
        self._account_labels: list[str] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # ── Combat Order ────────────────────────────────────────────────
        outer.addWidget(RuleHeading(ORDER_HEADING, self))

        # Simultaneous entry on the left, the player range on the right, one row: they are
        # one question — how many players, and in what order they walk in. The spin boxes
        # go *into* the order widget's own header row rather than into a row here, so the
        # two stay aligned when the chip grid is hidden — which is the state the checkbox
        # exists to produce. See ``CombatOrderWidget.add_header_widget``.
        self._order_widget = CombatOrderWidget(empty_label="Simultaneous entry")
        self._order_widget.changed.connect(self._on_changed)
        self._min_spin = QSpinBox()
        self._min_spin.setRange(1, 4)
        self._min_spin.valueChanged.connect(self._on_min_players_changed)
        self._min_spin.valueChanged.connect(self._on_changed)
        self._max_spin = QSpinBox()
        self._max_spin.setRange(1, 4)
        self._max_spin.valueChanged.connect(self._on_changed)
        self._max_spin.valueChanged.connect(self._on_max_players_changed)
        for label, spin in (("Min players:", self._min_spin), ("Max players:", self._max_spin)):
            self._order_widget.add_header_widget(QLabel(label))
            self._order_widget.add_header_widget(spin)
        outer.addWidget(self._order_widget)

        # ── Player Strategies ───────────────────────────────────────────
        outer.addWidget(RuleHeading(ASSIGNMENTS_HEADING, self))

        # Derived from the assignments below — not a second thing to maintain.
        self._requirements_lbl = QLabel(self)
        self._requirements_lbl.hide()
        apply_muted_color(self._requirements_lbl)
        outer.addWidget(self._requirements_lbl)

        # No inner QScrollArea: this widget lives inside the dialog's single detail-pane
        # scroll area, and nesting a second scrollable region here hid cards behind a tiny
        # inner scrollbar nobody could see was there. It is also why the card list is a
        # plain layout of ``CardFrame``s rather than a ``CardColumn``, which brings a
        # scroll area of its own. The drag is the same one ``CardColumn`` uses, and it
        # auto-scrolls the dialog's pane.
        self._cards_container = QWidget(self)
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(4)
        outer.addWidget(self._cards_container)
        self._drag = DragReorder(self._cards_layout, self)
        self._drag.reordered.connect(self._on_cards_reordered)
        self._drag.watch_for_show(self)

        self._add_card = AddCard(ADD_ASSIGNMENT_TEXT, parent=self)
        self._add_card.add_requested.connect(self._on_add_assignment)
        outer.addWidget(self._add_card)
        outer.addStretch(1)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, config: DuelConfig, all_pcs: list[PlayerCombatStrategy]) -> None:
        self._config = config
        self._all_pcs = all_pcs
        self._suppress = True
        self._min_spin.setValue(config.min_players)
        self._max_spin.setValue(config.max_players)
        self._order_widget.set_value(config.combat_order or [])
        self._rebuild_cards()
        self._suppress = False
        self._apply_max_players_constraints(config.max_players)
        self._update_add_card()
        self._update_requirements_label()

    def clear(self) -> None:
        """Detach from the loaded config. There is no pending state to drop — every edit
        is already live on the working copy.
        """
        self._config = None
        self._suppress = True
        self._rebuild_cards()
        self._suppress = False
        self._update_requirements_label()

    def set_pcs_list(self, all_pcs: list[PlayerCombatStrategy]) -> None:
        self._all_pcs = all_pcs
        for card in self._cards:
            card.set_pcs_list(all_pcs)
        self._update_requirements_label()

    def set_player_color_keys(self, keys: list[int | None], labels: list[str] | None = None) -> None:
        """Colour every card's slot pills by the account currently in each slot.

        Held as well as applied, because cards are built and rebuilt long after this
        arrives: an assignment added five minutes into a session has to get the same
        colours as the ones that were there when the dialog opened.
        """
        self._color_keys = list(keys)
        self._account_labels = list(labels or [])
        # The combat-order chips too, not just the assignment cards: both are references
        # to a player slot, and the point of issue #40 is that a slot reads the same
        # colour everywhere it is named.
        self._order_widget.set_player_color_keys(self._color_keys)
        for card in self._cards:
            card.set_player_color_keys(self._color_keys, self._account_labels)

    def set_editable(self, editable: bool) -> None:
        """Lock or unlock every control, including cards added later."""
        self._editable = editable
        self._min_spin.setEnabled(editable)
        self._max_spin.setEnabled(editable)
        self._order_widget.set_editable(editable)
        for card in self._cards:
            card.set_editable(editable)
        self._update_add_card()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    @property
    def cards(self) -> list[AssignmentCard]:
        """The assignment cards, in the order they are drawn.

        Public because a test naming a card should not have to reach past this class to
        find one.
        """
        return list(self._cards)

    @property
    def add_card(self) -> AddCard:
        """The dashed card that adds an assignment. Public so a caller can click it."""
        return self._add_card

    # ------------------------------------------------------------------
    # The card list
    # ------------------------------------------------------------------

    def _rebuild_cards(self) -> None:
        # Before the cards go: a drag in flight would otherwise be left holding one.
        self._drag.cancel()
        for card in self._cards:
            # setParent(None) as well as removeWidget: a widget merely taken out of a
            # layout stays a child and keeps painting where it last was, until the
            # deferred delete runs. See CombatOrderWidget._rebuild_chips.
            self._cards_layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        if self._config:
            for assignment in self._config.player_assignments:
                self._add_card_for(assignment)

    def _add_card_for(self, assignment: PlayerAssignment) -> None:
        card = AssignmentCard(assignment, self._all_pcs, parent=self._cards_container)
        card.set_player_color_keys(self._color_keys, self._account_labels)
        card.set_editable(self._editable)
        card.changed.connect(self._on_changed)
        card.remove_requested.connect(self._on_remove_card)
        card.players_changed.connect(self._enforce_exclusivity)
        card.promote_requested.connect(self.promote_requested)
        card.open_pcs_requested.connect(self.open_pcs_requested)
        self._cards_layout.addWidget(card)
        self._cards.append(card)
        self._drag.set_cards(self._cards)

    def _enforce_exclusivity(self, source_card: AssignmentCard, newly_added: set) -> None:
        """Remove newly-claimed players from every other card."""
        if not newly_added:
            return
        for card in self._cards:
            if card is source_card:
                continue
            current = card.assignment().players
            remaining = [p for p in current if p not in newly_added]
            if remaining != current:
                card.update_players_silently(remaining)

    def _on_add_assignment(self) -> None:
        if self._config is None:
            return
        # No editable guard: the Add card is *hidden* while a bot runs rather than
        # greyed, so there is no click to refuse -- and a card that did arrive by some
        # other route is built locked, which is the state that actually matters.
        max_p = self._max_spin.value()
        if len(self._cards) >= max_p:
            return
        # Default to players not yet claimed by any existing card.
        claimed = {p for card in self._cards for p in card.assignment().players}
        unclaimed = [p for p in range(1, max_p + 1) if p not in claimed]
        # New assignments start inline and empty — no library entry required.
        assignment = PlayerAssignment(players=unclaimed)
        self._config.player_assignments.append(assignment)
        self._add_card_for(assignment)
        self._cards[-1].set_max_players(max_p)
        self._update_add_card()
        self._update_requirements_label()
        self.changed.emit()

    def _on_remove_card(self, card: AssignmentCard) -> None:
        if self._config is None:
            return
        assignment = card.assignment()
        self._config.player_assignments = [a for a in self._config.player_assignments if a is not assignment]
        self._cards_layout.removeWidget(card)
        card.setParent(None)
        card.deleteLater()
        self._cards.remove(card)
        self._drag.set_cards(self._cards)
        self._update_add_card()
        self._update_requirements_label()
        self.changed.emit()

    def _on_cards_reordered(self, order: list[int]) -> None:
        """A drag has settled: the cards already stand in their new order."""
        self._apply_card_order([self._cards[index] for index in order])

    def move_assignment(self, src: int, dst: int) -> None:
        """Move the assignment at index ``src`` to sit at index ``dst``.

        ``dst`` is a position in the list as it stands *before* the move — see
        :func:`move_item`.

        Ordering is presentational. ``_enforce_exclusivity`` already guarantees a player
        appears in exactly one assignment, so ``resolve_dsl_for_player``'s first-match
        loop cannot come back with a different answer after a reorder; nothing about how
        the bot fights changes here.

        The reorder is live on the config immediately, like every other edit in this
        widget; it reaches disk when the dialog is saved, and not before.
        """
        if self._config is None:
            return
        reordered = move_item(self._cards, src, dst)
        if reordered != self._cards:
            self._apply_card_order(reordered)

    def _apply_card_order(self, cards: list[AssignmentCard]) -> None:
        """Put the assignments, and the cards drawing them, in the order of *cards*."""
        if self._config is None:
            return
        self._cards = list(cards)
        self._config.player_assignments = [card.assignment() for card in self._cards]
        for index, card in enumerate(self._cards):
            # insertWidget moves a widget the layout already owns; after a drag the cards
            # are already there, and this changes nothing on screen.
            self._cards_layout.insertWidget(index, card)
        self._drag.set_cards(self._cards)
        self.changed.emit()

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------

    def _update_requirements_label(self) -> None:
        """Refresh the derived school-requirement chips from the current assignments."""
        if self._config is None:
            self._requirements_lbl.hide()
            return
        pcs_by_id = {p.id: p for p in self._all_pcs}
        reqs = self._config.school_requirements(pcs_by_id)
        if not reqs:
            self._requirements_lbl.hide()
            return
        self._requirements_lbl.setText(" · ".join(f"{school} ×{n}" for school, n in reqs.items()))
        self._requirements_lbl.show()

    def _on_min_players_changed(self, value: int) -> None:
        """Drag Max up when Min passes it, so the range can never invert.

        Pushing the other spin box rather than narrowing its range: a range narrowed here
        would still be narrow at the next ``load()``, which silently clamps the stored
        value of whichever config is opened next.
        """
        if self._suppress:
            return
        if value > self._max_spin.value():
            self._max_spin.setValue(value)

    def _on_max_players_changed(self, value: int) -> None:
        if self._suppress:
            return
        if value < self._min_spin.value():
            self._min_spin.setValue(value)  # the mirror of _on_min_players_changed
        self._apply_max_players_constraints(value)
        self._update_add_card()

    def _apply_max_players_constraints(self, n: int) -> None:
        """Propagate max_players to the order widget and every assignment card."""
        self._order_widget.set_max_players(n)
        for card in self._cards:
            card.set_max_players(n)

    def _update_add_card(self) -> None:
        """Show or hide the Add card, and name the reason when it is gone.

        There are only two, and neither is guessable from a card that simply stops
        responding: the player slots are all spoken for, or the whole editor is locked
        because a bot is running. Hidden rather than greyed — an ``AddCard`` is a dashed
        *slot*, and a slot that cannot be filled is not a slot.
        """
        max_p = self._max_spin.value()
        room = len(self._cards) < max_p
        self._add_card.setVisible(self._editable and room)
        self._add_card.setToolTip(
            f"Every player slot (P1–P{max_p}) already has a strategy — raise Max players, or remove one first"
            if not room
            else "Give a set of players their own combat strategy in this duel"
        )

    def _on_changed(self) -> None:
        if self._suppress or self._config is None:
            return
        self._config.min_players = self._min_spin.value()
        self._config.max_players = self._max_spin.value()
        order = self._order_widget.get_value()
        self._config.combat_order = order if order else None
        self._update_requirements_label()
        self.changed.emit()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        # No stylesheet-dependent styling in this widget: every coloured thing in it —
        # the headings, the cards, the Add card — owns its own theme pass.
