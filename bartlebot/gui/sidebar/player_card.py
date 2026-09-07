"""``PlayerCard``: one game client in the Players tab, drawn as a card.

Replaces ``ClientWidget``, which was the surface #190 left behind: an unframed stack of
rows closed by a horizontal rule, with four 48px ``Copy`` buttons and a pair of ▲/▼
arrows for order, sitting next to accounts and launch profiles that had become cards.

What is new here beyond the frame:

**The colour is the account's.** Launch the blue account and its player card is blue,
striped down the left edge with the same slot the blue pill on that launch profile card
names. An account runs one character at a time, so one client is one account and the
stripe is unambiguous. ``None`` means *no stripe* -- a game window BartleBot did not
launch, or an identity not learned yet -- and because that identity arrives late (it is
filled by ``ClientManager``'s own loop, see ``account_for``) the key is re-applied on
every refresh tick rather than resolved once in the constructor. A card coloured only at
construction is an uncoloured card that never recovers.

**The value is the button.** The old row was ``Zone: <value>  [ Copy ]`` -- a 48px
labelled button per row, four of them down a 300px panel, and pressing one changed
nothing on screen. Here the value itself is the hit target and a 16px glyph sits at the
end of the row, swapping to a checkmark for a moment on copy. The noise drops and the hit
target grows, which is the usual failure mode of icon-ifying a button and is worth having
avoided; the confirmation is plausibly the bigger win, since there was none at all.

Not hover-revealed, deliberately. That is the launch screen's bargain, and the judgement
recorded in ``CardFrame`` is that hiding verbs behind hover on a surface that exists to
*run* them is backwards. The Players tab is that kind of surface.

**MEMBER mode, not MANAGE.** #204 asked for "``CardMode.MANAGE`` semantics -- chrome
pinned open, nothing refuses", and those are the two properties named. MEMBER has both,
and MANAGE carries a third this card must not have: a body verb. A MANAGE card wears the
pointing-hand cursor over its whole face, lights up on hover and emits ``activated`` on a
click -- three promises that clicking a player card does something, when it does not.
There is no "open this player" to wire them to. So the mode is the one whose definition
this card actually matches, and ``CardMode``'s docstring now names both surfaces that
mean it.

THE SIDEBAR FLOOR IS NOT TOUCHED

``screen_fit.right_sidebar_floor`` measures a throwaway ``QPushButton`` carrying the
widest of the old tool-row labels, and that number feeds ``layout_budget`` and through it
the resolution ladder in docs/WINDOW_SIZING.md. This card only ever needs *less* than the
one it replaces -- three 22px icon buttons where there were three labelled ones, and
elided rows where there were labelled rows each closed by a 48px ``Copy``. So the floor
stays exactly where it was, as a conservative bound rather than a tight one. Lowering it
to match is a window-sizing change, which is the thing #204 asked this issue not to
quietly become.

THE REFRESH CONTRACT

Carried over from ``ClientWidget`` unchanged, and it is the part of this file that must
not drift -- see "Client section refresh architecture" in gui/CLAUDE.md:

- the wizard name is a ``wizard_names.cached_name`` dict lookup and never triggers
  detection; ``resolve_names()`` can open the character page and flash the game UI
- ``_refresh`` checks ``is_loading()`` first and returns early, because pointers are
  stale during a zone transition and reads are slow or produce garbage
- each refresh is wrapped in ``wait_for(CLIENT_REFRESH_TIMEOUT)`` by ``ClientSection``
- Streamer Mode routes through ``streamer_mode.display_name``
- **no wizard name, and no ``m_nameKeys``, is ever logged** (CLAUDE.md, *No real player
  data*)
"""

from __future__ import annotations

import asyncio
import json
import time

import pyperclip
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from wizwalker import XYZ

