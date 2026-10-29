"""``ClientCard``: BartleBot's player card, which is core's with the client behind it.

Core's ``bartleby.qt.sidebar.player_card.PlayerCard`` draws a ``PlayerView`` and holds no
client (Stage 8 of #167). What only BartleBot needs is here, where the client is:

- **the reads that build the view**: zone, position and the closest enemy, under the
  refresh contract in core's ``bartleby/qt/CLAUDE.md`` ("Player cards are drawn from
  views"). ``ClientSection``'s refresh loop calls :meth:`ClientCard.refresh` once a tick.
- **XYZ and Enemy**, as the view's rows. XYZ reads at one decimal and copies as the JSON
  a node takes (:func:`xyz_clipboard_text`).
- **the debug verbs**: the Entity Browser and the UI Tree Browser, each a dialog on this
  card's client, on the face as core's product verbs.
- **the freecam pair**, under the rows.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace

from bartleby.qt.sidebar.player_card import PLACEHOLDER, PlayerCard, PlayerVerb
from bartleby.qt.widgets.buttons.action_button import ActionButton
from bartleby.runtime.game import wizard_names
from bartleby.runtime.views import PlayerRow, PlayerView
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from wizwalker import XYZ

#: What the XYZ row reads before a position has been read. Spelled out rather than
#: derived from :func:`format_xyz`, so the placeholder form and the value form cannot
#: drift into different spacings.
XYZ_PLACEHOLDER = f"X={PLACEHOLDER}  Y={PLACEHOLDER}  Z={PLACEHOLDER}"

#: Seconds between closest-mob refreshes. The one read on this card that walks an entity
#: list rather than dereferencing a known pointer, so it is throttled well below the
#: card's own poll rate.
MOB_INTERVAL = 5.0

XYZ_ROW = "XYZ"
ENEMY_ROW = "Enemy"
XYZ_TOOLTIP = "Click to copy this position as JSON"
ENEMY_TOOLTIP = "Click to copy the closest enemy's name"

#: The debug verbs, as core's player card takes a product's verbs. Labeled ``QPushButton``s
#: in a row of their own once; pinned open on the face now, since this surface exists to
#: run them. The tooltips carry the full names the labels could not: "Entity Browser"
#: and "UI Tree Browser" alone demanded 376px inside a 300px panel.
ENTITIES = PlayerVerb("entities", "Entities", "search", "Entity Browser — inspect nearby entities for this player")
UI_TREE = PlayerVerb(
    "ui_tree", "UI Tree", "layout", "UI Tree Browser — inspect the live WizWalker UI window tree for this player"
)
VERBS: dict[str, PlayerVerb] = {verb.id: verb for verb in (ENTITIES, UI_TREE)}

#: What a run's lock says on a grip.
RUN_LOCK_REASON = "Players can't be reordered while a bot is running"


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


def xyz_row(display: str = XYZ_PLACEHOLDER, copy_text: str = PLACEHOLDER) -> PlayerRow:
    return PlayerRow(XYZ_ROW, display, copy_text, XYZ_TOOLTIP)


def enemy_row(name: str = PLACEHOLDER) -> PlayerRow:
    return PlayerRow(ENEMY_ROW, name, None, ENEMY_TOOLTIP)


def initial_view(client, index: int, *, unhooked: bool = False) -> PlayerView:
    """What a card for *client* draws before anything has been read: every row a placeholder."""
    return PlayerView(
        hwnd=client.window_handle,
        slot=index + 1,
        unhooked=unhooked,
        rows=(xyz_row(), enemy_row()),
        verbs=tuple(VERBS),
    )


class ClientCard(PlayerCard):
    """Core's player card, for one of BartleBot's clients: its reads, its tools and its freecam."""

    def __init__(self, client, index: int, *, unhooked: bool = False, parent: QWidget | None = None) -> None:
        self._client = client
        self._in_freecam = False
        # Whether a bot run has the freecam pair locked. See set_locked.
        self._bot_running = False
        self._last_mob_time: float = 0.0
        super().__init__(initial_view(client, index, unhooked=unhooked), VERBS, parent=parent)
        self.verb_triggered.connect(self._on_verb)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_extras(self, layout: QVBoxLayout) -> None:
        # The freecam pair stays two buttons on the face. They are a stateful toggle and a
        # thing that acts on the *camera* rather than on the card, so neither is a verb in
        # the sense ``_verbs`` means -- a menu entry reading "Enter Freecam" that says
        # "Exit Freecam" half the time is a verb list that has to be rebuilt to be read.
        #
        # Both TERTIARY: they are sequential steps, not alternatives, and detaching the
        # camera is not what this card expects you to do. Glyphless because none of the
        # shipped icons is a camera, and an icon name that does not resolve is worse than
        # no glyph -- see gui/CLAUDE.md.
        #
        # "TP to Camera", not "TP to Freecam", and the reason is width rather than
        # wording: this pair is the card's widest row, and an ``ActionButton`` is ~14px
        # wider than the bare button it replaced -- enough to push the card past the
        # sidebar's own floor under ``windowsvista``, which is the failure
        # ``test_the_player_card_fits_the_right_sidebar_under_every_available_style``
        # exists to catch. The shorter label buys back 5px and says the same thing.
        # Anything added to this row has to be measured against that test.
        self._freecam_btn = ActionButton("", "Enter Freecam", parent=self)
        self._freecam_btn.clicked.connect(lambda: asyncio.ensure_future(self._toggle_freecam()))
        self._tp_btn = ActionButton("", "TP to Camera", parent=self)
        self._tp_btn.setEnabled(False)
        self._tp_btn.clicked.connect(lambda: asyncio.ensure_future(self._tp_to_freecam()))

        freecam_row = QHBoxLayout()
        freecam_row.setContentsMargins(0, 2, 0, 0)
        freecam_row.setSpacing(4)
        freecam_row.addWidget(self._freecam_btn, 1)
        freecam_row.addWidget(self._tp_btn, 1)
        layout.addLayout(freecam_row)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def client(self):
        """The WizWalker client this card stands for."""
        return self._client

    def set_locked(self, locked: bool, reason: str = "") -> None:
        """Lock Unhook and the grip as core's card does, and the freecam pair with them.

        BartleBot locks for a bot run. Detaching the camera or teleporting the player moves
        a client the FSM is driving, underneath whatever node is running.
        """
        super().set_locked(locked, reason)
        if self.is_unhooked:
            return
        self._bot_running = locked
        self._sync_freecam_buttons()

    # ------------------------------------------------------------------
    # Tool verbs
    # ------------------------------------------------------------------

    def _on_verb(self, _hwnd: int, verb_id: str) -> None:
        if verb_id == ENTITIES.id:
            self._open_entity_browser()
        elif verb_id == UI_TREE.id:
            self._open_ui_tree()

    def _open_entity_browser(self) -> None:
        from bartlebot.gui.dialogs.entity_browser import EntityBrowserDialog

        EntityBrowserDialog(self._client, parent=self).show()

    def _open_ui_tree(self) -> None:
        from bartlebot.gui.dialogs.ui_tree_browser import UITreeBrowserDialog

        UITreeBrowserDialog(self._client, parent=self).show()

    # ------------------------------------------------------------------
    # Freecam
    # ------------------------------------------------------------------

    async def exit_freecam(self) -> bool:
        """Put the camera back on the player without moving them. True if it was detached.

        Public for the run start, which must not hand the FSM a client whose camera is off
        somewhere else -- and whose Exit Freecam button the run is about to lock. The
        player stays where they are: this is Exit Freecam, not TP to Camera.

        A client that cannot be reached keeps its freecam state, since nothing here knows
        the camera came back.
        """
        if not self._in_freecam:
            return False
        try:
            await self._client.camera_elastic()
        except Exception:
            return False  # client may have disconnected
        self._set_freecam(False)
        return True

    async def _toggle_freecam(self) -> None:
        try:
            if self._in_freecam:
                await self.exit_freecam()
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
            # Exit freecam before teleporting — matches Deimos behavior.
            await self._client.camera_elastic()
            self._set_freecam(False)
            await self._client.teleport(pos, wait_on_inuse=True, purge_on_after_unuser_fixer=True)
        except Exception:
            pass

    def _set_freecam(self, active: bool) -> None:
        self._in_freecam = active
        self._freecam_btn.setText("Exit Freecam" if active else "Enter Freecam")
        self._sync_freecam_buttons()

    def _sync_freecam_buttons(self) -> None:
        """Enable the pair from the two facts that decide it: a run, and a detached camera.

        One place, because both facts change independently -- a run starting must not
        enable TP for a camera that is not detached, and leaving freecam must not re-enable
        a button a run has locked.
        """
        self._freecam_btn.setEnabled(not self._bot_running)
        self._tp_btn.setEnabled(self._in_freecam and not self._bot_running)

    @property
    def freecam_buttons(self) -> tuple[ActionButton, ActionButton]:
        """The Enter/Exit Freecam toggle and TP to Camera, for the callers that check their state."""
        return self._freecam_btn, self._tp_btn

    @property
    def in_freecam(self) -> bool:
        """Whether this client's camera is currently detached.

        Public because it is the state a rebuild would silently drop, and the reason
        the Players tab re-keys its cards rather than recreating them.
        """
        return self._in_freecam

    # ------------------------------------------------------------------
    # Periodic refresh (driven by ClientSection._refresh_loop)
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """Read zone, position and closest enemy from the client, and draw them."""
        try:
            fields = await self.read()
            # Onto the view as it is now, not as it was before the reads: the stripe may
            # have arrived on another tick while they were awaited.
            self.show_view(replace(self.view, **fields))
        except RuntimeError:
            return  # widget was deleted while awaiting

    async def read(self) -> dict:
        """What this tick read, as the ``PlayerView`` fields it changes."""
        # Skip every read during a zone transition — pointers are stale and reads are slow
        # or produce garbage, which is what used to freeze the sidebar.
        try:
            if await self._client.is_loading():
                return {"loading": True}
        except Exception:
            pass

        # A dict lookup in the shared cache, with no memory read of its own. The client
        # manager's loop is what fills it, so the card shows whatever is known and never
        # triggers detection.
        fields: dict = {"loading": False, "wizard_name": wizard_names.cached_name(self._client)}

        try:
            fields["zone"] = await self._client.zone_name() or PLACEHOLDER
        except Exception:
            fields["zone"] = PLACEHOLDER

        # The freecam position while the camera is detached, the body's otherwise: what
        # the user is looking at is what the row should say, and what they will paste.
        try:
            if self._in_freecam:
                free = await self._client.game_client.free_camera_controller()
                pos = await free.position()
            else:
                pos = await self._client.body.position()
            xyz = xyz_row(format_xyz(pos.x, pos.y, pos.z), xyz_clipboard_text(pos.x, pos.y, pos.z))
        except Exception:
            xyz = xyz_row()

        enemy = next((row for row in self.view.rows if row.label == ENEMY_ROW), enemy_row())
        now = time.monotonic()
        if now - self._last_mob_time >= MOB_INTERVAL:
            self._last_mob_time = now
            try:
                mob = await self._client.find_closest_mob()
                name = (await mob.object_name() if mob else PLACEHOLDER) or PLACEHOLDER
            except Exception:
                name = PLACEHOLDER
            enemy = enemy_row(name)
        fields["rows"] = (xyz, enemy)
        return fields
