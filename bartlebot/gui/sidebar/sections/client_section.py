"""Players tab content: a drag-reorderable column of player cards, and the unhooked rest.

TWO CONTAINERS, NOT ONE COLUMN
------------------------------

``ClientManager.clients`` is hooked-only and **contiguous** -- a hole in it is not
expressible -- so a drag must never land an active card in among the unhooked ones.
``CardColumn._index_at`` clamps to the whole card list it holds, which means a single
column holding both kinds *will* allow exactly that drop. So the column is handed the
active cards and nothing else, and the unhooked ones go into a ``SidebarSection`` beneath
it that is shown only when there is something in it. The unhooked cards are built with no
grip besides, so there is no gesture to make there in the first place.

The column is built with no title and no Add card. There is nothing to add to a list of
running game clients, and the tab's own label already says "Players" -- a bold heading
under it would be the panel saying the word twice. Both are one argument each to
``CardColumn``; the empty-title case is the small change the Players tab card rework asked to be checked for.

The Unhook All / Rehook All row stays where it was, above both. It acts on both buckets
at once, so putting half of it in each section header would split one control into two
that each look like they act on the section they sit in.

WHY THE CARDS ARE RE-KEYED
--------------------------

``set_all_clients`` used to delete and recreate every widget, which reset the freecam
toggle and blanked every row until the next tick. With ▲/▼ buttons nobody noticed -- a
swap is rare and a blank row for a hundred milliseconds reads as a redraw. With a drag it
is one gesture away and visible, and the drag is what this issue is *for*. So a card is
held against its ``hwnd`` and re-used while that window is still in the same bucket,
re-numbered rather than rebuilt (``PlayerCard.set_index``). A card whose client crossed
between hooked and unhooked is rebuilt, because which bucket it is in decides whether it
has a grip at all, and that is fixed at construction.

Survivors are re-parented off the column's layout *before* ``set_cards`` runs, because
that method ``deleteLater``s everything it finds there.

WHAT COLORS A CARD
-------------------

``ClientManager.account_for`` says which account a client was launched under, and the
answer arrives late: it is filled from inside the client loop and nothing signals it. So
the section is handed that lookup as a callable and re-asks on every refresh tick, which
is the same bargain the wizard name already makes with ``wizard_names.cached_name``.
"""

import asyncio
import logging

from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from bartlebot.gui.game_embed.card_columns import CardColumn
from bartlebot.gui.sidebar.player_card import PlayerCard, account_stripe_keys
from bartlebot.gui.sidebar.sidebar_section import SidebarSection
from bartlebot.gui.widgets.action_button import ActionButton
from bartlebot.nodes.timing import (
    CLIENT_REFRESH_INTERVAL_HIDDEN,
    CLIENT_REFRESH_INTERVAL_IDLE,
    CLIENT_REFRESH_INTERVAL_RUN,
    CLIENT_REFRESH_TIMEOUT,
)
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

NO_PLAYERS_HINT = "No players hooked — launch a game client to see it here"


def client_refresh_interval(bot_running: bool, players_visible: bool) -> float:
    """Poll interval for the client refresh loop, given bot state and tab visibility.

    Visibility is checked first on purpose: a hidden Players tab during a bot run
    is the worst case for contention and the least useful to refresh, since
    nothing on screen is reading the result.
    """
    if not players_visible:
        return CLIENT_REFRESH_INTERVAL_HIDDEN
    return CLIENT_REFRESH_INTERVAL_RUN if bot_running else CLIENT_REFRESH_INTERVAL_IDLE


