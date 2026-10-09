"""``DragReorder`` -- the lift-and-slide drag that reorders a column of cards.

One gesture for every card list in the app that can be reordered: the Accounts and
Launch Profiles columns, the Players tab, the Combat tab's decks and duel strategies, and
the Player Strategies in the combat strategy dialog. It began inside ``CardColumn`` and
moved out when the Combat tab needed it, because the drag belongs to a *list of cards in
a layout*, not to that one widget's heading, banner and scroll area.

HOW THE DRAG IS DRAWN

The reorder was always live -- the cards really did rearrange under the pointer -- and it
still surprised people, because nothing said which card was being held. The sort was the
only evidence a drag was happening, so what the user saw was the *other* cards moving on
their own. The gesture is the same one; what was missing was every signal around it:

- a **grip** (``widgets.card_frame.GripHandle``) that says a card can be dragged at all,
  and now the only place a drag starts, so the cursor can change exactly where it works;
- the dragged card **lifted out of the layout**, following the pointer continuously and
  leaving a ``DropPlaceholder`` hole where it came from, instead of snapping a row at a
  time with nothing at all happening in between;
- displaced cards **sliding** into their new slots rather than teleporting into them;
- **auto-scroll** at the ends of the viewport, without which the reorderable part of a
  long list is only whatever was on screen when the drag began;
- a **settle** the rest of the gesture waits for, so that a drop is the one moment nothing
  else is happening -- see ``_end_drag``.

That is the arrangement Trello, Linear, the macOS Dock and the iOS home screen all
converge on, and each piece is doing separate work: the grip answers *can I*, the lift
answers *what am I holding*, the slide answers *what just changed*, the scroll answers
*where can I put it*.

WHAT IT NEEDS FROM ITS HOST

A ``QBoxLayout`` holding the cards **contiguously**, and nothing else about the widget it
lives in. Other rows may sit before or after the run -- the Combat tab's Import all row
and its locked Default card above, an Add card below -- and never take part: the cards'
place is counted from the first of them, and a drag past either end lands in the end
card's slot. The scroll area it auto-scrolls is the nearest one enclosing the layout,
found when a drag starts, so a list inside a sidebar tab scrolls the tab rather than
bringing a second, nested scroll area of its own.

The engine never rebuilds anything. It reports the new order once the dropped card has
settled into its slot, with the cards already standing where they now belong, and the
host decides what the order means: a settings list rewritten, a deck handed to another
player, a purely cosmetic sort. Reported as a permutation of the list last handed to
:meth:`DragReorder.set_cards` -- which is also the host's cue to hand it the new one.

THE KEYBOARD ROUTE

A drag is not reachable from the keyboard, so every list it reorders also takes
**Alt+Up / Alt+Down** and offers **Move up / Move down** in each card's right-click menu
(#155). Both go through :meth:`DragReorder.move`, which takes the same path a drop does
-- the same slide, the same ``previewed`` and ``reordered`` -- so a host cannot tell a
keyboard move from a short drag and needs nothing of its own for it. ``set_cards`` hands
each card the route (``CardFrame.set_reorder_route``); the card owns the shortcut and the
menu entries, since both are about the card that has focus or was right-clicked. When a
host answers ``reordered`` by rebuilding its cards, the next ``set_cards`` puts focus back
on the moved card's replacement, so Alt+Down can be pressed again without reaching for the
mouse.

A TREE IN ONE COLUMN

Two hooks let a host draw something more structured than a flat list, and are what the
signature page's inputs tree is built on (``bartlebot/content/subgraphs/signature_tree.py``):

- **carry** -- a card that picks up the cards after it, as a group heading takes its
  members. The run travels as one block, behind one placeholder its own height.
- **accepts** -- a veto on a slot. Handed the order a move would make and the block that
  moved, it answers whether that order is legal. A refused slot is skipped: during a drag
  the hole stays where it last was allowed, and a keyboard move steps on past it.

Both default to off, which is a plain list.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QBoxLayout, QMenu, QScrollArea, QSizePolicy, QWidget

from bartlebot.gui.offscreen_warmup import warm_offscreen_render
from bartlebot.gui.theme import THEME

# How long a card that has been displaced takes to slide into the slot the dragged one
# vacated. Short enough not to lag the pointer, long enough to be a movement rather than a
# jump: under about 80ms the eye reads it as a teleport again, which is the thing the
# animation exists to stop, and over about 200ms a fast drag outruns its own list.
CARD_SHIFT_MS = 130

# How long the dropped card takes to travel from wherever it was let go into its slot.
# Quicker than the shift above, because it is confirming a position the user has already
# chosen rather than showing them a change they did not ask for.
CARD_SETTLE_MS = 100

# Auto-scroll while a drag sits near an end of the viewport: how close it has to get, how
# far each tick moves, and how often a tick comes. The margin is roughly a third of a card
# so that reaching it is deliberate; the step and the interval together are about 12px per
# frame at 60Hz, which crosses a full column in well under a second.
AUTOSCROLL_MARGIN = 28
AUTOSCROLL_STEP = 12
AUTOSCROLL_INTERVAL_MS = 16

# The dashed outline of the hole a lifted card leaves behind, matching the Add card's
# border for the same reason it is 2px there: Qt scales a dash pattern by the pen width,
# so a 1px dashed rounded rect draws as a row of dots that at this color is a line you
# have to look for.
PLACEHOLDER_BORDER_WIDTH = 2
PLACEHOLDER_RADIUS = 4

# The keyboard route's two verbs, as every reorderable list names them.
MOVE_UP = "Move up"
MOVE_DOWN = "Move down"
MOVE_UP_KEYS = QKeySequence("Alt+Up")
MOVE_DOWN_KEYS = QKeySequence("Alt+Down")


def add_move_actions(menu: QMenu, move: Callable[[int], object], can_move: Callable[[int], bool]) -> None:
    """Put Move up and Move down on *menu*, each greyed when there is nowhere to go.

    The keys are written into the label's shortcut column rather than set as the action's
    shortcut. The card or tree that built the menu already owns Alt+Up and Alt+Down as
    shortcuts; a second binding on a menu action that outlives its popup makes the two
    ambiguous, and Qt then fires neither.
    """
    for label, keys, step in ((MOVE_UP, MOVE_UP_KEYS, -1), (MOVE_DOWN, MOVE_DOWN_KEYS, 1)):
        action = menu.addAction(f"{label}\t{keys.toString(QKeySequence.SequenceFormat.NativeText)}")
        action.setEnabled(can_move(step))
        action.triggered.connect(lambda _=False, s=step: move(s))


class DropPlaceholder(QWidget):
    """The hole a lifted card leaves in the list, and the slot it would drop into.

    A real widget in the layout rather than a painted hint, because it is doing the
    layout's work: it holds the dragged card's height open so the cards below do not
    close up the moment the card leaves the flow, and moving *it* between slots is what
    makes the other cards shift. The dragged card itself is out of the layout entirely,
    following the pointer, so without this the list would collapse by one card-height
    on the first pixel of every drag and everything below would jump twice.

    It is drawn as an outline rather than filled: what the user is looking for is where
    the card will land, and an empty dashed frame the exact size of the card in their
    hand says that more plainly than a block of color, which reads as a card of its own.

    No theming ceremony -- ``THEME`` is read in ``paintEvent``, so there is no baked
    value to go stale and nothing for a theme switch to have to re-derive.
    """

    def __init__(self, height: int, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(height)
        # Expanding across to fill the list, exactly like the card it stands in for --
        # a placeholder narrower than the hole is a second thing moving on screen.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(THEME.border), PLACEHOLDER_BORDER_WIDTH, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = PLACEHOLDER_BORDER_WIDTH // 2
        painter.drawRoundedRect(
            self.rect().adjusted(inset, inset, -inset - 1, -inset - 1),
            PLACEHOLDER_RADIUS,
            PLACEHOLDER_RADIUS,
        )
        painter.end()


class DragReorder(QObject):
    """Drag-to-reorder for a contiguous run of cards in *layout*, by their grips."""

    # The cards' previous indices, in the order they are in now, reported once the drop
    # has settled. An index permutation rather than the cards themselves, because the
    # host still holds the list it handed over, in exactly that order.
    reordered = pyqtSignal(list)
    # The cards in the order they stand right now, on every re-sort during a drag and
    # when a drag is abandoned. For a host whose cards take something from their place
    # -- a deck card is whichever player's slot it stands in -- and has to say so while
    # the card is still moving, not only once it lands.
    previewed = pyqtSignal(list)

    def __init__(self, layout: QBoxLayout, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._layout = layout
        # The order the host handed the cards over in, and the order they are in now.
        # The first is what a reorder is reported against, and it moves only when the
        # host hands over a fresh list.
        self._baseline: list[QWidget] = []
        self._cards: list[QWidget] = []
        # The in-flight drag: which card the button went down on, where, and whether the
        # pointer has since traveled far enough for it to be a drag and not a click.
        self._press_card: QWidget | None = None
        self._press_pos = QPoint()
        # How far down the card the button went down. A drag moves the card, not the
        # pointer, so where the user grabbed it is what says where the card now is.
        self._grab_offset = 0
        self._dragging = False
        # Each card's grip, and the card it belongs to. The grip is the drag source, so
        # the event filter goes on it and has to be able to get back to the card.
        self._grips: dict[QWidget, QWidget] = {}
        # Where the run of cards starts in the layout, taken at lift: rows before it (a
        # header, a locked card) are not the host's to reorder.
        self._offset = 0
        # The hole the lifted card left, and the x and width it keeps while it is out of
        # the layout -- taken once at lift, because the layout is no longer setting them.
        self._placeholder: DropPlaceholder | None = None
        self._drag_x = 0
        self._drag_width = 0
        # Where every other card is *settling*, which is not where it currently is: a card
        # part-way through its slide has a position that means nothing to the question of
        # which slot the pointer is over. Compared against the animation's destination
        # instead, or the target flickers between two slots for the length of every shift.
        self._slots: dict[QWidget, QPoint] = {}
        self._anims: dict[QWidget, QPropertyAnimation] = {}
        # Where the pointer was at the last move, so an auto-scroll tick -- which is not a
        # mouse event and carries no position of its own -- can re-place the card.
        self._last_global = QPoint()
        # The scroll area enclosing the list, found at lift; None when there is none.
        self._area: QScrollArea | None = None
        # The card that has been let go and is still flying into its slot. The gesture is
        # not over while this is set: the card is still out of the layout, the placeholder
        # is still holding its hole, and the new order has not been reported yet.
        self._settling: QWidget | None = None
        # Exactly balanced against restoreOverrideCursor; see _pop_cursor.
        self._cursor_pushed = False
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(AUTOSCROLL_INTERVAL_MS)
        self._autoscroll.timeout.connect(self._autoscroll_step)
        # Fired one turn after the list is first shown; see ``watch_for_show``.
        self._warmup = QTimer(self)
        self._warmup.setSingleShot(True)
        self._warmup.timeout.connect(self._warm_offscreen_render)
        self._shown_host: QWidget | None = None
        # The host's two hooks for a list that is a tree -- see the module docstring.
        self._accepts: Callable[[list[QWidget], list[QWidget]], bool] | None = None
        self._carry: Callable[[QWidget], int] | None = None
        # The block in the air: the card pressed, then whatever it carries. One card for a
        # plain list. Each one's distance below the first is taken at lift and held, as
        # the x and the width are.
        self._lifted: list[QWidget] = []
        self._lift_offsets: dict[QWidget, int] = {}
        # Where a keyboard move left the moved card, for a host that answers ``reordered``
        # by rebuilding: the next ``set_cards`` gives focus to the card in this slot.
        self._focus_after: int | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def cards(self) -> list[QWidget]:
        """The cards, in the order they stand now."""
        return list(self._cards)

    @property
    def is_busy(self) -> bool:
        """Whether a drag is under way or a dropped card is still settling."""
        return self._dragging or self._settling is not None

    def set_cards(self, cards: list[QWidget]) -> None:
        """Take *cards* -- already in the layout, contiguous, in this order -- as the list.

        Abandons any drag in flight first: a rebuild can arrive mid-drag, and one that
        answers ``reordered`` always does. Cards without a grip are part of the order but
        cannot be picked up.
        """
        self.cancel()
        for grip in self._grips:
            grip.removeEventFilter(self)
        self._cards = list(cards)
        self._baseline = list(cards)
        self._grips = {}
        self._slots = {}
        self._anims = {}
        for card in cards:
            grip = getattr(card, "grip", None)
            if grip is not None:
                grip.installEventFilter(self)
                self._grips[grip] = card
                if hasattr(card, "set_reorder_route"):
                    card.set_reorder_route(*self._route_for(card))
        self._restore_focus()

    def _route_for(self, card: QWidget) -> tuple[Callable[[int], bool], Callable[[int], bool]]:
        """The move and the can-move a card is handed, holding neither the card nor this.

        Weakly, because a card keeps its route: a strong reference here closes a cycle
        (engine, cards, route, engine) that the garbage collector breaks by clearing this
        object's attributes while Qt still has it installed as the grips' event filter --
        the next event then lands in ``eventFilter`` on an object with no ``_grips``.
        """
        engine, target = weakref.ref(self), weakref.ref(card)

        def move(step: int) -> bool:
            e, c = engine(), target()
            return e is not None and c is not None and e.move(c, step)

        def can_move(step: int) -> bool:
            e, c = engine(), target()
            return e is not None and c is not None and e.can_move(c, step)

        return move, can_move

    def set_rules(
        self,
        *,
        accepts: Callable[[list[QWidget], list[QWidget]], bool] | None = None,
        carry: Callable[[QWidget], int] | None = None,
    ) -> None:
        """Make the list a tree: *carry* says how many cards follow a card it lifts, and
        *accepts* vetoes an order. See the module docstring. Both None is a plain list."""
        self._accepts = accepts
        self._carry = carry

    def can_move(self, card: QWidget, step: int) -> bool:
        """Whether :meth:`move` would move *card* -- what greys out Move up or Move down."""
        return self._step_order(card, step) is not None

    def move(self, card: QWidget, step: int) -> bool:
        """Move *card* one slot up (*step* -1) or down (+1): the keyboard's drag.

        Carries what *card* carries and steps on past any slot ``accepts`` refuses, then
        reports exactly as a drop does -- the cards slide, ``previewed`` goes out, and
        ``reordered`` follows at once, there being no flight to wait for. False when there
        is nowhere to go, the card's grip is locked, or a drag is under way.
        """
        order = self._step_order(card, step)
        if order is None:
            return False
        layout = self._layout
        offset = min(layout.indexOf(c) for c in self._cards)
        before = {c: c.pos() for c in self._cards}
        self._cards = order
        for index, c in enumerate(order):
            layout.insertWidget(offset + index, c)
        layout.activate()
        for c, origin in before.items():
            self._animate(c, origin, c.pos(), CARD_SHIFT_MS)
        focus = QApplication.focusWidget()
        if focus is not None and (focus is card or card.isAncestorOf(focus)):
            self._focus_after = order.index(card)
        self.previewed.emit(list(order))
        self._commit_order()
        # A host that keeps its cards still has the focus where it was; only a rebuild
        # needs it put back, and the rebuild's ``set_cards`` is what does that.
        focus = QApplication.focusWidget()
        if focus is not None and any(c is focus or c.isAncestorOf(focus) for c in self._cards):
            self._focus_after = None
        return True

    def _step_order(self, card: QWidget, step: int) -> list[QWidget] | None:
        """The order a keyboard move of *card* would make, or None for no move."""
        if self.is_busy or card not in self._cards:
            return None
        grip = getattr(card, "grip", None)
        if grip is None or not grip.isEnabled():
            return None
        start = self._cards.index(card)
        block = self._cards[start : start + 1 + self._carried(card)]
        rest = [c for c in self._cards if c not in block]
        target = start + step
        while 0 <= target <= len(rest):
            order = rest[:target] + block + rest[target:]
            if order != self._cards and (self._accepts is None or self._accepts(order, block)):
                return order
            target += step
        return None

    def _carried(self, card: QWidget) -> int:
        """How many of the cards after *card* travel with it, clamped to the list."""
        if self._carry is None:
            return 0
        after = len(self._cards) - self._cards.index(card) - 1
        return max(0, min(self._carry(card), after))

    def _restore_focus(self) -> None:
        """After a keyboard move and a rebuild, give focus to the moved card's replacement.

        An empty list keeps the request: a rebuild that clears the engine first
        (``set_cards([])``) and hands the new cards over after is still one rebuild.
        """
        if self._focus_after is None or not self._cards:
            return
        index, self._focus_after = self._focus_after, None
        focus = QApplication.focusWidget()
        if focus is not None and any(c is focus or c.isAncestorOf(focus) for c in self._cards):
            return
        self._cards[min(index, len(self._cards) - 1)].setFocus(Qt.FocusReason.OtherFocusReason)

    def watch_for_show(self, host: QWidget) -> None:
        """Warm *host*'s window's offscreen render path the first time *host* is shown.

        The lift attaches a drop shadow, which is the first thing in most sessions to
        render a widget offscreen -- and Qt spends 150-200ms on the *first* one, flushing
        every layout the window has been holding back for a hidden widget. Left alone,
        the user pays it between the two mouse moves that start their first drag, which
        is exactly what it looks like: the card sticks for a moment and then comes free.
        See ``gui/offscreen_warmup.py``.

        Deferred a turn, because a show event is the frame the host is being drawn on and
        this is 200ms of work that has nothing to do with drawing it. The timer is this
        object's own, so a host closed inside that turn takes its warm-up with it.
        """
        self._shown_host = host
        host.installEventFilter(self)

    def cancel(self) -> None:
        """Abandon an in-flight drag without reporting it. A no-op when there is none.

        The lifted card goes back where the placeholder is holding and the list goes back
        to its baseline order, so a host whose cards outlive the drag -- a deck card is
        never rebuilt -- is left exactly as it was before the press. A host about to
        delete the cards anyway loses nothing to it.

        A drop still in the air is abandoned rather than completed. Clearing ``_settling``
        is what disarms the animation's ``finished`` connection, which is queued behind
        this and would otherwise run ``_finish_drop`` against a card that may have been
        deleted -- and report an order for a list that no longer exists.
        """
        self._autoscroll.stop()
        self._pop_cursor()
        lifted = list(self._lifted) if (self._settling or self._dragging) else []
        self._press_card = None
        self._dragging = False
        self._settling = None
        self._lifted = []
        self._lift_offsets = {}
        for anim in self._anims.values():
            anim.stop()
        self._anims = {}
        self._slots = {}
        if self._placeholder is None:
            return
        self._layout.removeWidget(self._placeholder)
        self._placeholder.deleteLater()
        self._placeholder = None
        for card in lifted:
            if hasattr(card, "set_dragging"):
                card.set_dragging(False)
        if self._cards != self._baseline:
            self._cards = list(self._baseline)
            self.previewed.emit(list(self._cards))
        for index, card in enumerate(self._cards):
            # insertWidget moves a widget the layout already owns, and puts back the one
            # that was lifted out of it.
            self._layout.insertWidget(self._offset + index, card)
        self._layout.activate()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        """Turn a press-and-drag on a card's grip into a reorder.

        Filtered here rather than implemented on the card because order is a fact about
        the list: a card knows what it stands for and nothing about what is above it.
        The grip is only the card's half of the arrangement -- the place a press is
        allowed to start from, and the one thing on the card whose cursor may say so.

        The body is deliberately not a drag source. It was, and it worked in silence:
        nothing on a card announced that it could be dragged, and nothing could be made to
        announce it, because the cursor is how that is normally said and a card whose
        primary verb is *launch* cannot wear an open hand over its whole face. Moving the
        gesture onto a handle is what let the cursor tell the truth in both places.
        """
        kind = event.type()
        if obj is self._shown_host:
            if kind == QEvent.Type.Show:
                self._warmup.start(0)
            elif kind == QEvent.Type.Close:
                # Never leave a closed hand behind on a list that is going away.
                self.cancel()
            return False
        card = self._grips.get(obj)
        if card is None:
            return super().eventFilter(obj, event)
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if not obj.isEnabled():
                # A locked grip (``GripHandle.set_locked``). Asked of the grip rather than
                # held here as a flag, so there is one lock and nothing to keep in step with
                # it. Checked explicitly because event filters run before ``QWidget.event``,
                # which is where a disabled widget drops its mouse input; only the press
                # needs it, since every later branch requires a press this one accepted.
                return super().eventFilter(obj, event)
            if self._settling is not None:
                # A previous drop is still in the air: its card is out of the layout and
                # its placeholder still holds a slot, so a second drag started now would
                # be measured against a list that is one row out. The window is the
                # hundred milliseconds of the settle, and losing a press inside it costs
                # the user one grab.
                return super().eventFilter(obj, event)
            self._press_card = card
            self._press_pos = event.globalPosition().toPoint()
            # Measured against the card, not the grip: the card is what travels, so where
            # the pointer sits within *it* is what says where it now is.
            self._grab_offset = card.mapFromGlobal(event.globalPosition().toPoint()).y()
            self._dragging = False
        elif kind == QEvent.Type.MouseMove and self._press_card is card:
            pos = event.globalPosition().toPoint()
            moved = (pos - self._press_pos).manhattanLength()
            if not self._dragging and moved >= QApplication.startDragDistance():
                self._begin_drag(card)
            if self._dragging:
                self._drag_to(card, pos)
        elif kind == QEvent.Type.MouseButtonRelease and self._press_card is card:
            if self._dragging:
                self._end_drag(card)
            else:
                self._press_card = None
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Lifting a card out of the list, and putting it back
    # ------------------------------------------------------------------

    def _container(self) -> QWidget:
        """The widget the layout lays out -- the cards' parent, and their coordinates."""
        return self._layout.parentWidget()

    def _find_scroll_area(self) -> QScrollArea | None:
        """The nearest scroll area enclosing the list, for auto-scroll. None if there is none."""
        widget = self._container()
        while widget is not None:
            if isinstance(widget, QScrollArea):
                return widget
            widget = widget.parentWidget()
        return None

    def _begin_drag(self, card: QWidget) -> None:
        """Take *card* out of the layout and leave a hole the same size behind.

        Out of the layout, and not merely restyled in place, because that is the whole
        difference between a drag that reads and one that does not. A card the layout
        still owns can only ever be in a slot, so it snaps a full row at a time: drag it
        forty pixels and nothing whatever happens, then everything jumps at once. Lifted,
        it tracks the pointer from the first pixel, and the reorder underneath it becomes
        a consequence of where the card is rather than the only evidence a drag is
        happening at all.

        The x and the width are taken now and held for the rest of the drag. The card
        moves vertically only -- this is a single column, and a card that also wandered
        sideways would be pretending it could be dropped somewhere it cannot.
        """
        layout = self._layout
        index = layout.indexOf(card)
        self._offset = min(layout.indexOf(c) for c in self._cards)
        self._area = self._find_scroll_area()
        self._drag_x = card.x()
        self._drag_width = card.width()
        start = self._cards.index(card)
        self._lifted = self._cards[start : start + 1 + self._carried(card)]
        self._lift_offsets = {c: c.y() - card.y() for c in self._lifted}
        last = self._lifted[-1]
        # A carried block's hole is the whole run, gaps included: what closes up behind
        # it is the distance from the first card's top to the last card's bottom.
        self._placeholder = DropPlaceholder(last.y() + last.height() - card.y(), self._container())
        # Shown by hand, and only ever after the parent it was constructed with. A
        # QBoxLayout skips a hidden widget outright rather than reserving its space, so a
        # placeholder left unshown holds nothing open: the list closes up by one card
        # the instant the drag begins, and every slot the drag is measured against is a
        # row out of place. (Constructed parented, per the never-show-a-parentless-widget
        # rule -- an unparented show here would be a card-sized window on the desktop.)
        self._placeholder.show()
        for lifted in self._lifted:
            layout.removeWidget(lifted)
        layout.insertWidget(index, self._placeholder)
        layout.activate()
        # Raised after the placeholder is in, so the cards are above every sibling they
        # are about to travel over -- including the hole they just left.
        for lifted in self._lifted:
            lifted.raise_()
            lifted.resize(self._drag_width, lifted.height())
        self._dragging = True
        self._remember_slots(card)
        for lifted in self._lifted:
            if hasattr(lifted, "set_dragging"):
                lifted.set_dragging(True)
        self._push_cursor()
        self._autoscroll.start()

    def _end_drag(self, card: QWidget) -> None:
        """Fly *card* from where it was let go into the slot its placeholder is holding.

        The gesture is not over when the button comes up, and treating it as though it
        were is what made a drop jitter. The card used to be handed straight back to the
        layout and only *then* animated home from where it had been dropped -- so for the
        length of that animation two things were setting its position: the slide, and a
        layout that had just been told this widget was one of its rows again. Every
        deferred layout pass in that window snapped the card to its slot and the slide
        picked it up from there, which is the twitch. The order was reported in the same
        breath, and the rebuild that answers it tore the cards down mid-flight -- so the
        card also jumped whatever distance it had left, and the list it landed in was a
        different set of widgets: the flash.

        So nothing structural happens here. The card stays lifted, the placeholder keeps
        holding its slot open, and the only thing moving is the card. What the drop is
        *worth* -- the layout swap, the lifted state coming off, the new order going out
        -- waits for ``_finish_drop`` at the far end of the flight, by which point the
        list is already drawn exactly as the host will redraw it.

        The cursor and the auto-scroll timer go now rather than there, because they are
        the pointer's and the button is up: a hand that stays closed for another tenth of
        a second after the user let go reads as a drag that has not finished.
        """
        self._autoscroll.stop()
        self._pop_cursor()
        self._dragging = False
        self._press_card = None
        self._slots = {}
        self._settling = card

        # The other cards' slides are deliberately left running. Stopping them would
        # freeze each one wherever it had got to -- there is no layout pass here now to
        # tidy that up -- so a drop during a shift would leave the list visibly out of
        # alignment for the length of the settle.
        if self._placeholder is None:
            self._finish_drop(card)
            return
        home = self._placeholder.pos()
        for carried in self._lifted[1:]:
            self._animate(carried, carried.pos(), home + QPoint(0, self._lift_offsets[carried]), CARD_SETTLE_MS)
        anim = self._animate(card, card.pos(), home, CARD_SETTLE_MS)
        if anim is None:
            self._finish_drop(card)
            return
        anim.finished.connect(lambda: self._finish_drop(card))

    def _finish_drop(self, card: QWidget) -> None:
        """Land *card* in the layout, take the lifted state off it, and report the order.

        Guarded on ``_settling`` because it is reached from a queued animation signal: a
        rebuild or a close arriving mid-flight has already abandoned this drop, and its
        cards may be on their way out. ``cancel`` clearing the flag is what makes the
        connection that outlives it harmless.

        The swap itself is invisible by construction. The card is already sitting exactly
        where the placeholder is, so exchanging one for the other moves nothing on screen
        -- which is the whole reason the flight has to finish before this runs.
        """
        if self._settling is not card:
            return
        self._settling = None
        for anim in self._anims.values():
            anim.stop()
        self._anims = {}

        layout = self._layout
        lifted, self._lifted, self._lift_offsets = self._lifted or [card], [], {}
        if self._placeholder is not None:
            index = layout.indexOf(self._placeholder)
            layout.removeWidget(self._placeholder)
            self._placeholder.deleteLater()
            self._placeholder = None
            for k, landed in enumerate(lifted):
                layout.insertWidget(index + k, landed)
        layout.activate()
        for landed in lifted:
            if hasattr(landed, "set_dragging"):
                landed.set_dragging(False)
        self._commit_order()

    def _push_cursor(self) -> None:
        """Close the hand everywhere for the length of the drag.

        An application override rather than a cursor on the grip alone, because the
        pointer spends the drag over whatever the card is passing -- other cards, a
        banner, the gap below the list -- and a hand that opens again the moment it
        leaves a twelve-pixel square says the drag has ended when it has not.
        """
        if not self._cursor_pushed:
            QApplication.setOverrideCursor(Qt.CursorShape.ClosedHandCursor)
            self._cursor_pushed = True

    def _pop_cursor(self) -> None:
        """Give the cursor back, exactly once per push.

        The flag is the whole point: Qt keeps override cursors on a stack, so an
        unbalanced restore pops somebody else's and an unbalanced push leaves the closed
        hand over the entire application with no drag under it. Both are reachable from
        here, since a drag can end by release, by a rebuild arriving underneath it, or by
        the list being torn down.
        """
        if self._cursor_pushed:
            QApplication.restoreOverrideCursor()
            self._cursor_pushed = False

    # ------------------------------------------------------------------
    # Following the pointer
    # ------------------------------------------------------------------

    def _drag_to(self, card: QWidget, global_pos: QPoint) -> None:
        """Move *card* to wherever the pointer now is, and re-sort the list under it.

        Reordering the layout as the pointer travels, rather than drawing a drop line and
        leaving the list still, because the list *is* the preview: a handful of cards is
        the whole list on screen at once, and seeing it in the order it would be left in
        reads better than an indicator saying where it would land.

        Positioned in the container's coordinates from a *global* pointer, which is what
        makes auto-scrolling work for free: after a scroll the same global position maps
        to a different container y, so the card stays put under the user's hand while the
        content moves past it.
        """
        self._last_global = global_pos
        y = self._container().mapFromGlobal(global_pos).y() - self._grab_offset
        block = self._lifted or [card]
        for lifted in block:
            lifted.move(self._drag_x, y + self._lift_offsets.get(lifted, 0))

        target = self._index_at(card)
        current = self._cards.index(card)
        if target == current:
            return
        rest = [c for c in self._cards if c not in block]
        order = rest[:target] + block + rest[target:]
        if self._accepts is not None and not self._accepts(order, list(block)):
            # A refused slot: the hole stays where it last was allowed, and letting go
            # here lands the block there.
            return
        before = {other: other.pos() for other in rest}
        self._cards = order
        layout = self._layout
        layout.removeWidget(self._placeholder)
        layout.insertWidget(self._offset + target, self._placeholder)
        # Forced now rather than left to the next event-loop turn: the new positions are
        # needed *this* call, both as the animations' destination and as what the next
        # move compares against.
        layout.activate()
        self._remember_slots(card)
        for other, origin in before.items():
            self._animate(other, origin, other.pos(), CARD_SHIFT_MS)
        self.previewed.emit(list(self._cards))

    def _remember_slots(self, dragged: QWidget) -> None:
        """Record where every card not in the air has just been laid out."""
        block = self._lifted or [dragged]
        self._slots = {card: card.pos() for card in self._cards if card not in block}

    def _animate(self, card: QWidget, origin: QPoint, destination: QPoint, duration: int) -> QPropertyAnimation | None:
        """Slide *card* from *origin* to *destination* instead of letting it appear there.

        A displaced card used to teleport, which is legible when one card moves and
        unreadable when three do -- the list simply looked different than it had a frame
        earlier, and the user had to re-find the card they were holding. Motion is what
        makes a swap something you watched happen rather than something you have to
        notice; it is the whole reason iOS, Trello and the macOS Dock feel the way they do.

        Parented to the card and not to this object, deliberately. A host that answers
        ``reordered`` by rebuilding deletes these widgets while an animation may still be
        running on them -- an animation owned elsewhere would go on driving a property of
        a freed object. Owned by its own target, it dies with it.

        *origin* is read from where the card visibly is, which may be part-way through an
        earlier slide, so a card displaced twice in quick succession carries on from where
        it had got to rather than snapping back to restart.

        Hands back the animation it started, or None when there was nothing to move: the
        settle has to know whether a ``finished`` is coming before it waits for one.
        """
        if origin == destination:
            return None
        if (running := self._anims.get(card)) is not None:
            running.stop()
        card.move(origin)
        anim = QPropertyAnimation(card, b"pos", card)
        anim.setDuration(duration)
        anim.setStartValue(origin)
        anim.setEndValue(destination)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anims[card] = anim
        anim.start()
        return anim

    def _index_at(self, dragged: QWidget) -> int:
        """Which slot *dragged* now covers, counted past the cards its middle has cleared.

        The *card's* middle and not the pointer's: the card travels with the pointer, so
        comparing the pointer alone makes how far you have to drag depend on where you
        grabbed. Take hold of a card near its top and it would need a row and a half of
        travel to pass one row.

        Every other card is measured at the slot it is settling into rather than where it
        is drawn this frame. A card mid-slide is in neither slot, so comparing against its
        live position lets a shift that is still playing decide the next one, and the
        target oscillates between two indices for as long as anything is moving.

        Clamped to the cards rather than allowed off either end: whatever sits before or
        after the run is not part of the ordered list, so a drag past the last card lands
        in the last card's slot.

        A carried block is measured by the middle of the whole block, and counted against
        the cards outside it: the slot is where the block goes among the rest.
        """
        block = self._lifted or [dragged]
        last = block[-1]
        height = last.y() + last.height() - dragged.y()
        middle = dragged.y() + height // 2
        index = 0
        rest = 0
        for card in self._cards:
            if card in block:
                continue
            rest += 1
            top = self._slots.get(card, card.pos()).y()
            if middle >= top + card.height() // 2:
                index += 1
        return max(0, min(index, rest))

    # ------------------------------------------------------------------
    # Auto-scroll
    # ------------------------------------------------------------------

    def _autoscroll_step(self) -> None:
        """Scroll the list while the drag is held near one of its ends.

        Without it the reachable part of the list is whatever happened to be in the
        viewport when the drag started, so moving a card from the bottom of a long list
        to the top is not a gesture the user can perform at all -- they have to drop it
        part-way, scroll, and pick it up again. Every list that reorders by drag does this,
        and its absence is only invisible while the list is short.

        The card is re-placed from the last known pointer position after each scroll: the
        pointer has not moved, so no mouse event is coming, but the content under it has,
        and the target slot with it.
        """
        card = self._press_card
        if not self._dragging or card is None or self._area is None:
            return
        viewport = self._area.viewport()
        y = viewport.mapFromGlobal(self._last_global).y()
        if y < AUTOSCROLL_MARGIN:
            step = -AUTOSCROLL_STEP
        elif y > viewport.height() - AUTOSCROLL_MARGIN:
            step = AUTOSCROLL_STEP
        else:
            return
        bar = self._area.verticalScrollBar()
        before = bar.value()
        bar.setValue(before + step)
        if bar.value() != before:
            self._drag_to(card, self._last_global)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _commit_order(self) -> None:
        """Report the new order, in the indices the host handed the cards over in.

        Silent when the drag put everything back where it started, so a wobble on a card
        does not rewrite settings and rebuild a list underneath the pointer.
        """
        order = [self._baseline.index(card) for card in self._cards]
        if order == list(range(len(order))):
            return
        self.reordered.emit(order)

    def _warm_offscreen_render(self) -> None:
        """The deferred half of :meth:`watch_for_show`. A no-op once the window is warmed."""
        if self._shown_host is not None:
            warm_offscreen_render(self._shown_host)
