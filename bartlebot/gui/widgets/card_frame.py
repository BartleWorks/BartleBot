"""``CardFrame``: the frame, the grip, the hover and the verbs every card in this app shares.

A card here is a *row*: a gutter holding a drag handle, and beside it the bordered,
filled frame everything else means by "the card". This class owns that arrangement and
everything that goes with it -- the handle, the hover state, the picked-up state, the
theme pass, the verb list and the right-click menu built from it. What a card *says* is
the subclass's: ``_LaunchCard`` in gui/game_embed/launch_cards.py fills the frame with a
name, a detail line and a quiet line, and a player card in gui/sidebar fills it with live
rows and their copy affordances.

It lives out here rather than in ``game_embed`` because its second user does not: a frame
shared by two packages that sits inside one of them is how the fork this class exists to
prevent gets started. ``card_columns.py`` was written to end exactly that split -- see its
module docstring -- and forking the hover, drag and theming code across two card files
would reopen it one level down.

CLICK MODEL

The whole card is the primary verb. That keeps a one-click launch on the Quick Launch
screen and a one-click open in the Game tab; the alternative -- select, then press a
button -- would put a second click in front of the app's most common action.

Activation is on *release*, and only for a pointer that stayed put. A card that activated
on press launched four game clients on the first pixel of any wobble, and a press-and-
sweep across the face of a card -- what someone who has not found the grip yet will try --
has to end in nothing rather than in an activation on whichever card the pointer came to
rest over.

Nothing on a card should be a form control. A combo box on a surface that activates on
click is a hole in the hit target, and one that would have to swallow its own clicks to be
safe.

THE GUTTER

Reordering is a separate gesture on a separate target: ``GripHandle``, the six dots in the
gutter beside every card. It is the *only* drag source, and that is what makes the cursor
honest. The cursor is how a surface says "this drags", and it cannot say that over a whole
card whose primary verb is launch -- so the announcement was given a place of its own to
live, where an open hand is the truth. ``CardColumn`` owns what a drag then does, order
being a fact about the column rather than about any card.

That place is *outside* the card's frame: the handle in the gutter, everything else in the
frame, and only the frame carrying the border and the fill. Inside the header the handle
was two mistakes at once -- a target smaller than anything else on the surface, and a
member of the content row, so the space it reserved pushed the name in by eighteen pixels
and left a visible hole in front of it on every card the pointer was not over. Beside the
frame it is neither: the name starts at the card's own padding whether the dots are drawn
or not, and the handle is free to be as tall as the thing it moves. A card that is *not*
draggable carries the same indent (``GRIP_GUTTER``) so that its frame lines up with the
ones above it.

HOVER IS STATE, NOT A PSEUDO-CLASS

``_hovered`` picks which colours the theme pass uses, and the pass restyles the frame and
its labels together. A ``:hover`` pseudo-class would restyle the frame alone and leave
every child label in the old palette after a theme switch, which is the same finding
``_BotCard`` in gui/canvas/bot_canvas.py reached.

WHAT VARIES: ``CardMode``

One value rather than a handful of booleans, because it is one fact -- which surface the
card is on -- and three of the four things a card varies by are read off it:

- ``has_body_verb`` -- whether clicking the card body does anything at all. It does on the
  Quick Launch screen and in the Game settings tab, and it does **nothing** in the launch
  profile editor, where a card is a row in a list being assembled rather than a thing to
  open. That third value is one a boolean could not hold, and it is why a MEMBER card also
  drops the pointing-hand cursor and the hover highlight: both are promises that clicking
  works.
- ``pins_chrome`` -- whether the grip and the face buttons are pinned open or revealed on
  hover. Hover is right where the card's job is to be picked out by name; the other two
  surfaces exist to run the verbs, and hiding them there is backwards.
- ``refuses_when_unfit`` -- whether a card that will not fit goes grey and stops taking
  clicks. True only on the launch screen, where a click starts what does not fit.
  Elsewhere it is **noted, not greyed**: greying out the row the user came here to fix is
  hostile. The refusal itself is ``_LaunchCard``'s; this is only which surface honours it.
- ``_verbs()`` -- the list below.

VERBS ARE DATA

Every verb a card has is a ``CardVerb``: a label, an icon, a tooltip, what it does, and
whether it sits on the card's face as well as in its right-click menu. One list per card
per mode, so *which buttons does this card have* is answered by reading ``_verbs`` rather
than by reading the layout code, and a surface wanting a different set is handed one
instead of needing another branch inside ``_build``. The face buttons used to be a
hardcoded parallel copy of half this list, which is why removing one meant editing the
layout.

Destructive verbs stay off the face wherever the body click is destructive-adjacent: a
trash 22px from a body that starts a game client is a mis-click that costs a stored
credential. Everything is in the right-click menu either way -- that is the keyboard and
the discoverability route, and it is what lets a verb stay off the face without becoming
hard to reach.

None of the verbs are implemented by a card. Each ``run`` is a signal emission, and the
surface is what wires it -- so what a card does and how a card draws it stay separable,
which is what lets each surface promote a different subset of one list.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import NamedTuple

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.icon_utils import svg_icon, svg_pixmap
from bartlebot.gui.menu_utils import popup_menu
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin

_FACE_BTN_SIZE = 22

# The grip sits in a gutter *outside* the card's frame rather than at the head of its
# content, and it is the height of the card rather than the size of an icon.
#
# Inside the header it was two things at once and bad at both. It was a twelve-pixel
# square -- a target smaller than anything else on the surface, at the one place the
# pointer has to land precisely -- and it was a member of the content row, so the space it
# reserved pushed the card's name in by eighteen pixels. On the launch screen, where the
# handle is only drawn on hover, that gap sat empty most of the time: every card had a
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


class CardMode(Enum):
    """Which surface a card is on, and so what it does and what it shows.

    One value rather than a handful of booleans, because it is one fact: the things a
    card varies by are read off it (see the module docstring), and every name here is a
    surface that exists. Two booleans would describe four surfaces, two of which do not.
    """

    #: The Quick Launch screen. A body click launches, the chrome is revealed on hover,
    #: and a card that will not fit goes grey and refuses its clicks.
    LAUNCH = "launch"
    #: The Game settings tab. A body click edits, the chrome is pinned open, and a card
    #: that will not fit is noted rather than greyed.
    MANAGE = "manage"
    #: A card whose body is not a button. The chrome is pinned open and nothing refuses,
    #: exactly as in MANAGE, and the body has no verb at all -- so no pointing-hand cursor,
    #: no hover highlight and no ``activated``. Two surfaces mean it: a row in a launch
    #: profile being assembled, whose verbs are the grip and the cross; and a player card
    #: in the Players tab, whose verbs are on its face and whose body is four live values
    #: with nothing to open. Named for the first and defined by the property both share.
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


class GripHandle(ThemedMixin, QWidget):
    """The column of dots beside a card: the one place a press starts a reorder.

    The whole card used to be the drag source, which worked and told nobody. Nothing on
    the card said it could be dragged, and nothing could be made to say it: the cursor is
    the usual announcement, and a card whose primary verb is *launch* cannot wear an open
    hand over its whole face without advertising the wrong gesture. So the announcement
    got somewhere to live. Over the grip the cursor opens, and everywhere else on the card
    it stays the pointing hand that means "this activates".

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