from bartlebot import streamer_mode, wizard_names
from bartlebot.gui.account_accent import account_accent, account_color_keys
from bartlebot.gui.icon_utils import svg_pixmap
from bartlebot.gui.sidebar.sidebar_section import field_label
from bartlebot.gui.theme import THEME
from bartlebot.gui.themed import ThemedMixin
from bartlebot.gui.widgets.card_frame import CardFrame, CardMode, CardVerb
from bartlebot.gui.widgets.elided_label import ElidedLabel
from bartlebot.gui.widgets.tag_pill import NeutralPill

#: What every row reads before anything is known, and the one value nothing will copy.
PLACEHOLDER = "—"

#: Width of the account stripe down a card's left edge. The same number as the launch
#: cards' ``STRIPE_WIDTH``, and deliberately not imported from them: a sidebar module
#: reaching into ``game_embed`` for a constant is the coupling ``CardFrame`` was lifted
#: out to avoid. If the two ever have to move together they belong in the theme.
STRIPE_WIDTH = 3

#: How long the copy glyph stays a checkmark. Long enough to be seen by someone who was
#: looking at the value rather than at the glyph, short enough that a second copy of a
#: different row is not read as the first one still confirming.
COPY_CONFIRM_MS = 1200

#: Side of the copy glyph, and of the checkmark that replaces it.
GLYPH_PX = 16

#: What the XYZ row reads before a position has been read. Spelled out rather than
#: derived from :func:`format_xyz`, so the placeholder form and the value form cannot
#: drift into different spacings.
XYZ_PLACEHOLDER = f"X={PLACEHOLDER}  Y={PLACEHOLDER}  Z={PLACEHOLDER}"

#: Seconds between closest-mob refreshes. The one read on this card that walks an entity
#: list rather than dereferencing a known pointer, so it is throttled well below the
#: card's own poll rate.
MOB_INTERVAL = 5.0


# ---------------------------------------------------------------------------
# Pure helpers. Separated from the widgets so they can be tested without a
# QApplication, as CLAUDE.md requires of anything inside a QWidget subclass.
# ---------------------------------------------------------------------------


def account_stripe_keys(usernames: list[str | None], accounts: list[dict]) -> list[int | None]:
    """A palette slot per client, given the account each was launched under.

    *usernames* is what ``ClientManager.account_for`` said about each client in display
    order, ``None`` for one it could not name. The answer keeps that ``None``: an
    unidentified window gets **no stripe** rather than a default colour, because a colour
    on this card is a claim about which account is running and there is nothing to claim.

    Resolved through :func:`account_color_keys` over the *whole* account list rather than
    per username, because a colour is a property of the list -- see account_accent.py.
    A username no longer in that list also falls through to ``None``: an account that has
    been deleted has no slot, and picking one for it would put a colour on the card that
    matches nothing anywhere else on screen.
    """
    keys = account_color_keys(accounts)
    return [None if username is None else keys.get(username) for username in usernames]


def player_name_text(cached_name: str | None, index: int) -> str:
    """What the card's header reads, honouring Streamer Mode.

    Streamer Mode substitutes "Character N" rather than blanking the header: the card's
    pill already says which player this is, so the substitute is redundant -- but blanking
    it would read as "not detected yet", which is a real state the user needs to be able
    to see. Redundant beats ambiguous.
    """
    return streamer_mode.display_name(cached_name, index, placeholder=PLACEHOLDER)


def player_pill_text(index: int) -> str:
    """The ordinal on a hooked player's card: ``P1`` for the client at index 0."""
    return f"P{index + 1}"


def format_xyz(x: float, y: float, z: float) -> str:
    """The XYZ row as it is displayed: one decimal, space separated."""
    return f"X={x:.1f}  Y={y:.1f}  Z={z:.1f}"


def xyz_clipboard_text(x: float, y: float, z: float) -> str:
    """The XYZ row as it is *copied*: JSON at two decimals, which is what a node takes.

    Deliberately not the displayed form. The display is for reading down a column and the
    copy is for pasting into a bot file, and one string cannot be good at both -- which is
    why every row here is given its display text and its clipboard text separately.
    """
    return json.dumps({"x": round(x, 2), "y": round(y, 2), "z": round(z, 2)})


