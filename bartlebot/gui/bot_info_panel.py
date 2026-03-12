"""Left sidebar: bot metadata (read-only) and bot-wide options (editable)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFrame, QFormLayout,
    QLabel, QLineEdit, QScrollArea, QSizePolicy,
    QSpinBox, QVBoxLayout, QWidget,
)

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_PLACEHOLDER = "—"


class BotInfoPanel(QScrollArea):
    """Fixed-width left sidebar showing bot metadata and bot-wide option controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._option_rows: list[QWidget] = []
        self._option_widgets: dict[str, QWidget] = {}

        content = QWidget()
        self._outer = QVBoxLayout(content)
        self._outer.setContentsMargins(8, 8, 8, 8)
        self._outer.setSpacing(4)

        # ── Bot Info ──────────────────────────────────────────────────
        self._outer.addWidget(_section_header("Bot Info"))

        form = QFormLayout()
        form.setSpacing(2)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self._file_lbl = _value_label(wrap=True)
        self._name_lbl = _value_label()
        self._version_lbl = _value_label()
        self._desc_lbl = _value_label(wrap=True)
        self._start_lbl = _value_label(wrap=True)
        self._credits_lbl = _value_label(wrap=True)
        self._drops_lbl = _value_label(wrap=True)

        form.addRow(_field_label("File"), self._file_lbl)
        form.addRow(_field_label("Name"), self._name_lbl)
        form.addRow(_field_label("Version"), self._version_lbl)
        form.addRow(_field_label("Description"), self._desc_lbl)
        form.addRow(_field_label("Start"), self._start_lbl)
        form.addRow(_field_label("Credits"), self._credits_lbl)
        form.addRow(_field_label("Drops"), self._drops_lbl)
        self._outer.addLayout(form)

        # ── Divider ───────────────────────────────────────────────────
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setFrameShadow(QFrame.Shadow.Sunken)
        self._outer.addWidget(div)

        # ── Options ───────────────────────────────────────────────────
        self._outer.addWidget(_section_header("Options"))

        self._options_container = QWidget()
        self._options_layout = QVBoxLayout(self._options_container)
        self._options_layout.setContentsMargins(0, 0, 0, 0)
        self._options_layout.setSpacing(4)

        self._no_options_lbl = _value_label()
        self._no_options_lbl.setText(_PLACEHOLDER)
        self._options_layout.addWidget(self._no_options_lbl)

        self._outer.addWidget(self._options_container)
        self._outer.addStretch()

        self.setWidget(content)
        self.setWidgetResizable(True)
        self.setMinimumWidth(190)

        self.set_config(None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_file(self, filename: str | None) -> None:
        """Update the File field (just the basename, not the full path)."""
        self._file_lbl.setText(filename or _PLACEHOLDER)

    def set_config(self, config: BotConfig | None) -> None:
        """Populate the panel from a loaded BotConfig, or show placeholders."""
        if config is None:
            for lbl in (
                self._name_lbl, self._version_lbl, self._desc_lbl,
                self._start_lbl, self._credits_lbl, self._drops_lbl,
            ):
                lbl.setText(_PLACEHOLDER)
            self._rebuild_options([])
            return

        self._name_lbl.setText(config.name or _PLACEHOLDER)
        self._version_lbl.setText(config.version or _PLACEHOLDER)
        self._desc_lbl.setText(config.description or _PLACEHOLDER)
        self._start_lbl.setText(config.start_location or _PLACEHOLDER)
        self._credits_lbl.setText(config.credits or _PLACEHOLDER)
        self._drops_lbl.setText(", ".join(config.drops) if config.drops else _PLACEHOLDER)
        self._rebuild_options(config.options)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rebuild_options(self, options: list) -> None:
        for row in self._option_rows:
            self._options_layout.removeWidget(row)
            row.deleteLater()
        self._option_rows.clear()
        self._option_widgets.clear()

        if not options:
            self._no_options_lbl.show()
            return

        self._no_options_lbl.hide()
        for opt in options:
            row, widget = _build_option_row(opt)
            self._options_layout.addWidget(row)
            self._option_rows.append(row)
            self._option_widgets[opt.name] = widget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _section_header(text: str) -> QLabel:
    lbl = QLabel(text)
    font = QFont()
    font.setBold(True)
    lbl.setFont(font)
    return lbl


def _field_label(text: str) -> QLabel:
    lbl = QLabel(text + ":")
    lbl.setStyleSheet("color: #888; font-size: 10px;")
    lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
    return lbl


def _value_label(wrap: bool = False) -> QLabel:
    lbl = QLabel(_PLACEHOLDER)
    lbl.setWordWrap(wrap)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


def _build_option_row(opt) -> tuple[QWidget, QWidget]:
    """Return (container_widget, input_widget) for a single BotOptionModel."""
    row = QWidget()
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(1)
    tooltip = opt.description or ""

    if opt.type == "bool":
        widget = QCheckBox(opt.name)
        widget.setChecked(bool(opt.default))
        if tooltip:
            widget.setToolTip(tooltip)
        layout.addWidget(widget)
    else:
        lbl = _field_label(opt.name)
        if tooltip:
            lbl.setToolTip(tooltip)
        layout.addWidget(lbl)

        if opt.type == "int":
            widget = QSpinBox()
            widget.setRange(-2_147_483_648, 2_147_483_647)
            widget.setValue(int(opt.default))
        elif opt.type == "float":
            widget = QDoubleSpinBox()
            widget.setDecimals(4)
            widget.setRange(-1e12, 1e12)
            widget.setValue(float(opt.default))
        else:  # str
            widget = QLineEdit(str(opt.default))

        if tooltip:
            widget.setToolTip(tooltip)
        layout.addWidget(widget)

    return row, widget
