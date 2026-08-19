"""UI tree path browser dialog.

Opens a resizable dialog showing the live WizWalker UI window tree for a single
client.  Users can expand/collapse subtrees, filter with a search bar, copy a
selected element's path list, and copy the full tree as indented text.

Alongside each element the tree shows its Visible / Enabled / Grayed state, so
the question "why will this window not respond" can be answered by looking
rather than by writing a throwaway probe script.

Selecting an item draws a persistent red rectangle on the game window via a
transparent Qt overlay window positioned over the element.  The overlay is
managed by Qt's compositor and does not conflict with DirectX rendering.
"""

import asyncio
import logging

import pyperclip
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QPainter, QPalette, QPen, QWindow
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from wizwalker.memory.memory_objects.enums import WindowFlags

logger = logging.getLogger(__name__)

# Data roles stored on each QTreeWidgetItem.
_PATH_ROLE = Qt.ItemDataRole.UserRole  # list[str] — path from root to this node
_WINDOW_ROLE = Qt.ItemDataRole.UserRole + 1  # DynamicWindow object for scale_to_client()
_VISIBLE_ROLE = Qt.ItemDataRole.UserRole + 2  # bool — kept on column 0 for the filter
_STATE_ROLE = Qt.ItemDataRole.UserRole + 3  # bool | None — per-cell value for the color delegate

# Column indices.
_COL_ELEMENT = 0
_COL_VISIBLE = 1
_COL_ENABLED = 2
_COL_GRAYED = 3

# The value in each state column that is worth noticing, and the THEME token to
# paint it with.  Only the exceptional value is colored — painting every "true"
# green would produce a wall of green, since nearly every window is enabled.
_NOTABLE_STATE = {
    _COL_VISIBLE: (True, "success"),
    _COL_ENABLED: (False, "error"),
    _COL_GRAYED: (True, "warning"),
}

# Shown where a class has no such field at all, as opposed to having it set false.
_NO_VALUE = "—"

# Classes that carry ControlButton's ``m_bGreyed`` at offset 808, which is what
# ``Window.is_control_grayed()`` reads.  Plain ``Window`` does not have it, so
# reading that offset on an arbitrary node returns unrelated bytes — a garbage
# bool.  The set is not derivable from a name prefix (BadgeButton and
# PotionButton carry it; Window does not), so it is listed explicitly.
#
# Regenerate with:
#   poetry run python tools/typedump.py prop m_bGreyed
_GREYED_TYPES = frozenset(
    {
        "BadgeButton",
        "ChatPopupButton",
        "ControlButton",
        "ControlCheckBox",
        "ControlDecalButton",
        "ControlPopupButton",
        "ControlTab::Button",
        "DyeShopWindow::DyeShopItem",
        "InventoryPageWindow::InventoryItem",
        "PCSButton",
        "PetSnackCheckBox",
        "PotionButton",
        "RecipeWindow::RecipeItem",
        "ScrollButton",
        "ScrollablePopupButton",
        "SpellCheckBox",
    }
)


def decode_flags(flags_value: int) -> tuple[bool, bool]:
    """Return ``(visible, enabled)`` from a raw ``m_Flags`` value.

    One uint32 read answers both questions, which is why ``_fetch_node`` calls
    ``flags()`` rather than ``is_visible()`` plus a second read.  There is no
    ``is_enabled()`` in WizWalker; the disabled bit is only exposed as a flag.
    """
    flags = WindowFlags(flags_value)
    return (WindowFlags.visible in flags, WindowFlags.disabled not in flags)


def has_greyed_field(type_name: str) -> bool:
    """Whether ``is_control_grayed()`` reads a real field on this class."""
    return type_name in _GREYED_TYPES


def format_state(value: bool | None) -> str:
    """Render a state cell.  ``None`` means the class has no such field."""
    if value is None:
        return _NO_VALUE
    return "true" if value else "false"


def row_matches(row_text: str, node_visible: bool | None, text: str, hide_invisible: bool) -> bool:
    """Whether one row satisfies the filter on its own merits.

    ``text`` is matched against the element column only — matching the state
    columns as well would make every row containing "true" a hit.
    """
    if text and text not in row_text.lower():
        return False
    return not (hide_invisible and node_visible is False)


def apply_filter(item: QTreeWidgetItem, text: str, hide_invisible: bool) -> bool:
    """Hide ``item`` and its descendants per the filter; return whether it stays shown."""
    matched = row_matches(
        item.text(_COL_ELEMENT),
        item.data(_COL_ELEMENT, _VISIBLE_ROLE),
        text,
        hide_invisible,
    )
    child_matched = False
    for i in range(item.childCount()):
        if apply_filter(item.child(i), text, hide_invisible):
            child_matched = True
    # A non-matching parent survives as a spine to matching descendants.  That
    # matters most for "Hide invisible": is_visible() is a window's OWN flag and
    # ignores its parents, and the observed pattern runs the counter-intuitive
    # way — wndFriendsList reads visible=False while its child btnFriendListClose
    # reads visible=True (see node_helpers.is_visible_by_path).  Hiding the
    # container would hide exactly the rows being hunted for.
    keep = matched or child_matched
    item.setHidden(not keep)
    if child_matched:
        item.setExpanded(True)
    return keep


