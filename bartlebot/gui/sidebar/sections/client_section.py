"""Sidebar section: live client info widgets with periodic refresh."""

import asyncio
import logging

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout

from bartlebot.gui.sidebar.client_widget import ClientWidget
from bartlebot.gui.sidebar.sidebar_section import SidebarSection

logger = logging.getLogger(__name__)


class ClientSection(SidebarSection):
    """Collapsible 'Clients' section showing one ClientWidget per hooked client."""

    # Emitted when a move-up or move-down button is clicked.
    # Arguments are (idx_a, idx_b) to pass directly to AppController.swap_clients.
    swap_requested = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__("Clients", parent)
        self._widgets: list[ClientWidget] = []
        self._refresh_task: asyncio.Task | None = None

        self._clients_layout = QVBoxLayout()
        self._clients_layout.setSpacing(0)
        self.set_content_layout(self._clients_layout)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_clients(self, clients: list) -> None:
        """Rebuild widgets to match the current client list."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            self._refresh_task = None

        for w in self._widgets:
            self._clients_layout.removeWidget(w)
            w.deleteLater()
        self._widgets.clear()

        total = len(clients)
        for i, client in enumerate(clients):
            widget = ClientWidget(client, i, total, self.body)
            widget.move_up.connect(lambda idx: self.swap_requested.emit(idx, idx - 1))
            widget.move_down.connect(lambda idx: self.swap_requested.emit(idx, idx + 1))
            self._clients_layout.addWidget(widget)
            self._widgets.append(widget)

        self._start_refresh_loop()

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
                if self._widgets:
                    results = await asyncio.gather(
                        *[w.refresh() for w in self._widgets],
                        return_exceptions=True,
                    )
                    for r in results:
                        if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                            logger.debug("ClientWidget refresh error: %s", r)
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
