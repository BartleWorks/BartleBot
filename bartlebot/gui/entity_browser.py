"""Entity browser dialog.

Opens a resizable dialog showing all entities near a client, sorted by
distance.  Users can refresh the list on demand, copy an entity name
or XYZ position to the clipboard, and teleport the client to a selected
entity to look at it.  No continuous polling — the list is fetched once
on open and again whenever the Refresh button is pressed.

Each row carries both of an entity's names: the internal object name, which
is the identity a node's params are written against, and the display name a
player reads, resolved from the checked-in snapshot (``bartlebot.entity_names``).
The filter matches either, so "Crescent Moon Centaur" finds the row whose
object name is ``AQ-Centaur-Artemis``.
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from bartlebot import entity_names
from bartlebot.gui.theme import THEME
from bartlebot.gui.widgets.action_button import ActionButton
from bartlebot.gui.widgets.search_field import SearchLineEdit
from bartlebot.gui.widgets.themed_label import ThemedTextLabel

logger = logging.getLogger(__name__)

_DIST_ROLE = Qt.ItemDataRole.UserRole  # float — raw distance for numeric sort
_XYZ_ROLE = Qt.ItemDataRole.UserRole + 1  # str  — JSON xyz for clipboard
_POS_ROLE = Qt.ItemDataRole.UserRole + 2  # tuple — raw (x, y, z) for teleporting

_NAME_COL, _DISPLAY_COL, _DIST_COL, _LOC_COL = range(4)


class _EntityItem(QTreeWidgetItem):
    """QTreeWidgetItem with numeric sort on the distance column."""

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        col = self.treeWidget().sortColumn() if self.treeWidget() else 0
        if col == _DIST_COL:
            return (self.data(_DIST_COL, _DIST_ROLE) or 0.0) < (other.data(_DIST_COL, _DIST_ROLE) or 0.0)
        return super().__lt__(other)


class EntityBrowserDialog(QDialog):
    """Browse entities near a client, sorted by distance."""

    def __init__(self, client, parent=None) -> None:
        super().__init__(parent)
        self._client = client
        self._load_task: asyncio.Task | None = None
        self._teleport_task: asyncio.Task | None = None
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
        if self._teleport_task:
            self._teleport_task.cancel()
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

        self._refresh_btn = ActionButton("revert", "Refresh", fill=False)
        self._refresh_btn.setToolTip("Re-fetch entities from the game")
        self._refresh_btn.clicked.connect(self._on_refresh)
        top_row.addWidget(self._refresh_btn)

        root_layout.addLayout(top_row)

        # Button row: copy buttons (right-aligned) + status label. All TERTIARY, Teleport
        # included: these are four equal reads on one selection, and the most *interesting*
        # verb in a row is not the same thing as the one the surface expects. Nothing in an
        # equal-weight row outranks its neighbors — see gui/CLAUDE.md.
        btn_row = QHBoxLayout()

        self._status = ThemedTextLabel("Loading…", css=f"font-size: {THEME.font_small_px}px;")
        btn_row.addWidget(self._status, 1)

        self._copy_name_btn = ActionButton("copy", "Copy Name", fill=False)
        self._copy_name_btn.setToolTip("Copy the selected entity's name to clipboard")
        self._copy_name_btn.setEnabled(False)
        self._copy_name_btn.clicked.connect(self._copy_selected_name)
        btn_row.addWidget(self._copy_name_btn)

        self._copy_xyz_btn = ActionButton("copy", "Copy XYZ", fill=False)
        self._copy_xyz_btn.setToolTip("Copy the selected entity's XYZ as a JSON object")
        self._copy_xyz_btn.setEnabled(False)
        self._copy_xyz_btn.clicked.connect(self._copy_selected_xyz)
        btn_row.addWidget(self._copy_xyz_btn)

        self._teleport_btn = ActionButton("arrow_right", "Teleport", fill=False)
        self._teleport_btn.setToolTip("Teleport this player to the selected entity")
        self._teleport_btn.setEnabled(False)
        self._teleport_btn.clicked.connect(self._on_teleport)
        btn_row.addWidget(self._teleport_btn)

        root_layout.addLayout(btn_row)

        # Tree widget: Name | Display Name | Dist | Location.
        # Object name leads: it is the identity a node's params are written against, and
        # the one Copy Name hands over. The display name reads better but is not unique —
        # four separate templates all display as "Cerberus" — so it informs rather than keys.
        self._tree = QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["Name", "Display Name", "Dist", "Location (X, Y, Z)"])
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._tree.setRootIsDecorated(False)
        self._tree.setSortingEnabled(True)
        self._tree.header().setSectionResizeMode(_NAME_COL, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(_DISPLAY_COL, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(_DIST_COL, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setSectionResizeMode(_LOC_COL, QHeaderView.ResizeMode.ResizeToContents)
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
        self._teleport_btn.setEnabled(False)
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
                    # None is the ordinary case — most world objects are scenery the game
                    # never names — and an empty cell says that better than repeating column 0.
                    display = entity_names.display_name(name) or ""
                    item = _EntityItem([name, display, f"{dist:.1f}", f"{loc.x:.1f}, {loc.y:.1f}, {loc.z:.1f}"])
                    item.setData(_DIST_COL, _DIST_ROLE, dist)
                    item.setData(_NAME_COL, _XYZ_ROLE, xyz_json)
                    item.setData(_NAME_COL, _POS_ROLE, (loc.x, loc.y, loc.z))
                    self._tree.addTopLevelItem(item)
                except Exception:
                    continue  # skip unreadable entities

            self._tree.setSortingEnabled(True)
            self._tree.sortItems(_DIST_COL, Qt.SortOrder.AscendingOrder)
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
        self._teleport_btn.setEnabled(has_selection)

    def _on_search_changed(self, text: str) -> None:
        # Either name matches: a user looking for a boss knows what the game calls it, not
        # what its template is called, and the point of the column is to bridge the two.
        lowered = text.lower()
        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            hit = lowered in item.text(_NAME_COL).lower() or lowered in item.text(_DISPLAY_COL).lower()
            item.setHidden(bool(lowered) and not hit)

    def _copy_selected_name(self) -> None:
        items = self._tree.selectedItems()
        if items:
            pyperclip.copy(items[0].text(_NAME_COL))

    def _copy_selected_xyz(self) -> None:
        items = self._tree.selectedItems()
        if items:
            pyperclip.copy(items[0].data(_NAME_COL, _XYZ_ROLE) or "")

    def _on_teleport(self) -> None:
        """Start a teleport to the selected entity, replacing one still in flight.

        The task reference is kept for the same reason the load's is: this writes to
        the dialog's widgets after its await, and those writes hit a deleted C++
        object if the teleport outlives the dialog.
        """
        items = self._tree.selectedItems()
        if not items:
            return
        pos = items[0].data(_NAME_COL, _POS_ROLE)
        if not pos:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop: unit tests drive this dialog directly
        if self._teleport_task and not self._teleport_task.done():
            self._teleport_task.cancel()
        self._teleport_task = asyncio.ensure_future(self._teleport_to(pos))

    async def _teleport_to(self, pos: tuple[float, float, float]) -> None:
        from wizwalker import XYZ

        self._teleport_btn.setEnabled(False)
        try:
            await self._client.teleport(XYZ(*pos), wait_on_inuse=True, purge_on_after_unuser_fixer=True)
        except Exception:
            if self._closed:
                return
            logger.exception("Failed to teleport to entity")
            self._status.setText("Teleport failed — see logs")
        finally:
            if not self._closed:
                self._teleport_btn.setEnabled(bool(self._tree.selectedItems()))