# ---------------------------------------------------------------------------
# The copy affordance
# ---------------------------------------------------------------------------


class CopyValue(ThemedMixin, QWidget):
    """A live value that copies itself when clicked, with a glyph at the end of the row.

    The whole widget is the hit target, not the glyph: the value is the longest thing on
    the row and the easiest to aim at, and making the 16px mark the only target would be
    the icon-ification that shrinks what it was meant to declutter. The glyph is the
    *announcement* -- it says the row is copyable and, for a second after a copy, that the
    copy happened.

    A row showing the placeholder is not copyable. Nothing is written to the clipboard, no
    confirmation is shown and the cursor stays an arrow: an em-dash pasted into a bot
    param is a silently wrong value, and a confirmation for it would be a lie.

    Display text and clipboard text are set separately, because for two of the three rows
    they genuinely differ -- see :func:`xyz_clipboard_text`.
    """

    #: ``objectName``, so the hover rule below lands on this widget and not on its labels.
    OBJECT_NAME = "CopyValue"

    def __init__(
        self,
        tooltip: str,
        *,
        bold: bool = False,
        elide_mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(self.OBJECT_NAME)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._tooltip = tooltip
        self._copy_text = ""
        self._hovered = False
        self._confirming = False

        self._value = ElidedLabel(PLACEHOLDER, self, elide_mode=elide_mode)
        if bold:
            font = self._value.font()
            font.setBold(True)
            self._value.setFont(font)
        # Transparent to the mouse, so a press anywhere across the row -- label included --
        # reaches this widget's own handler rather than being eaten by the child under it.
        self._value.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._glyph = QLabel(self)
        self._glyph.setFixedSize(GLYPH_PX, GLYPH_PX)
        self._glyph.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        row = QHBoxLayout(self)
        row.setContentsMargins(3, 1, 3, 1)
        row.setSpacing(4)
        row.addWidget(self._value, 1)
        row.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignVCenter)

        # Parented to this widget, so it dies with it: a bare ``singleShot`` would fire
        # into a deleted label on a card the refresh loop rebuilt inside the second.
        self._reset = QTimer(self)
        self._reset.setSingleShot(True)
        self._reset.setInterval(COPY_CONFIRM_MS)
        self._reset.timeout.connect(self._stop_confirming)

        self._apply_affordance()
        self.refresh_theme_styles()

    # -- state ---------------------------------------------------------

    def set_value(self, display: str, copy_text: str | None = None) -> None:
        """Show *display*, and copy *copy_text* -- which defaults to the same string."""
        self._value.setText(display)
        self._copy_text = display if copy_text is None else copy_text
        self._apply_affordance()

    @property
    def display_text(self) -> str:
        """What the row currently reads, untruncated."""
        return self._value.full_text()

    @property
    def copy_text(self) -> str:
        """What a click would put on the clipboard."""
        return self._copy_text

    @property
    def copyable(self) -> bool:
        """Whether there is a real value here, as opposed to the placeholder."""
        return bool(self._copy_text) and self._copy_text != PLACEHOLDER

    def copy(self) -> bool:
        """Put the value on the clipboard and confirm it. False if there was nothing to copy.

        Public because it is the whole behaviour of this widget, and a test asserting that
        a click copies the value the row displays should be able to ask for the copy
        without synthesising a mouse.
        """
        if not self.copyable:
            return False
        pyperclip.copy(self._copy_text)
        self._confirming = True
        self._reset.start()
        self._apply_affordance()
        return True

    @property
    def confirming(self) -> bool:
        """Whether the glyph is currently showing the checkmark rather than the copy mark."""
        return self._confirming

    def _stop_confirming(self) -> None:
        self._confirming = False
        self._apply_affordance()

    def _apply_affordance(self) -> None:
        """Re-derive everything that depends on *is there a value* and *did we just copy*."""
        copyable = self.copyable
        self.setCursor(Qt.CursorShape.PointingHandCursor if copyable else Qt.CursorShape.ArrowCursor)
        self.setToolTip(self._tooltip if copyable else "")
        # Hidden rather than blank on an uncopyable row: a mark saying "click to copy"
        # over a row that will not copy is the affordance lying about itself.
        self._glyph.setVisible(copyable)
        self._glyph.setPixmap(
            svg_pixmap(
                "checkmark" if self._confirming else "copy",
                GLYPH_PX,
                tint=THEME.accent if self._confirming else THEME.icon_tint,
            )
        )
        self.refresh_theme_styles()

    # -- painting ------------------------------------------------------

    def apply_theme_styles(self) -> None:
        """The row's own fill, and the value's colour.

        Hover is state rather than a ``:hover`` pseudo-class, the same finding as
        ``CardFrame``: this pass colours the fill and the label together, and a
        pseudo-class would restyle one of them and leave the other in the old palette
        after a theme switch.
        """
        lit = self._hovered and self.copyable
        background = THEME.node_fill if lit else "transparent"
        self.setStyleSheet(f"#{self.OBJECT_NAME} {{ background: {background}; border-radius: 3px; }}")
        self._value.setStyleSheet(
            f"color: {THEME.text if self.copyable else THEME.text_subtle}; background: transparent;"
        )

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._hovered = True
        self.refresh_theme_styles()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._hovered = False
        self.refresh_theme_styles()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Copy on release, and consume the click either way.

        On release rather than on press for the same reason the card is -- and consumed
        rather than passed up regardless of whether anything was copied, so that a press
        aimed at a row that happens to be showing the placeholder does not become a press
        on the card behind it.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.copy()
            event.accept()
            return
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------


