"""Left sidebar: bot metadata (editable) and bot-wide options."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QScrollArea, QSizePolicy,
    QSpinBox, QVBoxLayout, QWidget,
)

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_PLACEHOLDER = "—"


class SectionHeader(QWidget):

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, parent=None):
        super().__init__(parent)

        self._expanded = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.arrow = QLabel("▼")
        self.arrow.setFixedWidth(12)

        self.label = QLabel(title)

        font = self.label.font()
        font.setBold(True)
        self.label.setFont(font)

        layout.addWidget(self.arrow)
        layout.addWidget(self.label)
        layout.addStretch()

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):  # noqa: ARG002
        self._expanded = not self._expanded
        self.arrow.setText("▼" if self._expanded else "▶")
        self.toggled.emit(self._expanded)


class CollapsibleSection(QWidget):
    """A section with a clickable header that shows/hides its body."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(2)

        self._header = SectionHeader(title)
        self._body = QWidget()
        self._body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        vbox.addWidget(self._header)
        vbox.addWidget(self._body)

        self._header.toggled.connect(self._body.setVisible)

    def set_content_layout(self, layout) -> None:
        self._body.setLayout(layout)

    @property
    def body(self) -> QWidget:
        return self._body


class BotInfoPanel(QScrollArea):
    """Fixed-width left sidebar showing bot metadata and bot-wide option controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config = None
        self._suppress: bool = False
        self._option_rows: list[QWidget] = []
        self._option_widgets: dict[str, QWidget] = {}
        self.on_config_changed: Callable[[], None] = lambda: None

        content = QWidget()
        self._outer = QVBoxLayout(content)
        self._outer.setContentsMargins(8, 8, 8, 8)
        self._outer.setSpacing(4)

        # ── Bot Info ──────────────────────────────────────────────────
        info_section = CollapsibleSection("Bot Info")

        form = QFormLayout()
        form.setSpacing(2)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # File is set externally (not part of the config) — stays a label
        self._file_lbl = _value_label(wrap=True)
        self._file_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        self._name_edit = _value_edit()
        self._version_edit = _value_edit()
        self._desc_edit = _value_edit_multi(lines=3)
        self._start_edit = _value_edit()
        self._authors_edit = _value_edit()
        self._drops_edit = _value_edit()
        self._drops_edit.setToolTip("Comma-separated list of expected drop items")

        self._initial_combo = QComboBox()
        self._initial_combo.setEnabled(False)

        form.addRow(_field_label("File"), self._file_lbl)
        form.addRow(_field_label("Name"), self._name_edit)
        form.addRow(_field_label("Version"), self._version_edit)
        form.addRow(_field_label("Description"), self._desc_edit)
        form.addRow(_field_label("Start"), self._start_edit)
        form.addRow(_field_label("Author(s)"), self._authors_edit)
        form.addRow(_field_label("Drops"), self._drops_edit)
        form.addRow(_field_label("Initial State"), self._initial_combo)

        info_section.set_content_layout(form)
        self._outer.addWidget(info_section)

        # ── Options ───────────────────────────────────────────────────
        self._options_section = CollapsibleSection("Options")

        self._options_layout = QVBoxLayout()
        self._options_layout.setContentsMargins(0, 0, 0, 0)
        self._options_layout.setSpacing(4)

        self._no_options_lbl = _value_label()
        self._no_options_lbl.setText(_PLACEHOLDER)
        self._options_layout.addWidget(self._no_options_lbl)

        self._options_section.set_content_layout(self._options_layout)
        self._outer.addWidget(self._options_section)

        self._outer.addStretch()

        self.setWidget(content)
        self.setWidgetResizable(True)
        self.setMinimumWidth(300)

        # Connect change signals
        self._name_edit.textChanged.connect(self._on_name_changed)
        self._version_edit.textChanged.connect(self._on_version_changed)
        self._desc_edit.textChanged.connect(self._on_desc_changed)
        self._start_edit.textChanged.connect(self._on_start_changed)
        self._authors_edit.textChanged.connect(self._on_authors_changed)
        self._drops_edit.textChanged.connect(self._on_drops_changed)
        self._initial_combo.currentTextChanged.connect(self._on_initial_changed)

        self.set_config(None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_file(self, filename: str | None) -> None:
        """Update the File field (just the basename, not the full path)."""
        self._file_lbl.setText(filename or _PLACEHOLDER)

    def set_config(self, config: BotConfig | None) -> None:
        """Populate the panel from a BotConfig and make fields editable,
        or show placeholders and lock fields when config is None."""
        self._config = config
        self._suppress = True
        line_edits = (
            self._name_edit, self._version_edit,
            self._start_edit, self._authors_edit, self._drops_edit,
        )
        if config is None:
            for w in line_edits:
                w.setText("")
                w.setReadOnly(True)
            self._desc_edit.setPlainText("")
            self._desc_edit.setReadOnly(True)
            self._initial_combo.clear()
            self._initial_combo.setEnabled(False)
            self._rebuild_options([])
        else:
            self._name_edit.setText(config.name or "")
            self._version_edit.setText(config.version or "")
            self._start_edit.setText(config.start_location or "")
            self._authors_edit.setText(config.authors or "")
            self._drops_edit.setText(", ".join(config.drops) if config.drops else "")
            self._desc_edit.setPlainText(config.description or "")
            self._populate_initial_combo(config.states, config.initial)
            self._rebuild_options(config.options)
            # Fields remain read-only; set_editable(True) enables them in EDIT mode
        self._suppress = False

    def refresh_states(self, states: list) -> None:
        """Rebuild the Initial State combo to reflect added/removed states."""
        if self._config is None:
            return
        self._populate_initial_combo(states, self._config.initial)

    def set_editable(self, editable: bool) -> None:
        """Toggle all metadata fields between editable and read-only."""
        if self._config is None:
            return
        line_edits = (
            self._name_edit, self._version_edit,
            self._start_edit, self._authors_edit, self._drops_edit,
        )
        for w in line_edits:
            w.setReadOnly(not editable)
        self._desc_edit.setReadOnly(not editable)
        self._initial_combo.setEnabled(editable)

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    def _on_name_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.name = text
        self.on_config_changed()

    def _on_version_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.version = text
        self.on_config_changed()

    def _on_desc_changed(self) -> None:
        if self._suppress or self._config is None:
            return
        self._config.description = self._desc_edit.toPlainText() or None
        self.on_config_changed()

    def _on_start_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.start_location = text or None
        self.on_config_changed()

    def _on_authors_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.authors = text or None
        self.on_config_changed()

    def _on_drops_changed(self, text: str) -> None:
        if self._suppress or self._config is None:
            return
        self._config.drops = [x.strip() for x in text.split(",") if x.strip()]
        self.on_config_changed()

    def _on_initial_changed(self, text: str) -> None:
        if self._suppress or self._config is None or not text:
            return
        self._config.initial = text
        self.on_config_changed()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _populate_initial_combo(self, states: list, current_initial: str) -> None:
        self._suppress = True
        self._initial_combo.clear()
        for state in states:
            self._initial_combo.addItem(state.id)
        idx = self._initial_combo.findText(current_initial)
        if idx >= 0:
            self._initial_combo.setCurrentIndex(idx)
        self._suppress = False

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


def _value_edit() -> QLineEdit:
    w = QLineEdit()
    w.setReadOnly(True)
    w.setPlaceholderText(_PLACEHOLDER)
    return w


def _value_edit_multi(lines: int = 3) -> QPlainTextEdit:
    w = QPlainTextEdit()
    w.setReadOnly(True)
    w.setPlaceholderText(_PLACEHOLDER)
    w.setFixedHeight(18 * lines + 10)
    return w


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
