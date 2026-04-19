"""Sidebar section: live client info widgets with periodic refresh."""

import asyncio

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout

from bartlebot.gui.sidebar.client_widget import ClientWidget
from bartlebot.gui.sidebar.sidebar_section import SidebarSection


class ClientSection(SidebarSection):
    """Collapsible 'Clients' section showing one ClientWidget per hooked client."""

    # Emitted when a move-up or move-down button is clicked.
    # Arguments are (idx_a, idx_b) to pass directly to AppController.swap_clients.
    swap_requested = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__("Clients", parent)
        self._widgets: list[ClientWidget] = []

        self._clients_layout = QVBoxLayout()
        self._clients_layout.setSpacing(0)
        self.set_content_layout(self._clients_layout)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._schedule_refresh)
        self._timer.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_clients(self, clients: list) -> None:
        """Rebuild widgets to match the current client list."""
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

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _schedule_refresh(self) -> None:
        if self._widgets:
            asyncio.ensure_future(self._refresh_all())

    async def _refresh_all(self) -> None:
        if self._widgets:
            await asyncio.gather(*[w.refresh() for w in self._widgets], return_exceptions=True)
