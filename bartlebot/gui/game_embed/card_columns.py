"""One column of launch cards, and the two surfaces' shared way of drawing it.

The Quick Launch screen and the Game settings tab describe the same two things --
accounts and launch profiles -- and used to do it in two unrelated visual languages: a
column of cards on one, a ``QListWidget`` of formatted strings on the other. Nothing
about an account's colour, its roster or whether it would fit survived the trip to the
surface where accounts are actually *edited* (#192).

They draw the same cards now, out of this one widget, which owns everything a column has
that is not a card: its heading, its fit banner, the scroll area, the Add card that
terminates it, the line an empty column shows, and the drag that reorders it.

WHY ONE HOST AND NOT A LIST WIDGET

The tab keeping its ``QListWidget`` and gaining ``setItemWidget`` was the other option,
and it is worse than it looks. A ``QListWidget`` takes the row height from the *item*,
not from the widget in it, so a card whose sizeHint is 84px draws into a 12px row unless
something pushes a height into every item by hand. Fixed-height cards can hardcode that
(``strategy_picker_dialog.py`` does); these cannot -- an ``AccountCard`` re-fits its
character line to the width it got and a wrapped refusal line adds height too, so the
recompute would have to run from inside the resize Qt is already running, which is the
loop #191 came out of. ``takeItem``/``insertItem`` also drops the item-to-widget
association, so a reordered row draws empty.

So the column is a plain ``QVBoxLayout`` in a ``QScrollArea``, sized ``Expanding`` across
and ``Preferred`` down with height-for-width -- the arrangement #191 arrived at, now in
one place where it cannot be fixed on one surface and left broken on the other.

THE TWO SURFACES ARE STILL DIFFERENT

Same cards, two verbs. A body click launches on the launch screen and edits in the tab,
and a card that will not fit is greyed and refuses its clicks there but is only *noted*
in the tab -- greying out the row the user came here to fix is hostile, and the tab is
where they fix it. Both of those are the card's own flags (see ``launch_cards``); what
this module varies is the banner's "Change resolution" link, which the tab omits because
the combo box it would point at is already on screen above the columns.

HOW THE DRAG IS DRAWN

The reorder was always live -- the cards really did rearrange under the pointer -- and it
still surprised people, because nothing said which card was being held. The sort was the
only evidence a drag was happening, so what the user saw was the *other* cards moving on
their own. The gesture is the same one; what was missing was every signal around it:

- a **grip** (``launch_cards.GripHandle``) that says a card can be dragged at all, and is
  now the only place a drag starts, so the cursor can change exactly where dragging works;
- the dragged card **lifted out of the layout**, following the pointer continuously and
  leaving a ``_DropPlaceholder`` hole where it came from, instead of snapping a row at a
  time with nothing at all happening in between;
- displaced cards **sliding** into their new slots rather than teleporting into them;
- **auto-scroll** at the ends of the viewport, without which the reorderable part of a
  long column is only whatever was on screen when the drag began;
- a **settle** the rest of the gesture waits for, so that a drop is the one moment nothing
  else is happening -- see ``_end_drag``.

That is the arrangement Trello, Linear, the macOS Dock and the iOS home screen all
converge on, and each piece is doing separate work: the grip answers *can I*, the lift
answers *what am I holding*, the slide answers *what just changed*, the scroll answers
*where can I put it*. The alternative family -- a still list with a drop line, which is
what ``duel_strategy_editor`` does -- was not taken here for the reason in ``_drag_to``:
with four cards on screen the column *is* the preview, and showing the resulting order
beats a line predicting it.
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bartlebot.game_sizing import FitProblem
from bartlebot.gui import screen_fit
from bartlebot.gui.game_embed.launch_cards import AddCard, ProfileCard
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.settings_manager import SettingsManager

# What an empty column says beneath its Add card. The Add card is the empty state -- it
# replaced "(No accounts)" outright, and says more than that hint did -- but a first-run
# user does not yet know what a launch profile *is*, so one line of what the thing is
# stays, and only while the column is empty. Once there are cards, they are the
# explanation.
EMPTY_ACCOUNTS_HINT = "An account is one Wizard101 login. Add one and it launches from here in a click."
EMPTY_PROFILES_HINT = "A launch profile starts several accounts at once, in the order you give them."

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
# so a 1px dashed rounded rect draws as a row of dots that at this colour is a line you
# have to look for.
PLACEHOLDER_BORDER_WIDTH = 2
PLACEHOLDER_RADIUS = 4


def _size(pair: tuple[int, int]) -> str:
    return f"{pair[0]}×{pair[1]}"


def account_banner_text(problem: FitProblem | None) -> str:
    """What the Accounts column says when a single client will not fit, or "".

    Every card in that column asks the identical question -- one client, the *default*
    resolution, this screen -- so this sentence is the whole refusal for all of them and
    the cards carry none of it.

    It names the default resolution specifically, because that is the setting to change:
    an account card never launches at a pinned one. And it offers two ways out rather than
    the three a profile gets. "Launch fewer clients" is advice that cannot be taken here
    -- one account is one client -- and a remedy the user cannot act on reads as the app
    not knowing what it is refusing.
    """
    if problem is None:
        return ""
    return (
        f"Your default game resolution ({_size(problem.resolution)}) needs "
        f"{_size(problem.need)}, but only {_size(problem.available)} is available. "
        f"Either lower the default game resolution or increase the size of the game view panel."
    )


def profile_banner_text(problems: list[FitProblem], total: int) -> str:
    """What the Launch Profiles column says about *problems* out of *total*, or "".

    Sized against the *panel*, not a resolution, which is the one number every profile in
    the column shares: profiles may each pin their own resolution, so naming one would
    point at a setting that is not the problem for the cards beside it.

    Whether some or all of them fail changes what the user needs to be told. All of them
    is a fact about the panel and comes with the ways out. Some of them is a fact about
    those profiles -- the column still works, and each greyed card carries its own numbers
    saying what it wanted -- so the banner says which state the list is in and leaves the
    per-profile detail on the per-profile cards.
    """
    if not problems:
        return ""
    where = f"in the game view panel at its current size ({_size(problems[0].available)})"
    if len(problems) < total:
        return f"Some launch profiles do not fit {where} and have been disabled."
    if total == 1:
        return f"This launch profile does not fit {where}."
    return (
        f"No launch profiles fit {where}. Lower the resolution they launch at, "
        f"increase the size of the game view panel, or launch fewer clients."
    )


class _FitBanner(ThemedMixin, QWidget):
    """One column's "these will not fit" notice, and the way out of it.

    A fit refusal has a half that belongs to the entry -- how much room this profile
    wanted -- and a half that belongs to the column, because the ways out are properties
    of the layout rather than of any one profile. Saying the second half once here is what
    stops the account column printing the identical wrapped paragraph under every account
    name, which is what it did when each card carried the whole sentence.

    The two columns word it differently and deliberately: they are refusing different
    things, they point at different settings, and one of the three standard remedies does
    not apply to a single account at all. ``account_banner_text`` and
    ``profile_banner_text`` hold the wording, out here where they can be read and tested
    without a QApplication.

    The link goes to the same place as the launch screen's own "Game settings" -- the tab
    holding the resolution -- because advice the user then has to go hunting to follow is
    half an answer. It is dropped when the column is already *in* that tab: a link to a
    combo box eighty pixels above it teaches nothing, and one that looks like it goes
    somewhere and does not is worse than none.
    """

    settings_requested = pyqtSignal()

    def __init__(self, show_link: bool = True, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(2)
        # Height-for-width, for the same reason as a card's quiet line: a wrapping label
        # whose layout does not know it wraps reports a sizeHint height measured at some
        # other width, and the column inherits it.
        self._text = ThemedTextLabel("", color="warning")
        self._text.setWordWrap(True)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self._text.setSizePolicy(policy)
        self._text.setMinimumWidth(1)
        layout.addWidget(self._text)

        # Parented to the banner *at construction*, before the visibility below is set —
        # the same rule as a card's quiet line. A parentless widget is a top-level window,
        # so setVisible(True) on one puts a button-sized window on the screen for the
        # frame between here and addWidget reparenting it: a flash in the corner of the
        # display, twice, every time the launch screen is built.
        self._link = QPushButton("Change resolution", self)
        self._link.setFlat(True)
        self._link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._link.clicked.connect(self.settings_requested)
        self._link.setVisible(show_link)
        layout.addWidget(self._link, alignment=Qt.AlignmentFlag.AlignLeft)

        self.setVisible(False)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        self._link.setStyleSheet(
            f"color: {THEME.accent}; text-decoration: underline; border: none; padding: 0px; text-align: left;"
        )

    @property
    def text(self) -> str:
        """What the banner currently says -- empty while it is hidden."""
        return self._text.text()

    def set_text(self, text: str) -> None:
        """Show *text*, or hide the banner entirely when it is empty.

        Hidden rather than blank: an empty wrapping label still holds a line of the
        column's height, and that is scroll range over nothing once the cards below it
        fill the viewport.
        """
        self._text.setText(text)
        self.setVisible(bool(text))


class _DropPlaceholder(QWidget):
    """The hole a lifted card leaves in the column, and the slot it would drop into.

    A real widget in the layout rather than a painted hint, because it is doing the
    layout's work: it holds the dragged card's height open so the cards below do not
    close up the moment the card leaves the flow, and moving *it* between slots is what
    makes the other cards shift. The dragged card itself is out of the layout entirely,
    following the pointer, so without this the column would collapse by one card-height
    on the first pixel of every drag and everything below would jump twice.

    It is drawn as an outline rather than filled: what the user is looking for is where
    the card will land, and an empty dashed frame the exact size of the card in their
    hand says that more plainly than a block of colour, which reads as a card of its own.

    No theming ceremony -- ``THEME`` is read in ``paintEvent``, so there is no baked
    value to go stale and nothing for a theme switch to have to re-derive.
    """

    def __init__(self, height: int, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(height)
        # Expanding across to fill the column, exactly like the card it stands in for --
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


class CardColumn(QWidget):
    """A titled, scrolling, drag-reorderable column of cards with an Add card at its foot.

    Not a ``ThemedMixin``, and deliberately: every coloured thing in here owns its own
    palette already -- the heading is a ``ThemedTextLabel``, the rule is drawn by the
    style, the banner and the cards restyle themselves. A mixin here would be an
    ``apply_theme_styles`` with nothing to put in it.

    Handed whole cards rather than the things they stand for: what an account card *is*
    belongs to ``launch_cards``, and what its clicks mean belongs to the surface hosting
    it. This class knows only that it has a list of widgets, that the last row is an Add
    card which is not one of them, and that dragging one past another changes the order
    the user meant.
    """

    add_requested = pyqtSignal()
    # The cards' previous indices, in the order they are in now. An index permutation
    # rather than the cards themselves or the names on them, because this widget
    # deliberately does not know what a card stands for -- and the caller, which does,
    # still holds the list it built them from, in exactly that order.
    reordered = pyqtSignal(list)
    settings_requested = pyqtSignal()

    def __init__(
        self,
        title: str,
        empty_hint: str = "",
        add_text: str = "",
        show_settings_link: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._empty_hint = empty_hint
        self._add_text = add_text
        # The order the caller handed the cards over in, and the order they are in now.
        # The first is what a reorder is reported against, and it moves only when the
        # caller hands over a fresh list.
        self._baseline: list[QWidget] = []
        self._cards: list[QWidget] = []
        # The Add card at the foot, when this column has one. Not a member of ``_cards``
        # -- it is the column's terminator, not an entry in the ordered list.
        self._add_card: QWidget | None = None
        # The in-flight drag: which card the button went down on, where, and whether the
        # pointer has since travelled far enough for it to be a drag and not a click.
        self._press_card: QWidget | None = None
        self._press_pos = QPoint()
        # How far down the card the button went down. A drag moves the card, not the
        # pointer, so where the user grabbed it is what says where the card now is.
        self._grab_offset = 0
        self._dragging = False
        # Each card's grip, and the card it belongs to. The grip is the drag source now,
        # so the event filter goes on it and has to be able to get back to the card.
        self._grips: dict[QWidget, QWidget] = {}
        # The hole the lifted card left, and the x and width it keeps while it is out of
        # the layout -- taken once at lift, because the layout is no longer setting them.
        self._placeholder: _DropPlaceholder | None = None
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
        # The card that has been let go and is still flying into its slot. The gesture is
        # not over while this is set: the card is still out of the layout, the placeholder
        # is still holding its hole, and the new order has not been reported yet.
        self._settling: QWidget | None = None
        # Exactly balanced against restoreOverrideCursor; see _release_cursor.
        self._cursor_pushed = False
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(AUTOSCROLL_INTERVAL_MS)
        self._autoscroll.timeout.connect(self._autoscroll_step)
        self._build_ui(title, show_settings_link)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self, title: str, show_settings_link: bool) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header = ThemedTextLabel(title)
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(rule)

        self._banner = _FitBanner(show_link=show_settings_link)
        self._banner.settings_requested.connect(self.settings_requested)
        layout.addWidget(self._banner)

        self._area = QScrollArea()
        self._area.setWidgetResizable(True)
        self._area.setFrameShape(QFrame.Shape.NoFrame)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        rows = QVBoxLayout(container)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(4)
        rows.addStretch()
        self._area.setWidget(container)
        layout.addWidget(self._area, stretch=1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def cards(self) -> list[QWidget]:
        """The column's cards, in the order they are drawn. Never the Add card."""
        return list(self._cards)

    @property
    def scroll_area(self) -> QScrollArea:
        """The column's viewport, for the callers and tests that measure what it drew."""
        return self._area

    @property
    def add_card(self) -> QWidget | None:
        """The card at the column's foot, or None for a column built without one.

        Public because a caller may need to *point at* it: the profile editor's Add card
        opens a picker popup, and a popup is positioned against the widget it belongs to.
        Held rather than found again, since the whole row set is replaced on every
        rebuild and a stale reference would anchor to a widget on its way out.
        """
        return self._add_card

    @property
    def banner_text(self) -> str:
        """What the column's fit banner currently says -- empty while it is hidden."""
        return self._banner.text

    def set_banner_text(self, text: str) -> None:
        """Say *text* above the cards, or hide the banner when it is empty."""
        self._banner.set_text(text)

    def set_cards(self, cards: list[QWidget]) -> None:
        """Replace the column's contents with *cards*, then its Add card.

        The Add card is always last and is never one of *cards*: it is the column's
        terminator rather than a member of the ordered list, so nothing is dropped after
        it and a caller walking ``cards`` to judge fit never sees it (#195).

        The empty hint is drawn beneath it only while there are no cards at all, where it
        is the one line saying what the thing being added is.
        """
        # Before anything is torn down: a rebuild can arrive mid-drag -- it is what
        # ``reordered`` provokes -- and the teardown below would otherwise strand the
        # closed-hand cursor over an app that is no longer dragging anything.
        self._cancel_drag()

        # Read before the rows go and put back after they return. Emptying the layout
        # collapses the scroll range to nothing, and a scroll bar whose range collapses
        # loses its value on the way: the column would come back scrolled to the top.
        # That is invisible on a short column and, on a long one, is the whole content
        # jumping under the pointer at the end of every drag that auto-scrolled to reach
        # its slot -- the drag ends by showing the user somewhere they did not drop it.
        scrolled_to = self._area.verticalScrollBar().value()

        layout = self._rows_layout()
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Assigned only after the previous cards have been deleteLater'd, so neither list
        # can hold a widget that is on its way out. A drag in flight goes with them.
        self._cards = list(cards)
        self._baseline = list(cards)
        self._add_card = None
        self._grips = {}
        self._slots = {}
        self._anims = {}

        for card in cards:
            self._add_row(card)
            # The card body is not a drag source and no longer needs watching; the grip
            # is, and a card built without one simply cannot be reordered by mouse.
            grip = getattr(card, "grip", None)
            if grip is not None:
                grip.installEventFilter(self)
                self._grips[grip] = card

        if self._add_text:
            add_card = AddCard(self._add_text)
            add_card.add_requested.connect(self.add_requested)
            self._add_row(add_card)
            self._add_card = add_card

        if not cards and self._empty_hint:
            # ThemedTextLabel because the hint is replaced on every rebuild -- there is
            # nothing here to hold a reference on.
            hint = ThemedTextLabel(self._empty_hint, css="font-size: 11px;")
            hint.setWordWrap(True)
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(hint)

        layout.addStretch()
        # Activated first, so the bar has its range back before the value is handed to it
        # -- setValue against a range that is still zero is silently clamped to zero.
        layout.activate()
        self._area.verticalScrollBar().setValue(scrolled_to)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rows_layout(self):
        return self._area.widget().layout()

    def _add_row(self, card: QWidget) -> None:
        # Expanding across, so a card fills the column; height-for-width down, so the
        # column asks each card how tall it is *at the width it got*. Fixed vertically
        # is what this used to be, and it pinned both the card's minimum and its maximum
        # to a sizeHint that a wrapped refusal line computes at some unrelated width --
        # with setWidgetResizable the scroll area then could not be shorter than that
        # sum, which is the scroll bar over empty space in #191.
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        card.setSizePolicy(policy)
        self._rows_layout().addWidget(card)

    # ------------------------------------------------------------------
    # Drag reordering
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        """Turn a press-and-drag on a card's grip into a reorder.

        Filtered here rather than implemented on the card because order is a fact about
        the column: a card knows what it stands for and nothing about what is above it.
        The grip is only the card's half of the arrangement -- the place a press is
        allowed to start from, and the one thing on the card whose cursor may say so.

        The body is deliberately not a drag source. It was, and it worked in silence:
        nothing on a card announced that it could be dragged, and nothing could be made to
        announce it, because the cursor is how that is normally said and a card whose
        primary verb is *launch* cannot wear an open hand over its whole face. Moving the
        gesture onto a handle is what let the cursor tell the truth in both places.
        """
        card = self._grips.get(obj)
        if card is None:
            return super().eventFilter(obj, event)
        kind = event.type()
        if kind == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if self._settling is not None:
                # A previous drop is still in the air: its card is out of the layout and
                # its placeholder still holds a slot, so a second drag started now would
                # be measured against a column that is one row out. The window is the
                # hundred milliseconds of the settle, and losing a press inside it costs
                # the user one grab.
                return super().eventFilter(obj, event)
            self._press_card = card
            self._press_pos = event.globalPosition().toPoint()
            # Measured against the card, not the grip: the card is what travels, so where
            # the pointer sits within *it* is what says where it now is.
            self._grab_offset = card.mapFromGlobal(event.globalPosition().toPoint()).y()
            self._dragging = False
        elif kind == event.Type.MouseMove and self._press_card is card:
            pos = event.globalPosition().toPoint()
            moved = (pos - self._press_pos).manhattanLength()
            if not self._dragging and moved >= QApplication.startDragDistance():
                self._begin_drag(card)
            if self._dragging:
                self._drag_to(card, pos)
        elif kind == event.Type.MouseButtonRelease and self._press_card is card:
            if self._dragging:
                self._end_drag(card)
            else:
                self._press_card = None
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Lifting a card out of the column, and putting it back
    # ------------------------------------------------------------------

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
        layout = self._rows_layout()
        index = layout.indexOf(card)
        self._drag_x = card.x()
        self._drag_width = card.width()
        self._placeholder = _DropPlaceholder(card.height(), self._area.widget())
        # Shown by hand, and only ever after the parent it was constructed with. A
        # QBoxLayout skips a hidden widget outright rather than reserving its space, so a
        # placeholder left unshown holds nothing open: the column closes up by one card
        # the instant the drag begins, and every slot the drag is measured against is a
        # row out of place. (Constructed parented, per the never-show-a-parentless-widget
        # rule -- an unparented show here would be a card-sized window on the desktop.)
        self._placeholder.show()
        layout.removeWidget(card)
        layout.insertWidget(index, self._placeholder)
        layout.activate()
        # Raised after the placeholder is in, so the card is above every sibling it is
        # about to travel over -- including the hole it just left.
        card.raise_()
        card.resize(self._drag_width, card.height())
        self._dragging = True
        self._remember_slots(card)
        if hasattr(card, "set_dragging"):
            card.set_dragging(True)
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
        card also jumped whatever distance it had left, and the column it landed in was a
        different set of widgets: the flash.

        So nothing structural happens here. The card stays lifted, the placeholder keeps
        holding its slot open, and the only thing moving is the card. What the drop is
        *worth* -- the layout swap, the lifted state coming off, the new order going out
        -- waits for ``_finish_drop`` at the far end of the flight, by which point the
        column is already drawn exactly as the rebuild will redraw it, and the rebuild is
        a replacement of identical rows rather than a visible event.

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
        # tidy that up -- so a drop during a shift would leave the column visibly out of
        # alignment for the length of the settle.
        if self._placeholder is None:
            self._finish_drop(card)
            return
        anim = self._animate(card, card.pos(), self._placeholder.pos(), CARD_SETTLE_MS)
        if anim is None:
            self._finish_drop(card)
            return
        anim.finished.connect(lambda: self._finish_drop(card))

    def _finish_drop(self, card: QWidget) -> None:
        """Land *card* in the layout, take the lifted state off it, and report the order.

        Guarded on ``_settling`` because it is reached from a queued animation signal: a
        rebuild or a close arriving mid-flight has already abandoned this drop, and its
        cards may be on their way out. ``_cancel_drag`` clearing the flag is what makes
        the connection that outlives it harmless.

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

        layout = self._rows_layout()
        if self._placeholder is not None:
            index = layout.indexOf(self._placeholder)
            layout.removeWidget(self._placeholder)
            self._placeholder.deleteLater()
            self._placeholder = None
            layout.insertWidget(index, card)
        layout.activate()
        if hasattr(card, "set_dragging"):
            card.set_dragging(False)
        self._commit_order()

    def _cancel_drag(self) -> None:
        """Abandon an in-flight drag without reporting it. A no-op when there is none.

        The cards are about to be deleted, so nothing here tries to put one back: what
        must not be skipped is the cursor and the timer, which belong to the application
        and to this column and would both outlive the widgets they were started for.

        A drop still in the air is abandoned rather than completed. Clearing ``_settling``
        is what disarms the animation's ``finished`` connection, which is queued behind
        this and would otherwise run ``_finish_drop`` against a card that has been deleted
        -- and report an order for a column that no longer exists.
        """
        self._autoscroll.stop()
        self._pop_cursor()
        self._press_card = None
        self._dragging = False
        self._settling = None
        for anim in self._anims.values():
            anim.stop()
        self._anims = {}
        self._slots = {}
        if self._placeholder is not None:
            self._placeholder.deleteLater()
            self._placeholder = None

    def _push_cursor(self) -> None:
        """Close the hand everywhere for the length of the drag.

        An application override rather than a cursor on the grip alone, because the
        pointer spends the drag over whatever the card is passing -- other cards, the
        banner, the gap below the column -- and a hand that opens again the moment it
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
        the column being torn down.
        """
        if self._cursor_pushed:
            QApplication.restoreOverrideCursor()
            self._cursor_pushed = False

    # ------------------------------------------------------------------
    # Following the pointer
    # ------------------------------------------------------------------

    def _drag_to(self, card: QWidget, global_pos: QPoint) -> None:
        """Move *card* to wherever the pointer now is, and re-sort the column under it.

        Reordering the layout as the pointer travels, rather than drawing a drop line and
        leaving the list still, because the column *is* the preview: a handful of cards is
        the whole list on screen at once, and seeing it in the order it would be left in
        reads better than an indicator saying where it would land.

        Positioned in the container's coordinates from a *global* pointer, which is what
        makes auto-scrolling work for free: after a scroll the same global position maps
        to a different container y, so the card stays put under the user's hand while the
        content moves past it.
        """
        self._last_global = global_pos
        y = self._area.widget().mapFromGlobal(global_pos).y() - self._grab_offset
        card.move(self._drag_x, y)

        target = self._index_at(card)
        current = self._cards.index(card)
        if target == current:
            return
        before = {other: other.pos() for other in self._cards if other is not card}
        self._cards.insert(target, self._cards.pop(current))
        layout = self._rows_layout()
        layout.removeWidget(self._placeholder)
        layout.insertWidget(target, self._placeholder)
        # Forced now rather than left to the next event-loop turn: the new positions are
        # needed *this* call, both as the animations' destination and as what the next
        # move compares against.
        layout.activate()
        self._remember_slots(card)
        for other, origin in before.items():
            self._animate(other, origin, other.pos(), CARD_SHIFT_MS)

    def _remember_slots(self, dragged: QWidget) -> None:
        """Record where every other card has just been laid out."""
        self._slots = {card: card.pos() for card in self._cards if card is not dragged}

    def _animate(self, card: QWidget, origin: QPoint, destination: QPoint, duration: int) -> QPropertyAnimation | None:
        """Slide *card* from *origin* to *destination* instead of letting it appear there.

        A displaced card used to teleport, which is legible when one card moves and
        unreadable when three do -- the column simply looked different than it had a frame
        earlier, and the user had to re-find the card they were holding. Motion is what
        makes a swap something you watched happen rather than something you have to
        notice; it is the whole reason iOS, Trello and the macOS Dock feel the way they do.

        Parented to the card and not to the column, deliberately. ``reordered`` rebuilds
        both columns, so these widgets are routinely deleted while an animation is still
        running on them -- an animation owned by the column would go on driving a property
        of a freed object. Owned by its own target, it dies with it.

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

        Clamped to the cards rather than allowed off the end: the Add card is the column's
        terminator and the ordered list does not extend past it, so a drag below the last
        card lands in the last card's slot.
        """
        middle = dragged.y() + dragged.height() // 2
        index = 0
        for card in self._cards:
            if card is dragged:
                continue
            top = self._slots.get(card, card.pos()).y()
            if middle >= top + card.height() // 2:
                index += 1
        return max(0, min(index, len(self._cards) - 1))

    # ------------------------------------------------------------------
    # Auto-scroll
    # ------------------------------------------------------------------

    def _autoscroll_step(self) -> None:
        """Scroll the column while the drag is held near one of its ends.

        Without it the reachable part of the list is whatever happened to be in the
        viewport when the drag started, so moving an account from the bottom of a long
        column to the top is not a gesture the user can perform at all -- they have to
        drop it part-way, scroll, and pick it up again. Every list that reorders by drag
        does this, and its absence is only invisible while the column is short.

        The card is re-placed from the last known pointer position after each scroll: the
        pointer has not moved, so no mouse event is coming, but the content under it has,
        and the target slot with it.
        """
        card = self._press_card
        if not self._dragging or card is None:
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
        """Report the new order, in the indices the caller handed the cards over in.

        Silent when the drag put everything back where it started, so a wobble on a card
        does not rewrite settings and rebuild two columns underneath the pointer.
        """
        order = [self._baseline.index(card) for card in self._cards]
        if order == list(range(len(order))):
            return
        self.reordered.emit(order)

    def closeEvent(self, event) -> None:
        """Never leave a closed hand behind on a column that is going away."""
        self._cancel_drag()
        super().closeEvent(event)


def apply_order(items: list, order: list[int]) -> list | None:
    """*items* rearranged into *order*, or None if *order* does not describe *items*.

    The inverse of what ``CardColumn.reordered`` emits, and it lives beside that signal
    rather than beside any one of its listeners: three surfaces now take the permutation,
    and a guard that only two of them apply is a guard.

    Guarded rather than trusted, because the permutation crosses a signal from a column
    that was built out of a list read some time earlier, and either surface — or a delete
    the same gesture raced — can have rewritten it in between. Refusing a stale
    permutation costs the user one drag; applying one silently reorders a list that is not
    the one they dragged, and can drop an account off the end of it.
    """
    if sorted(order) != list(range(len(items))):
        return None
    return [items[index] for index in order]


def refresh_column_fit(
    accounts: CardColumn,
    profiles: CardColumn,
    widget: QWidget,
    live_area: tuple[int, int] | None,
) -> None:
    """Re-judge both columns' cards against the game area *live_area* describes.

    Cheap by construction: ``set_unavailable`` returns immediately when the reason has not
    changed, so a splitter drag costs a few comparisons per card per event and restyles
    only on the frame where a verdict actually flips.

    Each column's banner is then given what its refusals have in common. The account
    column's answer is one answer by construction -- every card asks the same question --
    so the whole sentence, numbers included, belongs there and not on each card. A profile
    column's cards keep their own numbers; see ``_FitBanner``.

    Shared by both surfaces because it is the same question on each. What differs is only
    what a card *does* with the answer, which is that card's own ``refuses_when_unfit``:
    the launch screen greys and refuses, the settings tab merely notes. The launch screen
    measures itself and the settings tab is handed the panel's live size, so the
    measurement arrives as an argument either way.
    """
    resolution = SettingsManager.instance().game_resolution
    # One client, deliberately: an account card on the launch screen always launches the
    # first one, because that screen is only up when nothing is embedded. The +1 case --
    # adding a client to a running fleet -- belongs to the Launch menu, which is reachable
    # while tiles are on screen.
    account_problem = screen_fit.fit_details(resolution, 1, widget, live_area)
    account_cards = accounts.cards
    for card in account_cards:
        card.set_unavailable(account_problem)
    accounts.set_banner_text(account_banner_text(account_problem) if account_cards else "")

    problems: list[FitProblem] = []
    profile_cards = [card for card in profiles.cards if isinstance(card, ProfileCard)]
    for card in profile_cards:
        problem = screen_fit.profile_fit_details(card.profile, widget, live_area)
        card.set_unavailable(problem)
        if problem is not None:
            problems.append(problem)
    profiles.set_banner_text(profile_banner_text(problems, len(profile_cards)))
