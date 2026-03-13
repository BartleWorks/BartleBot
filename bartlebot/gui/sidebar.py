"""Client utilities sidebar — one ClientWidget per hooked client."""

import asyncio

from PyQt6.QtWidgets import QScrollArea, QWidget, QVBoxLayout
from PyQt6.QtCore import QTimer

from bartlebot.gui.client_widget import ClientWidget


class ClientSidebar(QScrollArea):
    """Fixed-width sidebar showing live info and controls for each hooked client."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._widgets: list[ClientWidget] = []

        # Content widget inside the scroll area
        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(0, 4, 0, 4)
        self._layout.addStretch()

        self.setWidget(self._content)
        self.setWidgetResizable(True)
        self.setMinimumWidth(300)

        # Refresh timer
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._schedule_refresh)
        self._timer.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_clients(self, clients: list) -> None:
        """Rebuild widgets to match the current client list."""
        # Remove old widgets
        for w in self._widgets:
            self._layout.removeWidget(w)
            w.deleteLater()
        self._widgets.clear()

        # Add new widgets (inserted before the trailing stretch)
        for i, client in enumerate(clients):
            widget = ClientWidget(client, i, self._content)
            self._layout.insertWidget(self._layout.count() - 1, widget)
            self._widgets.append(widget)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _schedule_refresh(self) -> None:
        if self._widgets:
            asyncio.ensure_future(self._refresh_all())

    async def _refresh_all(self) -> None:
        if self._widgets:
            await asyncio.gather(*[w.refresh() for w in self._widgets])
