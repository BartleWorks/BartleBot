"""The cards on the Quick Launch screen: one per account, one per launch profile.

Launching is the only thing this app does before a bot is open, and the screen that does
it used to say less about each entry than the bot library says about a bot file -- a bare
``QPushButton`` carrying a name and nothing else. A launch profile was the worst of it:
its name says nothing about which accounts it brings, which is the one thing you need to
know before pressing it.

``_BotCard`` in gui/canvas/bot_canvas.py is the shape these follow, down to the hover
handling: a styled ``QWidget`` with an ``objectName``-scoped stylesheet, a bold name row,
pills beside it and quieter secondary lines below.

WHAT IS HERE, AND WHAT IS IN ``CardFrame``

The card *frame* is bartleby-core's bartleby/qt/widgets/cards/card_frame.py: the gutter-and-frame split, the drag
handle, the hover state, the picked-up state, the theme pass, ``CardMode``, and the
verbs-as-data machinery with the right-click menu built from it. Read that module for the
click model and the reasoning behind the gutter -- it is shared with a second family of
cards outside this package, which is why it is not in this file.

What is left here is what a *launch* card is: the fit refusal, the account color stripe,
and a content shape of a bold name row, one detail line and one quiet line.

REFUSING A CARD THAT WILL NOT FIT

``set_unavailable`` grays a card and says why, rather than dropping it from the list. A
profile that silently vanishes leaves the user hunting for it with nothing on screen
saying what happened. Whether a surface honors that refusal is ``refuses_when_unfit`` --
the launch screen does, because activating a card there starts what does not fit; the
Game tab does not, because a click there is the user going to fix it. A refused card can
still be selected on the launch screen, and the Launch button says why it is off.

THREE SURFACES, ONE CARD

The same cards draw the Game settings tab, which used to render the identical two lists
as ``QListWidget`` rows of formatted text -- so the surface where accounts are *edited*
said strictly less about them than the surface where they are merely launched --
and the launch profile editor, which had the same fault one dialog deeper: it is the
surface where a profile's membership is *decided*, and it named its accounts in a list of
bare strings, with none of the color, roster or fit the cards behind it were showing.

``CardMode`` is what varies between the three. It was a ``manage`` boolean while there
were two, and that held only because those two happened to agree on all four of the
things it decides. The editor breaks the agreement in two places, so the flag is named
after the surface instead and the facts are read back off it -- ``card_frame.py`` lists
them.

WHAT EACH MODE GETS, AND WHY

Every verb a card has is a ``CardVerb`` (again ``card_frame.py``), and ``_verbs`` is the
one list a surface's buttons and its right-click menu are both built from. Which verbs
this file's two cards offer, on which surface, and what decides it:

- **Delete is menu-only on the launch screen.** A trash there would sit 22px from a body
  whose double-click starts a game client, and a mis-click costs a stored credential and a
  character roster. The Game tab does put it on the face, which is safe for exactly the
  reason it is not safe here: a body click there edits.
- **The editor's verb is a dismiss cross, and it is on the face.** It takes the account
  out of the profile being edited -- not the account itself -- so the rule above does not
  reach it: a mis-click costs one re-tick in the account picker, and in that mode there is
  no body verb beside it to mis-click away from. It is ``TagChip``'s cross, meaning what
  it means there.
- **Duplicate stays on the menu everywhere**, being the third verb of a card type that
  has three.

None of the verbs are implemented here. Each is a signal carrying a name, and the module
functions in settings/accounts_tab.py are what every surface calls -- so what a card does
and how a card draws it stay separable, which is what lets each surface promote a
different subset of one list.

WHAT THE PILLS MEAN

A launch profile's pills are its accounts, in launch order. That ordering is real -- it
decides which window becomes Player 1 -- so the pills are laid out in it rather than
sorted for looks.

Each pill is outlined in its account's color, and the account card carrying that
color wears it as a stripe down its left edge. The pair is the point: a color on the
profile pills alone would be decoration, because nothing else on the screen would say
which account it stands for. bartleby/qt/theming/account_accent.py owns the mapping and
explains where a color comes from.

Both card types are therefore handed the *whole* account list, not just the account
they draw: a color is only distinct with respect to the others on screen, and a card
that resolved its own hue in isolation is how the first version of this shipped three
colors for six accounts.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from bartleby.qt import streamer_mode
from bartleby.qt.game_embed.card_columns import CardColumn, account_banner_text, profile_banner_text
from bartleby.qt.theming.account_accent import account_accent, account_color_key, account_color_keys
from bartleby.qt.theming.theme import THEME

# Re-exported, not defined here. ``AddCard`` moved to ``widgets/`` when the Combat tab's
# Duel Combat Strategies section became its second caller -- a terminator card knows
# nothing about launching, and a sidebar should not import out of ``game_embed`` to draw
# one. The names stay importable from here because the launch columns and their tests
# have always spelled them this way, and the card is still what ends *these* columns.
from bartleby.qt.widgets.cards.add_card import ADD_CARD_BORDER_WIDTH, AddCard  # noqa: F401

# ``CardMode`` is imported from here by the surfaces that build these cards, since the
# card and the mode it is built in are one import. The frame itself lives outside
# game_embed because a second family of cards uses it -- see card_frame.py.
from bartleby.qt.widgets.cards.card_frame import CardFrame, CardMode, CardVerb
from bartleby.qt.widgets.pills.pill import AccountPill, NeutralPill
from bartleby.runtime.clients.credentials import CredentialStore
from bartleby.runtime.game.game_sizing import FitProblem
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)

from bartlebot.gui import screen_fit
from bartlebot.settings_manager import SettingsManager

# Characters listed in full on an account card before the rest become "+N more", used
# only when nothing has told the card how wide it is -- during construction, and in the
# tests that build the line without a QApplication. A live card measures instead: a fixed
# count truncates a card with room for two more names, which is what the width-aware
# path in ``fit_character_summary`` exists to avoid.
MAX_CHARACTERS_SHOWN = 4

# Rows of names a live card will grow to before the rest become "+N more". The account
# column has vertical space to spare -- five accounts do not fill it -- so the limit that
# matters is width, and a card is allowed to use three lines of it.
MAX_CHARACTER_ROWS = 3

NO_CHARACTERS_HINT = "No characters known yet — launch one to learn them"

# What separates two names, and what follows the last one when some were dropped.
_SEP = " · "
_MORE_GAP = "  "

# Width of an account card's color stripe. Wide enough to be a color rather than a
# line at a glance down the column, narrow enough that the card still reads as a card
# and not as a labeled swatch.
STRIPE_WIDTH = 3

# ---------------------------------------------------------------------------
# Pure text-building. Separated from the widgets so it can be tested without a
# QApplication, as CLAUDE.md requires of anything inside a QWidget subclass.
# ---------------------------------------------------------------------------


def character_names(roster: list[dict]) -> list[str]:
    """The names to show for *roster*, honoring Streamer Mode.

    Positions come from the roster's own order, so a character reads the same way every
    time the card is rebuilt -- which is the only thing that makes the placeholder usable
    for telling two of them apart.
    """
    return [streamer_mode.display_name(entry.get("name"), index) for index, entry in enumerate(roster)]


def character_summary(roster: list[dict]) -> str:
    """One line describing an account's characters, or a hint when none are known.

    The empty case says *why* it is empty rather than just that it is: nothing can be
    known about an account until a character on it has been played through BartleBot,
    and a bare "(none)" reads like a fault.
    """
    if not roster:
        return NO_CHARACTERS_HINT
    names = character_names(roster)
    if len(names) <= MAX_CHARACTERS_SHOWN:
        return _SEP.join(names)
    return _more_line(names, MAX_CHARACTERS_SHOWN)


def _more_line(names: list[str], shown: int) -> str:
    return _SEP.join(names[:shown]) + f"{_MORE_GAP}+{len(names) - shown} more"


def _pack_rows(pieces: list[tuple[str, str]], available: int, measure: Callable[[str], int]) -> list[str]:
    """Greedily lay (separator, text) *pieces* into rows no wider than *available*.

    The rows are built here rather than left to ``QLabel``'s own word wrap because Qt
    wraps at every space -- including the ones inside a two-word wizard name and inside
    the separator -- so a name would be split across two lines. Breaking only between
    names is the whole point of the line.

    A piece wider than the row on its own still gets one: the label elides it, which
    reads better than an empty card.
    """
    rows: list[str] = []
    current = ""
    for separator, text in pieces:
        if not current:
            current = text
            continue
        candidate = current + separator + text
        if measure(candidate) <= available:
            current = candidate
        else:
            rows.append(current)
            current = text
    if current:
        rows.append(current)
    return rows


def _pieces(names: list[str], shown: int) -> list[tuple[str, str]]:
    """The pieces of a line showing the first *shown* of *names*, with a tail if it drops any."""
    pieces = [(_SEP, name) for name in names[:shown]]
    if shown < len(names):
        pieces.append((_MORE_GAP, f"+{len(names) - shown} more"))
    return pieces


def fit_character_summary(
    names: list[str],
    available: int,
    measure: Callable[[str], int],
    max_rows: int = MAX_CHARACTER_ROWS,
) -> str:
    """As many of *names* as fit in *max_rows* rows of *available* pixels, then "+N more".

    Measured rather than counted because what a card can show is a property of its width
    and the names in it, not a constant: four short names and one long one are not the
    same line. *measure* is passed in so this stays testable without a QApplication --
    the card hands it a ``QFontMetrics.horizontalAdvance``.

    Several rows because the account column has vertical room to spare -- five accounts
    do not fill it -- and horizontal room is what it is short of. Three is where that
    stops: a card taller than that stops being a card, and the count pill beside the name
    already says how many characters there are.

    The tail is laid out as part of each candidate rather than measured on its own, since
    adding it is what can push a line that just fitted onto one more row.

    One name is always shown. A card too narrow for even that has the label elide it,
    which says more than "+3 more" on its own does.
    """
    if not names:
        return NO_CHARACTERS_HINT
    if available <= 0:
        return _SEP.join(names)
    for shown in range(len(names), 0, -1):
        rows = _pack_rows(_pieces(names, shown), available, measure)
        if len(rows) <= max_rows:
            return "\n".join(rows)
    # Not even one name and its tail fit the rows allowed. The tail is what gets dropped
    # by the row limit, so it is put back on the last row and left to overflow: an elided
    # line still says there are more characters, whereas a name alone claims there are not.
    rows = _pack_rows(_pieces(names, 1), available, measure)[:max_rows]
    rows[-1] += f"{_MORE_GAP}+{len(names) - 1} more"
    return "\n".join(rows)


class PillSpec(NamedTuple):
    """What one account pill needs: what it says, and which color says whose it is."""

    display: str
    color_key: int


def account_pill_specs(usernames: list[str], accounts: list[dict]) -> list[PillSpec]:
    """A pill per username, in the order given.

    Order is preserved because a profile's account order decides which window becomes
    Player 1 — sorting these for looks would misreport the launch.

    Colors come from the whole account list rather than one username at a time, so
    that two pills on one profile can never share a hue while a slot is going spare.

    A username the accounts list no longer holds still gets a pill, built from the raw
    username: dropping it would make a profile referring to a deleted account look
    complete, and that profile will fail to launch. It keeps a color too — a gray pill
    would read as "no account color" rather than as the missing account it is. That
    one is the bare derived slot, and so may repeat a real account's: it is outside the
    set being de-duplicated, and there is no card left for it to match anyway.
    """
    meta = {a.get("username"): a for a in accounts}
    keys = account_color_keys(accounts)
    out: list[PillSpec] = []
    for username in usernames:
        entry = meta.get(username)
        alias = None if entry is None else (entry.get("alias") or None)
        key = keys.get(username) or account_color_key(username, entry)
        out.append(PillSpec(CredentialStore.display_name(username, alias), key))
    return out


def resolution_label(profile: dict) -> str | None:
    """``"1280×720"`` for a profile that pins a resolution, else None.

    None means the profile follows the app setting, which the card shows nothing for: a
    pill on every card would be noise, and the interesting case is the profile that
    differs.
    """
    resolution = profile.get("resolution")
    if not resolution or len(resolution) != 2:
        return None
    return f"{resolution[0]}×{resolution[1]}"


def profile_summary(profile: dict) -> str:
    """The quiet line under a profile's pills: how many clients it launches."""
    count = len(profile.get("accounts") or [])
    if count == 0:
        return "No accounts — this profile cannot launch"
    return f"{count} client{'s' if count != 1 else ''}"


