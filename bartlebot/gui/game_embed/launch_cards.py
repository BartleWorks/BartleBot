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

On *release*, and only for a pointer that stayed put. A card that activated on press
launched four game clients on the first pixel of any wobble, and a press-and-sweep across
the face of a card -- what someone who has not found the grip yet will try -- has to end
in nothing rather than in a launch on whichever card the pointer came to rest over.

Reordering is a separate gesture on a separate target: ``GripHandle``, the six dots in the
gutter beside every card. It is the *only* drag source, and that is what makes the cursor
honest. The cursor is how a surface says "this drags", and it cannot say that over a whole
card whose primary verb is launch -- so the announcement was given a place of its own to
live, where an open hand is the truth. ``CardColumn`` owns what a drag then does, order
being a fact about the column rather than about any card.

That place is *outside* the card's frame. Each card is a gutter and a frame: the handle in
the first, everything else in the second, and only the second carrying the border, the
fill and the stripe. Inside the header the handle was two mistakes at once -- a target
smaller than anything else on the surface, and a member of the content row, so the space
it reserved pushed the name in by eighteen pixels and left a visible hole in front of it
on every card the pointer was not over. Beside the frame it is neither: the name starts at
the card's own padding whether the dots are drawn or not, and the handle is free to be as
tall as the thing it moves. The Add card carries the same indent (``GRIP_GUTTER``) so that
the column's last frame lines up with the ones above it.

THREE SURFACES, ONE CARD

