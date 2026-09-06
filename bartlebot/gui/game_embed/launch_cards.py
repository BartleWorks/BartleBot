"""The cards on the Quick Launch screen: one per account, one per launch profile.

Launching is the only thing this app does before a bot is open, and the screen that does
it used to say less about each entry than the bot library says about a bot file -- a bare
``QPushButton`` carrying a name and nothing else. A launch profile was the worst of it:
its name says nothing about which accounts it brings, which is the one thing you need to
know before pressing it.

``_BotCard`` in gui/canvas/bot_canvas.py is the shape these follow, down to the hover
handling: a styled ``QWidget`` with an ``objectName``-scoped stylesheet, a bold name row,
pills beside it and quieter secondary lines below.

CLICK MODEL

The whole card launches. That is the primary verb here and it keeps the one-click launch
the buttons had; the alternative -- select, then press Launch -- would put a second click
in front of the app's most common action to match a library screen whose primary verb is
"open", not "run".

Configuration is a hover-revealed pencil that opens the existing editor, which is what
Chrome's profile picker, Parallels' VM tiles and the Steam library all converge on for a
tile whose primary action is "launch". Nothing on the card is a form control: a combo box
on a surface that launches on click is a hole in the hit target, and one that would have
to swallow its own clicks to be safe.

On *release*, and only for a pointer that stayed put: the column these sit in reorders by
drag (``CardColumn``), and a card that activated on press launched four game clients on
the first pixel of every one.

TWO SURFACES, ONE CARD

The same cards draw the Game settings tab, which used to render the identical two lists
as ``QListWidget`` rows of formatted text -- so the surface where accounts are *edited*
said strictly less about them than the surface where they are merely launched (#192).
``manage=True`` is what varies between the two, and it is one flag rather than three
because it is one fact -- which surface this is:

- the body click **edits** instead of launching, that being the tab's primary verb;
- the header icons are **pinned open** rather than revealed on hover, there being nothing
  for them to compete with on a surface whose whole job is those verbs;
- a card that will not fit is **noted, not greyed**. Greying out the row the user came
  here to fix is hostile: on the launch screen a card goes grey because clicking it would
  launch something that cannot fit, and in the tab clicking it is how you fix that.

THE REST OF THE VERBS

Every other thing you can do to an account or a profile is on the right-click menu:
Delete on both, and Duplicate on profiles (#194). On the launch screen Delete is
deliberately *not* on the card face, where a trash icon would sit 22px from a body that
starts a game client -- a mis-click there costs a stored credential and a character
roster. The Game tab does carry it, which is safe there for exactly the reason it is not
here: a body click edits. Duplicate stays on the menu on both, being the third verb of a
card type that has three.

None of the verbs are implemented here. Each is a signal carrying a name, and the module
functions in settings/accounts_tab.py are what every surface calls -- so what a card does
and how a card draws it stay separable, which is what lets the Game tab promote a
different subset of the same list.

WHAT THE PILLS MEAN

A launch profile's pills are its accounts, in launch order. That ordering is real -- it
decides which window becomes Player 1 -- so the pills are laid out in it rather than
sorted for looks.

Each pill is outlined in its account's colour, and the account card carrying that
colour wears it as a stripe down its left edge. The pair is the point: a colour on the
profile pills alone would be decoration, because nothing else on the screen would say
which account it stands for. bartlebot/gui/account_accent.py owns the mapping and
explains where a colour comes from.

Both card types are therefore handed the *whole* account list, not just the account
they draw: a colour is only distinct with respect to the others on screen, and a card
that resolved its own hue in isolation is how the first version of this shipped three
colours for six accounts.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from bartlebot import streamer_mode
from bartlebot.credentials import CredentialStore
from bartlebot.game_sizing import FitProblem
from bartlebot.gui.account_accent import account_accent, account_color_key, account_color_keys
from bartlebot.gui.icon_utils import svg_icon, svg_pixmap
from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.tag_pill import AccountPill, NeutralPill

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

_EDIT_BTN_SIZE = 22

# Width of an account card's colour stripe. Wide enough to be a colour rather than a
# line at a glance down the column, narrow enough that the card still reads as a card
# and not as a labelled swatch.
STRIPE_WIDTH = 3

# Border width of the Add card's dashed outline. Two, not one, because Qt scales a dash
# pattern by the pen width: a 1px dashed border draws as a row of 1px dots, which at the
# subtle border colour is a line you have to look for -- on the top and left edges of a
# dark-theme card it was not visible at all. At 2px the dashes are dashes, and the card
# reads at about the weight of the 1px solid border on the launch cards above it, because
# the gaps give back what the extra pixel takes.
ADD_CARD_BORDER_WIDTH = 2


# ---------------------------------------------------------------------------
# Pure text-building. Separated from the widgets so it can be tested without a
# QApplication, as CLAUDE.md requires of anything inside a QWidget subclass.
# ---------------------------------------------------------------------------


def character_names(roster: list[dict]) -> list[str]:
    """The names to show for *roster*, honouring Streamer Mode.

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
    """What one account pill needs: what it says, and which colour says whose it is."""

    display: str
    color_key: int