def _card_line_for(reason: FitProblem | str | None) -> str:
    """What a refused card's quiet line reads -- its numbers, never the advice."""
    if reason is None:
        return ""
    return reason.numbers if isinstance(reason, FitProblem) else reason


def _tooltip_for(reason: FitProblem | str | None) -> str:
    """What a refused card's tooltip reads: the whole thing, advice included.

    A tooltip is read one at a time and only on purpose, so the repetition the card line
    avoids does not arise here -- and the pointer is already on the card that raised the
    question, which is the moment the advice is most wanted.
    """
    if reason is None:
        return ""
    return reason.sentence if isinstance(reason, FitProblem) else reason


def launch_note_for(reason: FitProblem | str | None) -> str:
    """The line under the launch screen's Launch button while the selected card cannot launch.

    Short, because the advice is already on screen: in the column's banner, and in the
    button's tooltip. What this line adds is that the *button* is off because of that,
    rather than for some reason of its own.
    """
    if reason is None:
        return ""
    if isinstance(reason, FitProblem):
        need, available = reason.need, reason.available
        return f"Doesn't fit: needs {need[0]}×{need[1]}, and {available[0]}×{available[1]} is available"
    return reason


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class _LaunchCard(CardFrame):
    """A card standing for something launchable: a name, two lines, and a fit refusal.

    ``CardFrame`` is the frame, the grip, the hover, the picked-up state and the verbs.
    What this adds is what makes one of those a *launch* card: the state a card that will
    not fit is put into, the account color its left edge is striped with, and the three
    labels the two subclasses below fill.
    """

    #: The frame keeps the object name it had before the frame itself was lifted out, so
    #: every rule in this file still reads ``#LaunchCard`` and still lands on the thing
    #: with the border.
    FRAME_NAME = "LaunchCard"

    def __init__(self, parent=None, *, mode: CardMode = CardMode.LAUNCH) -> None:
        super().__init__(parent, mode=mode)
        # Every plain label is held so _restyle_content can re-derive its color.
        # The pills are not: NeutralPill owns its own.
        self._name_lbl: QLabel | None = None
        self._detail_lbl: QLabel | None = None
        self._quiet_lbl: QLabel | None = None
        # Why this card cannot launch right now, or None while it can. Set rather than
        # the card being dropped from the list: see set_unavailable.
        self._unavailable: str | None = None
        # The account palette slot this card's left edge is striped with, when it stands
        # for a single account. None means no stripe -- a profile brings several accounts
        # and has no one color, which is what its pills are for.
        self._stripe_key: int | None = None

    def set_unavailable(self, reason: FitProblem | str | None) -> None:
        """Gray the card out and say why, or None to make it launchable again.

        Disabled rather than hidden. A profile that silently vanishes from the list
        leaves the user hunting for it with nothing on screen saying what happened,
        whereas a grayed row carrying "Needs 1624×651 …" says both that it still exists
        and that its size is what is wrong. The reason goes in the card's quiet line and
        in its tooltip, so it is readable without hovering.

        A ``FitProblem`` is split rather than printed: the card takes only its numbers,
        and the advice that goes with them is shown once for the whole column by the
        launch screen. Printing the whole sentence here is what put the same three
        wrapped lines on every account card, since every one of them asks the identical
        question -- one client, app resolution, this screen. A plain string is
        still taken whole, for a refusal that is not about size.

        What lands on the line is ``_refusal_line``, which a subclass whose whole column
        shares one refusal overrides away entirely -- see ``AccountCard``.
        """
        if reason == self._unavailable:
            return
        self._unavailable = reason
        refuses = bool(reason) and self.refuses_when_unfit
        # A refused card on the launch screen still takes a click, which selects it.
        clickable = self.has_body_verb and (self.selects_on_click or not refuses)
        self.setCursor(Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor)
        self.setToolTip(_tooltip_for(reason))
        if self._quiet_lbl is not None:
            text = self._refusal_line(reason) or self._available_quiet_text()
            self._quiet_lbl.setText(text)
            self._quiet_lbl.setWordWrap(bool(reason))
            # Hidden rather than empty: an empty QLabel still reserves a line in the
            # card's minimum height while drawing nothing, and that difference is scroll
            # range over blank space once a column holds enough cards.
            self._quiet_lbl.setVisible(bool(text))
        self.refresh_theme_styles()

    def _available_quiet_text(self) -> str:
        """What the quiet line says when the card can launch. Subclasses that have one override."""
        return ""

    def _refusal_line(self, reason: FitProblem | str | None) -> str:
        """What the quiet line says while the card cannot launch, "" to leave it alone."""
        return _card_line_for(reason)

    @property
    def unavailable_reason(self) -> FitProblem | str | None:
        """Why this card cannot launch, or None if it can.

        The refusal as it was handed in, not as it is drawn: the launch screen reads this
        back to decide what its column banner should say, and that needs the parts.
        """
        return self._unavailable

    @property
    def refusal_text(self) -> str:
        """Why this card will not launch, as one sentence with its advice, or "" if it will.

        Only while the surface honors the refusal: in the Game tab the card is noted, not
        refused. What the card's tooltip says, read back by the launch screen to put under
        its Launch button.
        """
        return _tooltip_for(self._unavailable) if self._refuses_activation() else ""

    @property
    def launch_key(self) -> tuple[str, str]:
        """What this card launches, as a ``recent_launches`` entry: its type and its id."""
        raise NotImplementedError

    @property
    def launch_label(self) -> str:
        """The name the Launch button says it will launch."""
        raise NotImplementedError

    @property
    def quiet_text(self) -> str:
        """What the card's quiet line currently reads — its summary, or the refusal."""
        return "" if self._quiet_lbl is None else self._quiet_lbl.text()

    @property
    def detail_text(self) -> str:
        """What the card's middle line reads — an account card's characters, or "".

        Public alongside ``quiet_text`` because what survives a refusal is part of this
        card's contract: an account card that goes gray keeps this line, since the reason
        it went gray is its column's and not its own.
        """
        return "" if self._detail_lbl is None else self._detail_lbl.text()

    def _refuses_activation(self) -> bool:
        """A card that cannot launch refuses to, where its surface refuses.

        The whole card is the hit target, so there is no QPushButton here whose disabled
        state would do it. It still selects, so the launch screen can say why. A card in
        the Game tab does not refuse, because what its click does there is edit — which is
        exactly what an unfittable card wants the user to go and do.
        """
        return self._unavailable is not None and self.refuses_when_unfit

    def _frame_extra_qss(self) -> str:
        """The account stripe down the card's left edge, or "" for a card without one.

        Handed to the frame rule rather than written into one, so that the resting palette
        and the dragging palette both keep it and neither has to remember to. The grayed
        palette is the one that drops it, and it drops it by writing its own rule: the
        stripe names an account, not a state.
        """
        if self._stripe_key is None:
            return ""
        return f" border-left: {STRIPE_WIDTH}px solid {account_accent(self._stripe_key)};"

    def _restyle_content(self) -> None:
        """The three labels at rest: the name at full contrast, the two lines quieter."""
        if self._name_lbl is not None:
            self._name_lbl.setStyleSheet(f"color: {THEME.text}; background: transparent;")
        for lbl, color in ((self._detail_lbl, THEME.text_muted), (self._quiet_lbl, THEME.text_subtle)):
            if lbl is not None:
                lbl.setStyleSheet(f"color: {color}; font-size: {THEME.font_subtext_px}px; background: transparent;")

    def _apply_state_palette(self) -> bool:
        """Gray a card that cannot launch, on a surface that refuses one."""
        if self._unavailable is None or not self.refuses_when_unfit:
            return False
        self._apply_unavailable_styles()
        return True

    def _apply_unavailable_styles(self) -> None:
        """The grayed-out palette. The stripe goes too: it names an account, not a state.

        Fill and text both move, because either alone reads as enabled — the same finding
        as the app-wide disabled palette (tests/gui/theming/test_disabled_affordance.py).
        A selected one keeps the accent outline, since the fill that marks a selection is
        the one this palette replaces.
        """
        border = THEME.accent if self.is_selected else THEME.border
        self._apply_frame_palette(THEME.surface_disabled, border, extra="")
        if self._name_lbl is not None:
            self._name_lbl.setStyleSheet(f"color: {THEME.text_disabled}; background: transparent;")
        for lbl in (self._detail_lbl, self._quiet_lbl):
            if lbl is not None:
                lbl.setStyleSheet(
                    f"color: {THEME.text_disabled}; font-size: {THEME.font_subtext_px}px; background: transparent;"
                )
        # The face icons are not tinted down with them, and are restyled by the base pass
        # after this returns: the pencil is the way out of the state the rest of the card
        # is grayed to report, so it stays at full contrast.

    def _make_name_label(self, text: str) -> QLabel:
        label = QLabel(text, self)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        self._name_lbl = label
        return label

    def _make_quiet_label(self, text: str = "") -> QLabel:
        """The card's bottom line: its summary while it can launch, its refusal when it cannot.

        Height-for-width, because ``set_unavailable`` turns word wrap on and a wrapping
        QLabel's sizeHint height is computed at a width that has nothing to do with the
        column's. Without the flag the layout believes that phantom height, the card's
        sizeHint grows to several times what it draws, and the scroll area it sits in
        cannot be shorter than the sum -- a scroll bar over empty space.

        Ignored horizontally, with a minimum of one pixel, for the same reason as the
        detail line: the text is fitted to whatever width the card ends up with, and must
        never be what decides that width.

        Parented to the card *at construction*, before the visibility below is set. A
        parentless widget is a top-level window, so ``setVisible(True)`` on one puts a
        label-sized window on the screen for the frame between here and ``addWidget``
        reparenting it -- a flash in the corner of the display every time the cards are
        rebuilt, which the profile editor does on the way out.
        """
        label = QLabel(text, self)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        label.setSizePolicy(policy)
        label.setMinimumWidth(1)
        label.setVisible(bool(text))
        self._quiet_lbl = label
        return label


