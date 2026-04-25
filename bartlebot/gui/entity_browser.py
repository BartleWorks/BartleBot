"""Entity browser dialog.

Opens a resizable dialog showing all entities near a client, sorted by
distance.  Users can refresh the list on demand and copy an entity name
to the clipboard.  No continuous polling — the list is fetched once on
open and again whenever the Refresh button is pressed.
"""

import asyncio
import logging

import pyperclip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class EntityBrowserDialog(QDialog):
    """Browse entities near a client, sorted by distance."""

    def __init__(self, client, parent=None) -> None:
        super().__init__(parent)
        self._client = client
        self._build_ui()
        asyncio.ensure_future(self._load_entities())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("Entity Browser")
        self.resize(520, 420)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        # Top row: refresh button + status label
        top_row = QHBoxLayout()

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setToolTip("Re-fetch entities from the game")
        self._refresh_btn.clicked.connect(self._on_refresh)
        top_row.addWidget(self._refresh_btn)

        self._status = QLabel("Loading…")
        from bartlebot.gui.theme import THEME

        self._status.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px;")
        top_row.addWidget(self._status, 1)

        self._copy_btn = QPushButton("Copy Name")
        self._copy_btn.setToolTip("Copy the selected entity's name to clipboard")
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._copy_selected_name)
        top_row.addWidget(self._copy_btn)

        root_layout.addLayout(top_row)

        # Tree widget: Name | Distance | Location
        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Name", "Dist", "Location (X, Y, Z)"])
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._tree.setRootIsDecorated(False)
        self._tree.setSortingEnabled(True)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        root_layout.addWidget(self._tree, 1)

    # ------------------------------------------------------------------
    # Async loading
    # ------------------------------------------------------------------

    async def _load_entities(self) -> None:
        self._refresh_btn.setEnabled(False)
        self._status.setText("Loading…")
        self._tree.clear()
        self._copy_btn.setEnabled(False)
        try:
            player_pos = await self._client.body.position()
            entities = await self._client.get_base_entity_list()
            for e in entities:
                try:
                    name = await e.object_name() or "(unnamed)"
                    loc = await e.location()
                    dist = (
                        (loc.x - player_pos.x) ** 2 + (loc.y - player_pos.y) ** 2 + (loc.z - player_pos.z) ** 2
                    ) ** 0.5
                    item = QTreeWidgetItem(
                        [
                            name,
                            f"{dist:.1f}",
                            f"{loc.x:.1f}, {loc.y:.1f}, {loc.z:.1f}",
                        ]
                    )
                    # Store raw distance as numeric sort key on the distance column
                    item.setData(1, Qt.ItemDataRole.UserRole, dist)
                    self._tree.addTopLevelItem(item)
                except Exception:
                    continue  # skip unreadable entities
            self._tree.sortItems(1, Qt.SortOrder.AscendingOrder)
            count = self._tree.topLevelItemCount()
            self._status.setText(f"{count} {'entity' if count == 1 else 'entities'}")
        except Exception:
            logger.exception("Failed to load entity list")
            self._status.setText("Error loading entities — see logs")
        finally:
            self._refresh_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_refresh(self) -> None:
        asyncio.ensure_future(self._load_entities())

    def _on_selection_changed(self) -> None:
        self._copy_btn.setEnabled(bool(self._tree.selectedItems()))

    def _copy_selected_name(self) -> None:
        items = self._tree.selectedItems()
        if items:
            pyperclip.copy(items[0].text(0))
