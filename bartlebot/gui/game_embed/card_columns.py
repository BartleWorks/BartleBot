"""One column of launch cards, and the two surfaces' shared way of drawing it.

The Quick Launch screen and the Game settings tab describe the same two things --
accounts and launch profiles -- and used to do it in two unrelated visual languages: a
column of cards on one, a ``QListWidget`` of formatted strings on the other. Nothing
about an account's color, its roster or whether it would fit survived the trip to the
surface where accounts are actually *edited*.

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
loop the empty-scroll-bar fix came out of. ``takeItem``/``insertItem`` also drops the item-to-widget
association, so a reordered row draws empty.

So the column is a plain ``QVBoxLayout`` in a ``QScrollArea``, sized ``Expanding`` across
and ``Preferred`` down with height-for-width -- the arrangement the empty-scroll-bar fix arrived at, now in
one place where it cannot be fixed on one surface and left broken on the other.

THE TWO SURFACES ARE STILL DIFFERENT

Same cards, two verbs. A body click selects on the launch screen, where a double-click or
the Launch button launches, and edits in the tab. A card that will not fit is grayed and
refuses to launch there but is only *noted* in the tab -- graying out the row the user came here to fix is hostile, and the tab is
where they fix it. Both of those are the card's own flags (see ``launch_cards``); what
this module varies is the banner's "Change resolution" link, which the tab omits because
the combo box it would point at is already on screen above the columns.

THE DRAG

The drag that reorders a column is ``widgets.drag_reorder.DragReorder``'s, which is where
its reasoning lives too: it began here and moved out when the Combat tab's cards needed
the same gesture. What stays here is that this column is one of its hosts -- it hands the
engine its cards on every ``set_cards`` and passes ``reordered`` straight on.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bartlebot.game.game_sizing import FitProblem
from bartlebot.gui import screen_fit
from bartlebot.gui.game_embed.launch_cards import AddCard, ProfileCard
from bartlebot.gui.theming.theme import THEME
from bartlebot.gui.widgets.buttons.link_button import LinkButton
from bartlebot.gui.widgets.cards.card_frame import GRIP_GUTTER
from bartlebot.gui.widgets.labels.themed_label import ThemedTextLabel
from bartlebot.gui.widgets.layout.drag_reorder import (  # noqa: F401 -- re-exported for callers of this module
    AUTOSCROLL_MARGIN,
    CARD_SETTLE_MS,
    CARD_SHIFT_MS,
    DragReorder,
)
from bartlebot.gui.widgets.layout.drag_reorder import DropPlaceholder as _DropPlaceholder  # noqa: F401
from bartlebot.settings_manager import SettingsManager

# What an empty column says beneath its Add card. The Add card is the empty state -- it
# replaced "(No accounts)" outright, and says more than that hint did -- but a first-run
# user does not yet know what a launch profile *is*, so one line of what the thing is
# stays, and only while the column is empty. Once there are cards, they are the
# explanation.
# "Wizard101", because testers read a bare "account" as one made with BartleBot (#261).
ACCOUNTS_TITLE = "Wizard101 accounts"
ADD_ACCOUNT = "Add Wizard101 account"
EMPTY_ACCOUNTS_HINT = "An account is one Wizard101 login. Add one to launch it from here."
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
    those profiles -- the column still works, and each grayed card carries its own numbers
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


class _FitBanner(QWidget):
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
        self._link = LinkButton("Change resolution", parent=self)
        self._link.clicked.connect(self.settings_requested)
        self._link.setVisible(show_link)
        layout.addWidget(self._link, alignment=Qt.AlignmentFlag.AlignLeft)

        self.setVisible(False)

    # No ``apply_theme_styles``: the banner holds no color of its own any more. Both of
    # its children re-derive their own on a theme switch, which is the whole point of
    # them being ``ThemedTextLabel`` and ``LinkButton`` rather than styled in here.

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

    Not a ``ThemedMixin``, and deliberately: every colored thing in here owns its own
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
        # How far the heading, the rule and the banner sit in from each side, and the
        # cards from the right. A card's frame starts a grip's gutter in from the column's
        # left edge, because the grip sits beside the frame; with the rule spanning the
        # full width, every card looked narrower than its column. Insetting the rule to the
        # frames puts the grips outside the column as drawn, and the matching gutter on the
        # right keeps that column centered where it was. The grips cannot leave the
        # column's *widget*: the scroll area clips what is outside it, and a grip that was
        # not the card's own child would hide itself as the pointer left the card for it
        # (see ``GripHandle``).
        #
        # Only under a heading: an untitled column draws no rule to line up with, and the
        # right gutter would only narrow its cards -- the Players tab's, in a sidebar.
        self._inset = GRIP_GUTTER if title else 0
        # The Add card at the foot, when this column has one. Not one of ``cards`` -- it
        # is the column's terminator, not an entry in the ordered list.
        self._add_card: QWidget | None = None
        self._build_ui(title, show_settings_link)
        # The drag, and the first-show warm-up it needs: see ``widgets/layout/drag_reorder.py``.
        self._drag = DragReorder(self._rows_layout(), self)
        self._drag.reordered.connect(self.reordered)
        self._drag.watch_for_show(self)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self, title: str, show_settings_link: bool) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Everything above the cards, inset by the grip's gutter on both sides. See
        # ``_inset``.
        top = QVBoxLayout()
        top.setContentsMargins(self._inset, 0, self._inset, 0)
        top.setSpacing(layout.spacing())
        layout.addLayout(top)

        # An empty *title* draws no heading and no rule, rather than an empty bold label
        # above a line that then reads as a stray divider. That is for a column whose
        # surface already names it: the Players tab's own label says "Players", and a
        # second heading under it is the panel saying the same word twice.
        if title:
            header = ThemedTextLabel(title)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            top.addWidget(header)

            rule = QFrame()
            rule.setFrameShape(QFrame.Shape.HLine)
            rule.setFrameShadow(QFrame.Shadow.Sunken)
            top.addWidget(rule)

        self._banner = _FitBanner(show_link=show_settings_link)
        self._banner.settings_requested.connect(self.settings_requested)
        top.addWidget(self._banner)

        self._area = QScrollArea()
        self._area.setWidgetResizable(True)
        self._area.setFrameShape(QFrame.Shape.NoFrame)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        rows = QVBoxLayout(container)
        # The right side's matching gutter, set by ``_fit_right_gutter``: the cards carry
        # the left one themselves, so their frames end where the rule does.
        rows.setContentsMargins(0, 0, self._inset, 0)
        self._area.verticalScrollBar().installEventFilter(self)
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
        return self._drag.cards

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
        it and a caller walking ``cards`` to judge fit never sees it.

        The empty hint is drawn beneath it only while there are no cards at all, where it
        is the one line saying what the thing being added is.
        """
        # Before anything is torn down: a rebuild can arrive mid-drag -- it is what
        # ``reordered`` provokes -- and the teardown below would otherwise strand the
        # closed-hand cursor over an app that is no longer dragging anything.
        self._drag.cancel()

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

        self._add_card = None

        for card in cards:
            self._add_row(card)
        # The grips are the drag sources; a card built without one simply cannot be
        # reordered by mouse.
        self._drag.set_cards(cards)

        if self._add_text:
            add_card = AddCard(self._add_text)
            add_card.add_requested.connect(self.add_requested)
            self._add_row(add_card)
            self._add_card = add_card

        if not cards and self._empty_hint:
            # ThemedTextLabel because the hint is replaced on every rebuild -- there is
            # nothing here to hold a reference on.
            hint = ThemedTextLabel(self._empty_hint, css=f"font-size: {THEME.font_small_px}px;")
            hint.setWordWrap(True)
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # Centered under the Add card's frame, which starts a gutter in, not under
            # the row, which the right gutter has already ended a gutter early.
            hint.setContentsMargins(self._inset, 0, 0, 0)
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

    def eventFilter(self, obj, event) -> bool:
        """Refit the right gutter whenever the scroll bar comes or goes."""
        if obj is self._area.verticalScrollBar() and event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self._fit_right_gutter()
        return super().eventFilter(obj, event)

    def _fit_right_gutter(self) -> None:
        """Let a visible scroll bar take its width out of the right gutter, not the cards.

        The bar narrows the viewport the cards are laid out in. With the full gutter still
        taken out of what is left, the cards ended a bar's width short of the rule and
        sat off center under it. So the gutter shrinks by the bar's width while it shows,
        and the bar sits in the gutter instead. That keeps the cards one width whether
        the bar is shown or not -- which matters beyond looks: a card that wrapped
        differently at a new width could change the column's height, and with it whether
        the bar is needed at all.

        Asked with ``isVisibleTo`` the scroll area, never ``isHidden``: Qt hides a
        container the bar sits in rather than the bar, so once a column had overflowed
        even once, its bar went on reporting itself shown and the column kept giving up
        the bar's width with no bar there.
        """
        bar = self._area.verticalScrollBar()
        taken = bar.sizeHint().width() if bar.isVisibleTo(self._area) else 0
        self._rows_layout().setContentsMargins(0, 0, max(0, self._inset - taken), 0)

    def _add_row(self, card: QWidget) -> None:
        # Expanding across, so a card fills the column; height-for-width down, so the
        # column asks each card how tall it is *at the width it got*. Fixed vertically
        # is what this used to be, and it pinned both the card's minimum and its maximum
        # to a sizeHint that a wrapped refusal line computes at some unrelated width --
        # with setWidgetResizable the scroll area then could not be shorter than that
        # sum, which is the scroll bar over empty space.
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        card.setSizePolicy(policy)
        self._rows_layout().addWidget(card)

    def closeEvent(self, event) -> None:
        """Never leave a closed hand behind on a column that is going away."""
        self._drag.cancel()
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
    the launch screen grays and refuses, the settings tab merely notes. The launch screen
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
