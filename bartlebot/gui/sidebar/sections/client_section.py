"""Players tab content: core's column of player cards, and the loop that reads what they show.

The column itself -- the drag-reorderable cards, the unhooked section beneath them, the
bulk row, and why the cards are re-keyed rather than rebuilt -- is core's
``bartleby.qt.sidebar.player_column.PlayerColumn`` (Stage 8 of #167). It draws views and
holds no client. What stays here is the half that needs the clients: each card is a
``ClientCard`` built on its client, the refresh loop reads them, and the account stripe is
asked for on every tick.

WHAT COLORS A CARD
-------------------

``ClientManager.account_for`` says which account a client was launched under, and the
answer arrives late: it is filled from inside the client loop and nothing signals it. So
the section is handed that lookup as a callable and re-asks on every refresh tick, which
is the same bargain the wizard name already makes with ``wizard_names.cached_name``.
"""

import asyncio
import logging
from dataclasses import replace

from bartleby.qt.sidebar.player_card import account_stripe_keys
from bartleby.qt.sidebar.player_column import PlayerColumn
from bartleby.runtime.timing import (
    CLIENT_REFRESH_INTERVAL_HIDDEN,
    CLIENT_REFRESH_INTERVAL_IDLE,
    CLIENT_REFRESH_INTERVAL_RUN,
    CLIENT_REFRESH_TIMEOUT,
)
from bartleby.runtime.views import PlayerView
from PyQt6.QtCore import pyqtSignal

from bartlebot.gui.sidebar.client_card import RUN_LOCK_REASON, VERBS, ClientCard, initial_view
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)


def client_refresh_interval(bot_running: bool, players_visible: bool) -> float:
    """Poll interval for the client refresh loop, given bot state and tab visibility.

    Visibility is checked first on purpose: a hidden Players tab during a bot run
    is the worst case for contention and the least useful to refresh, since
    nothing on screen is reading the result.
    """
    if not players_visible:
        return CLIENT_REFRESH_INTERVAL_HIDDEN
    return CLIENT_REFRESH_INTERVAL_RUN if bot_running else CLIENT_REFRESH_INTERVAL_IDLE


class ClientSection(PlayerColumn):
    """The Players tab's contents: core's column, of ``ClientCard``\\ s, kept current by a refresh loop.

    Lives in ``RightSidebar``'s Players tab, which supplies the margins and nothing else.
    """

    #: The cards' previous indices in the order they are in now: the column's
    #: ``order_changed`` as the permutation ``ClientManager.reorder_clients`` takes, since
    #: a drag is not a swap.
    reordered = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(VERBS, card_factory=self._make_card, parent=parent)
        self._clients: dict[int, object] = {}
        #: The hooked windows in the order last handed in, which a permutation indexes.
        self._order: list[int] = []
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
        self.order_changed.connect(self._on_order_changed)

    def _make_card(self, view: PlayerView) -> ClientCard:
        return ClientCard(self._clients[view.hwnd], view.slot - 1, unhooked=view.unhooked, parent=self)

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

        The signature is unchanged from before the column moved to core, deliberately: it is
        what ``AppController`` wires ``all_clients_changed`` to and what the two window-
        minimum tests build a sidebar with. A surviving card keeps the view it drew, with
        its new place, so a reorder blanks nothing.
        """
        self._stop_refresh_loop()
        self._clients = {client.window_handle: client for client in [*active_clients, *unhooked_clients]}
        self._order = [client.window_handle for client in active_clients]
        self.set_players(
            [self._view_for(client, i, unhooked=False) for i, client in enumerate(active_clients)],
            [self._view_for(client, i, unhooked=True) for i, client in enumerate(unhooked_clients)],
        )
        self.refresh_account_colors()
        self._start_refresh_loop()

    def _view_for(self, client, index: int, *, unhooked: bool) -> PlayerView:
        """The view this client's card shows at *index*: its card's own, moved, or a fresh one."""
        card = next((c for c in self.cards if c.hwnd == client.window_handle and c.is_unhooked == unhooked), None)
        if card is None:
            return initial_view(client, index, unhooked=unhooked)
        return replace(card.view, slot=index + 1)

    def set_bot_running(self, running: bool) -> None:
        """Switch refresh rate between idle and active-bot modes, and lock the unhooks."""
        self._bot_running = running
        self._apply_refresh_interval()
        self.set_locked(running, RUN_LOCK_REASON)

    async def exit_freecam(self) -> int:
        """Bring every hooked player's camera back to them, without teleporting anyone.

        Returns how many players were in freecam and came back out. Awaited by the run
        start before the FSM begins, so no node drives a client whose camera is detached.
        """
        cards = [card for card in self.cards if not card.is_unhooked and card.in_freecam]
        results = await asyncio.gather(*[card.exit_freecam() for card in cards])
        return sum(results)

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
        return [card.last_zone for card in self.cards]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_order_changed(self, hwnds: list) -> None:
        """The column's new order, as indices into the list the cards were built from."""
        if sorted(hwnds) != sorted(self._order):
            return
        self.reordered.emit([self._order.index(hwnd) for hwnd in hwnds])

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
        cards = self.cards
        usernames = [self._account_for(card.client) for card in cards]
        keys = account_stripe_keys(usernames, SettingsManager.instance().accounts)
        for card, key in zip(cards, keys):
            if key != card.color_key:
                card.show_view(replace(card.view, color_key=key))

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
                active = [c for c in self.cards if not c.is_unhooked]
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
