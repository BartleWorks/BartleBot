"""The launch profile editor: which accounts a profile brings, in which order, at what size.

The last surface still speaking the language from before the Game tab card rework, and
the worst place for it to have survived. This is where a profile's membership is *decided*, and it said less about each
account than the read-only cards behind it: a ``QListWidget`` of display strings, a pair
of ↑/↓ buttons, and an ``Add ▾`` menu that repopulated itself on every open and offered
"(no more accounts)" once it ran dry. A user picked accounts here and then went back to a
screen where those same accounts carry a colour, a roster and a fit verdict, with nothing
teaching them to read one surface from the other.

It draws the same ``AccountCard``s in the same ``CardColumn`` now, in ``CardMode.MEMBER``:
a row in a list being assembled, so the body has no verb, the grip is pinned open, and the
card's one button is a cross that takes it out of the profile.

TWO CONTROLS, ONE JOB EACH

The picker owns the *set* and the column owns the *order*. That split is the whole design:

- The account picker is a ``PickerPopup``, the same multi-select the bot tags use, and its
  selection **is** the profile's membership. Ticking four accounts is one open of one
  popup, where the old menu was four opens of a menu that closed after each pick and had
  to be rebuilt each time to hide what was already chosen. It also comes with a filter
  box, which the ``Add ▾`` menu could never have. It opens from a fixed **Add** button
  beside the column rather than a card at its foot: a foot-of-the-list card moves every
  time a tick or a cross changes how many rows are above it, so a popup anchored there
  drifts out from under itself mid-edit -- the one moment the anchor most needs to hold
  still. Beside the column, the button never moves regardless of what the list does.
- The column owns the order, because ``PickerPopup`` reports its selection in section
  order and the order here is *load-bearing* -- it decides which window becomes Player 1.
  So a tick appends, an untick drops in place, and rearranging is the drag the other two
  surfaces already use rather than a pair of arrow buttons and a selected row.

ONE REMOVAL PATH

There are two ways to take an account out -- the cross on its card, and unticking it in
the picker -- and they are the same call. Both go through :func:`reconcile_membership`,
which is handed the order that exists and the set that should exist and returns the order
that should exist: everything still chosen keeps its place, everything new lands at the
end. Two code paths maintaining one list is how you get a card whose cross removes it
while the popup still shows it ticked.

Nothing has to be pushed back the other way, because the popup holds no lasting state:
it is built fresh from the order on every open, so ``_order`` is the only copy of the
membership in the dialog and the two controls cannot drift. That is also why this is a
bare ``PickerPopup`` rather than a ``PickerButton`` -- the button exists to *show* a
selection between opens, and here the column above it is already doing that, in more
detail than a button could hold.

WHAT STAYED A COMBO BOX

The resolution. It is not card-shaped, ``PickerPopup`` is multi-select to its bones, and
the Game tab's global resolution is the same control -- so making this one a popup would
either split the two or drag a second surface along. The greyed-out entries and the
sentence beneath it say the same things they always did, judged against this profile's own
account count, which is what makes a fourth account visibly cost a resolution tier.

LIST ON THE LEFT, CONTROLS ON THE RIGHT

The Add button and the resolution combo are both things you set once and glance at, not
things you read a list of, so they share a column of their own beside the cards rather
than stacking above or below them. That also settles where the Add button lives without
another rule: it sits with the other control that is not a card, not with the cards.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot import character_roster
from bartlebot.credentials import CredentialStore
from bartlebot.gui import screen_fit
from bartlebot.gui.account_accent import account_accent, account_color_keys
from bartlebot.gui.game_embed.card_columns import CardColumn, apply_order
from bartlebot.gui.game_embed.launch_cards import AccountCard, CardMode
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.picker_popup import PickerPopup, PickerRow, Section, should_open
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.settings_manager import VALID_RESOLUTIONS, SettingsManager

# Wide enough for an account card to draw what it knows, plus the Add button and
# resolution combo beside it. An ``AccountCard`` fits its character line to the width it
# is given and is allowed three rows of it, so a column narrower than about 420 wears a
# "+3 more" on every card -- the card's whole reason for existing, truncated by the
# dialog holding it -- and the side column beside it needs its own room on top of that.
PROFILE_DIALOG_MIN_WIDTH = 620

# Tall enough for four cards and the controls around them, four being the account count
# the resolution ladder is interesting at. Below this the column scrolls immediately and
# the drag that reorders it has nowhere to go.
PROFILE_DIALOG_MIN_HEIGHT = 480

#: The column's heading. Says the ordering is real, which the ``P1``…``Pn`` pills on the
#: cards then let the user read rather than count.
ACCOUNTS_COLUMN_TITLE = "Accounts (in player order)"

#: Shown only while the profile has no accounts at all, in the empty column. The Add
#: button is beside the column rather than inside it now, so this is the only thing an
#: empty column says -- it has to name what the thing being built *is*, the way the other
#: two columns' empty hints do, rather than leaning on an Add card above it. An empty
#: profile stays saveable either way: a half-built profile is a legitimate thing to close
#: the dialog on.
EMPTY_PROFILE_HINT = "A profile launches its accounts together, in the order you list them here."

#: What the Add button beside the column says, and the popup it opens. Named as an
#: action on *this profile* rather than the bare "Add accounts" the column-foot version
#: used, since standing apart from the list it no longer reads as one of that list's own
#: rows -- and "Add accounts" alone suggests entering account details again, not choosing
#: among the ones already stored.
ADD_BUTTON_TEXT = "Add/remove profile accounts"
PICKER_TITLE = "Profile accounts"


def mark_unfittable_resolutions(
    combo: QComboBox,
    client_count: int,
    widget: QWidget,
    live_area: tuple[int, int] | None = None,
) -> None:
    """Grey out every entry in *combo* that *client_count* clients could not fit at.

    Disabled and explained rather than dropped from the list, which is the same rule the
    launch surfaces follow: a user who cannot find 1920x1080 in the list learns nothing,
    while one who sees it greyed out learns that their screen is the reason and that
    fewer clients would bring it back.

    The item's own resolution is read from its ``userData``, so the "Use global setting"
    entry — which carries None — is skipped: what it resolves to is the global combo's
    business, and that combo polices itself.
    """
    model = combo.model()
    for index in range(combo.count()):
        resolution = combo.itemData(index)
        if resolution is None:
            continue
        reason = screen_fit.fit_problem(tuple(resolution), client_count, widget, live_area)
        item = model.item(index)
        if item is not None:
            item.setEnabled(reason is None)
        combo.setItemData(index, reason or "", Qt.ItemDataRole.ToolTipRole)


# ---------------------------------------------------------------------------
# Pure, so the one rule both removal paths obey can be tested without a QApplication.
# ---------------------------------------------------------------------------


def reconcile_membership(order: list[str], selected: Iterable[str]) -> list[str]:
    """The order *order* becomes once the membership is *selected*.

    The single rule both the cross and the picker go through, and the reason they cannot
    disagree. Everything still selected keeps the place it had; everything newly selected
    lands at the end, in the order *selected* offers it.

    Appended rather than inserted anywhere cleverer because the order means player
    number: an account added to a profile is the next player, and slotting it into the
    middle would silently renumber the ones already there. Kept in place rather than
    re-derived from *selected* for the same reason -- ``PickerPopup`` reports its
    selection in section order, so trusting it would reshuffle a profile every time the
    user ticked a fifth account.

    Duplicates in *selected* collapse to the first mention, so a caller can hand over a
    list without de-duplicating it first.
    """
    chosen = list(dict.fromkeys(selected))
    keep = set(chosen)
    kept = [username for username in order if username in keep]
    already = set(kept)
    return kept + [username for username in chosen if username not in already]


def roster_label(count: int) -> str:
    """The picker row's right-hand meta text for an account with *count* characters.

    Carries the unit, as ``PickerRow.meta`` asks: a bare number beside a name reads as
    ambiguous until you work out what it counts, and beside a *wizard* account it would
    read as a level before it read as a roster size.
    """
    if count == 0:
        return "no characters yet"
    return "1 character" if count == 1 else f"{count} characters"


def account_sections(accounts: list[dict], rosters: dict[str, list[dict]]) -> list[Section]:
    """The picker's rows: every stored account, coloured and counted.

    The dot is the account's own colour -- the same one striping its card in the column
    below and outlining its pill on every profile card elsewhere -- so the popup, the
    column and the cards behind the dialog are all naming accounts in one language.
    Resolved against the whole list, because a colour is only distinct with respect to
    the others (see gui/account_accent.py).

    Nothing is dimmed. ``dim`` means present-but-empty, and an account with no known
    characters is perfectly launchable: the roster is learned as clients connect, so on a
    fresh install *every* account would be dimmed on the surface used to set them up.
    """
    keys = account_color_keys(accounts)
    rows = []
    for account in accounts:
        username = account.get("username", "")
        rows.append(
            PickerRow(
                key=username,
                label=CredentialStore.display_name(username, account.get("alias") or None),
                meta=roster_label(len(rosters.get(username, []))),
                accent=account_accent(keys[username]) if username in keys else None,
            )
        )
    return [("Accounts", rows)]


class ProfileDialog(QDialog):
    """Add or edit one launch profile: its name, its accounts in player order, its size.

    Membership and order are two questions with two controls -- see the module docstring.
    What is *not* here is an Edit verb on the account cards: editing an account record
    from inside the profile editor is a dialog inside a dialog, and the Game tab that
    opened this one is one Escape away.
    """

    def __init__(
        self,
        name: str = "",
        selected_usernames: list[str] | None = None,
        resolution: tuple[int, int] | None = None,
        parent=None,
        live_game_area: tuple[int, int] | None = None,
    ) -> None:
        super().__init__(parent)
        # The game area behind the settings dialog, so this picker and the Launch menu
        # agree about which resolutions can be launched. None falls back to the
        # configured layout alone — see gui/screen_fit.budget_for.
        self._live_game_area = live_game_area
        self.setWindowTitle("Launch Profile")
        self.setMinimumWidth(PROFILE_DIALOG_MIN_WIDTH)
        self.setMinimumHeight(PROFILE_DIALOG_MIN_HEIGHT)

        # Read once, at open. The Game tab behind this dialog cannot be touched while it
        # is up, so re-reading on every rebuild would cost a file read per card per drag
        # and never see a different answer.
        self._accounts = SettingsManager.instance().accounts
        self._rosters = {
            username: character_roster.load(username)
            for account in self._accounts
            if (username := account.get("username", ""))
        }
        self._order: list[str] = list(dict.fromkeys(selected_usernames or []))
        # The open account picker, and when the last one closed. The second is the same
        # guard ``PickerButton`` carries: a click on the Add button while the popup is up
        # dismisses the popup and is then delivered to the button, which would open a
        # second one on top of the first.
        self._picker: PickerPopup | None = None
        self._picker_closed_at: float | None = None

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._name_edit = QLineEdit(name)
        form.addRow("Profile name:", self._name_edit)
        layout.addLayout(form)

        # List on the left, controls on the right -- see the module docstring. No Add
        # card at the column's own foot and no "Change resolution" link on its banner:
        # the Add button and the resolution combo it would point at are both in the side
        # column beside it.
        content = QHBoxLayout()
        self._column = CardColumn(ACCOUNTS_COLUMN_TITLE, EMPTY_PROFILE_HINT, show_settings_link=False)
        self._column.reordered.connect(self._on_reordered)
        content.addWidget(self._column, 1)

        side = QVBoxLayout()
        self._add_button = QPushButton(ADD_BUTTON_TEXT)
        self._add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        # Not the dialog's default button: Qt hands that to the first ``autoDefault``
        # button built, which would otherwise be this one rather than OK, and Enter is
        # supposed to accept the dialog, not open a popup on top of it.
        self._add_button.setAutoDefault(False)
        self._add_button.clicked.connect(self.open_account_picker)
        side.addWidget(self._add_button)

        res_form = QFormLayout()
        self._res_combo = QComboBox()
        self._res_combo.addItem("Use global setting", userData=None)
        for w, h in VALID_RESOLUTIONS:
            self._res_combo.addItem(f"{w}×{h}", userData=(w, h))

        if resolution is not None:
            for i in range(self._res_combo.count()):
                if self._res_combo.itemData(i) == resolution:
                    self._res_combo.setCurrentIndex(i)
                    break

        res_form.addRow("Resolution:", self._res_combo)
        side.addLayout(res_form)

        self._res_hint = ThemedTextLabel("", css=f"font-size: {THEME.font_small_px}px;")
        self._res_hint.setWordWrap(True)
        side.addWidget(self._res_hint)
        side.addStretch(1)
        content.addLayout(side)

        layout.addLayout(content, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._apply_order(self._order)

    # ------------------------------------------------------------------
    # The one membership path
    # ------------------------------------------------------------------

    def open_account_picker(self) -> None:
        """Show the account picker under the Add button.

        Anchored to the button rather than anything in the column: the column's row count
        changes on every tick and every cross, and an anchor inside it would drift out
        from under the popup mid-edit. The button's own position never moves.

        Built from scratch on every open, like every other ``PickerPopup``: its rows carry
        roster counts, and rebuilding a handful of widgets is nothing next to keeping a
        second copy of the membership that could disagree with ``_order``.
        """
        if not should_open(time.monotonic(), self._picker_closed_at):
            return
        popup = PickerPopup(
            PICKER_TITLE,
            account_sections(self._accounts, self._rosters),
            self._order,
            search_placeholder="Filter accounts…",
            parent=self,
        )
        popup.selection_changed.connect(self._on_picker_changed)
        popup.closed.connect(self._on_picker_closed)
        self._picker = popup
        popup.popup_at(self._add_button)

    def _on_picker_closed(self) -> None:
        self._picker = None
        self._picker_closed_at = time.monotonic()

    def _apply_order(self, order: list[str]) -> None:
        """Adopt *order* as the profile's membership, and redraw everything that shows it.

        The single writer of ``_order``, which is the dialog's only copy of the
        membership. Every route in — the picker, a card's cross, a drag, and the initial
        load — ends here, which is what keeps the column and the picker from disagreeing.

        The column is rebuilt whole while a popup may be open above it: safe because the
        popup is anchored to the Add button, not to anything the rebuild touches.
        """
        self._order = list(order)
        self._rebuild_column()
        self._refresh_resolution_fit()

    def _on_picker_changed(self, keys: list) -> None:
        self._apply_order(reconcile_membership(self._order, [str(key) for key in keys]))

    def _on_remove(self, username: str) -> None:
        """The cross on a card. The same call the picker makes, with one name taken out."""
        self._apply_order(reconcile_membership(self._order, [u for u in self._order if u != username]))

    def _on_reordered(self, order: list) -> None:
        """A drag finished. Rebuilt rather than left alone, so the ``P``n pills renumber.

        A stale permutation is dropped, not applied — see ``apply_order``. Nothing here
        can go stale in practice, the dialog owning the only list its column was built
        from, but a silently misapplied permutation reorders a profile the user did not
        drag and the guard is one call.
        """
        if (reordered := apply_order(self._order, list(order))) is not None:
            self._apply_order(reordered)

    def _rebuild_column(self):
        cards = []
        for position, username in enumerate(self._order, start=1):
            card = AccountCard(
                self._account_record(username),
                self._rosters.get(username, []),
                self._accounts,
                mode=CardMode.MEMBER,
                position=position,
            )
            card.remove_requested.connect(self._on_remove)
            cards.append(card)
        self._column.set_cards(cards)

    def _account_record(self, username: str) -> dict:
        """The stored record for *username*, or a bare stand-in when there is none.

        A profile can name an account that has since been deleted. Synthesising a record
        keeps that name on screen and in the saved profile, where the user can see it and
        take it out deliberately; dropping it would rewrite their profile on open, which
        is the thing four levels of option resolution exist to prevent.
        """
        for account in self._accounts:
            if account.get("username") == username:
                return account
        return {"username": username}

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def _refresh_resolution_fit(self) -> None:
        """Re-judge which resolutions this profile could launch at, and say what fits.

        Judged against the profile's *own* account count, which is the number of clients
        this resolution will actually have to hold. Re-run on every change to the list,
        because adding a fourth account is exactly what turns a fitting profile into one
        that has to come down a tier.
        """
        count = max(1, len(self._order))
        mark_unfittable_resolutions(self._res_combo, count, self, self._live_game_area)
        largest = screen_fit.largest_fitting_resolution(count, self, self._live_game_area)
        clients = f"{count} client{'s' if count != 1 else ''}"
        self._res_hint.setText(
            f"Largest that fits {clients} on this screen: {largest[0]}×{largest[1]}."
            if largest is not None
            else f"No offered resolution fits {clients} on this screen — remove an account, "
            "or make the sidebars and bot canvas smaller in Settings → General."
        )

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name_edit.text().strip()

    @property
    def selected_usernames(self) -> list[str]:
        """The profile's accounts, in player order."""
        return list(self._order)

    @property
    def accounts_column(self) -> CardColumn:
        """The column of member cards. Public because a test has to reach the cards in it."""
        return self._column

    @property
    def account_picker(self) -> PickerPopup | None:
        """The open account picker, or None while it is closed."""
        return self._picker

    @property
    def add_button(self) -> QPushButton:
        """The button that opens the account picker. Public so a test can drive it."""
        return self._add_button

    @property
    def resolution(self) -> tuple[int, int] | None:
        return self._res_combo.currentData()