class AccountCard(_LaunchCard):
    """One account: its display name, and the characters known to live on it.

    The character line is why this card exists. ``wizard_names`` only knows a character
    while its client is hooked, which is never true of an account on this screen, so the
    names come from ``character_roster`` — learned as clients connect and persisted per
    account.

    The left edge carries the account's color, which is what the outlined pills on the
    profile cards beside it point at. A stripe rather than a colored name: the name is
    the thing you read, and it should stay at full contrast.

    That color is chosen against every other account, so the card is given the list it
    belongs to as well as its own entry.
    """

    launch_account_requested = pyqtSignal(str, str)  # (username, display_name)
    edit_account_requested = pyqtSignal(str)  # (username)
    delete_account_requested = pyqtSignal(str)  # (username)
    # Take this account out of the list being assembled -- MEMBER mode only. Deliberately
    # not ``delete_account_requested``: that one takes a password out of the Credential
    # Manager, this one takes a name out of a profile, and a surface that confused the two
    # would destroy a credential on a click meaning "not in this profile".
    remove_requested = pyqtSignal(str)  # (username)

    def __init__(
        self,
        account: dict,
        roster: list[dict],
        accounts: list[dict] | None = None,
        parent=None,
        *,
        mode: CardMode = CardMode.LAUNCH,
        position: int | None = None,
    ) -> None:
        super().__init__(parent, mode=mode)
        self._username = account.get("username", "")
        self._display_name = CredentialStore.display_name(self._username, account.get("alias") or None)
        self._roster = roster
        # Which player this account will be, when it is a row in an ordered list. The
        # profile editor's list *is* an order -- it decides which window becomes Player 1
        # -- and position in a column is a weak way to say a number the user has to count.
        # None everywhere else: on the launch screen an account card is not in an order.
        self._position = position
        # The list this account sits in, because its color is chosen against the others
        # rather than from its username alone. Defaulting to the account by itself is for
        # the caller that genuinely has one — there is nothing for it to collide with.
        siblings = [account] if accounts is None else accounts
        # Set before refresh_theme_styles: the stripe is painted by the frame's own rule.
        self._stripe_key = account_color_keys(siblings).get(self._username) or account_color_key(
            self._username, account
        )
        self._build()
        self.refresh_theme_styles()
        # Nothing is connected in MEMBER mode: the body has no verb there. See
        # ``has_body_verb`` -- this is the branch the ``manage`` boolean could not express.
        if mode is CardMode.MANAGE:
            self.activated.connect(lambda: self.edit_account_requested.emit(self._username))
        elif mode is CardMode.LAUNCH:
            self.activated.connect(lambda: self.launch_account_requested.emit(self._username, self._display_name))

    @property
    def color_key(self) -> int:
        """The account palette slot this card is striped with, and its pills outlined in."""
        return self._stripe_key

    @property
    def launch_key(self) -> tuple[str, str]:
        return ("account", self._username)

    @property
    def launch_label(self) -> str:
        return self._display_name

    def _refusal_line(self, reason: FitProblem | str | None) -> str:
        """Nothing: an account card's refusal belongs to its column, numbers included.

        Every card in this column launches one client at the app resolution on the one
        screen, so all of them get the same numbers as well as the same advice, and the
        column banner already carries both. Repeating them here would put four identical
        lines under four different account names -- the whole bug -- and it would cost the
        card the line it is actually for, which is its characters. So the card stays gray
        and keeps its own summary, and the tooltip still holds the full sentence for
        anyone who asks a specific card.

        A ``ProfileCard`` keeps its line, because two profiles genuinely need different
        amounts of room.
        """
        return ""

    def _build(self) -> None:
        layout = self._build_frame("accounts")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        # Ahead of the name, where an ordinal belongs: "P2 Alt" reads as a position and a
        # name, while the same pill trailing the name reads as another count beside the
        # roster's. Only ever present in an ordered list -- see ``_position``.
        if self._position is not None:
            header.addWidget(NeutralPill(f"P{self._position}"), 0, Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self._make_name_label(self._display_name), 0)
        if self._roster:
            header.addWidget(NeutralPill(str(len(self._roster))), 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch()
        self._add_face_verbs(header)
        layout.addLayout(header)

        self._detail_lbl = QLabel(character_summary(self._roster), self)
        # Ignored, with a minimum of one pixel: the line is fitted to whatever width the
        # card ends up with, so its own text must never be what decides that width.
        # Left as Preferred, a long roster would push the card's minimum width out and
        # take the launch screen's minimum size with it.
        self._detail_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._detail_lbl.setMinimumWidth(1)
        # Word wrap stays off: the rows are built by fit_character_summary, which breaks
        # between names. Qt's own wrap breaks at any space, splitting a two-word wizard
        # name across two lines.
        self._detail_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._detail_lbl)

        # Empty, and so hidden, until set_unavailable fills it. Built here rather than on
        # demand so the reason has somewhere to land without rebuilding the card.
        layout.addWidget(self._make_quiet_label())

    def _verbs(self) -> list[CardVerb]:
        """Edit and Delete -- or, in a list being assembled, Remove and nothing else.

        No Duplicate on any of them: two accounts sharing a username is not a thing.

        Delete reaches the *face* only in the Game tab. It is the harshest verb this card
        has, taking a password out of the Credential Manager and a character roster off
        disk, neither of which comes back by adding the account again -- so on the launch
        screen, where it would sit 22px from a body whose double-click starts a game
        client, it stays on the right-click menu.

        Remove is the whole verb list in MEMBER mode, and it is on the face, because it is
        not that button: it takes this account out of the profile being edited and costs
        one re-tick in the picker to undo. Edit is not offered beside it -- editing the
        account record from inside the profile editor is a dialog inside a dialog, and the
        Game tab is one Escape away.
        """
        if self._mode is CardMode.MEMBER:
            return [
                CardVerb(
                    "Remove",
                    "dismiss",
                    f"Remove '{self._display_name}' from this profile",
                    lambda: self.remove_requested.emit(self._username),
                    on_face=True,
                ),
            ]
        return [
            CardVerb(
                "Edit",
                "pencil",
                f"Edit the '{self._display_name}' account",
                lambda: self.edit_account_requested.emit(self._username),
                on_face=True,
            ),
            CardVerb(
                "Delete",
                "trash",
                f"Delete the '{self._display_name}' account",
                lambda: self.delete_account_requested.emit(self._username),
                on_face=self._mode is CardMode.MANAGE,
            ),
        ]

    def resizeEvent(self, event) -> None:
        """Re-fit the character line: how many names fit is a function of the width.

        The card is only ever as wide as the column it sits in, and that is not known
        until it is laid out -- so the constructor's count-based line is a placeholder
        that the first resize replaces.
        """
        super().resizeEvent(event)
        self._refit_characters()

    def _refit_characters(self) -> None:
        if self._detail_lbl is None or not self._roster:
            return
        margins = self.layout().contentsMargins()
        available = self.width() - margins.left() - margins.right() - STRIPE_WIDTH
        metrics = QFontMetrics(self._detail_lbl.font())
        self._detail_lbl.setText(
            fit_character_summary(character_names(self._roster), available, metrics.horizontalAdvance)
        )