class ClientSection(QWidget):
    """The Players tab's contents: the hooked column, the unhooked section, the bulk row.

    Lives in ``RightSidebar``'s Players tab, which supplies the margins and nothing else
    -- the scrolling is the column's own now. This widget draws no header of its own since
    the tab label already names it.

    No ``ThemedMixin``: the only thing it ever re-derived from ``THEME`` was the two
    glyphs on the bulk row, and those are the buttons' own to re-derive now.
    """

    #: The cards' previous indices in the order they are in now, straight from
    #: ``CardColumn.reordered``. A permutation rather than a pair, because a drag is not a
    #: swap: see ``ClientManager.reorder_clients``, which is what this reaches.
    reordered = pyqtSignal(list)
    unhook_requested = pyqtSignal(int)  # index of active client to unhook
    rehook_requested = pyqtSignal(int)  # HWND of unhooked client to rehook
    unhook_all_requested = pyqtSignal()
    rehook_all_requested = pyqtSignal()

    _BULK_ICON_SIZE = QSize(14, 14)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards: list[PlayerCard] = []
        self._refresh_task: asyncio.Task | None = None
        self._bot_running = False
        # Matches RightSidebar's initial current tab, which currentChanged never announces.
        self._players_visible = True
        self._refresh_interval: float = client_refresh_interval(False, True)
        # How a client is turned into an account username. Injected rather than imported,
        # so this widget does not reach for a ClientManager it has no business holding;
        # left unset it simply colors nothing, which is the same answer as "not known
        # yet" and so needs no branch anywhere else.
        self._account_for = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._build_bulk_row())

        self._column = CardColumn("", NO_PLAYERS_HINT, add_text="", show_settings_link=False)
        self._column.reordered.connect(self.reordered)
        layout.addWidget(self._column, 1)

        self._unhooked_section = SidebarSection("Unhooked")
        self._unhooked_layout = QVBoxLayout()
        self._unhooked_layout.setSpacing(4)
        self._unhooked_section.set_content_layout(self._unhooked_layout)
        self._unhooked_section.setVisible(False)
        layout.addWidget(self._unhooked_section)

    def _build_bulk_row(self) -> QWidget:
        """Row of Unhook All / Rehook All buttons pinned above both buckets."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Two halves of the same bulk verb, so neither outranks the other. The glyphs are
        # the button's own now — ``ActionButton`` re-derives them on a theme pass, which is
        # what the hand-written ``apply_theme_styles`` here used to be for.
        self._unhook_all_btn = ActionButton("unhook", "Unhook All", icon_size=self._BULK_ICON_SIZE.width())
        self._unhook_all_btn.setToolTip("Unhook every client (leaves all game windows open)")
        self._unhook_all_btn.setEnabled(False)
        self._unhook_all_btn.clicked.connect(self.unhook_all_requested)
        layout.addWidget(self._unhook_all_btn)

        self._rehook_all_btn = ActionButton("hook", "Rehook All", icon_size=self._BULK_ICON_SIZE.width())
        self._rehook_all_btn.setToolTip("Re-hook every unhooked client into BartleBot")
        self._rehook_all_btn.setEnabled(False)
        self._rehook_all_btn.clicked.connect(self.rehook_all_requested)
        layout.addWidget(self._rehook_all_btn)

        return row

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_account_source(self, account_for) -> None:
        """Tell the section how to ask which account a client was launched under.

        A callable taking a client and returning a username or ``None`` --
        ``ClientManager.account_for``. Injected for the reason given in ``__init__``, and
        re-asked every tick because the answer arrives after the client does.
        """
        self._account_for = account_for
        self.refresh_account_colors()

    def set_all_clients(self, active_clients: list, unhooked_clients: list) -> None:
        """Rebuild the two buckets, re-using the card for any window still in the same one.

        The signature is unchanged from the version this replaces, deliberately: it is
        what ``AppController`` wires ``all_clients_changed`` to and what the two window-
        minimum tests build a sidebar with.
        """
        self._stop_refresh_loop()

        survivors = {(c.hwnd, c.is_unhooked): c for c in self._cards}
        active = [self._card_for(survivors, client, i, unhooked=False) for i, client in enumerate(active_clients)]
        unhooked = [self._card_for(survivors, client, i, unhooked=True) for i, client in enumerate(unhooked_clients)]
        keep = set(map(id, active + unhooked))

        # Off the layouts before either container tears its rows down: ``set_cards``
        # deleteLater's everything it finds in the column, and re-parenting is what takes a
        # survivor out of that sweep. The card is parented to this widget in the gap, so it
        # is never briefly parentless -- which is to say never briefly a top-level window.
        for card in self._cards:
            if id(card) in keep:
                card.setParent(self)

        for card in self._cards:
            if id(card) not in keep:
                card.setParent(None)
                card.deleteLater()

        self._cards = active + unhooked
        self._column.set_cards(active)
        self._fill_unhooked(unhooked)

        self.refresh_account_colors()
        self._update_bulk_buttons()
        for card in active:
            card.set_bot_running(self._bot_running)
        self._start_refresh_loop()

    def _card_for(self, survivors: dict, client, index: int, *, unhooked: bool) -> PlayerCard:
        """The existing card for this window in this bucket, re-numbered -- or a new one."""
        card = survivors.get((client.window_handle, unhooked))
        if card is not None:
            card.set_index(index)
            return card
        card = PlayerCard(client, index, unhooked=unhooked, parent=self)
        card.unhook_requested.connect(self.unhook_requested)
        card.rehook_requested.connect(self.rehook_requested)
        return card

    def _fill_unhooked(self, cards: list[PlayerCard]) -> None:
        """Put *cards* in the Unhooked section, and hide the section when there are none."""
        while self._unhooked_layout.count():
            item = self._unhooked_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
        for card in cards:
            self._unhooked_layout.addWidget(card)
        self._unhooked_section.setVisible(bool(cards))

    def set_bot_running(self, running: bool) -> None:
        """Switch refresh rate between idle and active-bot modes, and lock the unhooks."""
        self._bot_running = running
        self._apply_refresh_interval()
        for card in self._cards:
            card.set_bot_running(running)
        self._update_bulk_buttons()

    async def exit_freecam(self) -> int:
        """Bring every hooked player's camera back to them, without teleporting anyone.

        Returns how many players were in freecam and came back out. Awaited by the run
        start before the FSM begins, so no node drives a client whose camera is detached.
        """
        cards = [card for card in self._cards if not card.is_unhooked and card.in_freecam]
        results = await asyncio.gather(*[card.exit_freecam() for card in cards])
        return sum(results)

    @property
    def cards(self) -> list[PlayerCard]:
        """Every card on the tab, hooked ones first. Public for the callers that count them."""
        return list(self._cards)

    @property
    def column(self) -> CardColumn:
        """The hooked players' column -- the only thing here a drag can reorder."""
        return self._column

    @property
    def unhooked_section(self) -> SidebarSection:
        """The collapsible section beneath the column, shown only when it holds a card."""
        return self._unhooked_section

    @property
    def players_visible(self) -> bool:
        """Whether the Players tab is the one on screen, which sets the poll rate."""
        return self._players_visible

    def set_players_visible(self, visible: bool) -> None:
        """Tell the section whether its tab is on screen, so polling can back off."""
        if visible == self._players_visible:
            return
        self._players_visible = visible
        self._apply_refresh_interval()
        if visible:
            # Restart rather than wait out the in-flight hidden-rate sleep, which
            # would otherwise leave the tab showing frozen values for seconds
            # after the user switches to it.
            self._stop_refresh_loop()
            self._start_refresh_loop()

    def client_zones(self) -> list[str]:
        """Last-known zone name for each card, in display order."""
        return [card.last_zone for card in self._cards]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def refresh_account_colors(self) -> None:
        """Re-stripe every card from the account its client is running.

        Public because it is what the refresh tick calls, and the tick is the only thing
        that makes a late-arriving identity reach the screen at all.

        Cheap enough to run on every tick -- a dict lookup per card plus one pass over the
        account list -- and it has to run on every tick, because an identity that is not
        known yet is the normal state for a client hooked a moment ago and nothing
        announces it arriving.
        """
        if self._account_for is None:
            return
        usernames = [self._account_for(card.client) for card in self._cards]
        keys = account_stripe_keys(usernames, SettingsManager.instance().accounts)
        for card, key in zip(self._cards, keys):
            card.set_color_key(key)

    def _update_bulk_buttons(self) -> None:
        """Enable each bulk button only when it has clients to act on.

        Unhooking mid-run would pull clients out from under the FSM, so that button
        follows the same bot-running lockout as the per-client Unhook verb.
        """
        has_active = any(not c.is_unhooked for c in self._cards)
        has_unhooked = any(c.is_unhooked for c in self._cards)
        self._unhook_all_btn.setEnabled(has_active and not self._bot_running)
        self._rehook_all_btn.setEnabled(has_unhooked)

    def _apply_refresh_interval(self) -> None:
        self._refresh_interval = client_refresh_interval(self._bot_running, self._players_visible)

    def _start_refresh_loop(self) -> None:
        try:
            self._refresh_task = asyncio.get_running_loop().create_task(self._refresh_loop())
        except RuntimeError:
            pass  # event loop not yet running; AppController will call set_clients again once running

    def _stop_refresh_loop(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = None

    @staticmethod
    async def _refresh_one(widget) -> None:
        """Refresh one card under a timeout, creating its coroutine only once running.

        The coroutine has to be built *inside* this wrapper.  ``gather`` turns each
        argument into a Task synchronously but those Tasks have not stepped yet, so a
        cancel landing in that window (a tab switch, an unhook, shutdown) kills them
        before they await anything.  Passing ``w.refresh()`` in directly meant the
        coroutine was already constructed by then and got garbage collected unawaited —
        the ``coroutine 'ClientWidget.refresh' was never awaited`` warning.
        """
        await asyncio.wait_for(widget.refresh(), timeout=CLIENT_REFRESH_TIMEOUT)

    async def _refresh_loop(self) -> None:
        try:
            while True:
                self.refresh_account_colors()
                active = [c for c in self._cards if not c.is_unhooked]
                if active:
                    results = await asyncio.gather(
                        *[self._refresh_one(c) for c in active],
                        return_exceptions=True,
                    )
                    for r in results:
                        if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                            logger.debug("PlayerCard refresh error: %s", r)
                await asyncio.sleep(self._refresh_interval)
        except asyncio.CancelledError:
            pass
