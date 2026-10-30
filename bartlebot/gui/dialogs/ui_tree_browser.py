"""UI tree path browser dialog.

Opens a resizable dialog showing the live WizWalker UI window tree for a single
client.  Users can expand/collapse subtrees, filter with a search bar, copy a
selected element's path list, and copy the full tree as indented text.

Alongside each element the tree shows its Visible / Enabled / Grayed state, so
the question "why will this window not respond" can be answered by looking
rather than by writing a throwaway probe script.

The Text column shows what an element displays.  Paths match on name alone, so a
menu of identically-named rows is ambiguous by path, and the displayed text is
what tells the rows apart; Copy Text puts it on the clipboard for pasting into
click_path's Match Text.

Text is read on demand, never for the whole tree.  Selecting a row reads that one
window's text, and "Load Text" fills the column for the selected row and its
descendants — the menu being looked at, not the several thousand windows behind
it.  Each read is a synchronous ReadProcessMemory on the event loop thread, so
reading every node's text at load time freezes the dialog for seconds.

Selecting an item draws a persistent red rectangle on the game window via a
transparent Qt overlay window positioned over the element.  The overlay is
managed by Qt's compositor and does not conflict with DirectX rendering.
"""

import asyncio
import logging

import pyperclip
from bartleby.qt.theming.theme import THEME
from bartleby.qt.widgets.buttons.action_button import ActionButton
from bartleby.qt.widgets.inputs.search_field import SearchLineEdit
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QPainter, QPalette, QPen, QWindow
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from wizwalker.memory.memory_objects.enums import WindowFlags

from bartlebot.nodes.node_helpers import strip_html

logger = logging.getLogger(__name__)

# Data roles stored on each QTreeWidgetItem.
_PATH_ROLE = Qt.ItemDataRole.UserRole  # list[str] — path from root to this node
_WINDOW_ROLE = Qt.ItemDataRole.UserRole + 1  # DynamicWindow object for scale_to_client()
_VISIBLE_ROLE = Qt.ItemDataRole.UserRole + 2  # bool — kept on column 0 for the filter
_STATE_ROLE = Qt.ItemDataRole.UserRole + 3  # bool | None — per-cell value for the color delegate
# str — full untruncated window text, for Copy Text.  None means "not read yet", which is
# every node until it is selected or covered by Load Text; "" means read and empty.
_TEXT_ROLE = Qt.ItemDataRole.UserRole + 4

# Column indices.
_COL_ELEMENT = 0
_COL_TEXT = 1
_COL_VISIBLE = 2
_COL_ENABLED = 3
_COL_GRAYED = 4

# Longest window text shown in the Text column.  Some windows hold whole paragraphs of
# dialogue; the full string is kept on _TEXT_ROLE for Copy Text.
_TEXT_DISPLAY_LIMIT = 60

# Most nodes Load Text will read in one go.  A user who selects the root row would
# otherwise ask for the whole tree, which is the freeze this on-demand design exists to
# avoid.  The interesting case — one menu's rows — is a few dozen nodes.
_TEXT_LOAD_LIMIT = 400

# The value in each state column that is worth noticing, and the THEME token to
# paint it with.  Only the exceptional value is colored — painting every "true"
# green would produce a wall of green, since nearly every window is enabled.
_NOTABLE_STATE = {
    _COL_VISIBLE: (True, "success"),
    _COL_ENABLED: (False, "error"),
    _COL_GRAYED: (True, "warning"),
}