def account_pill_specs(usernames: list[str], accounts: list[dict]) -> list[PillSpec]:
    """A pill per username, in the order given.

    Order is preserved because a profile's account order decides which window becomes
    Player 1 — sorting these for looks would misreport the launch.

    Colours come from the whole account list rather than one username at a time, so
    that two pills on one profile can never share a hue while a slot is going spare.

    A username the accounts list no longer holds still gets a pill, built from the raw
    username: dropping it would make a profile referring to a deleted account look
    complete, and that profile will fail to launch. It keeps a colour too — a grey pill
    would read as "no account colour" rather than as the missing account it is. That
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


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class _LaunchCard(ThemedMixin, QWidget):
    """Shared frame, hover behaviour and click-to-launch for both card types.

    Hover is state rather than a QSS pseudo-class for the same reason as ``_BotCard``:
    the frame and the labels are restyled by one pass, and ``_hovered`` picks which
    colours that pass uses. A pseudo-class would restyle the frame and leave every label
    in the old palette after a theme switch.
    """

    # The card body was activated. What that *means* is the subclass's, because it is the
    # surface's: it launches on the Quick Launch screen and edits in the Game tab. Named
    # for the gesture rather than for either verb, since one signal carrying "launch" to a
    # slot that edits is the kind of thing that reads fine and misleads for a year.
    activated = pyqtSignal()

    def __init__(self, parent=None, *, manage: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("LaunchCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hovered = False
        # Which surface this card is on. One flag rather than three that would always have
        # to agree, because they are one fact: in the Game tab the body click edits, the
        # verbs are on the card face rather than hidden behind hover, and a card that will
        # not fit is *noted* rather than greyed out and made unclickable. That last one is
        # the point — greying out the row the user came here to fix is hostile, and on the
        # launch screen a card is greyed because clicking it would launch something that
        # cannot fit, which is not what clicking it does here.
        self._manage = manage
        # The card's action buttons, with the icon each was built from: svg_icon bakes the
        # tint in at render time, so every one of them is re-rendered on a theme pass.
        # ``_edit_btn`` is the first of them and is held separately because the hover
        # handlers and the subclasses both reach for it by name.
        self._action_btns: list[tuple[QToolButton, str]] = []
        self._edit_btn: QToolButton | None = None
        # Where the left button went down, and whether it has travelled since. A card
        # activates on release and only if the pointer stayed put, so that the drag its
        # column turns a press-and-move into cannot also launch what it was reordering.
        self._press_pos: QPoint | None = None
        # Every plain label is held so apply_theme_styles can re-derive its colour.
        # The pills are not: NeutralPill owns its own.
        self._name_lbl: QLabel | None = None
        self._detail_lbl: QLabel | None = None
        self._quiet_lbl: QLabel | None = None
        # Why this card cannot launch right now, or None while it can. Set rather than
        # the card being dropped from the list: see set_unavailable.
        self._unavailable: str | None = None
        # The account palette slot this card's left edge is striped with, when it stands
        # for a single account. None means no stripe -- a profile brings several accounts
        # and has no one colour, which is what its pills are for.
        self._stripe_key: int | None = None

    def set_unavailable(self, reason: FitProblem | str | None) -> None:
        """Grey the card out and say why, or None to make it launchable again.

        Disabled rather than hidden. A profile that silently vanishes from the list
        leaves the user hunting for it with nothing on screen saying what happened,
        whereas a greyed row carrying "Needs 1624×651 …" says both that it still exists
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
        self.setCursor(Qt.CursorShape.ArrowCursor if refuses else Qt.CursorShape.PointingHandCursor)
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

    @property
    def refuses_when_unfit(self) -> bool:
        """Whether a card that will not fit goes grey and stops taking clicks.

        True on the launch screen, where a click starts what does not fit. False in the
        Game tab, where the same card is a thing being edited and the refusal is a note
        above and beneath it rather than a state it is put into — see ``_manage``.
        """
        return not self._manage

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
    def quiet_text(self) -> str:
        """What the card's quiet line currently reads — its summary, or the refusal."""
        return "" if self._quiet_lbl is None else self._quiet_lbl.text()

    @property
    def detail_text(self) -> str:
        """What the card's middle line reads — an account card's characters, or "".

        Public alongside ``quiet_text`` because what survives a refusal is part of this
        card's contract: an account card that goes grey keeps this line, since the reason
        it went grey is its column's and not its own.
        """
        return "" if self._detail_lbl is None else self._detail_lbl.text()

    def apply_theme_styles(self) -> None:
        if self._unavailable is not None and self.refuses_when_unfit:
            # Fill and text both move, because either alone reads as enabled — the same
            # finding as the app-wide disabled palette (tests/test_disabled_affordance.py).
            self._apply_unavailable_styles()
            return
        bg = THEME.node_fill if self._hovered else THEME.surface
        border = THEME.accent if self._hovered else THEME.border
        # The stripe rule comes after the shorthand, which sets all four sides: written
        # the other way round the shorthand would overwrite it and the card would lose
        # its colour with nothing in the stylesheet looking wrong.
        stripe = (
            ""
            if self._stripe_key is None
            else f" border-left: {STRIPE_WIDTH}px solid {account_accent(self._stripe_key)};"
        )
        self.setStyleSheet(
            f"#LaunchCard {{ background: {bg}; border: 1px solid {border}; border-radius: 4px;{stripe} }}"
        )
        if self._name_lbl is not None:
            self._name_lbl.setStyleSheet(f"color: {THEME.text}; background: transparent;")
        for lbl, color in ((self._detail_lbl, THEME.text_muted), (self._quiet_lbl, THEME.text_subtle)):
            if lbl is not None:
                lbl.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent;")
        self._restyle_edit_button()

    def _restyle_edit_button(self) -> None:
        """Re-render every action icon in the current theme.

        svg_icon bakes THEME.icon_tint in at render time, so an icon has to be rebuilt on
        every theme pass rather than set once in _build -- an icon set once goes stale on
        a theme switch, with no stylesheet anywhere to grep for. Which is why each button
        is held beside the name it was drawn from.
        """
        for button, icon_name in self._action_btns:
            button.setIcon(svg_icon(icon_name, size=14))

    def _apply_unavailable_styles(self) -> None:
        """The greyed-out palette. The stripe goes too: it names an account, not a state."""
        self.setStyleSheet(
            f"#LaunchCard {{ background: {THEME.surface_disabled}; "
            f"border: 1px solid {THEME.border}; border-radius: 4px; }}"
        )
        if self._name_lbl is not None:
            self._name_lbl.setStyleSheet(f"color: {THEME.text_disabled}; background: transparent;")
        for lbl in (self._detail_lbl, self._quiet_lbl):
            if lbl is not None:
                lbl.setStyleSheet(f"color: {THEME.text_disabled}; font-size: 10px; background: transparent;")
        # Not tinted down with them: the pencil is the way out of the state the rest of
        # the card is greyed to report, so it stays at full contrast.
        self._restyle_edit_button()

    def _make_action_button(self, icon_name: str, tooltip: str) -> QToolButton:
        """One icon in a card's header row: this card's verb, as a button.

        A real child widget rather than a hit-tested region of the card, so that it
        consumes its own clicks. The whole card is a hit target, and a painted-on icon
        would launch, or edit, the thing the user was trying to do something else to.

        Revealed on hover on the launch screen, because it is secondary to launching: a
        button drawn permanently on every card competes with the name for attention, on
        the one screen whose job is to let you pick a name. Hover-only is not the whole
        answer to reaching it — that is what ``contextMenuEvent`` below is for, which is
        also the route for anyone not using a mouse.

        Pinned open in the Game tab, where editing *is* the primary verb: there is nothing
        for the icons to compete with there, hiding the verbs behind hover on the surface
        that exists to run them is backwards, and the tab's cards carry a trash as well —
        which is only safe because a body click there edits rather than launches.

        The button's space is held while it is hidden, or revealing it on hover would
        resize the card under the pointer -- which can move the card out from under it,
        hiding the button again, resizing back: the whole card jitters as long as the
        pointer sits near its edge.
        """
        button = QToolButton(self)
        button.setFixedSize(_EDIT_BTN_SIZE, _EDIT_BTN_SIZE)
        policy = button.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        button.setSizePolicy(policy)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tooltip)
        button.setVisible(self._manage)
        self._action_btns.append((button, icon_name))
        return button

    def _make_edit_button(self, tooltip: str) -> QToolButton:
        """The pencil: the first action button on every card, and the one held by name.

        A pencil, not a gear. The gear meant "settings" when this opened the Settings
        dialog; it opens one profile's editor now (#193), and an icon should promise what
        happens.
        """
        self._edit_btn = self._make_action_button("pencil", tooltip)
        return self._edit_btn

    def _context_actions(self) -> list[tuple[str, str, Callable[[], None]]]:
        """(label, icon name, what it does) for this card's right-click menu.

        Subclasses fill it in; a card that returns nothing gets no menu. The label and the
        icon are the rendering, and the callable is one of the module functions in
        settings/accounts_tab.py — so promoting an entry to a button later is a change to
        one ``_build`` and not to any of the verbs.

        Pass "" as the icon name for an entry with no icon.
        """
        return []

    def contextMenuEvent(self, event) -> None:
        """Right-click: every verb this card has, including the ones not on its face.

        This is where Delete lives on the launch screen, deliberately and only. The card
        body starts a game client, and a trash icon 22px from it is a mis-click that
        costs a stored credential and a character roster -- so the destructive verb is
        the one that takes a deliberate second gesture. It is also the keyboard and
        discoverability answer to a pencil that only appears on hover.

        Offered on an unfittable card too, for the same reason the pencil is: editing the
        profile -- lowering its resolution, dropping an account -- is exactly what the
        greyed card is telling the user to do.
        """
        actions = self._context_actions()
        if not actions:
            return
        menu = popup_menu(self)
        for label, icon_name, handler in actions:
            action = menu.addAction(svg_icon(icon_name, size=14), label) if icon_name else menu.addAction(label)
            action.triggered.connect(handler)
        menu.exec(event.globalPos())

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
        cannot be shorter than the sum -- a scroll bar over empty space (#191).

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

    def _set_actions_visible(self, visible: bool) -> None:
        """Show or hide the header icons — a no-op where they are pinned open."""
        if self._manage:
            return
        for button, _icon in self._action_btns:
            button.setVisible(visible)

    def enterEvent(self, event) -> None:
        self._hovered = True
        self._set_actions_visible(True)
        self.refresh_theme_styles()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._set_actions_visible(False)
        self.refresh_theme_styles()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        """Remember where the press landed. Activation waits for the release."""
        if event.button() == Qt.MouseButton.LeftButton:
            # In screen coordinates, not the card's own: a drag moves the card itself
            # between the press and the release, so a local origin measures the distance
            # against a card that has since slid out from under the pointer.
            self._press_pos = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """Activate, if the pointer never left the card and never really moved.

        On release rather than on press, and only for a pointer that stayed put, because
        the card's own column turns a press-and-drag into a reorder (see
        ``CardColumn.eventFilter``). Activating on press fired on the first pixel of every
        drag — launching four game clients, or opening an editor, on top of the reorder
        the user was actually performing. Qt's own buttons activate on release for the
        same reason.

        The whole card is the hit target, so a refusing one has to swallow its own clicks:
        there is no QPushButton here whose disabled state would do it. A card in the Game
        tab does not refuse, because what its click does there is edit — which is exactly
        what an unfittable card wants the user to go and do.
        """
        pressed = self._press_pos
        self._press_pos = None
        refuses = self._unavailable is not None and self.refuses_when_unfit
        if (
            event.button() == Qt.MouseButton.LeftButton
            and pressed is not None
            and not refuses
            and self.rect().contains(event.position().toPoint())
            and (event.globalPosition().toPoint() - pressed).manhattanLength() < QApplication.startDragDistance()
        ):
            self.activated.emit()
        super().mouseReleaseEvent(event)


class AccountCard(_LaunchCard):
    """One account: its display name, and the characters known to live on it.

    The character line is why this card exists. ``wizard_names`` only knows a character
    while its client is hooked, which is never true of an account on this screen, so the
    names come from ``character_roster`` — learned as clients connect and persisted per
    account.

    The left edge carries the account's colour, which is what the outlined pills on the
    profile cards beside it point at. A stripe rather than a coloured name: the name is
    the thing you read, and it should stay at full contrast.

    That colour is chosen against every other account, so the card is given the list it
    belongs to as well as its own entry.
    """

    launch_account_requested = pyqtSignal(str, str)  # (username, display_name)
    edit_account_requested = pyqtSignal(str)  # (username)
    delete_account_requested = pyqtSignal(str)  # (username)

    def __init__(
        self,
        account: dict,
        roster: list[dict],
        accounts: list[dict] | None = None,
        parent=None,
        *,
        manage: bool = False,
    ) -> None:
        super().__init__(parent, manage=manage)
        self._username = account.get("username", "")
        self._display_name = CredentialStore.display_name(self._username, account.get("alias") or None)
        self._roster = roster
        # The list this account sits in, because its colour is chosen against the others
        # rather than from its username alone. Defaulting to the account by itself is for
        # the caller that genuinely has one — there is nothing for it to collide with.
        siblings = [account] if accounts is None else accounts
        # Set before refresh_theme_styles: the stripe is painted by the frame's own rule.
        self._stripe_key = account_color_keys(siblings).get(self._username) or account_color_key(
            self._username, account
        )
        self._build()
        self.refresh_theme_styles()
        if manage:
            self.activated.connect(lambda: self.edit_account_requested.emit(self._username))
        else:
            self.activated.connect(lambda: self.launch_account_requested.emit(self._username, self._display_name))

    @property
    def color_key(self) -> int:
        """The account palette slot this card is striped with, and its pills outlined in."""
        return self._stripe_key

    def _refusal_line(self, reason: FitProblem | str | None) -> str:
        """Nothing: an account card's refusal belongs to its column, numbers included.

        Every card in this column launches one client at the app resolution on the one
        screen, so all of them get the same numbers as well as the same advice, and the
        column banner already carries both. Repeating them here would put four identical
        lines under four different account names -- the whole bug -- and it would cost the
        card the line it is actually for, which is its characters. So the card stays grey
        and keeps its own summary, and the tooltip still holds the full sentence for
        anyone who asks a specific card.

        A ``ProfileCard`` keeps its line, because two profiles genuinely need different
        amounts of room.
        """
        return ""

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        header.addWidget(self._make_name_label(self._display_name), 0)
        if self._roster:
            header.addWidget(NeutralPill(str(len(self._roster))), 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch()
        edit_btn = self._make_edit_button(f"Edit the '{self._display_name}' account")
        edit_btn.clicked.connect(lambda: self.edit_account_requested.emit(self._username))
        header.addWidget(edit_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        if self._manage:
            delete_btn = self._make_action_button("trash", f"Delete the '{self._display_name}' account")
            delete_btn.clicked.connect(lambda: self.delete_account_requested.emit(self._username))
            header.addWidget(delete_btn, 0, Qt.AlignmentFlag.AlignVCenter)
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

    def _context_actions(self) -> list[tuple[str, str, Callable[[], None]]]:
        """Edit and Delete. No Duplicate: two accounts sharing a username is not a thing.

        Delete is here and not on the card face -- see ``_LaunchCard.contextMenuEvent``.
        It is the harshest verb on this screen: it takes a password out of the Credential
        Manager and a character roster off disk, neither of which comes back by adding the
        account again.
        """
        return [
            ("Edit", "pencil", lambda: self.edit_account_requested.emit(self._username)),
            ("Delete", "trash", lambda: self.delete_account_requested.emit(self._username)),
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

    Each pill is outlined in its account's colour, matching the stripe on that account's
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

    def __init__(self, profile: dict, accounts: list[dict], parent=None, *, manage: bool = False) -> None:
        super().__init__(parent, manage=manage)
        self._profile = profile
        self._name = profile.get("name", "")
        self._accounts = accounts
        self._build()
        self.refresh_theme_styles()
        if manage:
            self.activated.connect(lambda: self.edit_profile_requested.emit(self._name))
        else:
            self.activated.connect(lambda: self.launch_profile_requested.emit(self._name))

    @property
    def profile(self) -> dict:
        """The launch profile this card stands for.

        Public because whether the card can launch is re-judged from outside it — the
        screen it is on can change without the profile changing — and the judge needs the
        accounts and the resolution to ask about.
        """
        return self._profile

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        header.addWidget(self._make_name_label(self._name), 0)
        if (resolution := resolution_label(self._profile)) is not None:
            header.addWidget(NeutralPill(resolution), 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch()

        edit_btn = self._make_edit_button(f"Edit the '{self._name}' profile")
        edit_btn.clicked.connect(lambda: self.edit_profile_requested.emit(self._name))
        header.addWidget(edit_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        if self._manage:
            delete_btn = self._make_action_button("trash", f"Delete the '{self._name}' profile")
            delete_btn.clicked.connect(lambda: self.delete_profile_requested.emit(self._name))
            header.addWidget(delete_btn, 0, Qt.AlignmentFlag.AlignVCenter)
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

    def _context_actions(self) -> list[tuple[str, str, Callable[[], None]]]:
        """Edit, Duplicate, Delete. The middle one is the reason profiles have a menu.

        Duplicate is how the second profile gets built -- this one plus an account, or
        this one at a smaller resolution -- and it has no icon because it does not need
        one: it sits between two entries that do, which is enough to read the menu by.

        No kebab button promoting it to the card face. With Delete already reachable here,
        a kebab would hold Duplicate alone, on one of the two card types, which is a menu
        that teaches nothing. If it turns out to be undiscoverable, promoting it is a
        change to ``_build`` -- the verbs are already a list.
        """
        return [
            ("Edit", "pencil", lambda: self.edit_profile_requested.emit(self._name)),
            ("Duplicate", "", lambda: self.duplicate_profile_requested.emit(self._name)),
            ("Delete", "trash", lambda: self.delete_profile_requested.emit(self._name)),
        ]


class AddCard(ThemedMixin, QWidget):
    """The card at the foot of a launch column: "Add account", "Add profile".

    A fresh install opened on two columns reading "(No accounts)" and "(No profiles)",
    and nothing on either said what to do about that -- the only way forward was to
    notice a small underlined link between them, open a dialog and find a button under a
    list (#195). The answer to "how do I get another one of these" belongs where the ones
    you have are, which is the convention every card collection whose members are
    launched has settled on: Chrome's profile picker, the Figma file grid, Docker
    Desktop. Always last, and always a card.

    Deliberately *not* a ``_LaunchCard``. It launches nothing and has no resolution to
    judge, so keeping it outside that hierarchy is what makes the fit pass unable to grey
    it: ``refresh_fit`` walks the launch cards and calls ``set_unavailable`` on each, and
    this class does not have that method to call. It is also not a member of the ordered
    list -- nothing is dragged onto it and nothing is dropped after it. It is the
    column's terminator.

    Dashed rather than solid, which is the whole visual argument: same width, same
    radius, same hover as the cards above it, so it reads as one of them, but an outline
    that says it is a slot rather than a thing.
    """

    add_requested = pyqtSignal()

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AddCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hovered = False
        self._text = text
        self._build()
        self.refresh_theme_styles()

    @property
    def text(self) -> str:
        """What the card reads. Public so a test can name the column it belongs to."""
        return self._text

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        layout.addStretch()

        self._icon_lbl = QLabel(self)
        self._icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self._icon_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self._name_lbl = QLabel(self._text, self)
        self._name_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self._name_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addStretch()

    def apply_theme_styles(self) -> None:
        border = THEME.accent if self._hovered else THEME.border
        color = THEME.text if self._hovered else THEME.text_muted
        # Transparent rather than THEME.surface: an outline the same fill as a real card
        # reads as one, and the point of the dashes is that this is the empty slot after
        # them. The hover is where it takes a fill, because that is a hit target
        # confirming itself.
        bg = THEME.node_fill if self._hovered else "transparent"
        self.setStyleSheet(
            f"#AddCard {{ background: {bg}; border: {ADD_CARD_BORDER_WIDTH}px dashed {border}; border-radius: 4px; }}"
        )
        self._name_lbl.setStyleSheet(f"color: {color}; background: transparent;")
        # svg_icon bakes the tint in at render time, so the pixmap is rebuilt on every
        # pass rather than set once in _build -- see ProfileCard.apply_theme_styles.
        self._icon_lbl.setPixmap(svg_pixmap("plus", 12, tint=color))

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.refresh_theme_styles()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.refresh_theme_styles()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.add_requested.emit()
        super().mousePressEvent(event)
