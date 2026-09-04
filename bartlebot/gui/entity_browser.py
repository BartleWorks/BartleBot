"""Entity browser dialog.

Opens a resizable dialog showing all entities near a client, sorted by
distance.  Users can refresh the list on demand and copy an entity name
or XYZ position to the clipboard.  No continuous polling — the list is
fetched once on open and again whenever the Refresh button is pressed.
"""

import asyncio
import json
import logging

import pyperclip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from bartlebot.gui.widgets.search_field import SearchLineEdit
from bartlebot.gui.widgets.themed_label import ThemedTextLabel

logger = logging.getLogger(__name__)

_DIST_ROLE = Qt.ItemDataRole.UserRole  # float — raw distance for numeric sort
_XYZ_ROLE = Qt.ItemDataRole.UserRole + 1  # str  — JSON xyz for clipboard


class _EntityItem(QTreeWidgetItem):
    """QTreeWidgetItem with numeric sort on the distance column."""

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        if col == 1:
            return (self.data(1, _DIST_ROLE) or 0.0) < (other.data(1, _DIST_ROLE) or 0.0)
        return super().__lt__(other)


class EntityBrowserDialog(QDialog):
    """Browse entities near a client, sorted by distance."""

    def __init__(self, client, parent=None) -> None:
        super().__init__(parent)
        self._client = client
        self._load_task: asyncio.Task | None = None
        self._closed = False
        self._build_ui()
        self._start_load()

    def _start_load(self) -> None:
        """Schedule a fresh entity load, replacing any load still in flight.

        The task reference is kept so it is not garbage-collected mid-flight and
        so ``closeEvent`` can cancel it — ``_load_entities`` writes to this
        dialog's widgets after every await, and those writes hit a deleted C++
        object if the load outlives the dialog.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop: unit tests construct this dialog directly. The
            # app always has one (qasync), so this only skips work in tests.
            return
        if self._load_task and not self._load_task.done():
            self._load_task.cancel()
        self._load_task = asyncio.ensure_future(self._load_entities())

    def closeEvent(self, event) -> None:
        self._closed = True
        if self._load_task:
            self._load_task.cancel()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("Entity Browser")
        self.resize(560, 440)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        # Top row: search bar + refresh button
        top_row = QHBoxLayout()

        self._search = SearchLineEdit("Filter name…")
        self._search.textChanged.connect(self._on_search_changed)
        top_row.addWidget(self._search, 1)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setToolTip("Re-fetch entities from the game")
        self._refresh_btn.clicked.connect(self._on_refresh)
        top_row.addWidget(self._refresh_btn)

        root_layout.addLayout(top_row)

        # Button row: copy buttons (right-aligned) + status label
        btn_row = QHBoxLayout()

        self._status = ThemedTextLabel("Loading…", css="font-size: 11px;")
        btn_row.addWidget(self._status, 1)

        self._copy_name_btn = QPushButton("Copy Name")
        self._copy_name_btn.setToolTip("Copy the selected entity's name to clipboard")
        self._copy_name_btn.setEnabled(False)
        self._copy_name_btn.clicked.connect(self._copy_selected_name)
        btn_row.addWidget(self._copy_name_btn)

        self._copy_xyz_btn = QPushButton("Copy XYZ")
        self._copy_xyz_btn.setToolTip("Copy the selected entity's XYZ as a JSON object")
        self._copy_xyz_btn.setEnabled(False)
        self._copy_xyz_btn.clicked.connect(self._copy_selected_xyz)
        btn_row.addWidget(self._copy_xyz_btn)

        root_layout.addLayout(btn_row)

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
        self._search.clear()
        self._status.setText("Loading…")
        self._tree.clear()
        self._copy_name_btn.setEnabled(False)
        self._copy_xyz_btn.setEnabled(False)
        try:
            player_pos = await self._client.body.position()
            entities = await self._client.get_base_entity_list()
            if self._closed:
                return

            # Disable sorting during population so inserts don't re-sort on every add
            self._tree.setSortingEnabled(False)
            for e in entities:
                if self._closed:
                    return
                try:
                    name = await e.object_name() or "(unnamed)"
                    loc = await e.location()
                    dist = (
                        (loc.x - player_pos.x) ** 2 + (loc.y - player_pos.y) ** 2 + (loc.z - player_pos.z) ** 2
                    ) ** 0.5
                    xyz_json = json.dumps({"x": round(loc.x, 2), "y": round(loc.y, 2), "z": round(loc.z, 2)})
                    item = _EntityItem([name, f"{dist:.1f}", f"{loc.x:.1f}, {loc.y:.1f}, {loc.z:.1f}"])
                    item.setData(1, _DIST_ROLE, dist)
                    item.setData(0, _XYZ_ROLE, xyz_json)
                    self._tree.addTopLevelItem(item)
                except Exception:
                    continue  # skip unreadable entities

            self._tree.setSortingEnabled(True)
            self._tree.sortItems(1, Qt.SortOrder.AscendingOrder)
            count = self._tree.topLevelItemCount()
            self._status.setText(f"{count} {'entity' if count == 1 else 'entities'}")
        except Exception:
            if self._closed:
                return
            logger.exception("Failed to load entity list")
            self._status.setText("Error loading entities — see logs")
        finally:
            if not self._closed:
                self._refresh_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_refresh(self) -> None:
        self._start_load()

    def _on_selection_changed(self) -> None:
        has_selection = bool(self._tree.selectedItems())
        self._copy_name_btn.setEnabled(has_selection)
        self._copy_xyz_btn.setEnabled(has_selection)

    def _on_search_changed(self, text: str) -> None:
        lowered = text.lower()
        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            item.setHidden(bool(lowered) and lowered not in item.text(0).lower())

    def _copy_selected_name(self) -> None:
        items = self._tree.selectedItems()
        if items:
            pyperclip.copy(items[0].text(0))

    def _copy_selected_xyz(self) -> None:
        items = self._tree.selectedItems()
        if items:
            pyperclip.copy(items[0].data(0, _XYZ_ROLE) or "")
