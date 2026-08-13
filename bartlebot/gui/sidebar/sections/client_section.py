"""Sidebar section: live client info widgets with periodic refresh."""

import asyncio
import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from bartlebot.gui.sidebar.client_widget import ClientWidget
from bartlebot.gui.sidebar.sidebar_section import SidebarSection
from bartlebot.nodes.timing import CLIENT_REFRESH_INTERVAL_IDLE, CLIENT_REFRESH_INTERVAL_RUN, CLIENT_REFRESH_TIMEOUT

logger = logging.getLogger(__name__)


class ClientSection(SidebarSection):
    """Collapsible 'Clients' section showing one ClientWidget per hooked client."""

    # Emitted when a move-up or move-down button is clicked.
    # Arguments are (idx_a, idx_b) to pass directly to AppController.swap_clients.
    swap_requested = pyqtSignal(int, int)
    unhook_requested = pyqtSignal(int)  # index of active client to unhook
    rehook_requested = pyqtSignal(int)  # HWND of unhooked client to rehook
    unhook_all_requested = pyqtSignal()
    rehook_all_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Players", parent)
        self._widgets: list[ClientWidget] = []
        self._refresh_task: asyncio.Task | None = None
        self._refresh_interval: float = CLIENT_REFRESH_INTERVAL_IDLE
        self._bot_running = False

        self._clients_layout = QVBoxLayout()
        self._clients_layout.setSpacing(0)
        self._clients_layout.addWidget(self._build_bulk_row())
        self.set_content_layout(self._clients_layout)

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
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            self._refresh_task = None

        for w in self._widgets:
            self._clients_layout.removeWidget(w)
            w.deleteLater()
        self._widgets.clear()

        total = len(active_clients)
        for i, client in enumerate(active_clients):
            widget = ClientWidget(client, i, total, self.body)
            widget.move_up.connect(lambda idx: self.swap_requested.emit(idx, idx - 1))
            widget.move_down.connect(lambda idx: self.swap_requested.emit(idx, idx + 1))
            widget.unhook_requested.connect(self.unhook_requested)
            self._clients_layout.addWidget(widget)
            self._widgets.append(widget)

        for client in unhooked_clients:
            widget = ClientWidget(client, -1, 0, self.body)
            widget.rehook_requested.connect(self.rehook_requested)
            widget.set_unhooked(True)
            self._clients_layout.addWidget(widget)
            self._widgets.append(widget)

        self._update_bulk_buttons()
        self._start_refresh_loop()

    def set_bot_running(self, running: bool) -> None:
        """Switch refresh rate between idle and active-bot modes."""
        self._bot_running = running
        self._refresh_interval = CLIENT_REFRESH_INTERVAL_RUN if running else CLIENT_REFRESH_INTERVAL_IDLE
        for w in self._widgets:
            w.set_bot_running(running)
        self._update_bulk_buttons()

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

    def _start_refresh_loop(self) -> None:
        try:
            self._refresh_task = asyncio.get_running_loop().create_task(self._refresh_loop())
        except RuntimeError:
            pass  # event loop not yet running; AppController will call set_clients again once running

    async def _refresh_loop(self) -> None:
        try:
            while True:
                active_widgets = [w for w in self._widgets if not w.is_unhooked]
                if active_widgets:
                    results = await asyncio.gather(
                        *[asyncio.wait_for(w.refresh(), timeout=CLIENT_REFRESH_TIMEOUT) for w in active_widgets],
                        return_exceptions=True,
                    )
                    for r in results:
                        if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                            logger.debug("ClientWidget refresh error: %s", r)
                await asyncio.sleep(self._refresh_interval)
        except asyncio.CancelledError:
            pass