class CardFrame(ThemedMixin, QWidget):
    """A card's frame, grip, hover, drag state and verbs -- everything but its content.

    Subclass it, call :meth:`_build_frame` to get the frame's layout, fill that layout,
    and return the card's verbs from :meth:`_verbs`. Three hooks shape the theme pass
    without reimplementing it: :meth:`_frame_extra_qss` adds to the frame's own rule,
    :meth:`_restyle_content` recolours whatever the subclass put in it, and
    :meth:`_apply_state_palette` takes the pass over entirely for a state this class knows
    nothing about.

    Hover is state rather than a QSS pseudo-class for the same reason as ``_BotCard``:
    the frame and the labels are restyled by one pass, and ``_hovered`` picks which
    colours that pass uses. A pseudo-class would restyle the frame and leave every label
    in the old palette after a theme switch.
    """

    #: ``objectName`` on the frame, and so what every rule this class writes is scoped to.
    #: A subclass overrides it to keep a name its own stylesheet rules already use.
    FRAME_NAME = "CardFrame"

    # The card body was activated. What that *means* is the subclass's, because it is the
    # surface's: it launches on the Quick Launch screen and edits in the Game tab. Named
    # for the gesture rather than for either verb, since one signal carrying "launch" to a
    # slot that edits is the kind of thing that reads fine and misleads for a year.
    activated = pyqtSignal()

    def __init__(self, parent=None, *, mode: CardMode = CardMode.LAUNCH) -> None:
        super().__init__(parent)
        # Which surface this card is on, and the first thing set: the cursor below is one
        # of the facts read off it. See the module docstring for the others.
        self._mode = mode
        # The widget is the *row*: a gutter holding the drag handle, and beside it the
        # frame everything else means by "the card". Only the frame is styled, and it
        # carries ``FRAME_NAME`` as its object name, so a rule scoped to that name lands
        # on the thing with the border -- a stylesheet set on this widget cascades to it.
        self._body = QFrame(self)
        self._body.setObjectName(self.FRAME_NAME)
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
        # column turns a press-and-move into cannot also activate what it was reordering.
        self._press_pos: QPoint | None = None

    # ------------------------------------------------------------------
    # What the surface decides
    # ------------------------------------------------------------------

    @property
    def mode(self) -> CardMode:
        """Which surface this card is on -- see :class:`CardMode`.

        Public because the things that vary by it are read from outside as well as in:
        a column asks whether its cards refuse, and a test asks what it built.
        """
        return self._mode

    @property
    def refuses_when_unfit(self) -> bool:
        """Whether a card that cannot be used goes grey and stops taking clicks.

        True on the launch screen, where a click starts what does not fit. False on the
        other two, where the same card is a thing being edited or a row being assembled,
        and the refusal is a note above and beneath it rather than a state it is put into.
        """
        return self._mode is CardMode.LAUNCH

    @property
    def pins_chrome(self) -> bool:
        """Whether the grip and the face buttons stay visible instead of awaiting hover.

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

    # ------------------------------------------------------------------
    # The frame and its gutter
    # ------------------------------------------------------------------

    def _build_frame(self, what: str, *, draggable: bool = True) -> QVBoxLayout:
        """Lay out the handle and the card frame, and hand back the frame's own layout.

        Every card is built into this rather than into itself, so that the grip is beside
        the frame instead of inside the header. What a subclass then fills is the frame,
        which is what the border, the fill and every hover rule are drawn on -- the gutter
        to its left is transparent and holds only the handle.

        *what* names the list a drag would reorder, for the handle's tooltip.

        *draggable* is False for a card that is not in an ordered list at all -- an
        unhooked player, which sits outside the run ``ClientManager`` keeps contiguous.
        The gutter stays, holding a spacer of exactly the handle's width: a card built
        without one is not a card that has moved twenty-two pixels left of the ones above
        it. ``CardColumn`` reads ``grip`` and skips a card that has none, so this is also
        what makes such a card undraggable rather than merely unlabelled -- the same
        arrangement ``AddCard`` uses for the column's terminator.
        """
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(_GRIP_GAP)
        if draggable:
            row.addWidget(self._make_grip(what), 0)
        else:
            # GRIP_GUTTER, not _GRIP_WIDTH: an explicit spacer gets none of the row's
            # inter-item spacing the way two real widgets do, so a bare _GRIP_WIDTH
            # spacer left this frame 4px left of a draggable card's -- and of AddCard,
            # which reserves the same GRIP_GUTTER as its own left margin.
            row.addSpacing(GRIP_GUTTER)
        row.addWidget(self._body, 1)

        inner = QVBoxLayout(self._body)
        inner.setContentsMargins(10, 7, 10, 7)
        inner.setSpacing(2)
        return inner

    def _make_grip(self, what: str) -> GripHandle:
        """This card's drag handle, named after what a drag would reorder.

        Revealed and hidden with the face buttons, and pinned open on the surfaces they
        are pinned open on -- ``pins_chrome`` is one answer for both. It is the same
        judgement: on the launch screen the card's job is to be picked out by name and
        everything else defers to that, while the Game tab and the profile editor exist to
        do things to the card, so the things you can do to it are on its face.
        """
        self._grip = GripHandle(f"Drag to reorder {what}", self)
        self._grip.setVisible(self.pins_chrome)
        return self._grip

    @property
    def frame(self) -> QFrame:
        """The bordered part of the card -- everything except the drag handle's gutter.

        Public because where it starts is the contract the gutter exists to hold: the
        handle is outside this frame, so the content inside it stays where it is whether
        the handle is drawn or not. Reading the widget is also how the palettes are
        checked, all of them being rules on ``FRAME_NAME``.
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

    @property
    def is_dragging(self) -> bool:
        """Whether the column currently has this card lifted out of the layout."""
        return self._dragging

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

    # ------------------------------------------------------------------
    # The theme pass
    # ------------------------------------------------------------------

    def apply_theme_styles(self) -> None:
        """Restyle the frame, the content in it and every face icon, in that order.

        The dragging branch comes first, ahead of anything a subclass would add: a card
        that cannot be used can still be reordered, and while it is in the air what the
        user needs to see is which card they are holding, not what it would refuse to do
        if they let go.
        """
        if self._dragging:
            self._apply_dragging_styles()
        elif not self._apply_state_palette():
            # Hover is the card saying "click me", so a card whose body has no verb does
            # not say it: in MEMBER mode the highlight would promise an activation that
            # never comes. Nothing is lost by staying quiet there, the chrome being pinned
            # open.
            lit = self._hovered and self.has_body_verb
            self._apply_frame_palette(
                THEME.node_fill if lit else THEME.surface,
                THEME.accent if lit else THEME.border,
            )
            self._restyle_content()
        self._restyle_action_icons()

    def _apply_frame_palette(self, background: str, border: str, extra: str | None = None) -> None:
        """Write the frame's own rule: a fill, a 1px border, and whatever a subclass adds.

        *extra* defaults to :meth:`_frame_extra_qss` and is passed explicitly only by a
        palette that means to drop it -- a greyed card, say, whose stripe names an account
        rather than a state.

        Anything *extra* touches has to come after the border shorthand, which sets all
        four sides: written the other way round the shorthand would overwrite it and the
        card would lose the addition with nothing in the stylesheet looking wrong.
        """
        addition = self._frame_extra_qss() if extra is None else extra
        self.setStyleSheet(
            f"#{self.FRAME_NAME} {{ background: {background}; "
            f"border: 1px solid {border}; border-radius: 4px;{addition} }}"
        )

    def _apply_dragging_styles(self) -> None:
        """The held card: filled, outlined in the accent, and keeping every addition.

        The border stays one pixel wide. Widening it to two is the obvious way to say
        "picked up" and it moves every label in the card in by a pixel at the moment the
        drag starts, which reads as the card twitching in the user's hand. The fill and
        the shadow carry the state instead, and neither touches the content box.

        Whatever ``_frame_extra_qss`` adds survives, because it names the card rather than
        a state -- the same reason a refusal palette drops it and this one does not.
        """
        self._apply_frame_palette(THEME.node_fill_selected, THEME.accent)
        self._restyle_content()

    def _apply_state_palette(self) -> bool:
        """Take the theme pass over for a state only the subclass knows about, or don't.

        Returning True means the frame and its content have both been styled and the
        resting palette must not run. The face icons are restyled either way, by
        ``apply_theme_styles``.
        """
        return False

    def _frame_extra_qss(self) -> str:
        """Declarations appended to the frame's own rule -- an account card's stripe.

        Returned rather than written, so that every palette that wants it gets it in the
        one place the border shorthand is emitted. See :meth:`_apply_frame_palette`.
        """
        return ""

    def _restyle_content(self) -> None:
        """Recolour whatever the subclass put inside the frame. Called by every palette."""

    def _restyle_action_icons(self) -> None:
        """Re-render every face icon in the current theme.

        svg_icon bakes THEME.icon_tint in at render time, so an icon has to be rebuilt on
        every theme pass rather than set once in ``_build`` -- an icon set once goes stale
        on a theme switch, with no stylesheet anywhere to grep for. Which is why each
        button is held beside the verb it was drawn from.
        """
        for button, verb in self._action_btns:
            button.setIcon(svg_icon(verb.icon, size=14))

    # ------------------------------------------------------------------
    # Verbs
    # ------------------------------------------------------------------

    def _verbs(self) -> list[CardVerb]:
        """Everything this card can do, in the order it is offered.

        Subclasses fill it in; a card that returns nothing gets no menu and no buttons.
        The label, the icon and ``on_face`` are the rendering, and ``run`` is a signal
        emission that some surface has wired up -- so moving a verb between the face and
        the menu is one flag here rather than an edit to any layout code.
        """
        return []

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
        would activate the thing the user was trying to do something else to.

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
        button.setFixedSize(_FACE_BTN_SIZE, _FACE_BTN_SIZE)
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

    def contextMenuEvent(self, event) -> None:
        """Right-click: every verb this card has, including the ones not on its face.

        This is where a destructive verb lives on a surface whose body click is itself
        costly. On the launch screen the card body starts a game client, and a trash icon
        22px from it is a mis-click that costs a stored credential and a character roster
        -- so the destructive verb is the one that takes a deliberate second gesture. It is
        also the keyboard and discoverability answer to a pencil that only appears on
        hover.

        Offered on a card that is refusing its clicks too, for the same reason the pencil
        is: editing the thing -- lowering its resolution, dropping an account -- is exactly
        what the greyed card is telling the user to do.
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

    # ------------------------------------------------------------------
    # Hover and activation
    # ------------------------------------------------------------------

    def _set_actions_visible(self, visible: bool) -> None:
        """Show or hide the grip and the face buttons — a no-op where they are pinned open."""
        if self.pins_chrome:
            return
        for button, _verb in self._action_btns:
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
        makes a press-and-sweep across the card do *nothing* rather than activate on the
        far side of it, which is the behaviour a user who has not found the grip yet will
        try first; and activating on press would fire on the first pixel of any pointer
        wobble, launching four game clients on a hand that had not decided yet. Qt's own
        buttons activate on release for the same reason.

        The whole card is the hit target, so a refusing one has to swallow its own clicks:
        there is no QPushButton here whose disabled state would do it. That is what
        ``_refuses_activation`` answers, and a surface where a body click *fixes* the
        problem — the Game tab, where it edits — does not refuse.

        The *frame* is the hit target rather than this widget's whole rectangle, which
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
        if (
            event.button() == Qt.MouseButton.LeftButton
            and pressed is not None
            and self.has_body_verb
            and not self._refuses_activation()
            and self.frame.geometry().contains(event.position().toPoint())
            and (event.globalPosition().toPoint() - pressed).manhattanLength() < QApplication.startDragDistance()
        ):
            self.activated.emit()
        super().mouseReleaseEvent(event)

    def _refuses_activation(self) -> bool:
        """Whether a body click is currently being swallowed. Subclasses that refuse override.

        False here: a bare frame has nothing to refuse *for*. ``_LaunchCard`` returns True
        for a card that cannot fit on a surface whose ``refuses_when_unfit`` is set.
        """
        return False