The same cards draw the Game settings tab, which used to render the identical two lists
as ``QListWidget`` rows of formatted text -- so the surface where accounts are *edited*
said strictly less about them than the surface where they are merely launched (#192) --
and the launch profile editor, which had the same fault one dialog deeper: it is the
surface where a profile's membership is *decided*, and it named its accounts in a list of
bare strings, with none of the colour, roster or fit the cards behind it were showing.

``CardMode`` is what varies between the three. It was a ``manage`` boolean while there
were two, and that held only because those two happened to agree on all four of the
things it decides. The editor breaks the agreement in two places, so the flag is named
after the surface instead and the four facts are read back off it:

- ``has_body_verb`` -- whether clicking the card body does anything, and what. It
  launches on the launch screen, edits in the tab, and does **nothing** in the editor,
  where a card is a row in a list being assembled rather than a thing to open. That third
  value is one the boolean could not hold, and it is why a MEMBER card also drops the
  pointing-hand cursor and the hover highlight: both are promises that clicking works.
- ``pins_chrome`` -- whether the grip and the header icons are pinned open or revealed on
  hover. Hover is right where the card's job is to be picked out by name; the other two
  surfaces exist to run the verbs, and hiding them there is backwards.
- ``refuses_when_unfit`` -- whether a card that will not fit goes grey and stops taking
  clicks. True only on the launch screen, where a click starts what does not fit.
  Elsewhere it is **noted, not greyed**: greying out the row the user came here to fix is
  hostile.
- ``_verbs()`` -- the list below.

VERBS ARE DATA

Every verb a card has is a ``CardVerb``: a label, an icon, a tooltip, what it does, and
whether it sits on the card's face as well as in its right-click menu. One list per card
per mode, so *which buttons does this card have* is answered by reading ``_verbs`` rather
than by reading ``_build``, and a surface wanting a different set is handed one instead of
needing another branch inside the layout code. The face buttons used to be a hardcoded
parallel copy of half this list, which is why removing one meant editing ``_build``.

What each mode gets, and why:

- **Delete is menu-only on the launch screen.** A trash there would sit 22px from a body
  that starts a game client, and a mis-click costs a stored credential and a character
  roster. The Game tab does put it on the face, which is safe for exactly the reason it is
  not safe here: a body click there edits.
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
from enum import Enum
from typing import NamedTuple

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

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

# The grip sits in a gutter *outside* the card's frame rather than at the head of its
# content, and it is the height of the card rather than the size of an icon.
#
# Inside the header it was two things at once and bad at both. It was a twelve-pixel
# square -- a target smaller than anything else on the surface, at the one place the
# pointer has to land precisely -- and it was a member of the content row, so the space it
# reserved pushed the account's name in by eighteen pixels. On the launch screen, where
# the handle is only drawn on hover, that gap sat empty most of the time: every card had a
# hole punched in front of its name and nothing in it.
#
# Out in the gutter both problems are the same fix. The handle is no longer in the row it
# was displacing, so the name starts at the card's own padding whether the dots are drawn
# or not, and it is free to be as tall as the card -- which turns a 12x12 target into an
# 18-by-a-whole-card one without taking a pixel from the content. That is the arrangement
# Notion and Linear use, and for this reason: a handle beside the thing it moves can be
# the height of the thing it moves.
#
# The dots follow it up. Not by scaling the glyph -- six dots blown up to the height of a
# card are a decoration, and at that size they are the loudest thing on a card whose whole
# job is a name -- but by *repeating* it, so the mark stays the weight it always was and
# runs the length of what it grips. ``grip.svg`` is one tile of that column rather than a
# standalone six-dot glyph, seated so that the seam between two tiles is the same gap as
# the rows inside one.
#
# ``_GRIP_DOTS`` is therefore the tile, not the mark: how tall one repeat is, and with it
# how far apart the dots sit. It is a multiple of six because the tile's three rows sit at
# a sixth, a half and five sixths of it -- at any other size they rasterise onto fractional
# pixels, and a column of two-pixel dots half of which straddle a pixel boundary reads as a
# column of unevenly spaced dots, which is the one thing the seam arithmetic exists to
# avoid. ``_GRIP_INSET`` is what the column stops short of the card's own edges by, so the
# handle reads as sitting beside the card rather than ruled against it.
_GRIP_DOTS = 12
_GRIP_INSET = 4
_GRIP_WIDTH = 18
_GRIP_GAP = 4
# What a card that is not draggable has to indent by to line its frame up with the ones
# that are. The Add card is the only such card, and a column whose last frame starts
# twenty-two pixels to the left of every frame above it reads as a mistake.
GRIP_GUTTER = _GRIP_WIDTH + _GRIP_GAP

# How far the lifted card's shadow spreads, and how far it is offset down. Small numbers
# deliberately: this is a card rising a few pixels off a column, not a modal over a page.
_DRAG_SHADOW_BLUR = 16
_DRAG_SHADOW_OFFSET = 3
_DRAG_SHADOW_ALPHA = 90

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


class CardMode(Enum):
    """Which surface a card is on, and so what it does and what it shows.

    One value rather than a handful of booleans, because it is one fact: the four things
    a card varies by are read off it (see the module docstring), and every name here is a
    surface that exists. Two booleans would describe four surfaces, two of which do not.
    """

    #: The Quick Launch screen. A body click launches, the chrome is revealed on hover,
    #: and a card that will not fit goes grey and refuses its clicks.
    LAUNCH = "launch"
    #: The Game settings tab. A body click edits, the chrome is pinned open, and a card
    #: that will not fit is noted rather than greyed.
    MANAGE = "manage"
    #: A row in a list being assembled -- the launch profile editor. The body has no verb
    #: at all, the chrome is pinned open, and nothing here refuses.
    MEMBER = "member"


class CardVerb(NamedTuple):
    """One thing a card can do, as data rather than as a line of ``_build``.

    *label* names it in the right-click menu and *tooltip* on the button. They differ
    because a menu entry sits under a heading the card already gives it, while a bare icon
    has to say what it acts on: "Remove" against "Remove 'Main' from this profile".

    *on_face* promotes it to a button in the card's header, and a verb that takes it needs
    a real *icon* to be drawn from. Everything is in the menu either way -- that is the
    keyboard and the discoverability route, and it is what lets a verb stay off the face
    without becoming hard to reach.
    """

    label: str
    icon: str  # icon name for svg_icon; "" is allowed only for a menu-only verb
    tooltip: str
    run: Callable[[], None]
    on_face: bool = False


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


class GripHandle(ThemedMixin, QWidget):
    """The column of dots beside a card: the one place a press starts a reorder.

    The whole card used to be the drag source, which worked and told nobody. Nothing on
    the card said it could be dragged, and nothing could be made to say it: the cursor is
    the usual announcement, and a card whose primary verb is *launch* cannot wear an open
    hand over its whole face without advertising the wrong gesture. So the announcement
    got somewhere to live. Over the grip the cursor opens, and everywhere else on the card
    it stays the pointing hand that means "this launches".

    That is also why the grip is now the *only* drag source rather than a hint at a card
    that drags everywhere. A cursor that changes on one twelve-pixel square of a widget
    that is draggable all over is a smaller lie than no cursor at all, but it is still a
    lie, and the honest version costs the user nothing they can see. ``duel_strategy_editor``
    reached the same place from the other direction -- there it was a text box that had to
    keep its own mouse-down.

    Presses are **accepted**, not passed on. A plain widget ignores them by default and Qt
    re-sends what a child ignores to its parent, which would hand the press to the card --
    and, more to the point, would send every subsequent mouse *move* to the card as well.
    The moves are what ``CardColumn`` turns into a reorder, so a grip that does not keep
    the press is a grip that never sees the drag.

    Still a child of the card, for all that it is drawn outside its frame. Qt sends no
    Leave to a widget when the pointer moves onto one of its own children, so a handle
    that belongs to the card is one the pointer can travel to without the card deciding it
    is no longer hovered -- and on the launch screen the hover is what draws the handle in
    the first place. A sibling in a row above the card would have hidden itself the instant
    the user reached for it.
    """

    def __init__(self, tooltip: str = "Drag to reorder", parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_GRIP_WIDTH)
        # The width is pinned and the height is left alone: a box layout gives an item the
        # full cross height unless something stops it, so the handle grows to the card's
        # own height and the target is the whole left edge. An *Expanding* vertical policy
        # would do the same to the target and one thing more -- a layout inherits its
        # items' expanding directions, so the card would report itself vertically
        # expanding to the column and stretch to fill it, which is a 400px account card in
        # a column with room to spare.
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(tooltip)
        # Held while hidden, so the gutter is the same width whether the handle is drawn
        # in it or not. Without this the card's frame would slide left on every hover-out,
        # which is the shift that moving the grip out of the header exists to stop.
        policy = self.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.setSizePolicy(policy)
        # One repeat of the dot column, tinted for the current theme. The stack of them is
        # laid down at paint time; this is only the mark being stacked.
        self._tile = QPixmap()
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        # svg_pixmap bakes the tint in at render time, so the dots are re-rendered on
        # every theme pass rather than set once in __init__.
        self._tile = svg_pixmap("grip", _GRIP_DOTS, tint=THEME.text_subtle)
        self.update()

    @property
    def column_height(self) -> int:
        """How tall the stack of dots draws -- the card's height, less its inset, rounded
        down to whole tiles.

        Public because it is what "the handle matches the target" means, and the only way
        to ask is to compute it: nothing is stored, since the answer changes with the
        height and the height is the layout's to give.
        """
        return max(1, (self.height() - 2 * _GRIP_INSET) // _GRIP_DOTS) * _GRIP_DOTS

    def paintEvent(self, event) -> None:
        """Stack tiles of ``grip.svg`` down the middle of the handle.

        Stacked rather than stretched, because the tile is a circle: a renderer told to
        fill an 18x54 box from a 24x24 viewBox either draws ellipses or drops one small
        glyph adrift in the middle of the target. Repeating it keeps the mark the size it
        was drawn at and lets the *column* be the thing that matches the card.

        Painted rather than composed into a pixmap and set once, because on the launch
        screen this widget spends most of its life hidden -- and a layout does not resize
        what it is not showing, so a cached pixmap would still be one tile tall on the
        frame the user first hovers a card. Painting asks the current height every time
        and has nothing to keep in sync.
        """
        rows = self.column_height // _GRIP_DOTS
        painter = QPainter(self)
        left = (self.width() - _GRIP_DOTS) // 2
        top = (self.height() - rows * _GRIP_DOTS) // 2
        for row in range(rows):
            painter.drawPixmap(left, top + row * _GRIP_DOTS, self._tile)
        painter.end()

    def set_held(self, held: bool) -> None:
        """Close the hand while the card this grip belongs to is being dragged."""
        self.setCursor(Qt.CursorShape.ClosedHandCursor if held else Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)


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

    def __init__(self, parent=None, *, mode: CardMode = CardMode.LAUNCH) -> None:
        super().__init__(parent)
        # Which surface this card is on, and the first thing set: the cursor below is one
        # of the four facts read off it. See the module docstring for the other three.
        self._mode = mode
        # The widget is the *row*: a gutter holding the drag handle, and beside it the
        # frame everything else means by "the card". Only the frame is styled, and it
        # keeps the ``LaunchCard`` object name, so every rule in this file still reads
        # ``#LaunchCard`` and still lands on the thing with the border -- a stylesheet set
        # on this widget cascades to it.
        self._body = QFrame(self)
        self._body.setObjectName("LaunchCard")
        self._body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # A pointing hand is the surface promising that clicking does something. In MEMBER
        # mode nothing happens on a body click, so the promise would be a lie.
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.has_body_verb else Qt.CursorShape.ArrowCursor)
        self._hovered = False
        # The card's action buttons, with the verb each was built from: svg_icon bakes the
        # tint in at render time, so every one of them is re-rendered on a theme pass, and
        # a caller after a particular button asks for it by the verb's label.
        self._action_btns: list[tuple[QToolButton, CardVerb]] = []
        # The drag handle, and whether the column currently has this card off the layout
        # and following the pointer. ``_dragging`` is a styling state exactly like
        # ``_hovered`` -- one flag that picks which palette apply_theme_styles uses -- and
        # it is set by CardColumn, which is the only thing that knows a drag is happening.
        self._grip: GripHandle | None = None
        self._dragging = False
        self._shadow: QGraphicsDropShadowEffect | None = None
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
        clickable = self.has_body_verb and not refuses
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

    @property
    def mode(self) -> CardMode:
        """Which surface this card is on -- see :class:`CardMode`.

        Public because the things that vary by it are read from outside as well as in:
        a column asks whether its cards refuse, and a test asks what it built.
        """
        return self._mode

    @property
    def refuses_when_unfit(self) -> bool:
        """Whether a card that will not fit goes grey and stops taking clicks.

        True on the launch screen, where a click starts what does not fit. False on the
        other two, where the same card is a thing being edited or a row being assembled,
        and the refusal is a note above and beneath it rather than a state it is put into.
        """
        return self._mode is CardMode.LAUNCH

    @property
    def pins_chrome(self) -> bool:
        """Whether the grip and the header buttons stay visible instead of awaiting hover.

        False only on the launch screen, whose job is to let a name be picked out: a
        button drawn permanently on every card competes with the name there, and there is
        nothing for it to compete with on a surface that exists to run the verbs.
        """
        return self._mode is not CardMode.LAUNCH

    @property
    def has_body_verb(self) -> bool:
        """Whether clicking the card body does anything at all.

        False in MEMBER mode, where the card is a row in a list being assembled: its
        verbs are the grip and the cross, and the body is not a third one. So it takes no
        pointing-hand cursor, draws no hover highlight, and emits no ``activated``.
        """
        return self._mode is not CardMode.MEMBER

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
        if self._dragging:
            # Ahead of the refusal branch: a card that cannot launch can still be
            # reordered, and while it is in the air what the user needs to see is which
            # card they are holding, not what it would refuse to do if they let go.
            self._apply_dragging_styles()
            return
        if self._unavailable is not None and self.refuses_when_unfit:
            # Fill and text both move, because either alone reads as enabled — the same
            # finding as the app-wide disabled palette (tests/test_disabled_affordance.py).
            self._apply_unavailable_styles()
            return
        # Hover is the card saying "click me", so a card whose body has no verb does not
        # say it: in MEMBER mode the highlight would promise an activation that never
        # comes. Nothing is lost by staying quiet there, the chrome being pinned open.
        lit = self._hovered and self.has_body_verb
        bg = THEME.node_fill if lit else THEME.surface
        border = THEME.accent if lit else THEME.border
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
        self._restyle_action_icons()

    def _apply_dragging_styles(self) -> None:
        """The held card: filled, outlined in the accent, and still wearing its stripe.

        The border stays one pixel wide. Widening it to two is the obvious way to say
        "picked up" and it moves every label in the card in by a pixel at the moment the
        drag starts, which reads as the card twitching in the user's hand. The fill and
        the shadow carry the state instead, and neither touches the content box.

        The stripe survives because it names the account, not a state -- the same reason
        the refusal palette drops it and this one does not. Losing it here would recolour
        the one card whose colour is being matched against the pills beside it, at exactly
        the moment the user is looking at where it lands.
        """
        stripe = (
            ""
            if self._stripe_key is None
            else f" border-left: {STRIPE_WIDTH}px solid {account_accent(self._stripe_key)};"
        )
        self.setStyleSheet(
            f"#LaunchCard {{ background: {THEME.node_fill_selected}; "
            f"border: 1px solid {THEME.accent}; border-radius: 4px;{stripe} }}"
        )
        if self._name_lbl is not None:
            self._name_lbl.setStyleSheet(f"color: {THEME.text}; background: transparent;")
        for lbl, color in ((self._detail_lbl, THEME.text_muted), (self._quiet_lbl, THEME.text_subtle)):
            if lbl is not None:
                lbl.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent;")
        self._restyle_action_icons()

    @property
    def is_dragging(self) -> bool:
        """Whether the column currently has this card lifted out of the layout."""
        return self._dragging

    @property
    def frame(self) -> QFrame:
        """The bordered part of the card -- everything except the drag handle's gutter.

        Public because where it starts is the contract the gutter exists to hold: the
        handle is outside this frame, so the content inside it stays where it is whether
        the handle is drawn or not. Reading the widget is also how the fit and hover
        palettes are checked, all of them being rules on ``#LaunchCard``.
        """
        return self._body

    @property
    def grip(self) -> GripHandle | None:
        """This card's drag handle, or None if it was built without one.

        Public because the column installs its event filter on it: the drag belongs to
        ``CardColumn`` -- order is a fact about the column, not about any card -- and the
        grip is the card's half of that arrangement.
        """
        return self._grip

    def set_dragging(self, dragging: bool) -> None:
        """Show or clear the picked-up state. Called by the column, both ways.

        The shadow is a ``QGraphicsDropShadowEffect`` attached only for the duration of
        the drag rather than kept and toggled. An effect on a widget forces Qt to render
        that widget through an offscreen surface for as long as it is set, and these cards
        redraw on every hover of a five-card column; paying for that permanently to get an
        elevation that is visible for the two seconds of a drag is the wrong trade.

        ``setGraphicsEffect(None)`` deletes the effect that was there, so the reference is
        dropped in the same breath -- keeping it would leave a dangling one behind.
        """
        if dragging == self._dragging:
            return
        self._dragging = dragging
        if dragging:
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(_DRAG_SHADOW_BLUR)
            shadow.setOffset(0, _DRAG_SHADOW_OFFSET)
            shadow.setColor(QColor(0, 0, 0, _DRAG_SHADOW_ALPHA))
            self.setGraphicsEffect(shadow)
            self._shadow = shadow
        else:
            self.setGraphicsEffect(None)
            self._shadow = None
        if self._grip is not None:
            self._grip.set_held(dragging)
        self.refresh_theme_styles()

    def _build_frame(self, what: str) -> QVBoxLayout:
        """Lay out the handle and the card frame, and hand back the frame's own layout.

        Every card is built into this rather than into itself, so that the grip is beside
        the frame instead of inside the header. What a subclass then fills is the frame,
        which is what the border, the fill, the stripe and every hover rule are drawn on --
        the gutter to its left is transparent and holds only the handle.

        *what* names the list a drag would reorder, for the handle's tooltip.
        """
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(_GRIP_GAP)
        row.addWidget(self._make_grip(what), 0)
        row.addWidget(self._body, 1)

        inner = QVBoxLayout(self._body)
        inner.setContentsMargins(10, 7, 10, 7)
        inner.setSpacing(2)
        return inner

    def _make_grip(self, what: str) -> GripHandle:
        """This card's drag handle, named after what a drag would reorder.

        Revealed and hidden with the action buttons, and pinned open on the surfaces they
        are pinned open on -- ``pins_chrome`` is one answer for both. It is the same
        judgement: on the launch screen the card's job is to be picked out by name and
        everything else defers to that, while the Game tab and the profile editor exist to
        do things to the card, so the things you can do to it are on its face.
        """
        self._grip = GripHandle(f"Drag to reorder {what}", self)
        self._grip.setVisible(self.pins_chrome)
        return self._grip

    def _restyle_action_icons(self) -> None:
        """Re-render every action icon in the current theme.

        svg_icon bakes THEME.icon_tint in at render time, so an icon has to be rebuilt on
        every theme pass rather than set once in _build -- an icon set once goes stale on
        a theme switch, with no stylesheet anywhere to grep for. Which is why each button
        is held beside the verb it was drawn from.
        """
        for button, verb in self._action_btns:
            button.setIcon(svg_icon(verb.icon, size=14))

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
        self._restyle_action_icons()

    def _add_face_verbs(self, header: QHBoxLayout) -> None:
        """Put every verb marked ``on_face`` into *header*, in the order ``_verbs`` gives.

        The one place a card's buttons are built, so which buttons a card has is decided
        by its verb list and nowhere else. ``_build`` used to name each button in the
        layout code, which meant a surface wanting a different set needed a branch in
        there -- and there was no way to ask a card what its verbs were without reading it.
        """
        for verb in self._verbs():
            if verb.on_face:
                header.addWidget(self._make_verb_button(verb), 0, Qt.AlignmentFlag.AlignVCenter)

    def _make_verb_button(self, verb: CardVerb) -> QToolButton:
        """One icon in a card's header row: one of this card's verbs, as a button.

        A real child widget rather than a hit-tested region of the card, so that it
        consumes its own clicks. The whole card is a hit target, and a painted-on icon
        would launch, or edit, the thing the user was trying to do something else to.

        Revealed on hover on the launch screen, because it is secondary to launching: a
        button drawn permanently on every card competes with the name for attention, on
        the one screen whose job is to let you pick a name. Hover-only is not the whole
        answer to reaching it — that is what ``contextMenuEvent`` below is for, which is
        also the route for anyone not using a mouse.

        Pinned open wherever the card is the thing being worked on rather than launched:
        there is nothing for the icons to compete with on those surfaces, and hiding the
        verbs behind hover on a surface that exists to run them is backwards. It is also
        what lets those surfaces carry a trash, or a cross, on the face at all.

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
        button.setToolTip(verb.tooltip)
        button.clicked.connect(verb.run)
        button.setVisible(self.pins_chrome)
        self._action_btns.append((button, verb))
        return button

    @property
    def action_buttons(self) -> list[QToolButton]:
        """This card's face buttons, in header order. Empty for a card with no face verbs."""
        return [button for button, _verb in self._action_btns]

    def action_button(self, label: str) -> QToolButton | None:
        """The face button for the verb *label*, or None when that verb is menu-only here.

        Asked by label rather than by position, because which verbs reach the face is a
        function of the mode: the same card carries a trash on one surface and not on the
        next, so "the second button" means two different things.
        """
        for button, verb in self._action_btns:
            if verb.label == label:
                return button
        return None

    def _verbs(self) -> list[CardVerb]:
        """Everything this card can do, in the order it is offered.

        Subclasses fill it in; a card that returns nothing gets no menu and no buttons.
        The label, the icon and ``on_face`` are the rendering, and ``run`` is a signal
        emission that some surface has wired to one of the module functions in
        settings/accounts_tab.py — so moving a verb between the face and the menu is one
        flag here rather than an edit to any layout code.
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
        verbs = self._verbs()
        if not verbs:
            return
        menu = popup_menu(self)
        for verb in verbs:
            action = (
                menu.addAction(svg_icon(verb.icon, size=14), verb.label) if verb.icon else menu.addAction(verb.label)
            )
            action.triggered.connect(verb.run)
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
        if self.pins_chrome:
            return
        for button, _icon in self._action_btns:
            button.setVisible(visible)
        if self._grip is not None:
            self._grip.setVisible(visible)

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

        On release rather than on press, and only for a pointer that stayed put. Reorders
        start on the grip now (``GripHandle``), so the body no longer competes with a
        drag — but the stayed-put test earns its keep twice over regardless. It is what
        makes a press-and-sweep across the card do *nothing* rather than launch on the
        far side of it, which is the behaviour a user who has not found the grip yet will
        try first; and activating on press would fire on the first pixel of any pointer
        wobble, launching four game clients on a hand that had not decided yet. Qt's own
        buttons activate on release for the same reason.

        The whole card is the hit target, so a refusing one has to swallow its own clicks:
        there is no QPushButton here whose disabled state would do it. A card in the Game
        tab does not refuse, because what its click does there is edit — which is exactly
        what an unfittable card wants the user to go and do.

        The *frame* is the hit target rather than this widget's whole rectangle, which now
        includes the drag handle's gutter. Nothing is drawn in the few pixels between the
        handle and the frame, and a strip of blank column that silently launches a game
        client is not a hit target anyone aimed at.

        A MEMBER card has no body verb, so it never gets this far. Nothing is connected to
        ``activated`` there, but the guard is explicit rather than relying on that: a card
        that emits an activation nobody is listening for is a signal waiting to be wired
        up by accident on the one surface where a body click is meant to do nothing.
        """
        pressed = self._press_pos
        self._press_pos = None
        refuses = self._unavailable is not None and self.refuses_when_unfit
        if (
            event.button() == Qt.MouseButton.LeftButton
            and pressed is not None
            and self.has_body_verb
            and not refuses
            and self.frame.geometry().contains(event.position().toPoint())
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
        screen, where it would sit 22px from a body that starts a game client, it stays on
        the right-click menu.

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
        # Framed like a launch card, and for the same reason: the dashed outline is drawn
        # on an inner frame so that this card can carry the drag gutter's indent without
        # its border reaching back across it. A terminator whose frame starts twenty-two
        # pixels left of every frame above it does not read as the last card in the
        # column, it reads as a misplaced one.
        self._body = QFrame(self)
        self._body.setObjectName("AddCard")
        self._body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
        row = QHBoxLayout(self)
        row.setContentsMargins(GRIP_GUTTER, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._body)

        layout = QHBoxLayout(self._body)
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
