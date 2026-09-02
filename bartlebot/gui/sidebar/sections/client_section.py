"""Players tab content: live client info widgets with periodic refresh."""

import asyncio
import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from bartlebot.gui.sidebar.client_widget import ClientWidget
from bartlebot.nodes.timing import (
    CLIENT_REFRESH_INTERVAL_HIDDEN,
    CLIENT_REFRESH_INTERVAL_IDLE,
    CLIENT_REFRESH_INTERVAL_RUN,
    CLIENT_REFRESH_TIMEOUT,
)

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


class ClientSection(QWidget):
    """One ClientWidget per hooked client, plus the bulk hook controls.

    Lives in RightSidebar's Players tab, which supplies the surrounding scroll
    area and margins; this widget draws no header of its own since the tab label
    already names it.
    """

    # Emitted when a move-up or move-down button is clicked.
    # Arguments are (idx_a, idx_b) to pass directly to AppController.swap_clients.
    swap_requested = pyqtSignal(int, int)
    unhook_requested = pyqtSignal(int)  # index of active client to unhook
    rehook_requested = pyqtSignal(int)  # HWND of unhooked client to rehook
    unhook_all_requested = pyqtSignal()
    rehook_all_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._widgets: list[ClientWidget] = []
        self._refresh_task: asyncio.Task | None = None
        self._bot_running = False
        # Matches RightSidebar's initial current tab, which currentChanged never announces.
        self._players_visible = True
        self._refresh_interval: float = client_refresh_interval(False, True)

        # Maximum, not the QWidget default of Preferred: without this the card
        # stack spreads to fill the tab instead of stacking at the top.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        self._clients_layout = QVBoxLayout(self)
        self._clients_layout.setContentsMargins(0, 0, 0, 0)
        self._clients_layout.setSpacing(0)
        self._clients_layout.addWidget(self._build_bulk_row())

    def _build_bulk_row(self) -> QWidget:
        """Row of Unhook All / Rehook All buttons pinned above the per-client widgets."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 0, 6, 4)
        layout.setSpacing(4)

        self._unhook_all_btn = QPushButton("Unhook All")
        self._unhook_all_btn.setToolTip("Unhook every client (leaves all game windows open)")
        self._unhook_all_btn.setEnabled(False)
        self._unhook_all_btn.clicked.connect(self.unhook_all_requested)
        layout.addWidget(self._unhook_all_btn)

        self._rehook_all_btn = QPushButton("Rehook All")
        self._rehook_all_btn.setToolTip("Re-hook every unhooked client into BartleBot")
        self._rehook_all_btn.setEnabled(False)
        self._rehook_all_btn.clicked.connect(self.rehook_all_requested)
        layout.addWidget(self._rehook_all_btn)

        return row

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_all_clients(self, active_clients: list, unhooked_clients: list) -> None:
        """Rebuild widgets: active clients first, then unhooked clients (grayed out)."""
        self._stop_refresh_loop()

        for w in self._widgets:
            self._clients_layout.removeWidget(w)
            w.deleteLater()
        self._widgets.clear()

        total = len(active_clients)
        for i, client in enumerate(active_clients):
            widget = ClientWidget(client, i, total, self)
            widget.move_up.connect(lambda idx: self.swap_requested.emit(idx, idx - 1))
            widget.move_down.connect(lambda idx: self.swap_requested.emit(idx, idx + 1))
            widget.unhook_requested.connect(self.unhook_requested)
            self._clients_layout.addWidget(widget)
            self._widgets.append(widget)

        for client in unhooked_clients:
            widget = ClientWidget(client, -1, 0, self)
            widget.rehook_requested.connect(self.rehook_requested)
            widget.set_unhooked(True)
            self._clients_layout.addWidget(widget)
            self._widgets.append(widget)

        self._update_bulk_buttons()
        self._start_refresh_loop()

    def set_bot_running(self, running: bool) -> None:
        """Switch refresh rate between idle and active-bot modes."""
        self._bot_running = running
        self._apply_refresh_interval()
        for w in self._widgets:
            w.set_bot_running(running)
        self._update_bulk_buttons()

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
        """Last-known zone name for each client widget, in display order."""
        return [w.last_zone for w in self._widgets]

    def _update_bulk_buttons(self) -> None:
        """Enable each bulk button only when it has clients to act on.

        Unhooking mid-run would pull clients out from under the FSM, so that button
        follows the same bot-running lockout as the per-client Unhook button.
        """
        has_active = any(not w.is_unhooked for w in self._widgets)
        has_unhooked = any(w.is_unhooked for w in self._widgets)
        self._unhook_all_btn.setEnabled(has_active and not self._bot_running)
        self._rehook_all_btn.setEnabled(has_unhooked)

    def _update_bulk_buttons(self) -> None:
        """Enable each bulk button only when it has clients to act on.

        Unhooking mid-run would pull clients out from under the FSM, so that button
        follows the same bot-running lockout as the per-client Unhook button.
        """
        has_active = any(not w.is_unhooked for w in self._widgets)
        has_unhooked = any(w.is_unhooked for w in self._widgets)
        self._unhook_all_btn.setEnabled(has_active and not self._bot_running)
        self._rehook_all_btn.setEnabled(has_unhooked)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

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
    async def _refresh_one(widget: ClientWidget) -> None:
        """Refresh one widget under a timeout, creating its coroutine only once running.

        The coroutine has to be built *inside* this wrapper.  ``gather`` turns each
        argument into a Task synchronously but those Tasks have not stepped yet, so a
        cancel landing in that window (a tab switch, an unhook, shutdown) kills them
        before they await anything.  Passing ``w.refresh()`` in directly meant the
        coroutine was already constructed by then and got garbage collected unawaited —
        the ``coroutine 'ClientWidget.refresh' was never awaited`` warning in #128.
        """
        await asyncio.wait_for(widget.refresh(), timeout=CLIENT_REFRESH_TIMEOUT)

    async def _refresh_loop(self) -> None:
        try:
            while True:
                active_widgets = [w for w in self._widgets if not w.is_unhooked]
                if active_widgets:
                    results = await asyncio.gather(
                        *[self._refresh_one(w) for w in active_widgets],
                        return_exceptions=True,
                    )
                    for r in results:
                        if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                            logger.debug("ClientWidget refresh error: %s", r)
                await asyncio.sleep(self._refresh_interval)
        except asyncio.CancelledError:
            pass