# Width each column opens at, in pixels.  Element is widest because it carries the tree
# indentation on top of the window name.
_DEFAULT_COLUMN_WIDTHS = {
    _COL_ELEMENT: 320,
    _COL_TEXT: 180,
    _COL_VISIBLE: 70,
    _COL_ENABLED: 70,
    _COL_GRAYED: 70,
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
#   uv run python tools/typedump.py prop m_bGreyed
_GRAYED_TYPES = frozenset(
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


def has_grayed_field(type_name: str) -> bool:
    """Whether ``is_control_grayed()`` reads a real field on this class."""
    return type_name in _GRAYED_TYPES


def format_state(value: bool | None) -> str:
    """Render a state cell.  ``None`` means the class has no such field."""
    if value is None:
        return _NO_VALUE
    return "true" if value else "false"


def format_text(value: str | None) -> str:
    """Render the Text cell, truncating anything long enough to wreck the column.

    ``None`` — not read yet — renders blank rather than as the "no such field" dash, so
    an unread row is never mistaken for a row that carries no text.
    """
    if value is None:
        return ""
    if not value:
        return _NO_VALUE
    if len(value) <= _TEXT_DISPLAY_LIMIT:
        return value
    return value[: _TEXT_DISPLAY_LIMIT - 1] + "…"


def subtree_items(root_item: QTreeWidgetItem, limit: int = _TEXT_LOAD_LIMIT) -> tuple[list[QTreeWidgetItem], bool]:
    """Return ``(items, truncated)`` for root_item and its descendants, breadth-first.

    Breadth-first so a limited load covers the rows directly under the selected node —
    the menu the user is looking at — before descending into any one row's internals.
    """
    items: list[QTreeWidgetItem] = []
    pending = [root_item]
    while pending:
        if len(items) >= limit:
            return items, True
        item = pending.pop(0)
        items.append(item)
        pending.extend(item.child(i) for i in range(item.childCount()))
    return items, False


def row_matches(
    row_text: str,
    node_visible: bool | None,
    text: str,
    hide_invisible: bool,
    window_text: str = "",
) -> bool:
    """Whether one row satisfies the filter on its own merits.

    ``text`` is matched against the element and Text columns only — matching the
    state columns as well would make every row containing "true" a hit.  The Text
    column is included because a list of identically-named rows is findable by the
    label it displays and by nothing else.
    """
    if text and text not in row_text.lower() and text not in window_text.lower():
        return False
    return not (hide_invisible and node_visible is False)


def apply_filter(item: QTreeWidgetItem, text: str, hide_invisible: bool) -> bool:
    """Hide ``item`` and its descendants per the filter; return whether it stays shown."""
    matched = row_matches(
        item.text(_COL_ELEMENT),
        item.data(_COL_ELEMENT, _VISIBLE_ROLE),
        text,
        hide_invisible,
        item.data(_COL_ELEMENT, _TEXT_ROLE) or "",
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
        from bartleby.qt.theming.theme import THEME

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
        from bartleby.qt.theming.theme import THEME

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
        self._text_task: asyncio.Task | None = None
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
        self._search = SearchLineEdit("Filter…")
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

        expand_btn = ActionButton("arrow_down", "Expand All", fill=False)
        expand_btn.setToolTip("Expand all tree nodes")
        expand_btn.clicked.connect(self._tree_expand_all)
        top_row.addWidget(expand_btn)

        collapse_btn = ActionButton("arrow_right", "Collapse All", fill=False)
        collapse_btn.setToolTip("Collapse all tree nodes")
        collapse_btn.clicked.connect(self._tree_collapse_all)
        top_row.addWidget(collapse_btn)

        self._refresh_btn = ActionButton("revert", "Refresh", fill=False)
        self._refresh_btn.setToolTip("Re-fetch the UI tree from the game")
        self._refresh_btn.clicked.connect(self._on_refresh)
        top_row.addWidget(self._refresh_btn)

        root_layout.addLayout(top_row)

        # Second row: clipboard buttons (right-aligned). Every button on this surface is
        # TERTIARY and fill=False: an inspector has no single expected verb — these are
        # four equal reads on whatever is selected — and they sit after a stretch, which
        # an expanding button would eat. See gui/CLAUDE.md.
        #
        # Most of them start disabled, and a disabled ActionButton is borderless, so this
        # row is where that reads most strongly: nothing is outlined until a row is picked.
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self._copy_path_btn = ActionButton("copy", "Copy Path", fill=False)
        self._copy_path_btn.setToolTip("Copy selected element's path list to clipboard")
        self._copy_path_btn.setEnabled(False)
        self._copy_path_btn.clicked.connect(self._copy_path)
        btn_row.addWidget(self._copy_path_btn)

        self._load_text_btn = ActionButton("download", "Load Text", fill=False)
        self._load_text_btn.setToolTip(
            "Read the displayed text of the selected element and everything inside it.\n"
            "Text is not loaded with the tree because reading it for every window\n"
            "freezes the dialog — load it for the menu you are looking at instead.\n"
            "Once loaded, the filter box searches the Text column too."
        )
        self._load_text_btn.setEnabled(False)
        self._load_text_btn.clicked.connect(self._on_load_text)
        btn_row.addWidget(self._load_text_btn)

        self._copy_text_btn = ActionButton("copy", "Copy Text", fill=False)
        self._copy_text_btn.setToolTip(
            "Copy the selected element's displayed text to clipboard.\n"
            "Paste it into click_path's Match Text to pick this row out of\n"
            "several that share the same path."
        )
        self._copy_text_btn.setEnabled(False)
        self._copy_text_btn.clicked.connect(self._copy_text)
        btn_row.addWidget(self._copy_text_btn)

        self._copy_all_btn = ActionButton("copy", "Copy All", fill=False)
        self._copy_all_btn.setToolTip("Copy the entire tree as indented text to clipboard")
        self._copy_all_btn.setEnabled(False)
        self._copy_all_btn.clicked.connect(self._copy_all)
        btn_row.addWidget(self._copy_all_btn)

        root_layout.addLayout(btn_row)

        # Tree widget: Element | Visible | Enabled | Grayed.  Sorting stays off —
        # this is a hierarchy, not a flat table like the entity browser.
        self._tree = QTreeWidget()
        self._tree.setColumnCount(5)
        self._tree.setHeaderLabels(["Element", "Text", "Visible", "Enabled", "Grayed"])
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        # Every column is Interactive so the header dividers can be dragged.  Stretch and
        # ResizeToContents both lock a section against user resizing, which is what kept
        # Element and the state columns fixed.  Nothing stretches, so the widths
        # below are what the dialog opens at and the user owns them from there.
        header = self._tree.header()
        header.setStretchLastSection(False)
        for col, width in _DEFAULT_COLUMN_WIDTHS.items():
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self._tree.setColumnWidth(col, width)
        self._tree.setItemDelegate(_StateColorDelegate(self._tree))
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        root_layout.addWidget(self._tree, 1)

        # Status bar
        self._status = ThemedTextLabel("Loading…", css=f"font-size: {THEME.font_small_px}px;")
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
        if has_grayed_field(type_name):
            try:
                grayed = await window.is_control_grayed()
            except Exception:
                grayed = None

        # Window text is deliberately NOT read here.  maybe_text() is another synchronous
        # ReadProcessMemory, and a tree runs to several thousand nodes — reading it for all
        # of them froze the dialog on load.  It is filled in on selection and by Load Text.
        item = QTreeWidgetItem(
            [
                f"[{name}]  {type_name}",
                format_text(None),
                format_state(visible),
                format_state(enabled),
                format_state(grayed),
            ]
        )
        item.setData(_COL_ELEMENT, _TEXT_ROLE, None)
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
        self._load_text_btn.setEnabled(False)
        self._copy_text_btn.setEnabled(False)
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

    def _copy_text(self) -> None:
        items = self._tree.selectedItems()
        if items:
            pyperclip.copy(items[0].data(_COL_ELEMENT, _TEXT_ROLE) or "")

    def _copy_all(self) -> None:
        lines: list[str] = []

        def collect(item: QTreeWidgetItem, depth: int) -> None:
            state = (
                f"visible={item.text(_COL_VISIBLE)} enabled={item.text(_COL_ENABLED)} grayed={item.text(_COL_GRAYED)}"
            )
            window_text = item.data(_COL_ELEMENT, _TEXT_ROLE) or ""
            label = item.text(_COL_ELEMENT) + (f"  text={window_text!r}" if window_text else "")
            lines.append("    " * depth + f"{label}    {state}")
            for i in range(item.childCount()):
                collect(item.child(i), depth + 1)

        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            collect(root.child(i), 0)

        pyperclip.copy("\n".join(lines))

    # ------------------------------------------------------------------
    # Window text, read on demand
    # ------------------------------------------------------------------

    async def _read_item_text(self, item: QTreeWidgetItem) -> bool:
        """Read one item's window text into the Text column. Returns whether it was read.

        A no-op for an item whose text is already loaded, so re-selecting a row or loading
        an already-loaded subtree costs nothing.  maybe_text() reads a per-class offset and
        simply fails on classes that carry no text, which is why it is caught rather than
        treated as an error.
        """
        if self._closed or item.data(_COL_ELEMENT, _TEXT_ROLE) is not None:
            return False
        window = item.data(_COL_ELEMENT, _WINDOW_ROLE)
        if window is None:
            return False
        try:
            text = strip_html(await window.maybe_text() or "")
        except Exception:
            text = ""
        item.setData(_COL_ELEMENT, _TEXT_ROLE, text)
        item.setText(_COL_TEXT, format_text(text))
        if text:
            item.setToolTip(_COL_TEXT, text)
        return True

    def _on_load_text(self) -> None:
        items = self._tree.selectedItems()
        if not items:
            return
        if self._text_task is not None:
            self._text_task.cancel()
        self._text_task = asyncio.ensure_future(self._load_text(items[0]))

    async def _load_text(self, root_item: QTreeWidgetItem) -> None:
        """Read window text for one item and its descendants, up to _TEXT_LOAD_LIMIT."""
        self._load_text_btn.setEnabled(False)
        try:
            items, truncated = subtree_items(root_item)
            for item in items:
                if self._closed:
                    return
                await self._read_item_text(item)
            suffix = f" (stopped at the {_TEXT_LOAD_LIMIT}-element limit)" if truncated else ""
            self._status.setText(f"Text loaded for {len(items)} elements{suffix}")
            self._on_filter_changed()
            if root_item in self._tree.selectedItems():
                self._copy_text_btn.setEnabled(bool(root_item.data(_COL_ELEMENT, _TEXT_ROLE)))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to load window text")
            if not self._closed:
                self._status.setText("Error loading text — see logs")
        finally:
            if not self._closed:
                self._load_text_btn.setEnabled(bool(self._tree.selectedItems()))

    # ------------------------------------------------------------------
    # Rectangle overlay
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        items = self._tree.selectedItems()
        self._copy_path_btn.setEnabled(bool(items))
        self._load_text_btn.setEnabled(bool(items))
        self._copy_text_btn.setEnabled(bool(items) and bool(items[0].data(_COL_ELEMENT, _TEXT_ROLE)))
        if items:
            window = items[0].data(_COL_ELEMENT, _WINDOW_ROLE)
            if window is not None:
                if self._overlay_task is not None:
                    self._overlay_task.cancel()
                # One text read rides along with the overlay's own read, so the selected
                # row always shows its text and Copy Text is usable without a second click.
                self._overlay_task = asyncio.ensure_future(self._on_select_async(items[0], window))
                return
        self._overlay.hide()

    async def _on_select_async(self, item: QTreeWidgetItem, window) -> None:
        await self._read_item_text(item)
        if self._closed:
            return
        if item in self._tree.selectedItems():
            self._copy_text_btn.setEnabled(bool(item.data(_COL_ELEMENT, _TEXT_ROLE)))
        await self._update_overlay(window)

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
        if self._text_task is not None:
            self._text_task.cancel()
        self._overlay.hide()
        self._overlay.deleteLater()
        super().closeEvent(event)