class PlayerCard(CardFrame):
    """One game client: who it is, where it is, and the three things you can do to it.

    Built for one bucket and one only. A hooked client's card carries a ``P{n}`` pill, a
    grip and an Unhook verb; an unhooked one carries none of the first two and a Rehook
    verb instead. Nothing turns one into the other in place, because the grip is decided
    at construction and because the two live in different containers: ``ClientSection``
    rebuilds a card that changes bucket and re-uses one that has not.
    """

    #: Its own name, so the frame rules written here cannot collide with ``#LaunchCard``.
    FRAME_NAME = "PlayerCard"

    unhook_requested = pyqtSignal(int)  # index of an active client
    rehook_requested = pyqtSignal(int)  # HWND of an unhooked client
    entities_requested = pyqtSignal()
    ui_tree_requested = pyqtSignal()

    def __init__(self, client, index: int, *, unhooked: bool = False, parent=None) -> None:
        # MEMBER, not MANAGE -- see the module docstring. The two agree on everything #204
        # named and disagree about whether the body is a button, which this card's is not.
        super().__init__(parent, mode=CardMode.MEMBER)
        self._client = client
        self._index = index
        self._is_unhooked = unhooked
        self._in_freecam = False
        self._stripe_key: int | None = None
        self._last_zone = PLACEHOLDER
        self._last_mob_time: float = 0.0
        self._build()
        self.refresh_theme_styles()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        # An unhooked card has no grip: ``ClientManager.clients`` is hooked-only and
        # contiguous, so there is no order here for a drag to land in. It keeps the
        # gutter's indent so its frame lines up with the hooked cards above it.
        layout = self._build_frame("players", draggable=not self._is_unhooked)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        # Ahead of the name, where an ordinal belongs, and only on a hooked card: an
        # unhooked client is not Player anything until it is hooked back in.
        self._pill = NeutralPill(player_pill_text(self._index)) if not self._is_unhooked else None
        if self._pill is not None:
            header.addWidget(self._pill, 0, Qt.AlignmentFlag.AlignVCenter)
        self._name = CopyValue("Click to copy this wizard's name", bold=True, parent=self)
        header.addWidget(self._name, 1)
        self._add_face_verbs(header)
        layout.addLayout(header)

        # Zone is elided from the LEFT: a zone path's tail (…/WC_Streets/Interior) is what
        # distinguishes it, and the prefix is shared by everything in the world.
        self._zone = self._data_row(layout, "Zone", "Click to copy this zone name", Qt.TextElideMode.ElideLeft)
        # An ElidedLabel and not a word-wrapped QLabel, for XYZ as for the rest: a wrapped
        # QLabel reports a 26px sizeHint for a single 12px line, which made this row
        # visibly taller than its neighbours and pushed the value below its prefix.
        self._xyz = self._data_row(layout, "XYZ", "Click to copy this position as JSON")
        self._mob = self._data_row(layout, "Enemy", "Click to copy the closest enemy's name")
        self._xyz.set_value(XYZ_PLACEHOLDER, PLACEHOLDER)

        # The freecam pair stays two ordinary buttons. They are a stateful toggle and a
        # thing that acts on the *camera* rather than on the card, so neither is a verb in
        # the sense ``_verbs`` means -- a menu entry reading "Enter Freecam" that says
        # "Exit Freecam" half the time is a verb list that has to be rebuilt to be read.
        self._freecam_btn = QPushButton("Enter Freecam", self)
        self._freecam_btn.clicked.connect(lambda: asyncio.ensure_future(self._toggle_freecam()))
        self._tp_btn = QPushButton("TP to Freecam", self)
        self._tp_btn.setEnabled(False)
        self._tp_btn.clicked.connect(lambda: asyncio.ensure_future(self._tp_to_freecam()))

        freecam_row = QHBoxLayout()
        freecam_row.setContentsMargins(0, 2, 0, 0)
        freecam_row.setSpacing(4)
        freecam_row.addWidget(self._freecam_btn, 1)
        freecam_row.addWidget(self._tp_btn, 1)
        layout.addLayout(freecam_row)

        self.entities_requested.connect(self._open_entity_browser)
        self.ui_tree_requested.connect(self._open_ui_tree)

    def _data_row(
        self,
        layout: QVBoxLayout,
        caption: str,
        tooltip: str,
        elide_mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
    ) -> CopyValue:
        """One ``Caption  <value>  [glyph]`` row, and the copy affordance that is its value."""
        value = CopyValue(tooltip, elide_mode=elide_mode, parent=self)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(field_label(caption), 0)
        row.addWidget(value, 1)
        layout.addLayout(row)
        return value

    def _verbs(self) -> list[CardVerb]:
        """Entities, UI Tree, and the hook toggle -- all three on the face.

        The three that were labelled ``QPushButton``s in a row of their own. They are the
        card's whole verb list, none of them is destructive, and this surface exists to
        run them, so all three are pinned open on the face and all three are in the menu
        as well. The tooltips carry the full names the labels could not: "Entity Browser"
        and "UI Tree Browser" alone demanded 376px inside a 300px panel.

        The hook verb is fixed at construction rather than toggled, because a card is
        built for one bucket -- see the class docstring.
        """
        if self._is_unhooked:
            hook = CardVerb(
                "Rehook",
                "hook",
                "Re-hook this client into BartleBot",
                lambda: self.rehook_requested.emit(self._client.window_handle),
                on_face=True,
            )
        else:
            hook = CardVerb(
                "Unhook",
                "unhook",
                "Unhook this client (leaves the game window open)",
                lambda: self.unhook_requested.emit(self._index),
                on_face=True,
            )
        return [
            CardVerb(
                "Entities",
                "search",
                "Entity Browser — inspect nearby entities for this player",
                self.entities_requested.emit,
                on_face=True,
            ),
            CardVerb(
                "UI Tree",
                "layout",
                "UI Tree Browser — inspect the live WizWalker UI window tree for this player",
                self.ui_tree_requested.emit,
                on_face=True,
            ),
            hook,
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def client(self):
        """The WizWalker client this card stands for."""
        return self._client

    @property
    def hwnd(self) -> int:
        """The game window this card is keyed by, across a rebuild of the section."""
        return self._client.window_handle

    @property
    def is_unhooked(self) -> bool:
        return self._is_unhooked

    @property
    def index(self) -> int:
        """This card's position in its own list -- the player number, less one."""
        return self._index

    @property
    def last_zone(self) -> str:
        """Most recent zone name read by :meth:`refresh`, or the placeholder if unknown."""
        return self._last_zone

    @property
    def color_key(self) -> int | None:
        """The account palette slot this card is striped with, or None for no stripe."""
        return self._stripe_key

    @property
    def pill_text(self) -> str | None:
        """The ordinal on this card's header, or None on an unhooked one, which has none."""
        return None if self._pill is None else self._pill.text()

    @property
    def rows(self) -> dict[str, CopyValue]:
        """This card's four copy affordances, by the caption each sits behind.

        Keyed by caption rather than handed back as a list, because which row is which is
        the whole question a caller has -- "does the zone row copy the zone" -- and a
        position is a worse way to ask it than the word already printed beside the value.
        """
        return {"Wizard": self._name, "Zone": self._zone, "XYZ": self._xyz, "Enemy": self._mob}

    def set_index(self, index: int) -> None:
        """Re-number the card after a reorder, without rebuilding it.

        Called rather than the card being replaced, because everything else on it is live:
        rebuilding to change a pill would reset the freecam toggle and blank every row
        until the next tick. The index is read back by the Unhook verb, so it has to move
        with the pill and not merely beside it.
        """
        if index == self._index:
            return
        self._index = index
        if self._pill is not None:
            self._pill.setText(player_pill_text(index))

    def set_color_key(self, key: int | None) -> None:
        """Stripe the card's left edge with an account's colour, or None to drop the stripe.

        Re-applied on every refresh tick rather than resolved once: ``account_for`` learns
        an identity from inside ``ClientManager``'s loop and signals nothing when it does,
        so a card coloured in its constructor is one that was built before the answer
        existed and never asks again.
        """
        if key == self._stripe_key:
            return
        self._stripe_key = key
        self.refresh_theme_styles()

    def set_bot_running(self, running: bool) -> None:
        """Lock the Unhook verb for the length of a run.

        Unhooking mid-run pulls a client out from under the FSM. An unhooked card is not
        touched: rehooking during a run is how a client that dropped out is brought back.
        """
        if self._is_unhooked:
            return
        button = self.action_button("Unhook")
        if button is not None:
            button.setEnabled(not running)

    # ------------------------------------------------------------------
    # The theme pass
    # ------------------------------------------------------------------

    def _frame_extra_qss(self) -> str:
        """The account stripe down the card's left edge, or "" for a client with no account.

        Handed to the frame's rule rather than written into one, so that every palette
        keeps it without having to remember to -- see ``CardFrame._apply_frame_palette``.
        """
        if self._stripe_key is None:
            return ""
        return f" border-left: {STRIPE_WIDTH}px solid {account_accent(self._stripe_key)};"

    def _apply_state_palette(self) -> bool:
        """Sit an unhooked card back on the disabled surface, keeping its stripe.

        Greyed, but not by the launch cards' rule: this state is not a refusal. The client
        is still a real client on a real account and its card still says which, so the
        stripe survives -- what has changed is that nothing on it is live any more. The
        fill and the captions both move, because either alone reads as enabled
        (tests/test_disabled_affordance.py).
        """
        if not self._is_unhooked:
            return False
        self._apply_frame_palette(THEME.surface_disabled, THEME.border)
        return True

    # ------------------------------------------------------------------
    # Tool verbs
    # ------------------------------------------------------------------

    def _open_entity_browser(self) -> None:
        from bartlebot.gui.entity_browser import EntityBrowserDialog

        EntityBrowserDialog(self._client, parent=self).show()

    def _open_ui_tree(self) -> None:
        from bartlebot.gui.ui_tree_browser import UITreeBrowserDialog

        UITreeBrowserDialog(self._client, parent=self).show()

    # ------------------------------------------------------------------
    # Freecam
    # ------------------------------------------------------------------

    async def _toggle_freecam(self) -> None:
        try:
            if self._in_freecam:
                await self._client.camera_elastic()
                self._set_freecam(False)
            else:
                # Pre-position the free camera at the player's location + 200 Z before
                # entering freecam — without this the world freezes on entry.
                pos = await self._client.body.position()
                yaw = await self._client.body.yaw()
                camera = await self._client.game_client.free_camera_controller()
                await camera.write_position(XYZ(pos.x, pos.y, pos.z + 200))
                await camera.write_yaw(yaw)
                await self._client.camera_freecam()
                self._set_freecam(True)
        except Exception:
            pass  # client may have disconnected

    async def _tp_to_freecam(self) -> None:
        """Teleport the client to the current freecam position, then exit freecam."""
        try:
            free = await self._client.game_client.free_camera_controller()
            pos = await free.position()
            # Exit freecam before teleporting — matches Deimos behaviour.
            await self._client.camera_elastic()
            self._set_freecam(False)
            await self._client.teleport(pos, wait_on_inuse=True, purge_on_after_unuser_fixer=True)
        except Exception:
            pass

    def _set_freecam(self, active: bool) -> None:
        self._in_freecam = active
        self._freecam_btn.setText("Exit Freecam" if active else "Enter Freecam")
        self._tp_btn.setEnabled(active)

    @property
    def in_freecam(self) -> bool:
        """Whether this client's camera is currently detached.

        Public because it is the state a rebuild would silently drop, and the reason
        ``ClientSection`` re-keys its cards rather than recreating them.
        """
        return self._in_freecam

    # ------------------------------------------------------------------
    # Periodic refresh (driven by ClientSection._refresh_loop)
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """Read zone, position and closest enemy from the client and update the rows."""
        try:
            await self._refresh()
        except RuntimeError:
            return  # widget was deleted while awaiting

    async def _refresh(self) -> None:
        # Skip every read during a zone transition — pointers are stale and reads are slow
        # or produce garbage, which is what used to freeze the sidebar.
        try:
            if await self._client.is_loading():
                return
        except Exception:
            pass

        # A dict lookup in the shared cache, with no memory read of its own.
        # ``ClientManager``'s loop is what fills it, so the card shows whatever is known
        # and never triggers detection.
        self._name.set_value(player_name_text(wizard_names.cached_name(self._client), self._index))

        try:
            zone = await self._client.zone_name() or PLACEHOLDER
        except Exception:
            zone = PLACEHOLDER
        self._last_zone = zone
        self._zone.set_value(zone)

        # The freecam position while the camera is detached, the body's otherwise: what
        # the user is looking at is what the row should say, and what they will paste.
        try:
            if self._in_freecam:
                free = await self._client.game_client.free_camera_controller()
                pos = await free.position()
            else:
                pos = await self._client.body.position()
            self._xyz.set_value(format_xyz(pos.x, pos.y, pos.z), xyz_clipboard_text(pos.x, pos.y, pos.z))
        except Exception:
            self._xyz.set_value(XYZ_PLACEHOLDER, PLACEHOLDER)

        now = time.monotonic()
        if now - self._last_mob_time >= MOB_INTERVAL:
            self._last_mob_time = now
            try:
                mob = await self._client.find_closest_mob()
                name = (await mob.object_name() if mob else PLACEHOLDER) or PLACEHOLDER
            except Exception:
                name = PLACEHOLDER
            self._mob.set_value(name)
