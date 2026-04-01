"""UI tree path browser dialog.

Opens a resizable dialog showing the live WizWalker UI window tree for a single
client.  Users can expand/collapse subtrees, filter with a search bar, copy a
selected element's path list, and copy the full tree as indented text.

Selecting an item draws a persistent red rectangle on the game window via a
transparent Qt overlay window positioned over the element.  The overlay is
managed by Qt's compositor and does not conflict with DirectX rendering.
"""

import asyncio
import logging

import pyperclip
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QWindow
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Data roles stored on each QTreeWidgetItem.
_PATH_ROLE = Qt.ItemDataRole.UserRole  # list[str] — path from root to this node
_WINDOW_ROLE = Qt.ItemDataRole.UserRole + 1  # DynamicWindow object for scale_to_client()


class _RectOverlay(QWidget):
    """Frameless transparent window that draws a solid red border rectangle.

    Positioned over a game UI element to provide a persistent highlight that
    does not conflict with DirectX rendering.
    """

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        pen = QPen(QColor(255, 50, 50), 3)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect().adjusted(2, 2, -2, -2))

    def move_to(self, x: int, y: int, w: int, h: int) -> None:
        self.setGeometry(x, y, w, h)
        self.show()


class UITreeBrowserDialog(QDialog):
    """Browse the live WizWalker UI window tree for a single client."""

    def __init__(self, client, parent=None) -> None:
        super().__init__(parent)
        self._client = client
        self._overlay = _RectOverlay()
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

        # Second row: clipboard buttons (right-aligned)
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
            self._status.setText(f"{self._node_count} nodes loaded")
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
        self._overlay.hide()
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
    # Rectangle overlay
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        items = self._tree.selectedItems()
        self._copy_path_btn.setEnabled(bool(items))
        if items:
            window = items[0].data(0, _WINDOW_ROLE)
            if window is not None:
                asyncio.ensure_future(self._update_overlay(window))
                return
        self._overlay.hide()

    async def _update_overlay(self, window) -> None:
        try:
            rect = await window.scale_to_client()
            qt_win = QWindow.fromWinId(self._client.window_handle)
            if qt_win is None:
                self._overlay.hide()
                return
            # scale_to_client() returns physical pixels; mapToGlobal() expects logical
            # (device-independent) pixels.  Divide by the device pixel ratio to convert.
            dpr = qt_win.devicePixelRatio()
            top_left = qt_win.mapToGlobal(QPoint(int(rect.x1 / dpr), int(rect.y1 / dpr)))
            bottom_right = qt_win.mapToGlobal(QPoint(int(rect.x2 / dpr), int(rect.y2 / dpr)))
            self._overlay.move_to(
                top_left.x(),
                top_left.y(),
                bottom_right.x() - top_left.x(),
                bottom_right.y() - top_left.y(),
            )
        except Exception:
            self._overlay.hide()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._overlay.hide()
        self._overlay.deleteLater()
        super().closeEvent(event)
