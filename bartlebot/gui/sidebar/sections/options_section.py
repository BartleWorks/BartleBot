"""Sidebar section: bot-wide options (add / edit / delete)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import BotOptionModel
from bartlebot.gui.sidebar.sidebar_section import SidebarSection, field_label
from bartlebot.gui.theme import THEME
from bartlebot.nodes.registry import NODE_SPECS

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig

_PLACEHOLDER = "—"


# ---------------------------------------------------------------------------
# Add / Edit dialog
# ---------------------------------------------------------------------------


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
        self._existing_names = [n for n in existing_names if n != existing_option.name] if edit_mode else existing_names
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

        self._default_edit = QLineEdit()

        self._default_stack = QStackedWidget()
        self._default_stack.addWidget(self._bool_widget)  # index 0
        self._default_stack.addWidget(self._default_edit)  # index 1

        form.addRow("Name", self._name_edit)
        form.addRow("Type", self._type_combo)
        form.addRow("Description", self._desc_edit)
        form.addRow("Default", self._default_stack)

        self._err_lbl = QLabel()
        self._err_lbl.setStyleSheet(f"color: {THEME.error}; font-size: 11px;")
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
            except ValueError as err:
                raise ValueError("Default must be an integer.") from err
        if type_str == "float":
            try:
                return float(text) if text else 0.0
            except ValueError as err:
                raise ValueError("Default must be a number.") from err
        return text

    def _show_error(self, msg: str) -> None:
        self._err_lbl.setText(msg)
        self._err_lbl.setVisible(True)


# ---------------------------------------------------------------------------
# Options section
# ---------------------------------------------------------------------------


class OptionsSection(SidebarSection):
    """Collapsible 'Options' section: per-option value widgets + CRUD controls."""

    options_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Options", parent)
        self._config: BotConfig | None = None
        self._suppress: bool = False
        self._editable: bool = False
        self._option_rows: list[QWidget] = []
        self._option_widgets: dict[str, QWidget] = {}
        self._edit_btns: list[QPushButton] = []
        self._delete_btns: list[QPushButton] = []

        outer = QVBoxLayout()
        outer.setSpacing(4)

        self._options_layout = QVBoxLayout()
        self._options_layout.setSpacing(4)

        self._no_options_lbl = QLabel(_PLACEHOLDER)
        self._no_options_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._options_layout.addWidget(self._no_options_lbl)

        self._add_option_btn = QPushButton("+ Add Option")
        self._add_option_btn.setVisible(False)
        self._add_option_btn.clicked.connect(self._on_add_option)

        outer.addLayout(self._options_layout)
        outer.addWidget(self._add_option_btn)

        self.set_content_layout(outer)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_config(self, config: BotConfig | None) -> None:
        self._config = config
        self._rebuild_options(config.options if config else [])

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._add_option_btn.setVisible(editable)
        for btn in self._edit_btns:
            btn.setVisible(editable)
        for btn in self._delete_btns:
            btn.setVisible(editable)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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

        # Compute which option names are locked by at least one node in the bot.
        required_names: set[str] = set()
        if self._config is not None:
            for state in self._config.states:
                spec = NODE_SPECS.get(state.node_type)
                if spec:
                    for req in spec.required_bot_options:
                        required_names.add(req.name)

        _SYSTEM_TOOLTIP = "This option is required by one or more nodes and cannot be edited or deleted."

        self._no_options_lbl.hide()
        for opt in options:
            inner, widget = _build_option_row(opt)
            self._connect_option_widget(widget, opt)

            is_system = opt.name in required_names

            edit_btn = QPushButton("Edit")
            if is_system:
                edit_btn.setEnabled(False)
                edit_btn.setToolTip(_SYSTEM_TOOLTIP)
            else:
                edit_btn.setToolTip(f"Edit option '{opt.name}'")
            edit_btn.clicked.connect(lambda _checked, name=opt.name: self._on_edit_option(name))

            del_btn = QPushButton("Delete")
            if is_system:
                del_btn.setEnabled(False)
                del_btn.setToolTip(_SYSTEM_TOOLTIP)
            else:
                del_btn.setToolTip(f"Remove option '{opt.name}'")
            del_btn.clicked.connect(lambda _checked, name=opt.name: self._on_delete_option(name))

            outer = QWidget()
            outer_layout = QHBoxLayout(outer)
            outer_layout.setContentsMargins(0, 0, 0, 0)
            outer_layout.setSpacing(4)
            outer_layout.addWidget(inner, 1)
            outer_layout.addWidget(edit_btn, 0, Qt.AlignmentFlag.AlignBottom)
            outer_layout.addWidget(del_btn, 0, Qt.AlignmentFlag.AlignBottom)

            # Set visibility AFTER parenting — calling setVisible on a parentless
            # widget makes Qt show it as a top-level window (causes a visible flash).
            edit_btn.setVisible(self._editable)
            del_btn.setVisible(self._editable)

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
            self.options_changed.emit()

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
        if updated.name != name:
            for t in self._config.transitions:
                if t.flag == name:
                    t.flag = updated.name
        idx = next(i for i, o in enumerate(self._config.options) if o.name == name)
        self._config.options[idx] = updated
        self._rebuild_options(self._config.options)
        self.options_changed.emit()

    def _on_delete_option(self, name: str) -> None:
        if self._config is None:
            return
        self._config.options = [o for o in self._config.options if o.name != name]
        for t in self._config.transitions:
            if t.flag == name:
                t.flag = None
        self._rebuild_options(self._config.options)
        self.options_changed.emit()

    def _connect_option_widget(self, widget: QWidget, opt) -> None:
        def _update(value) -> None:
            if self._suppress or self._config is None:
                return
            opt.default = value
            self.options_changed.emit()

        if isinstance(widget, QCheckBox):
            widget.toggled.connect(_update)
        elif isinstance(widget, QSpinBox):
            widget.valueChanged.connect(_update)
        elif isinstance(widget, QDoubleSpinBox):
            widget.valueChanged.connect(_update)
        elif isinstance(widget, QLineEdit):
            if opt.type == "int_list":
                widget.editingFinished.connect(
                    lambda: _update(
                        [int(x.strip()) for x in widget.text().split(",") if x.strip().lstrip("-").isdigit()]
                    )
                )
            else:
                widget.editingFinished.connect(lambda: _update(widget.text()))


# ---------------------------------------------------------------------------
# Option row builder
# ---------------------------------------------------------------------------


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
        lbl = field_label(opt.name)
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
        elif opt.type == "int_list":
            val_str = (
                ", ".join(str(v) for v in opt.default) if isinstance(opt.default, list) else str(opt.default or "")
            )
            widget = QLineEdit(val_str)
        else:  # str
            widget = QLineEdit(str(opt.default))

        if tooltip:
            widget.setToolTip(tooltip)
        layout.addWidget(widget)

    if opt.description:
        desc_lbl = QLabel(opt.description)
        desc_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 9px;")
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

    return row, widget