class _StateColorDelegate(QStyledItemDelegate):
    """Colors the state columns, reading THEME at paint time.

    Deliberately a delegate rather than ``QTreeWidgetItem.setForeground()``:
    per CLAUDE.md, a widget that reads THEME at paint time follows theme
    switches for free, whereas a baked-in foreground brush goes stale and would
    need a ``changeEvent`` that walks every one of several thousand items.
    """

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        notable = _NOTABLE_STATE.get(index.column())
        if notable is None:
            return
        from bartlebot.gui.theme import THEME

        notable_value, notable_token = notable
        value = index.data(_STATE_ROLE)
        token = notable_token if value is notable_value else "text_muted"
        color = THEME.qcolor(token)
        option.palette.setColor(QPalette.ColorRole.Text, color)
        option.palette.setColor(QPalette.ColorRole.HighlightedText, color)


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
        from bartlebot.gui.theme import THEME

        pen = QPen(THEME.qcolor("error"), 3)
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
        self._load_task: asyncio.Task | None = None
        self._overlay_task: asyncio.Task | None = None
        self._closed = False
        self._node_count = 0
        self._build_ui()
        self._load_task = asyncio.ensure_future(self._load_tree())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("UI Tree Browser")
        self.resize(760, 520)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        # Top row: search + tree-control buttons
        top_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter…")
        self._search.textChanged.connect(self._on_filter_changed)
        top_row.addWidget(self._search, 1)

        self._hide_invisible = QCheckBox("Hide invisible")
        self._hide_invisible.setToolTip(
            "Hide elements whose Visible flag is false.\n"
            "Invisible parents of visible elements are kept, because is_visible()\n"
            "is a window's own flag and ignores its parents."
        )
        self._hide_invisible.toggled.connect(self._on_filter_changed)
        top_row.addWidget(self._hide_invisible)

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

        # Tree widget: Element | Visible | Enabled | Grayed.  Sorting stays off —
        # this is a hierarchy, not a flat table like the entity browser.
        self._tree = QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["Element", "Visible", "Enabled", "Grayed"])
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._tree.header().setSectionResizeMode(_COL_ELEMENT, QHeaderView.ResizeMode.Stretch)
        for col in (_COL_VISIBLE, _COL_ENABLED, _COL_GRAYED):
            self._tree.header().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setItemDelegate(_StateColorDelegate(self._tree))
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        root_layout.addWidget(self._tree, 1)

        # Status bar
        self._status = QLabel("Loading…")
        from bartlebot.gui.theme import THEME

        self._status.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px;")
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
        """Fetch name+type+state for one window, create a QTreeWidgetItem, recurse into children."""
        try:
            name = await window.name()
            type_name = await window.maybe_read_type_name()
        except Exception:
            return

        # WizWalker memory reads are synchronous ReadProcessMemory calls on the
        # event loop thread, so gathering these would not overlap them — keep the
        # per-node read count down instead.  flags() answers Visible and Enabled
        # together, and the grayed byte is only read where the class has it.
        visible: bool | None = None
        enabled: bool | None = None
        try:
            visible, enabled = decode_flags(int(await window.flags()))
        except Exception:
            pass

        grayed: bool | None = None
        if has_greyed_field(type_name):
            try:
                grayed = await window.is_control_grayed()
            except Exception:
                grayed = None

        item = QTreeWidgetItem(
            [
                f"[{name}]  {type_name}",
                format_state(visible),
                format_state(enabled),
                format_state(grayed),
            ]
        )
        # Root stores [] so _get_window_from_path(root_window, []) returns root_window.
        # All other nodes store the full path to themselves (excluding the root window).
        own_path = [] if parent_item is None else path + [name]
        item.setData(_COL_ELEMENT, _PATH_ROLE, own_path)
        item.setData(_COL_ELEMENT, _WINDOW_ROLE, window)
        item.setData(_COL_ELEMENT, _VISIBLE_ROLE, visible)
        item.setData(_COL_VISIBLE, _STATE_ROLE, visible)
        item.setData(_COL_ENABLED, _STATE_ROLE, enabled)
        item.setData(_COL_GRAYED, _STATE_ROLE, grayed)
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
            await self._fetch_node(child, item, own_path)

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
        self._load_task = asyncio.ensure_future(self._load_tree())

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

    def _on_filter_changed(self, _value=None) -> None:
        text = self._search.text().lower()
        hide_invisible = self._hide_invisible.isChecked()
        root = self._tree.invisibleRootItem()
        if not text and not hide_invisible:
            self._set_all_hidden(root, False)
            return
        apply_filter(root, text, hide_invisible)

    def _set_all_hidden(self, item: QTreeWidgetItem, hidden: bool) -> None:
        for i in range(item.childCount()):
            child = item.child(i)
            child.setHidden(hidden)
            self._set_all_hidden(child, hidden)

    # ------------------------------------------------------------------
    # Clipboard actions
    # ------------------------------------------------------------------

    def _copy_path(self) -> None:
        items = self._tree.selectedItems()
        if items:
            path = items[0].data(_COL_ELEMENT, _PATH_ROLE) or []
            pyperclip.copy(str(path))

    def _copy_all(self) -> None:
        lines: list[str] = []

        def collect(item: QTreeWidgetItem, depth: int) -> None:
            state = (
                f"visible={item.text(_COL_VISIBLE)} enabled={item.text(_COL_ENABLED)} grayed={item.text(_COL_GRAYED)}"
            )
            lines.append("    " * depth + f"{item.text(_COL_ELEMENT)}    {state}")
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
            window = items[0].data(_COL_ELEMENT, _WINDOW_ROLE)
            if window is not None:
                if self._overlay_task is not None:
                    self._overlay_task.cancel()
                self._overlay_task = asyncio.ensure_future(self._update_overlay(window))
                return
        self._overlay.hide()

    async def _update_overlay(self, window) -> None:
        if self._closed:
            return
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
            if not self._closed:
                self._overlay.hide()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._closed = True
        if self._load_task is not None:
            self._load_task.cancel()
        if self._overlay_task is not None:
            self._overlay_task.cancel()
        self._overlay.hide()
        self._overlay.deleteLater()
        super().closeEvent(event)
