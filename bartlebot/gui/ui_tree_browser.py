"""UI tree path browser dialog.

Opens a resizable dialog showing the live WizWalker UI window tree for a single
client.  Users can expand/collapse subtrees, filter with a search bar, copy a
selected element's path list, and copy the full tree as indented text.

Selecting an item also attempts to draw a red highlight rectangle on the game
window via WizWalker's debug_paint().  Wizard101 renders with DirectX so the
rectangle will flash periodically rather than persist — this is expected.
"""

import asyncio
import logging

import pyperclip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)

# Data roles stored on each QTreeWidgetItem.
_PATH_ROLE = Qt.ItemDataRole.UserRole  # list[str] — path from root to this node
_WINDOW_ROLE = Qt.ItemDataRole.UserRole + 1  # DynamicWindow object for direct debug_paint()


class UITreeBrowserDialog(QDialog):
    """Browse the live WizWalker UI window tree for a single client."""

    def __init__(self, client, parent=None) -> None:
        super().__init__(parent)
        self._client = client
        self._paint_timer: QTimer | None = None
        self._selected_window = None  # DynamicWindow of the currently selected item
        self._node_count = 0
        self._build_ui()
        asyncio.ensure_future(self._load_tree())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("UI Tree Browser")
        self.resize(640, 520)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        # Top row: search + tree-control buttons
        top_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter…")
        self._search.textChanged.connect(self._on_search_changed)
        top_row.addWidget(self._search, 1)

        expand_btn = QPushButton("Expand All")
        expand_btn.setToolTip("Expand all tree nodes")
        expand_btn.clicked.connect(self._tree_expand_all)
        top_row.addWidget(expand_btn)

        collapse_btn = QPushButton("Collapse All")
        collapse_btn.setToolTip("Collapse all tree nodes")
        collapse_btn.clicked.connect(self._tree_collapse_all)
        top_row.addWidget(collapse_btn)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setToolTip("Re-fetch the UI tree from the game")
        self._refresh_btn.clicked.connect(self._on_refresh)
        top_row.addWidget(self._refresh_btn)

        root_layout.addLayout(top_row)

        # Second row: clipboard buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self._copy_path_btn = QPushButton("Copy Path")
        self._copy_path_btn.setToolTip("Copy selected element's path list to clipboard")
        self._copy_path_btn.setEnabled(False)
        self._copy_path_btn.clicked.connect(self._copy_path)
        btn_row.addWidget(self._copy_path_btn)

        self._copy_all_btn = QPushButton("Copy All")
        self._copy_all_btn.setToolTip("Copy the entire tree as indented text to clipboard")
        self._copy_all_btn.setEnabled(False)
        self._copy_all_btn.clicked.connect(self._copy_all)
        btn_row.addWidget(self._copy_all_btn)

        root_layout.addLayout(btn_row)

        # Tree widget
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setColumnCount(1)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        root_layout.addWidget(self._tree, 1)

        # Status bar
        self._status = QLabel("Loading…")
        self._status.setStyleSheet("color: #999999; font-size: 11px;")
        root_layout.addWidget(self._status)

        # Placeholder while loading
        self._add_loading_placeholder()

    def _add_loading_placeholder(self) -> None:
        placeholder = QTreeWidgetItem(self._tree, ["Loading…"])
        placeholder.setDisabled(True)

    # ------------------------------------------------------------------
    # Async tree loading
    # ------------------------------------------------------------------

    async def _load_tree(self) -> None:
        try:
            self._node_count = 0
            await self._fetch_node(self._client.root_window, None, [])
            # Remove the "Loading…" placeholder (first item if still disabled)
            root = self._tree.invisibleRootItem()
            if root.childCount() >= 1 and root.child(0).isDisabled():
                root.removeChild(root.child(0))
            self._tree.expandToDepth(1)
            self._status.setText(f"{self._node_count} nodes loaded  •  rectangle highlight flashes on DirectX windows")
            self._copy_all_btn.setEnabled(True)
            self._refresh_btn.setEnabled(True)
        except Exception:
            logger.exception("Failed to load UI tree")
            self._status.setText("Error loading tree — see logs")
            self._refresh_btn.setEnabled(True)

    async def _fetch_node(self, window, parent_item: QTreeWidgetItem | None, path: list[str]) -> None:
        """Fetch name+type for one window, create a QTreeWidgetItem, recurse into children."""
        try:
            name = await window.name()
            type_name = await window.maybe_read_type_name()
        except Exception:
            return

        item = QTreeWidgetItem([f"[{name}]  {type_name}"])
        # path for this node: root stores [] so _get_window_from_path returns client.root_window.
        # Children start with [name].
        item.setData(0, _PATH_ROLE, path)
        item.setData(0, _WINDOW_ROLE, window)
        self._node_count += 1

        if parent_item is None:
            self._tree.invisibleRootItem().addChild(item)
        else:
            parent_item.addChild(item)

        try:
            children = await window.children()
        except Exception:
            return

        for child in children:
            await self._fetch_node(child, item, path + [name])

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _on_refresh(self) -> None:
        self._stop_rect_timer()
        self._selected_window = None
        self._copy_path_btn.setEnabled(False)
        self._copy_all_btn.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._search.clear()
        self._tree.clear()
        self._status.setText("Loading…")
        self._add_loading_placeholder()
        asyncio.ensure_future(self._load_tree())

    # ------------------------------------------------------------------
    # Expand / Collapse All
    # ------------------------------------------------------------------

    def _tree_expand_all(self) -> None:
        self._tree.expandAll()

    def _tree_collapse_all(self) -> None:
        self._tree.collapseAll()

    # ------------------------------------------------------------------
    # Search filtering
    # ------------------------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        root = self._tree.invisibleRootItem()
        if not text:
            self._set_all_hidden(root, False)
            return
        self._apply_filter(root, text.lower())

    def _set_all_hidden(self, item: QTreeWidgetItem, hidden: bool) -> None:
        for i in range(item.childCount()):
            child = item.child(i)
            child.setHidden(hidden)
            self._set_all_hidden(child, hidden)

    def _apply_filter(self, item: QTreeWidgetItem, text: str) -> bool:
        """Return True if item or any descendant matches text; set item hidden accordingly."""
        matched = text in item.text(0).lower()
        child_matched = False
        for i in range(item.childCount()):
            if self._apply_filter(item.child(i), text):
                child_matched = True
        visible = matched or child_matched
        item.setHidden(not visible)
        if child_matched:
            item.setExpanded(True)
        return visible

    # ------------------------------------------------------------------
    # Clipboard actions
    # ------------------------------------------------------------------

    def _copy_path(self) -> None:
        items = self._tree.selectedItems()
        if items:
            path = items[0].data(0, _PATH_ROLE) or []
            pyperclip.copy(str(path))

    def _copy_all(self) -> None:
        lines: list[str] = []

        def collect(item: QTreeWidgetItem, depth: int) -> None:
            lines.append("    " * depth + item.text(0))
            for i in range(item.childCount()):
                collect(item.child(i), depth + 1)

        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            collect(root.child(i), 0)

        pyperclip.copy("\n".join(lines))

    # ------------------------------------------------------------------
    # Rectangle highlight
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        items = self._tree.selectedItems()
        self._selected_window = items[0].data(0, _WINDOW_ROLE) if items else None
        self._copy_path_btn.setEnabled(self._selected_window is not None)
        if self._selected_window is not None:
            asyncio.ensure_future(self._paint_rect())
            self._start_rect_timer()
        else:
            self._stop_rect_timer()

    def _start_rect_timer(self) -> None:
        if self._paint_timer is not None:
            return
        self._paint_timer = QTimer(self)
        self._paint_timer.setInterval(100)
        self._paint_timer.timeout.connect(lambda: asyncio.ensure_future(self._paint_rect()))
        self._paint_timer.start()

    def _stop_rect_timer(self) -> None:
        if self._paint_timer is not None:
            self._paint_timer.stop()
            self._paint_timer.deleteLater()
            self._paint_timer = None

    async def _paint_rect(self) -> None:
        window = self._selected_window
        if window is None:
            return
        try:
            await window.debug_paint()
        except Exception:
            pass  # window may have become invalid; silently skip

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._stop_rect_timer()
        super().closeEvent(event)
