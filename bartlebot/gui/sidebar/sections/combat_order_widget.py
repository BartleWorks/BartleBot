"""CombatOrderWidget — drag-and-drop combat entry order selector."""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, QEvent, QMimeData, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QDrag
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from bartlebot.gui.account_accent import account_accent, account_tint
from bartlebot.gui.theme import THEME

_CHIP_MIME = "application/x-bartlebot-combat-order-chip"
_ORDINALS = ["1st", "2nd", "3rd", "4th"]


def _ordinal(n: int) -> str:
    return _ORDINALS[n - 1] if 1 <= n <= len(_ORDINALS) else f"{n}th"


class _PlayerChip(QFrame):
    """Draggable P{n} chip for CombatOrderWidget. Follows _SlotButton DnD pattern."""

    def __init__(self, player_num: int, widget: CombatOrderWidget, color_key: int | None = None) -> None:
        super().__init__()
        self._player_num = player_num
        self._widget = widget
        self._drag_start_pos: QPoint | None = None
        self._drop_highlight = False
        self._enabled_visual = True
        # The account currently in this slot, as a palette slot rather than a colour, so a
        # theme switch re-resolves. ``None`` is the normal case — nothing hooked — and
        # draws the chip exactly as it always did. See gui/account_accent.py.
        self._color_key = color_key
        self.setAcceptDrops(True)
        self.setFixedHeight(28)
        self.setMinimumWidth(40)
        self._label = QLabel(f"P{player_num}")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(self._label)
        self._refresh_style()

    def _refresh_style(self) -> None:
        if not self._enabled_visual:
            # Matches the app-wide disabled palette (surface_disabled /
            # text_disabled) rather than inventing a third "off" look — these
            # chips sit inches from real disabled buttons in the combat tab.
            bg = THEME.surface_disabled
            text_color = THEME.text_disabled
            border = THEME.surface_disabled
            border_width = "1px"
        else:
            # Tinted and outlined in the account's colour rather than filled with it. That
            # is the app's whole convention for account colour -- see ``AccountPill`` --
            # and it matters more here than anywhere: four saturated fills in a row would
            # make the combat order the loudest thing on the panel, when what it is
            # actually saying is an *order*, which the positions already say.
            tinted = self._color_key is not None and not self._drop_highlight
            bg = account_tint(self._color_key) if tinted else THEME.surface
            text_color = THEME.text
            if self._drop_highlight:
                border, border_width = THEME.accent, "2px"
            elif self._color_key is not None:
                border, border_width = account_accent(self._color_key), "1px"
            else:
                border, border_width = THEME.border, "1px"
        self.setStyleSheet(f"QFrame {{ background: {bg}; border: {border_width} solid {border}; border-radius: 4px; }}")
        self._label.setStyleSheet(f"color: {text_color}; font-size: 11px; font-weight: 600; border: none;")

    def set_enabled_visual(self, enabled: bool) -> None:
        self._enabled_visual = enabled
        self.setCursor(Qt.CursorShape.OpenHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self._refresh_style()

    # ------------------------------------------------------------------
    # Drag source
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.MouseButton.LeftButton and self._widget._drag_enabled:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if (
            self._drag_start_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and self._widget._drag_enabled
            and (event.pos() - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance()
        ):
            self._start_drag()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _start_drag(self) -> None:
        self._drag_start_pos = None
        mime = QMimeData()
        mime.setData(_CHIP_MIME, QByteArray(str(self._player_num).encode()))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(self.rect().center())
        drag.exec(Qt.DropAction.MoveAction)

    # ------------------------------------------------------------------
    # Drop target
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001
        if event.mimeData().hasFormat(_CHIP_MIME):
            src_player = int(bytes(event.mimeData().data(_CHIP_MIME)).decode())
            if src_player != self._player_num:
                self._drop_highlight = True
                self._refresh_style()
                event.acceptProposedAction()
                return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001
        self._drop_highlight = False
        self._refresh_style()

    def dropEvent(self, event) -> None:  # noqa: ANN001
        self._drop_highlight = False
        self._refresh_style()
        if event.mimeData().hasFormat(_CHIP_MIME):
            src_player = int(bytes(event.mimeData().data(_CHIP_MIME)).decode())
            if src_player != self._player_num:
                self._widget._swap_players(src_player, self._player_num)
            event.acceptProposedAction()


class CombatOrderWidget(QWidget):
    """Two-row slot widget for specifying combat entry order.

    Top row: ordinal slot labels ("1st", "2nd", ...).
    Bottom row: draggable P-chips reorderable by drag-and-drop.
    A checkbox above the grid represents the empty-order state
    (simultaneous entry or inherit-from-profile, depending on context).

    get_value() returns [] when the checkbox is checked (empty order).
    set_value([]) checks the checkbox; any non-empty list unchecks it and
    arranges the chips. Partial orders are padded with missing players in
    ascending order so the chip row always shows max_players chips.
    """

    changed = pyqtSignal()

    def __init__(self, empty_label: str = "Simultaneous", max_players: int = 4, parent=None) -> None:
        super().__init__(parent)
        self._max_players = max_players
        self._order: list[int] = list(range(1, max_players + 1))
        self._editable = True
        self._drag_enabled = True
        self._suppress = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # Row 1: checkbox (checked = empty order), and whatever the embedder puts beside
        # it. The row is a layout rather than a bare widget so that a surface can hang its
        # own controls off the right-hand end -- the duel editor's Min/Max players belong
        # on the same line as "Simultaneous entry", being the other half of the same
        # question. Offered as :meth:`add_header_widget` rather than by handing out the
        # checkbox, so this widget keeps owning what it built.
        self._header_layout = QHBoxLayout()
        self._header_layout.setContentsMargins(0, 0, 0, 0)
        self._header_layout.setSpacing(6)
        self._checkbox = QCheckBox(empty_label)
        self._checkbox.toggled.connect(self._on_checkbox_toggled)
        self._header_layout.addWidget(self._checkbox)
        self._header_layout.addStretch(1)
        outer.addLayout(self._header_layout)

        # Row 2: ordinal header labels
        self._header_row = QWidget()
        header_layout = QHBoxLayout(self._header_row)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        self._header_labels: list[QLabel] = []
        for i in range(1, max_players + 1):
            lbl = QLabel(_ordinal(i))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 10px;")
            header_layout.addWidget(lbl, 1)
            self._header_labels.append(lbl)
        outer.addWidget(self._header_row)

        # Row 3: draggable chip row
        self._chip_row = QWidget()
        self._chip_layout = QHBoxLayout(self._chip_row)
        self._chip_layout.setContentsMargins(0, 0, 0, 0)
        self._chip_layout.setSpacing(4)
        self._chips: list[_PlayerChip] = []
        # A palette slot per player, or empty when nothing is hooked. Held rather than
        # applied, because the chips are rebuilt on every reorder and every max-players
        # change and each rebuild has to arrive already coloured.
        self._color_keys: list[int | None] = []
        outer.addWidget(self._chip_row)

        self._rebuild_chips()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_player_color_keys(self, keys: list[int | None]) -> None:
        """Colour each chip by the account currently in that player slot.

        Shorter lists are the normal case and are fine: two clients hooked colours P1 and
        P2 and leaves the rest plain, which is the truth about what is running.
        """
        self._color_keys = list(keys)
        self._rebuild_chips()

    def _color_key_for(self, player_num: int) -> int | None:
        index = player_num - 1
        return self._color_keys[index] if 0 <= index < len(self._color_keys) else None

    def add_header_widget(self, widget: QWidget) -> None:
        """Add *widget* to the right-hand end of the row the empty-order checkbox is on.

        For a surface whose own controls answer the same question this widget does — the
        duel editor's Min/Max players spin boxes, which say how many players there are
        while the checkbox and the chips say in what order they enter. Putting them here
        rather than in a row of the embedder's own is what keeps the two aligned when the
        chip grid is hidden, which is the state the checkbox exists to produce.
        """
        self._header_layout.addWidget(widget)

    def get_value(self) -> list[int]:
        """Return current order, or [] if the checkbox is checked."""
        if self._checkbox.isChecked():
            return []
        return list(self._order)

    def set_value(self, order: list[int]) -> None:
        """Set the displayed order. [] checks the checkbox; non-empty unchecks it."""
        self._suppress = True
        self._checkbox.blockSignals(True)
        if not order:
            self._checkbox.setChecked(True)
            self._drag_enabled = False
        else:
            # Normalize to a full-length permutation of 1..max_players.
            seen: set[int] = set()
            valid: list[int] = []
            for n in order:
                if 1 <= n <= self._max_players and n not in seen:
                    seen.add(n)
                    valid.append(n)
            missing = [n for n in range(1, self._max_players + 1) if n not in seen]
            self._order = valid + missing
            self._checkbox.setChecked(False)
            self._drag_enabled = self._editable
        self._checkbox.blockSignals(False)
        checked = self._checkbox.isChecked()
        self._header_row.setVisible(not checked)
        self._chip_row.setVisible(not checked)
        self._rebuild_chips()
        self._suppress = False

    def set_max_players(self, n: int) -> None:
        """Update the number of player slots shown. Clamps any out-of-range players from the order."""
        if n == self._max_players:
            return
        self._max_players = n
        for i, lbl in enumerate(self._header_labels):
            lbl.setVisible(i < n)
        self._order = [p for p in self._order if p <= n]
        missing = [p for p in range(1, n + 1) if p not in self._order]
        self._order = self._order + missing
        self._rebuild_chips()

    def set_editable(self, enabled: bool) -> None:
        self._editable = enabled
        self._checkbox.setEnabled(enabled)
        self._drag_enabled = enabled and not self._checkbox.isChecked()
        for chip in self._chips:
            chip.set_enabled_visual(self._drag_enabled)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rebuild_chips(self) -> None:
        for chip in self._chips:
            # Unparented, not merely removed from the layout. ``removeWidget`` takes a
            # widget out of the layout but leaves it a child of this one, so it keeps
            # painting at its last geometry until the deferred delete runs -- which shows
            # up as ghost chips overlapping the new row for a frame or more. Reparenting
            # to None takes it off the screen in the same breath.
            self._chip_layout.removeWidget(chip)
            chip.setParent(None)
            chip.deleteLater()
        self._chips.clear()
        enabled = self._drag_enabled
        for player_num in self._order:
            chip = _PlayerChip(player_num, self, self._color_key_for(player_num))
            chip.set_enabled_visual(enabled)
            self._chip_layout.addWidget(chip, 1)
            self._chips.append(chip)

    def _swap_players(self, src_player: int, dst_player: int) -> None:
        try:
            i = self._order.index(src_player)
            j = self._order.index(dst_player)
        except ValueError:
            return
        self._order[i], self._order[j] = self._order[j], self._order[i]
        self._rebuild_chips()
        self.changed.emit()

    def _on_checkbox_toggled(self, checked: bool) -> None:
        if self._suppress:
            return
        self._drag_enabled = self._editable and not checked
        self._header_row.setVisible(not checked)
        self._chip_row.setVisible(not checked)
        for chip in self._chips:
            chip.set_enabled_visual(self._drag_enabled)
        self.changed.emit()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            for lbl in self._header_labels:
                lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 10px;")
            for chip in self._chips:
                chip._refresh_style()