class ProfileCard(_LaunchCard):
    """One launch profile: which accounts it brings, and any resolution it pins.

    The account pills are the point. A profile's name says nothing about its membership,
    which is the single hardest thing to see on the old screen — you had to open Settings
    to find out what a profile would do.

    Each pill is outlined in its account's color, matching the stripe on that account's
    own card in the column beside it, so "which accounts is this?" can be answered by
    looking rather than by reading four censored usernames. The resolution pill stays
    neutral: it names a setting, not an account, and giving it a hue would put the one
    pill on the card that means nothing about membership in the same language as the
    ones that do.
    """

    launch_profile_requested = pyqtSignal(str)  # (profile_name)
    edit_profile_requested = pyqtSignal(str)  # (profile_name)
    duplicate_profile_requested = pyqtSignal(str)  # (profile_name)
    delete_profile_requested = pyqtSignal(str)  # (profile_name)

    def __init__(self, profile: dict, accounts: list[dict], parent=None, *, mode: CardMode = CardMode.LAUNCH) -> None:
        super().__init__(parent, mode=mode)
        self._profile = profile
        self._name = profile.get("name", "")
        self._accounts = accounts
        self._build()
        self.refresh_theme_styles()
        if mode is CardMode.MANAGE:
            self.activated.connect(lambda: self.edit_profile_requested.emit(self._name))
        elif mode is CardMode.LAUNCH:
            self.activated.connect(lambda: self.launch_profile_requested.emit(self._name))

    @property
    def profile(self) -> dict:
        """The launch profile this card stands for.

        Public because whether the card can launch is re-judged from outside it — the
        screen it is on can change without the profile changing — and the judge needs the
        accounts and the resolution to ask about.
        """
        return self._profile

    @property
    def launch_key(self) -> tuple[str, str]:
        return ("profile", self._name)

    @property
    def launch_label(self) -> str:
        return self._name

    def _build(self) -> None:
        layout = self._build_frame("launch profiles")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        header.addWidget(self._make_name_label(self._name), 0)
        if (resolution := resolution_label(self._profile)) is not None:
            header.addWidget(NeutralPill(resolution), 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch()
        self._add_face_verbs(header)
        layout.addLayout(header)

        pills = QHBoxLayout()
        pills.setContentsMargins(0, 0, 0, 0)
        pills.setSpacing(4)
        for spec in account_pill_specs(self._profile.get("accounts") or [], self._accounts):
            pills.addWidget(AccountPill(spec.display, spec.color_key), 0, Qt.AlignmentFlag.AlignVCenter)
        pills.addStretch()
        layout.addLayout(pills)

        layout.addWidget(self._make_quiet_label(profile_summary(self._profile)))

    def _available_quiet_text(self) -> str:
        return profile_summary(self._profile)

    def _verbs(self) -> list[CardVerb]:
        """Edit, Duplicate, Delete. The middle one is the reason profiles have a menu.

        Duplicate is how the second profile gets built -- this one plus an account, or
        this one at a smaller resolution -- and it has no icon because it does not need
        one: it sits between two entries that do, which is enough to read the menu by. It
        stays off the face on every surface, being the third verb of a card with three.

        No kebab button promoting it. With Delete already reachable on the menu, a kebab
        would hold Duplicate alone, on one of the two card types, which is a menu that
        teaches nothing. If it turns out to be undiscoverable, promoting it is one flag.

        A profile is never a MEMBER of anything -- profiles hold accounts, not the other
        way round -- so there is no third list here, only the one flag on Delete.
        """
        return [
            CardVerb(
                "Edit",
                "pencil",
                f"Edit the '{self._name}' profile",
                lambda: self.edit_profile_requested.emit(self._name),
                on_face=True,
            ),
            CardVerb(
                "Duplicate",
                "",
                f"Duplicate the '{self._name}' profile",
                lambda: self.duplicate_profile_requested.emit(self._name),
            ),
            CardVerb(
                "Delete",
                "trash",
                f"Delete the '{self._name}' profile",
                lambda: self.delete_profile_requested.emit(self._name),
                on_face=self._mode is CardMode.MANAGE,
            ),
        ]


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

    Here rather than beside ``CardColumn`` in core's ``card_columns``, because it reads
    BartleBot's settings and ``screen_fit``, which move to core in Stage 9 of #167.

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
