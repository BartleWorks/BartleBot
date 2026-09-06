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
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
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
        # The in-flight drag: which card the button went down on, where, and whether the
        # pointer has since travelled far enough for it to be a drag and not a click.
        self._press_card: QWidget | None = None
        self._press_pos = QPoint()
        # How far down the card the button went down. A drag moves the card, not the
        # pointer, so where the user grabbed it is what says where the card now is.
        self._grab_offset = 0
        self._dragging = False
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
        layout = self._rows_layout()
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Assigned only after the previous cards have been deleteLater'd, so neither list
        # can hold a widget that is on its way out. A drag in flight goes with them.
        self._cards = list(cards)
        self._baseline = list(cards)
        self._press_card = None
        self._dragging = False

        for card in cards:
            self._add_row(card)
            card.installEventFilter(self)

        if self._add_text:
            add_card = AddCard(self._add_text)
            add_card.add_requested.connect(self.add_requested)
            self._add_row(add_card)

        if not cards and self._empty_hint:
            # ThemedTextLabel because the hint is replaced on every rebuild -- there is
            # nothing here to hold a reference on.
            hint = ThemedTextLabel(self._empty_hint, css="font-size: 11px;")
            hint.setWordWrap(True)
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(hint)

        layout.addStretch()

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
        """Turn a press-and-drag on a card into a reorder, and swallow the click it was not.

        Filtered here rather than implemented on the card because order is a fact about
        the column: a card knows what it stands for and nothing about what is above it.

        The drag has to be able to cancel the click, which is why a card activates on
        *release* (see ``_LaunchCard.mouseReleaseEvent``). Activating on press would have
        launched a game client, or opened an editor, on the first pixel of every drag --
        the same argument that keeps a trash icon off the launch screen's card faces.
        """
        if obj not in self._cards:
            return super().eventFilter(obj, event)
        kind = event.type()
        if kind == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._press_card = obj
            self._press_pos = event.globalPosition().toPoint()
            self._grab_offset = event.position().toPoint().y()
            self._dragging = False
        elif kind == event.Type.MouseMove and self._press_card is obj:
            moved = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
            if self._dragging or moved >= QApplication.startDragDistance():
                self._dragging = True
                self._drag_to(obj, event.globalPosition().toPoint())
        elif kind == event.Type.MouseButtonRelease and self._press_card is obj:
            was_drag = self._dragging
            self._press_card = None
            self._dragging = False
            if was_drag:
                self._commit_order()
                # Swallowed: the pointer came to rest on a card, but the gesture was a
                # move and not a click on it.
                return True
        return super().eventFilter(obj, event)

    def _drag_to(self, card: QWidget, global_pos: QPoint) -> None:
        """Move *card* to wherever the pointer now is, live.

        Reordering the layout as the pointer travels, rather than drawing a floating copy
        and an insertion line, because the column *is* the preview: a handful of cards is
        the whole list on screen at once, and seeing it in the order it would be left in
        reads better than an indicator saying where it would land.
        """
        target = self._index_at(global_pos, card)
        current = self._cards.index(card)
        if target == current:
            return
        self._cards.insert(target, self._cards.pop(current))
        self._rows_layout().insertWidget(target, card)

    def _index_at(self, global_pos: QPoint, dragged: QWidget) -> int:
        """Which slot *dragged* now covers, counted past the cards its middle has cleared.

        The *card's* middle and not the pointer's: the card travels with the pointer, so
        comparing the pointer alone makes how far you have to drag depend on where you
        grabbed. Take hold of a card near its top and it would need a row and a half of
        travel to pass one row.

        Clamped to the cards rather than allowed off the end: the Add card is the column's
        terminator and the ordered list does not extend past it, so a drag below the last
        card lands in the last card's slot.
        """
        y = self._area.widget().mapFromGlobal(global_pos).y()
        middle = y - self._grab_offset + dragged.height() // 2
        index = 0
        for card in self._cards:
            if card is dragged:
                continue
            if middle >= card.y() + card.height() // 2:
                index += 1
        return max(0, min(index, len(self._cards) - 1))

    def _commit_order(self) -> None:
        """Report the new order, in the indices the caller handed the cards over in.

        Silent when the drag put everything back where it started, so a wobble on a card
        does not rewrite settings and rebuild two columns underneath the pointer.
        """
        order = [self._baseline.index(card) for card in self._cards]
        if order == list(range(len(order))):
            return
        self.reordered.emit(order)


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
