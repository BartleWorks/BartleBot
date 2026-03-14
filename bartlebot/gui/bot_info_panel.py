"""Left sidebar: bot metadata (editable) and bot-wide options."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget,
    QVBoxLayout, QWidget,
)

from bartlebot.bot.schema import BotOptionModel
from bartlebot.gui.sidebar_section import SidebarSection

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_PLACEHOLDER = "—"


class _AddOptionDialog(QDialog):
    """Dialog for adding or editing a bot-wide option.

    Pass ``existing_option`` to open in edit mode (pre-populated fields,
    "Save" button, name uniqueness check excludes the original name).
    """

    _TYPE_DEFAULTS = {"int": "0", "float": "0.0", "str": ""}

    def __init__(
        self,
        existing_names: list[str],
        parent=None,
        existing_option: BotOptionModel | None = None,
    ):
        super().__init__(parent)
        edit_mode = existing_option is not None
        self.setWindowTitle("Edit Option" if edit_mode else "Add Option")
        self.setModal(True)
        self.setMinimumWidth(320)
        # In edit mode, the original name is allowed (it's the same option).
        self._existing_names = (
            [n for n in existing_names if n != existing_option.name]
            if edit_mode else existing_names
        )
        self._result: BotOptionModel | None = None

        form = QFormLayout()
        form.setSpacing(6)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. repeat")

        self._type_combo = QComboBox()
        self._type_combo.addItems(["bool", "int", "float", "str"])

        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("Optional description")

        # Bool default: radio buttons
        self._bool_widget = QWidget()
        bool_layout = QHBoxLayout(self._bool_widget)
        bool_layout.setContentsMargins(0, 0, 0, 0)
        bool_layout.setSpacing(12)
        self._bool_true = QRadioButton("True")
        self._bool_false = QRadioButton("False")
        self._bool_group = QButtonGroup(self)
        self._bool_group.addButton(self._bool_true)
        self._bool_group.addButton(self._bool_false)
        bool_layout.addWidget(self._bool_true)
        bool_layout.addWidget(self._bool_false)
        bool_layout.addStretch()

        # Non-bool default: text edit
        self._default_edit = QLineEdit()

        # Stacked widget switches between the two
        self._default_stack = QStackedWidget()
        self._default_stack.addWidget(self._bool_widget)   # index 0
        self._default_stack.addWidget(self._default_edit)  # index 1

        form.addRow("Name", self._name_edit)
        form.addRow("Type", self._type_combo)
        form.addRow("Description", self._desc_edit)
        form.addRow("Default", self._default_stack)

        self._err_lbl = QLabel()
        self._err_lbl.setStyleSheet("color: #cc4444; font-size: 11px;")
        self._err_lbl.setWordWrap(True)
        self._err_lbl.setVisible(False)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Save" if edit_mode else "Add")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_ok)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)

        outer = QVBoxLayout(self)
        outer.setSpacing(8)
        outer.addLayout(form)
        outer.addWidget(self._err_lbl)
        outer.addLayout(btn_row)

        self._type_combo.currentTextChanged.connect(self._on_type_changed)

        # Pre-populate in edit mode, then apply _on_type_changed
        if edit_mode:
            self._name_edit.setText(existing_option.name)
            idx = self._type_combo.findText(existing_option.type)
            if idx >= 0:
                self._type_combo.setCurrentIndex(idx)
            self._desc_edit.setText(existing_option.description or "")
            if existing_option.type == "bool":
                if bool(existing_option.default):
                    self._bool_true.setChecked(True)
                else:
                    self._bool_false.setChecked(True)
            else:
                self._default_edit.setText(str(existing_option.default))
        else:
            self._bool_false.setChecked(True)

        self._on_type_changed(self._type_combo.currentText())

    def get_option(self) -> BotOptionModel:
        assert self._result is not None
        return self._result

    def _on_type_changed(self, type_str: str) -> None:
        if type_str == "bool":
            self._default_stack.setCurrentIndex(0)
        else:
            self._default_stack.setCurrentIndex(1)
            self._default_edit.setPlaceholderText(self._TYPE_DEFAULTS.get(type_str, ""))

    def _on_ok(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            self._show_error("Name is required.")
            return
        if name in self._existing_names:
            self._show_error(f"An option named '{name}' already exists.")
            return
        type_str = self._type_combo.currentText()
        try:
            if type_str == "bool":
                default = self._bool_true.isChecked()
            else:
                default = self._parse_default(type_str, self._default_edit.text().strip())
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self._result = BotOptionModel(
            name=name,
            type=type_str,
            default=default,
            description=self._desc_edit.text().strip() or None,
        )
        self.accept()

    @staticmethod
    def _parse_default(type_str: str, text: str):
        if type_str == "int":
            try:
                return int(text) if text else 0
            except ValueError:
                raise ValueError("Default must be an integer.")
        if type_str == "float":
            try:
                return float(text) if text else 0.0
            except ValueError:
                raise ValueError("Default must be a number.")
        return text  # str — any value is valid

    def _show_error(self, msg: str) -> None:
        self._err_lbl.setText(msg)
        self._err_lbl.setVisible(True)


class BotInfoPanel(QScrollArea):
    """Fixed-width left sidebar showing bot metadata and bot-wide option controls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config = None
        self._suppress: bool = False
        self._editable: bool = False
        self._option_rows: list[QWidget] = []
        self._option_widgets: dict[str, QWidget] = {}
        self._edit_btns: list[QPushButton] = []
        self._delete_btns: list[QPushButton] = []
        self.on_config_changed: Callable[[], None] = lambda: None

        content = QWidget()
        self._outer = QVBoxLayout(content)
        self._outer.setContentsMargins(8, 8, 8, 8)
        self._outer.setSpacing(4)

        # ── Bot Info ──────────────────────────────────────────────────
        info_section = SidebarSection("Bot Info")

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
        self._options_section = SidebarSection("Options")

        options_outer = QVBoxLayout()
        options_outer.setSpacing(4)

        self._options_layout = QVBoxLayout()
        self._options_layout.setSpacing(4)

        self._no_options_lbl = _value_label()
        self._no_options_lbl.setText(_PLACEHOLDER)
        self._options_layout.addWidget(self._no_options_lbl)

        self._add_option_btn = QPushButton("+ Add Option")
        self._add_option_btn.setVisible(False)
        self._add_option_btn.clicked.connect(self._on_add_option)

        options_outer.addLayout(self._options_layout)
        options_outer.addWidget(self._add_option_btn)

        self._options_section.set_content_layout(options_outer)
        self._outer.addWidget(self._options_section)

        # ── Combat Config ─────────────────────────────────────────────
        self._combat_section = SidebarSection("Combat Config")

        combat_layout = QVBoxLayout()
        combat_layout.setSpacing(4)

        self._use_bartlebot_default_chk = QCheckBox("Use BartleBot default combat config")
        self._use_bartlebot_default_chk.setChecked(True)
        combat_layout.addWidget(self._use_bartlebot_default_chk)

        self._combat_config_edit = _value_edit_multi(lines=8)
        self._combat_config_edit.setPlaceholderText("Enter WizSprinter combat DSL…")
        combat_layout.addWidget(self._combat_config_edit)

        self._combat_section.set_content_layout(combat_layout)
        self._outer.addWidget(self._combat_section)

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
        self._use_bartlebot_default_chk.toggled.connect(self._on_use_bartlebot_default_changed)
        self._combat_config_edit.textChanged.connect(self._on_combat_config_changed)
        # Checkbox controls whether the DSL text field is editable
        self._use_bartlebot_default_chk.toggled.connect(
            lambda checked: self._combat_config_edit.setReadOnly(checked)
        )

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
            self._use_bartlebot_default_chk.setChecked(True)
            self._use_bartlebot_default_chk.setEnabled(False)
            self._combat_config_edit.setPlainText("")
            self._combat_config_edit.setReadOnly(True)
        else:
            self._name_edit.setText(config.name or "")
            self._version_edit.setText(config.version or "")
            self._start_edit.setText(config.start_location or "")
            self._authors_edit.setText(config.authors or "")
            self._drops_edit.setText(", ".join(config.drops) if config.drops else "")
            self._desc_edit.setPlainText(config.description or "")
            self._populate_initial_combo(config.states, config.initial)
            self._rebuild_options(config.options)
            self._use_bartlebot_default_chk.setChecked(config.use_bartlebot_default_combat_config)
            self._combat_config_edit.setPlainText(config.default_combat_config or "")
            # Fields remain read-only; set_editable(True) enables them in EDIT mode
        self._suppress = False

    def refresh_states(self, states: list) -> None:
        """Rebuild the Initial State combo to reflect added/removed states."""
        if self._config is None:
            return
        self._populate_initial_combo(states, self._config.initial)

    def set_editable(self, editable: bool) -> None:
        """Toggle all metadata fields between editable and read-only."""
        self._editable = editable
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
        self._use_bartlebot_default_chk.setEnabled(editable)
        # Combat DSL field is only editable when the checkbox is unchecked
        use_default = self._use_bartlebot_default_chk.isChecked()
        self._combat_config_edit.setReadOnly(not editable or use_default)
        # Options add/edit/delete controls
        self._add_option_btn.setVisible(editable)
        for btn in self._edit_btns:
            btn.setVisible(editable)
        for btn in self._delete_btns:
            btn.setVisible(editable)

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

    def _on_use_bartlebot_default_changed(self, checked: bool) -> None:
        if self._suppress or self._config is None:
            return
        self._config.use_bartlebot_default_combat_config = checked
        self.on_config_changed()

    def _on_combat_config_changed(self) -> None:
        if self._suppress or self._config is None:
            return
        text = self._combat_config_edit.toPlainText()
        self._config.default_combat_config = text or None
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
        self._edit_btns.clear()
        self._delete_btns.clear()

        if not options:
            self._no_options_lbl.show()
            return

        self._no_options_lbl.hide()
        for opt in options:
            inner, widget = _build_option_row(opt)
            self._connect_option_widget(widget, opt)

            edit_btn = QPushButton("Edit")
            edit_btn.setToolTip(f"Edit option '{opt.name}'")
            edit_btn.setVisible(self._editable)
            edit_btn.clicked.connect(
                lambda _checked, name=opt.name: self._on_edit_option(name)
            )

            del_btn = QPushButton("Delete")
            del_btn.setToolTip(f"Remove option '{opt.name}'")
            del_btn.setVisible(self._editable)
            del_btn.clicked.connect(
                lambda _checked, name=opt.name: self._on_delete_option(name)
            )

            outer = QWidget()
            outer_layout = QHBoxLayout(outer)
            outer_layout.setContentsMargins(0, 0, 0, 0)
            outer_layout.setSpacing(4)
            outer_layout.addWidget(inner, 1)
            outer_layout.addWidget(edit_btn, 0, Qt.AlignmentFlag.AlignTop)
            outer_layout.addWidget(del_btn, 0, Qt.AlignmentFlag.AlignTop)

            self._options_layout.addWidget(outer)
            self._option_rows.append(outer)
            self._option_widgets[opt.name] = widget
            self._edit_btns.append(edit_btn)
            self._delete_btns.append(del_btn)

    def _on_add_option(self) -> None:
        if self._config is None:
            return
        existing = [o.name for o in self._config.options]
        dlg = _AddOptionDialog(existing, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._config.options.append(dlg.get_option())
            self._rebuild_options(self._config.options)
            self.on_config_changed()

    def _on_edit_option(self, name: str) -> None:
        if self._config is None:
            return
        original = next((o for o in self._config.options if o.name == name), None)
        if original is None:
            return
        existing = [o.name for o in self._config.options]
        dlg = _AddOptionDialog(existing, parent=self, existing_option=original)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dlg.get_option()
        # If name changed, update flag references in transitions
        if updated.name != name:
            for t in self._config.transitions:
                if t.flag == name:
                    t.flag = updated.name
        # Replace in-place to preserve list order
        idx = next(i for i, o in enumerate(self._config.options) if o.name == name)
        self._config.options[idx] = updated
        self._rebuild_options(self._config.options)
        self.on_config_changed()

    def _on_delete_option(self, name: str) -> None:
        if self._config is None:
            return
        self._config.options = [o for o in self._config.options if o.name != name]
        # Clear flag references in transitions that pointed to this option
        for t in self._config.transitions:
            if t.flag == name:
                t.flag = None
        self._rebuild_options(self._config.options)
        self.on_config_changed()

    def _connect_option_widget(self, widget: QWidget, opt) -> None:
        """Connect a widget's change signal to update opt.default and emit on_config_changed."""
        def _update(value) -> None:
            if self._suppress or self._config is None:
                return
            opt.default = value
            self.on_config_changed()

        if isinstance(widget, QCheckBox):
            widget.toggled.connect(_update)
        elif isinstance(widget, QSpinBox):
            widget.valueChanged.connect(_update)
        elif isinstance(widget, QDoubleSpinBox):
            widget.valueChanged.connect(_update)
        elif isinstance(widget, QLineEdit):
            widget.textChanged.connect(_update)


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
