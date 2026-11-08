"""The properties panel's region page: a plain region's label, color, and Collapse / Expand.

A plain region is reached by selecting a whole region's members, the pill click, or its
collapsed node; a subgraph instance has the call node page instead. ``param_panel._ParamPanel``
shows it in its stack.
"""

from bartleby.qt.theming.theme import THEME
from bartleby.qt.theming.themed import ThemedMixin
from bartleby.qt.widgets.buttons.action_button import ActionButton
from bartleby.qt.widgets.labels.themed_label import ThemedTextLabel
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import RegionModel


class _RegionColorRow(ThemedMixin, QWidget):
    """The six region swatches inline, the selected one ringed in the accent."""

    changed = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._key: int = THEME.REGION_PALETTE[0]
        self._btns: dict[int, QPushButton] = {}
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for key in THEME.REGION_PALETTE:
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"Region color {key}")
            btn.clicked.connect(lambda _=None, k=key: self._on_clicked(k))
            self._btns[key] = btn
            row.addWidget(btn)
        row.addStretch()
        self.refresh_theme_styles()

    @property
    def key(self) -> int:
        return self._key

    def set_key(self, key: int) -> None:
        self._key = key
        self.refresh_theme_styles()

    def _on_clicked(self, key: int) -> None:
        if key == self._key:
            return
        self.set_key(key)
        self.changed.emit(key)

    def apply_theme_styles(self) -> None:
        for key, btn in self._btns.items():
            color_hex = THEME.region_color(key).name()
            border = THEME.accent if key == self._key else "transparent"
            btn.setStyleSheet(
                f"QPushButton {{ background: {color_hex}; border: 2px solid {border}; border-radius: 11px; }}"
                f"QPushButton:hover {{ border-color: {THEME.text_muted}; }}"
            )


class RegionPage(QWidget):
    """One plain region's page. Edits the shown ``RegionModel`` in place.

    The signals are the panel's own, under the same names, which forwards each one: see
    ``_ParamPanel`` for what each means.
    """

    edit_starting = pyqtSignal(str)
    structural_property_changed = pyqtSignal(str)
    region_property_changed = pyqtSignal()
    region_collapse_requested = pyqtSignal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._region: RegionModel | None = None
        self._suppress: bool = False

        region_vbox = QVBoxLayout(self)
        region_vbox.setContentsMargins(0, 0, 0, 0)
        region_vbox.setSpacing(0)
        region_form_w = QWidget()
        rf = QFormLayout(region_form_w)
        rf.setContentsMargins(6, 6, 6, 6)
        self._region_label_edit = QLineEdit()
        self._region_color_row = _RegionColorRow()
        rf.addRow("Label", self._region_label_edit)
        rf.addRow("Color", self._region_color_row)
        region_vbox.addWidget(region_form_w)
        # Collapsing draws the region as one node and closes up the space it used.
        self._region_fold_note = ThemedTextLabel("", color="text_muted")
        self._region_fold_note.setWordWrap(True)
        self._region_fold_note.setContentsMargins(6, 0, 6, 0)
        self._region_collapse_btn = ActionButton("region_collapse", "Collapse", fill=False)
        region_actions = QWidget()
        region_actions_row = QHBoxLayout(region_actions)
        region_actions_row.setContentsMargins(6, 8, 6, 0)
        region_actions_row.addStretch(1)
        region_actions_row.addWidget(self._region_collapse_btn)
        region_vbox.addWidget(self._region_fold_note)
        region_vbox.addWidget(region_actions)
        self._region_collapse_btn.clicked.connect(self._on_region_collapse_clicked)
        self._region_collapsed = False  # as drawn, for the button's next click
        region_vbox.addStretch()

        self._region_label_edit.textChanged.connect(self._on_region_label_changed)
        self._region_color_row.changed.connect(self._on_region_color_changed)

    @property
    def region(self) -> RegionModel | None:
        """The region on the page, or None once the panel has moved to another page."""
        return self._region

    def clear(self) -> None:
        """Let go of the shown region: the panel is showing another page."""
        self._region = None

    def show_region(
        self,
        region: RegionModel,
        *,
        collapsed: bool | None = None,
        collapse_blocker: str | None = None,
        fights: list[str] | None = None,
    ) -> None:
        """Show a plain region's page: its label, color, and Collapse / Expand. Edits ``region`` in place.

        ``collapsed`` is whether it is drawn collapsed, which in Run mode can differ
        from its saved flag; it defaults to the flag. ``collapse_blocker`` is why the
        region cannot collapse, shown on the disabled button. ``fights`` are the fights a collapsed region holds, named because they
        are out of sight on the canvas.
        """
        self._region = region
        self._suppress = True
        try:
            self._region_label_edit.setText(region.label)
            self._region_color_row.set_key(region.color)
            collapsed = bool(region.collapsed) if collapsed is None else collapsed
            self._region_collapsed = collapsed
            self._region_collapse_btn.setText("Expand" if collapsed else "Collapse")
            self._region_collapse_btn.set_icon_name("region_expand" if collapsed else "region_collapse")
            self._region_collapse_btn.setEnabled(collapsed or collapse_blocker is None)
            self._region_collapse_btn.setToolTip(
                "Draw the region's nodes again"
                if collapsed
                else (collapse_blocker or "Draw the region as one node and close up the space it uses")
            )
            lines = []
            if collapsed:
                lines.append("Drawn as one node. Double-click it to step in.")
                if fights:
                    lines.append(f"Fights inside: {', '.join(fights)}. Their strategies are set on the Combat tab.")
            self._region_fold_note.setText("\n".join(lines))
            self._region_fold_note.setVisible(bool(lines))
        finally:
            self._suppress = False

    def _on_region_collapse_clicked(self) -> None:
        if self._suppress or self._region is None:
            return
        self.region_collapse_requested.emit(self._region.id, not self._region_collapsed)

    @property
    def region_collapse_button(self) -> ActionButton:
        return self._region_collapse_btn

    def _on_region_label_changed(self, text: str) -> None:
        if self._suppress or self._region is None:
            return
        label = text.strip()
        if not label:
            return
        self.edit_starting.emit("Edit region")
        self._region.label = label
        self.region_property_changed.emit()

    def _on_region_color_changed(self, key: int) -> None:
        if self._suppress or self._region is None:
            return
        self.structural_property_changed.emit("Change Region Color")
        self._region.color = key
        self.region_property_changed.emit()

    @property
    def region_label_edit(self) -> QLineEdit:
        return self._region_label_edit

    @property
    def region_color_row(self) -> _RegionColorRow:
        return self._region_color_row
