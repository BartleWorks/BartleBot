"""FavoritesBar — 10-slot hotkey quick-add strip for the bot canvas.

Each slot corresponds to a bare number key (1–9 then 0). Left-click fires
node_requested; right-click opens a context menu to assign or clear the slot.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMenu,
    QPushButton,
    QWidget,
)

from bartlebot.gui.theme import THEME
from bartlebot.nodes.registry import NODE_SPECS
from bartlebot.settings_manager import SettingsManager


def _slot_key_label(slot: int) -> str:
    """Return the display key character for a 0-based slot index."""
    return str(slot + 1) if slot < 9 else "0"


def _humanize(node_type: str) -> str:
    return node_type.replace("_", " ").title()


class _SlotButton(QPushButton):
    def __init__(self, slot: int, bar: FavoritesBar) -> None:
        super().__init__()
        self._slot = slot
        self._bar = bar
        self._node_type = ""
        self._key = _slot_key_label(slot)
        self.setFixedHeight(26)
        self.setMinimumWidth(80)
        self.set_node_type("")
        self.clicked.connect(self._on_click)

    def set_node_type(self, node_type: str) -> None:
        self._node_type = node_type
        key = self._key
        if node_type:
            label = f"{key}  {_humanize(node_type)}"
            spec = NODE_SPECS.get(node_type)
            tip = f"[{key}] {_humanize(node_type)}"
            if spec and spec.description:
                tip += f"\n{spec.description}"
            self.setToolTip(tip)
        else:
            label = f"{key}  —"
            self.setToolTip(f"[{key}] No node assigned — right-click to assign")
        self.setText(label)
        self._refresh_style()

    def _refresh_style(self) -> None:
        filled = bool(self._node_type)
        bg = THEME.surface if filled else THEME.bg
        text_color = THEME.text if filled else THEME.text_muted
        self.setStyleSheet(
            f"QPushButton {{ background: {bg}; color: {text_color}; border: 1px solid {THEME.border}; "
            f"border-radius: 4px; padding: 1px 6px; font-size: 11px; text-align: left; }}"
            f"QPushButton:hover {{ background: {THEME.node_fill_hover}; }}"
            f"QPushButton:pressed {{ background: {THEME.node_fill_selected}; }}"
        )

    def _on_click(self) -> None:
        if self._node_type:
            self._bar.node_requested.emit(self._node_type)

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001
        menu = QMenu(self)
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
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(4)
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
