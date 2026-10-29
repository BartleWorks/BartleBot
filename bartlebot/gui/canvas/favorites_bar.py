"""FavoritesBar — 10-slot hotkey quick-add strip for the bot canvas.

Each slot corresponds to a bare number key (1–9 then 0). Left-click fires
node_requested; right-click opens a context menu to assign or clear the slot.
Slots can be rearranged by dragging one button onto another.
"""

from __future__ import annotations

from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin
from PyQt6.QtCore import QByteArray, QEvent, QMimeData, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QDrag
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from bartlebot.gui.menu_utils import popup_menu
from bartlebot.nodes.registry import NODE_SPECS, node_display_name
from bartlebot.settings_manager import SettingsManager

_SLOT_MIME = "application/x-bartlebot-favorites-slot"
# Gap between the slots, and the strip's margin on every side -- the same number,
# deliberately: see ``FavoritesBar.__init__``.
_SLOT_GAP = 4


def _slot_key_label(slot: int) -> str:
    """Return the display key character for a 0-based slot index."""
    return str(slot + 1) if slot < 9 else "0"


class _SlotButton(ThemedMixin, QPushButton):
    def __init__(self, slot: int, bar: FavoritesBar) -> None:
        super().__init__()
        self._slot = slot
        self._bar = bar
        self._node_type = ""
        self._key = _slot_key_label(slot)
        self._drag_start_pos: QPoint | None = None
        self._did_drag = False
        self._drop_highlight = False
        self.setFixedHeight(26)
        self.setMinimumWidth(80)
        self.setAcceptDrops(True)
        self.set_node_type("")
        self.clicked.connect(self._on_click)

    def set_node_type(self, node_type: str) -> None:
        self._node_type = node_type
        key = self._key
        if node_type:
            label = f"{key}  {node_display_name(node_type)}"
            spec = NODE_SPECS.get(node_type)
            tip = f"[{key}] {node_display_name(node_type)}"
            if spec and spec.description:
                tip += f"\n{spec.description}"
            tip += "\n\nDrag to rearrange"
            self.setToolTip(tip)
        else:
            label = f"{key}  —"
            self.setToolTip(f"[{key}] No node assigned — right-click to assign")
        self.setText(label)
        self.refresh_theme_styles()

    def apply_theme_styles(self) -> None:
        filled = bool(self._node_type)
        bg = THEME.surface if filled else THEME.bg
        text_color = THEME.text if filled else THEME.text_muted
        border_color = THEME.accent if self._drop_highlight else THEME.border
        border_width = "2px" if self._drop_highlight else "1px"
        self.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: {text_color}; "
            f"border: {border_width} solid {border_color}; "
            f"border-radius: 4px; padding: 1px 6px; font-size: {THEME.font_small_px}px; text-align: left; }}"
            f"QPushButton:hover {{ background: {THEME.node_fill_hover}; }}"
            f"QPushButton:pressed {{ background: {THEME.node_fill_selected}; }}"
        )

    def _on_click(self) -> None:
        if not self._did_drag and self._node_type:
            self._bar.node_requested.emit(self._node_type)

    # ------------------------------------------------------------------
    # Drag source
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton and self._node_type:
            self._drag_start_pos = event.pos()
        self._did_drag = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if (
            self._drag_start_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and self._node_type
            and (event.pos() - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance()
        ):
            self._start_drag()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _start_drag(self) -> None:
        self._drag_start_pos = None
        self._did_drag = True
        mime = QMimeData()
        mime.setData(_SLOT_MIME, QByteArray(str(self._slot).encode()))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(self.rect().center())
        drag.exec(Qt.DropAction.MoveAction)

    # ------------------------------------------------------------------
    # Drop target
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001
        if event.mimeData().hasFormat(_SLOT_MIME):
            src_slot = int(bytes(event.mimeData().data(_SLOT_MIME)).decode())
            if src_slot != self._slot:
                self._drop_highlight = True
                self.refresh_theme_styles()
                event.acceptProposedAction()
                return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001
        self._drop_highlight = False
        self.refresh_theme_styles()

    def dropEvent(self, event) -> None:  # noqa: ANN001
        self._drop_highlight = False
        self.refresh_theme_styles()
        if event.mimeData().hasFormat(_SLOT_MIME):
            src_slot = int(bytes(event.mimeData().data(_SLOT_MIME)).decode())
            dst_slot = self._slot
            if src_slot != dst_slot:
                sm = SettingsManager.instance()
                slots = sm.favorited_hotkey_nodes
                slots[src_slot], slots[dst_slot] = slots[dst_slot], slots[src_slot]
                sm.set("favorited_hotkey_nodes", value=slots)
                sm.save()
                self._bar.refresh()
            event.acceptProposedAction()

    # ------------------------------------------------------------------
    # Context menu (assign / clear)
    # ------------------------------------------------------------------

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001
        menu = popup_menu(self)
        assign_act = menu.addAction("Assign node…")
        menu.addSeparator()
        clear_act = menu.addAction("Clear")
        clear_act.setEnabled(bool(self._node_type))

        action = menu.exec(event.globalPos())
        if action == assign_act:
            self._assign()
        elif action == clear_act:
            SettingsManager.instance().clear_favorited_hotkey_node(self._slot)
            self._bar.refresh()

    def _assign(self) -> None:
        from PyQt6.QtWidgets import QGraphicsView  # noqa: PLC0415

        from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog  # noqa: PLC0415

        # Walk up the widget tree to find a QGraphicsView for use as dialog parent.
        view = None
        widget: QWidget | None = self
        while widget is not None:
            if isinstance(widget, QGraphicsView):
                view = widget
                break
            widget = widget.parentWidget()

        dlg = NodeBrowserDialog(view, mode="add")
        if dlg.exec() and dlg.selected_node_type:
            SettingsManager.instance().set_favorited_hotkey_node(self._slot, dlg.selected_node_type)
            self._bar.refresh()


class FavoritesBar(QWidget):
    """Horizontal strip of 10 node-type hotkey slots (keys 1–9, 0)."""

    node_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        # The margin matches the gap between the slots on every side: a strip whose
        # slots sit closer to the toolbar above them than to each other reads as
        # crowded upwards rather than as one row of equal things.
        row.setContentsMargins(_SLOT_GAP, _SLOT_GAP, _SLOT_GAP, _SLOT_GAP)
        row.setSpacing(_SLOT_GAP)
        self._slots: list[_SlotButton] = []
        for i in range(10):
            btn = _SlotButton(i, self)
            self._slots.append(btn)
            row.addWidget(btn)
        self.refresh()

    def refresh(self) -> None:
        """Re-read settings and update all slot button labels."""
        hotkeys = SettingsManager.instance().favorited_hotkey_nodes
        for i, btn in enumerate(self._slots):
            btn.set_node_type(hotkeys[i] if i < len(hotkeys) else "")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            for btn in self._slots:
                btn.refresh_theme_styles()
