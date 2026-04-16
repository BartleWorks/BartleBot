"""Properties panel widgets for the bot canvas node editor.

_DynamicParamForm  — generic schema-driven form used for node parameters.
_ParamPanel        — stacked panel showing node, edge, or idle views; owns the
                     Record button and delegates recording to ``recording.py``.
"""

import asyncio
import json
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import FlagCondition, StateModel, TransitionModel
from bartlebot.gui.canvas import recording
from bartlebot.gui.theme import THEME
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS, NODE_REGISTRY, NODE_SPECS

# ---------------------------------------------------------------------------
# Sentinel QLineEdit subclasses  (identified by get_params())
# ---------------------------------------------------------------------------


class _IntListLineEdit(QLineEdit):
    """QLineEdit variant used for 'int_list' param fields.

    Stores values as a comma-separated string (e.g. "1, 2, 3") and is
    identified by get_params() to deserialise back to list[int].
    """


class _StrListLineEdit(QLineEdit):
    """QLineEdit variant used for 'str_list' param fields.

    Stores values as a comma-separated string (e.g. "IronBloom, GoldKey") and
    is identified by get_params() to deserialise back to list[str].
    """


class _IntChoiceComboBox(QComboBox):
    """QComboBox variant used for 'int_choice' param fields.

    Displays a fixed dropdown of integer options defined by ``ParamField.choices``.
    ``get_params()`` returns the selected value as an int.
    """


# ---------------------------------------------------------------------------
# _DynamicParamForm
# ---------------------------------------------------------------------------


class _DynamicParamForm(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._form = QVBoxLayout(self)
        self._form.setContentsMargins(6, 6, 6, 6)
        self._form.setSpacing(4)
        self._form.addStretch()
        self._widgets: dict[str, Any] = {}
        self._required_widgets: dict[str, Any] = {}
        self._suppress = False

    def load_schema(self, schema: list, params: dict) -> None:
        self._suppress = True
        # Remove everything except the trailing stretch.
        while self._form.count() > 1:
            item = self._form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._widgets.clear()
        self._required_widgets.clear()
        insert_idx = 0
        for field in schema:
            label_text = field.label
            if field.required:
                label_text += f" <span style='color:{THEME.error}'>*</span>"
            label_w = QLabel(label_text)
            label_w.setTextFormat(Qt.TextFormat.RichText)
            _muted = f"color: {THEME.text_muted}; font-size: 11px; font-weight: 600;"
            style = _muted + (" margin-top: 8px;" if insert_idx > 0 else "")
            label_w.setStyleSheet(style)
            if field.help:
                label_w.setToolTip(field.help)
            val = params.get(field.key, field.default)
            widget = self._make_widget(
                field.type, val, field.help, field.key, getattr(field, "choices", []), getattr(field, "placeholder", "")
            )
            self._widgets[field.key] = widget
            if field.required:
                self._required_widgets[field.key] = widget
            display_w = widget if not isinstance(widget, tuple) else widget[3]
            if field.type == "bool":
                container = QWidget()
                row = QHBoxLayout(container)
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(8)
                row.addWidget(label_w)
                row.addStretch()
                row.addWidget(display_w)
                self._form.insertWidget(insert_idx, container)
                insert_idx += 1
            else:
                self._form.insertWidget(insert_idx, label_w)
                self._form.insertWidget(insert_idx + 1, display_w)
                insert_idx += 2
            if field.default_hint and not field.required:
                hint_lbl = QLabel(field.default_hint)
                hint_lbl.setStyleSheet(f"color: {THEME.text_muted};")
                hint_lbl.setWordWrap(True)
                self._form.insertWidget(insert_idx, hint_lbl)
                insert_idx += 1
        if not schema:
            placeholder = QLabel("This node type has no parameters.")
            placeholder.setStyleSheet(f"color: {THEME.text_muted};")
            self._form.insertWidget(0, placeholder)
        # Wire disabled_by: when controller checkbox is checked, disable the target widget
        for field in schema:
            if field.disabled_by and field.disabled_by in self._widgets:
                controller = self._widgets[field.disabled_by]
                controlled = self._widgets[field.key]
                display_w = controlled if not isinstance(controlled, tuple) else controlled[3]
                if isinstance(controller, QCheckBox):
                    display_w.setEnabled(not controller.isChecked())
                    controller.checkStateChanged.connect(
                        lambda state, w=display_w: w.setEnabled(state != Qt.CheckState.Checked)
                    )
        self._suppress = False
        self._validate_required_fields()

    def _validate_required_fields(self) -> None:
        """Apply or clear a red border on required input widgets based on whether they are empty."""
        for widget in self._required_widgets.values():
            if isinstance(widget, tuple):
                # xyz — cannot be meaningfully empty
                display_w = widget[3]
                empty = False
            elif isinstance(widget, QPlainTextEdit):
                display_w = widget
                empty = not widget.toPlainText().strip()
            elif isinstance(widget, QLineEdit):
                display_w = widget
                empty = not widget.text().strip()
            else:
                # QSpinBox, QDoubleSpinBox, QCheckBox — always have a value
                display_w = widget
                empty = False
            display_w.setStyleSheet(f"border: 1px solid {THEME.error};" if empty else "")

    def get_params(self) -> dict:
        result: dict[str, Any] = {}
        for key, w in self._widgets.items():
            if isinstance(w, tuple):
                x_s, y_s, z_s, _ = w
                result[key] = {"x": x_s.value(), "y": y_s.value(), "z": z_s.value()}
            elif isinstance(w, QPlainTextEdit):
                result[key] = w.toPlainText()
            elif isinstance(w, _IntListLineEdit):
                result[key] = [int(x.strip()) for x in w.text().split(",") if x.strip().lstrip("-").isdigit()]
            elif isinstance(w, _StrListLineEdit):
                result[key] = [x.strip() for x in w.text().split(",") if x.strip()]
            elif isinstance(w, QLineEdit):
                result[key] = w.text()
            elif isinstance(w, QSpinBox):
                result[key] = w.value()
            elif isinstance(w, QDoubleSpinBox):
                result[key] = w.value()
            elif isinstance(w, _IntChoiceComboBox):
                try:
                    result[key] = int(w.currentText())
                except (TypeError, ValueError):
                    result[key] = 0
            elif isinstance(w, QCheckBox):
                result[key] = w.isChecked()
        return result

    def _make_widget(
        self,
        field_type: str,
        val: Any,
        help_text: str,
        key: str = "",
        choices: list | None = None,
        placeholder: str = "",
    ) -> Any:
        if field_type == "text":
            w = QPlainTextEdit()
            w.setPlainText(str(val) if val is not None else "")
            w.setFixedHeight(120)
            w.setPlaceholderText(placeholder)
            if help_text:
                w.setToolTip(help_text)
            w.textChanged.connect(self._emit_changed)
            return w
        if field_type == "str":
            w = QLineEdit()
            w.setText(str(val) if val is not None else "")
            if help_text:
                w.setToolTip(help_text)
            w.textChanged.connect(self._emit_changed)
            return w
        if field_type == "int_list":
            w = _IntListLineEdit()
            if isinstance(val, list):
                w.setText(", ".join(str(v) for v in val))
            else:
                w.setText(str(val) if val else "")
            w.setPlaceholderText("e.g. 1, 2, 3")
            if help_text:
                w.setToolTip(help_text)
            w.textChanged.connect(self._emit_changed)
            return w
        if field_type == "str_list":
            w = _StrListLineEdit()
            if isinstance(val, list):
                w.setText(", ".join(str(v) for v in val))
            else:
                w.setText(str(val) if val else "")
            w.setPlaceholderText("e.g. ItemA, ItemB")
            if help_text:
                w.setToolTip(help_text)
            w.textChanged.connect(self._emit_changed)
            return w
        if field_type == "int":
            w = QSpinBox()
            w.setRange(-999_999, 999_999)
            try:
                w.setValue(int(val) if val is not None else 0)
            except (TypeError, ValueError):
                w.setValue(0)
            if help_text:
                w.setToolTip(help_text)
            w.valueChanged.connect(self._emit_changed)
            return w
        if field_type == "float":
            w = QDoubleSpinBox()
            w.setRange(-999_999.0, 999_999.0)
            w.setDecimals(3)
            w.setSingleStep(1.0)
            try:
                w.setValue(float(val) if val is not None else 0.0)
            except (TypeError, ValueError):
                w.setValue(0.0)
            if help_text:
                w.setToolTip(help_text)
            w.valueChanged.connect(self._emit_changed)
            return w
        if field_type == "bool":
            w = QCheckBox()
            w.setChecked(bool(val) if val is not None else False)
            if help_text:
                w.setToolTip(help_text)
            w.checkStateChanged.connect(self._emit_changed)
            return w
        if field_type == "xyz":
            xyz = val if isinstance(val, dict) else {}
            container = QWidget()
            row = QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            spins = []
            for axis, axis_key in [("X", "x"), ("Y", "y"), ("Z", "z")]:
                lbl = QLabel(axis)
                lbl.setFixedWidth(12)
                spin = QDoubleSpinBox()
                spin.setRange(-999_999.0, 999_999.0)
                spin.setDecimals(2)
                spin.setSingleStep(1.0)
                try:
                    spin.setValue(float(xyz.get(axis_key, 0.0)))
                except (TypeError, ValueError):
                    spin.setValue(0.0)
                spin.valueChanged.connect(self._emit_changed)
                row.addWidget(lbl)
                row.addWidget(spin, 1)
                spins.append(spin)
            if help_text:
                container.setToolTip(help_text)
            paste_btn = QPushButton("Paste")
            paste_btn.setFixedWidth(48)
            paste_btn.setToolTip("Fill from clipboard (Copy XYZ format)")
            paste_btn.clicked.connect(lambda _checked, k=key: self._paste_xyz(k))
            row.addWidget(paste_btn)
            return (spins[0], spins[1], spins[2], container)
        if field_type == "int_choice":
            w = _IntChoiceComboBox()
            for c in choices or []:
                w.addItem(str(c))
            try:
                idx = w.findText(str(int(val)) if val is not None else "")
                if idx >= 0:
                    w.setCurrentIndex(idx)
            except (TypeError, ValueError):
                pass
            if help_text:
                w.setToolTip(help_text)
            w.currentTextChanged.connect(self._emit_changed)
            return w
        w = QLabel(f"(unsupported type: {field_type})")
        w.setStyleSheet(f"color: {THEME.text_muted};")
        return w

    def _emit_changed(self, *_) -> None:
        if not self._suppress:
            self._validate_required_fields()
            self.changed.emit()

    def _paste_xyz(self, key: str) -> None:
        try:
            data = json.loads(QApplication.clipboard().text().strip())
        except (json.JSONDecodeError, AttributeError):
            return
        if not isinstance(data, dict):
            return
        widgets = self._widgets.get(key)
        if not isinstance(widgets, tuple):
            return
        x_s, y_s, z_s, _ = widgets
        self._suppress = True
        try:
            x_s.setValue(float(data["x"]))
        except (KeyError, TypeError, ValueError):
            pass
        try:
            y_s.setValue(float(data["y"]))
        except (KeyError, TypeError, ValueError):
            pass
        try:
            z_s.setValue(float(data["z"]))
        except (KeyError, TypeError, ValueError):
            pass
        self._suppress = False
        self._emit_changed()

    def set_str(self, key: str, value: str) -> None:
        """Programmatically set a 'str' field by key."""
        widget = self._widgets.get(key)
        if not isinstance(widget, QLineEdit):
            return
        self._suppress = True
        widget.setText(value)
        self._suppress = False
        self._emit_changed()

    def set_xyz(self, key: str, x: float, y: float, z: float) -> None:
        """Programmatically set an 'xyz' field by key."""
        widgets = self._widgets.get(key)
        if not isinstance(widgets, tuple):
            return
        x_s, y_s, z_s, _ = widgets
        self._suppress = True
        x_s.setValue(x)
        y_s.setValue(y)
        z_s.setValue(z)
        self._suppress = False
        self._emit_changed()


# ---------------------------------------------------------------------------
# Per-node option-override helpers
# ---------------------------------------------------------------------------


def _make_override_widget(type_str: str, val: Any, help_text: str = "") -> QWidget:
    """Create a value input widget for a bot-option override row."""
    w: QWidget
    if type_str == "bool":
        wb = QCheckBox()
        wb.setChecked(bool(val) if val is not None else False)
        w = wb
    elif type_str == "int":
        wi = QSpinBox()
        wi.setRange(-999_999, 999_999)
        try:
            wi.setValue(int(val) if val is not None else 0)
        except (TypeError, ValueError):
            wi.setValue(0)
        w = wi
    elif type_str == "float":
        wf = QDoubleSpinBox()
        wf.setRange(-999_999.0, 999_999.0)
        wf.setDecimals(3)
        wf.setSingleStep(0.1)
        try:
            wf.setValue(float(val) if val is not None else 0.0)
        except (TypeError, ValueError):
            wf.setValue(0.0)
        w = wf
    elif type_str == "int_list":
        w = _IntListLineEdit()
        if isinstance(val, list):
            w.setText(", ".join(str(v) for v in val))
        else:
            w.setText(str(val) if val else "")
    else:  # str
        ws = QLineEdit()
        ws.setText(str(val) if val is not None else "")
        w = ws
    if help_text:
        w.setToolTip(help_text)
    return w


def _read_override_widget(w: QWidget) -> Any:
    if isinstance(w, QCheckBox):
        return w.isChecked()
    if isinstance(w, QSpinBox):
        return w.value()
    if isinstance(w, QDoubleSpinBox):
        return w.value()
    if isinstance(w, _IntListLineEdit):
        return [int(x.strip()) for x in w.text().split(",") if x.strip().lstrip("-").isdigit()]
    if isinstance(w, QLineEdit):
        return w.text()
    return None


def _connect_override_widget(w: QWidget, callback: Callable[[], None]) -> None:
    if isinstance(w, QCheckBox):
        w.checkStateChanged.connect(lambda _: callback())
    elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
        w.valueChanged.connect(lambda _: callback())
    elif isinstance(w, QLineEdit):
        w.textChanged.connect(lambda _: callback())


# ---------------------------------------------------------------------------
# _FlagConditionsWidget  — dynamic AND/OR/NOT flag condition list for edges
# ---------------------------------------------------------------------------


class _FlagConditionsWidget(QWidget):
    """Widget that edits a list of FlagConditions on a TransitionModel.

    Layout:
        [AND | OR ▾]   ← operator selector (top row)
        [NOT □] [option ▾]  [✕]   ← one row per condition
        [NOT □] [option ▾]  [✕]
        [+ Add condition]          ← append button
    """

    changed = pyqtSignal()

    # Width budget for the NOT checkbox and remove button
    _NOT_W = 40
    _REM_W = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self._transition: TransitionModel | None = None
        self._option_names: list[str] = []
        self._updating: bool = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # Operator row
        op_row = QHBoxLayout()
        op_row.setContentsMargins(0, 0, 0, 0)
        op_lbl = QLabel("Combine with:")
        op_lbl.setStyleSheet(
            f"color: {__import__('bartlebot.gui.theme', fromlist=['THEME']).THEME.text_muted}; font-size: 11px; font-weight: 600;"
        )
        self._op_combo = QComboBox()
        self._op_combo.addItems(["AND", "OR"])
        self._op_combo.setFixedWidth(64)
        op_row.addWidget(op_lbl)
        op_row.addWidget(self._op_combo)
        op_row.addStretch()
        outer.addLayout(op_row)

        # Rows container
        self._rows_widget = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        outer.addWidget(self._rows_widget)

        # Add button
        self._add_btn = QPushButton("+ Add condition")
        self._add_btn.setFixedHeight(22)
        self._add_btn.clicked.connect(self._on_add)
        outer.addWidget(self._add_btn)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {THEME.border};")
        outer.addWidget(sep)

        self._op_combo.currentTextChanged.connect(self._on_op_changed)
        self._row_widgets: list[tuple[QCheckBox, QComboBox, QPushButton]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_conditions(
        self,
        flags: list[FlagCondition],
        flag_op: str,
        option_names: list[str],
    ) -> None:
        """Rebuild the widget from a transition's flags list."""
        self._updating = True
        self._option_names = list(option_names)

        # Clear existing rows
        for not_cb, opt_cb, rem_btn in self._row_widgets:
            not_cb.setParent(None)
            opt_cb.setParent(None)
            rem_btn.setParent(None)
        self._row_widgets.clear()
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Operator
        idx = self._op_combo.findText(flag_op)
        self._op_combo.setCurrentIndex(idx if idx >= 0 else 0)

        # One row per FlagCondition
        for fc in flags:
            self._add_row(fc.option, fc.negate)

        self._updating = False

    def get_conditions(self) -> tuple[list[FlagCondition], str]:
        """Return the current flags list and flag_op string."""
        flags = []
        for not_cb, opt_cb, _rem in self._row_widgets:
            option = opt_cb.currentText()
            if option:
                flags.append(FlagCondition(option=option, negate=not_cb.isChecked()))
        return flags, self._op_combo.currentText()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_row(self, option: str = "", negate: bool = False) -> None:
        from bartlebot.gui.theme import THEME

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)

        not_cb = QCheckBox("NOT")
        not_cb.setChecked(negate)
        not_cb.setFixedWidth(self._NOT_W)

        opt_combo = QComboBox()
        opt_combo.addItems(self._option_names)
        if option and opt_combo.findText(option) < 0:
            opt_combo.addItem(option)
        if option:
            opt_combo.setCurrentText(option)

        rem_btn = QPushButton("✕")
        rem_btn.setFixedWidth(self._REM_W)
        rem_btn.setStyleSheet(f"color: {THEME.error}; font-weight: bold; border: none; padding: 0;")

        row_layout.addWidget(not_cb)
        row_layout.addWidget(opt_combo, 1)
        row_layout.addWidget(rem_btn)
        self._rows_layout.addWidget(row)

        self._row_widgets.append((not_cb, opt_combo, rem_btn))

        not_cb.checkStateChanged.connect(self._on_row_changed)
        opt_combo.currentTextChanged.connect(self._on_row_changed)
        rem_btn.clicked.connect(lambda _=None, r=row, t=(not_cb, opt_combo, rem_btn): self._on_remove(r, t))

    def _on_add(self) -> None:
        default = self._option_names[0] if self._option_names else ""
        self._add_row(option=default, negate=False)
        if not self._updating:
            self._emit()

    def _on_remove(self, row: QWidget, widgets: tuple) -> None:
        if widgets in self._row_widgets:
            self._row_widgets.remove(widgets)
        self._rows_layout.removeWidget(row)
        row.deleteLater()
        if not self._updating:
            self._emit()

    def _on_op_changed(self, _text: str) -> None:
        if not self._updating:
            self._emit()

    def _on_row_changed(self, *_) -> None:
        if not self._updating:
            self._emit()

    def _emit(self) -> None:
        self.changed.emit()


# ---------------------------------------------------------------------------
# _ParamPanel
# ---------------------------------------------------------------------------

_PAGE_IDLE = 0
_PAGE_NODE = 1
_PAGE_EDGE = 2


class _ParamPanel(QWidget):
    properties_changed = pyqtSignal()
    # Emitted when the user changes source or dest via the dropdowns.
    # Args: old_source, old_when (str|None), old_dest, new_source, new_dest
    edge_endpoints_changed = pyqtSignal(str, object, str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: StateModel | None = None
        self._transition: TransitionModel | None = None
        self._suppress: bool = False
        self._get_clients: Callable[[], list] = lambda: []
        self._record_task: asyncio.Task | None = None
        # All state IDs for the source/dest dropdowns; populated by show_transition()
        self._all_state_ids: list[str] = []
        # Other transitions in the config — for duplicate-check in endpoint change
        self._all_transitions: list[TransitionModel] = []

        self._stack = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._stack)

        # Page 0 — idle
        idle = QLabel("Select a node or edge to edit its properties.")
        idle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idle.setStyleSheet(f"color: {THEME.text_muted};")
        self._stack.addWidget(idle)

        # Page 1 — node
        node_w = QWidget()
        nf = QFormLayout(node_w)
        nf.setContentsMargins(6, 6, 6, 6)
        self._node_id_lbl = QLabel()
        self._node_label_edit = QLineEdit()
        self._node_type_combo = QComboBox()
        self._node_type_combo.addItems(sorted(NODE_REGISTRY.keys()))
        nf.addRow("ID", self._node_id_lbl)
        nf.addRow("Label", self._node_label_edit)
        nf.addRow("Node type", self._node_type_combo)

        self._param_form = _DynamicParamForm()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        scroll.setWidget(self._param_form)

        self._returns_section = QWidget()
        returns_layout = QVBoxLayout(self._returns_section)
        returns_layout.setContentsMargins(6, 4, 6, 4)
        returns_layout.setSpacing(2)
        returns_title = QLabel("Returns:")
        returns_title.setStyleSheet(f"font-weight: bold; color: {THEME.text_subtle};")
        returns_layout.addWidget(returns_title)
        self._returns_body_layout = QVBoxLayout()
        self._returns_body_layout.setSpacing(1)
        returns_layout.addLayout(self._returns_body_layout)
        self._returns_section.setVisible(False)

        # Option overrides section (per-node override of bot-wide options)
        self._overrides_section = QWidget()
        _ov_vbox = QVBoxLayout(self._overrides_section)
        _ov_vbox.setContentsMargins(6, 4, 6, 4)
        _ov_vbox.setSpacing(4)
        _ov_title = QLabel("Option Overrides:")
        _ov_title.setStyleSheet(f"font-weight: bold; color: {THEME.text_subtle};")
        _ov_vbox.addWidget(_ov_title)
        self._overrides_body = QVBoxLayout()
        self._overrides_body.setSpacing(4)
        _ov_vbox.addLayout(self._overrides_body)
        self._overrides_section.setVisible(False)
        self._override_rows: list[QWidget] = []
        self._override_widgets: dict[str, tuple[QCheckBox, QWidget]] = {}

        # Record row — shown only for recordable node types
        self._record_row = QWidget()
        _rr_layout = QHBoxLayout(self._record_row)
        _rr_layout.setContentsMargins(6, 0, 6, 4)
        _rr_layout.setSpacing(6)
        self._record_btn = QPushButton("Record")
        self._record_btn.setFixedWidth(70)
        self._record_status_lbl = QLabel("")
        self._record_status_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px;")
        _rr_layout.addWidget(self._record_btn)
        _rr_layout.addWidget(self._record_status_lbl, 1)
        self._record_row.setVisible(False)
        self._record_btn.clicked.connect(self._on_record_clicked)

        node_outer = QWidget()
        node_vbox = QVBoxLayout(node_outer)
        node_vbox.setContentsMargins(0, 0, 0, 0)
        node_vbox.setSpacing(0)
        node_vbox.addWidget(node_w)
        node_vbox.addWidget(self._record_row)
        node_vbox.addWidget(scroll, 1)
        node_vbox.addWidget(self._overrides_section)
        node_vbox.addWidget(self._returns_section)
        self._stack.addWidget(node_outer)

        # Page 2 — edge
        edge_outer = QWidget()
        edge_vbox = QVBoxLayout(edge_outer)
        edge_vbox.setContentsMargins(0, 0, 0, 0)
        edge_vbox.setSpacing(0)

        edge_form_w = QWidget()
        ef = QFormLayout(edge_form_w)
        ef.setContentsMargins(6, 6, 6, 6)

        self._edge_src_combo = QComboBox()
        self._edge_src_combo.setToolTip("Source state — the state that runs before this transition.")
        self._edge_dst_combo = QComboBox()
        self._edge_dst_combo.setToolTip("Destination state — the state to enter when this transition fires.")
        self._edge_when_combo = QComboBox()

        ef.addRow("Source", self._edge_src_combo)
        ef.addRow("Dest", self._edge_dst_combo)
        ef.addRow("When", self._edge_when_combo)

        flags_lbl = QLabel("Flag conditions")
        flags_lbl.setStyleSheet(
            f"color: {__import__('bartlebot.gui.theme', fromlist=['THEME']).THEME.text_muted};"
            " font-size: 11px; font-weight: 600; margin-top: 4px;"
        )
        flags_lbl.setToolTip(
            "Bot options that must be satisfied for this transition to fire.\n"
            "Combine multiple conditions with AND (all must be true) or OR (any must be true).\n"
            "Check NOT to invert a condition."
        )

        self._flag_conditions_widget = _FlagConditionsWidget()

        edge_vbox.addWidget(edge_form_w)
        edge_vbox.addWidget(flags_lbl)
        edge_vbox.addWidget(self._flag_conditions_widget)
        edge_vbox.addStretch()
        self._stack.addWidget(edge_outer)

        self._node_label_edit.textChanged.connect(self._on_node_label_changed)
        self._node_type_combo.currentTextChanged.connect(self._on_node_type_changed)
        self._param_form.changed.connect(self._on_param_form_changed)
        self._edge_when_combo.currentTextChanged.connect(self._on_edge_when_changed)
        self._edge_src_combo.currentTextChanged.connect(self._on_edge_src_changed)
        self._edge_dst_combo.currentTextChanged.connect(self._on_edge_dst_changed)
        self._flag_conditions_widget.changed.connect(self._on_flag_conditions_changed)

    def show_idle(self) -> None:
        self._cancel_record()
        self._state = None
        self._transition = None
        self._stack.setCurrentIndex(_PAGE_IDLE)

    def show_state(self, state: StateModel) -> None:
        self._cancel_record()
        self._state = state
        self._transition = None
        self._suppress = True
        self._node_id_lbl.setText(state.id)
        self._node_label_edit.setText(state.label or "")
        idx = self._node_type_combo.findText(state.node_type)
        if idx >= 0:
            self._node_type_combo.setCurrentIndex(idx)
        schema = NODE_PARAM_SCHEMAS.get(state.node_type, [])
        self._param_form.load_schema(schema, state.params or {})
        self._rebuild_overrides(state)
        self._rebuild_returns(state.node_type)
        self._suppress = False
        self._record_row.setVisible(state.node_type in recording.RECORDABLE_NODES)
        self._stack.setCurrentIndex(_PAGE_NODE)

    def show_transition(
        self,
        transition: TransitionModel,
        src_returns: list[str] | None = None,
        option_names: list[str] | None = None,
        state_ids: list[str] | None = None,
        all_transitions: list[TransitionModel] | None = None,
    ) -> None:
        self._transition = transition
        self._state = None
        self._all_state_ids = list(state_ids or [])
        self._all_transitions = list(all_transitions or [])
        self._suppress = True

        # Source / dest dropdowns
        self._edge_src_combo.clear()
        self._edge_src_combo.addItems(self._all_state_ids)
        src_idx = self._edge_src_combo.findText(transition.source)
        self._edge_src_combo.setCurrentIndex(src_idx if src_idx >= 0 else 0)

        self._edge_dst_combo.clear()
        self._edge_dst_combo.addItems(self._all_state_ids)
        dst_idx = self._edge_dst_combo.findText(transition.dest)
        self._edge_dst_combo.setCurrentIndex(dst_idx if dst_idx >= 0 else 0)

        # When combo from source node's possible return values
        self._edge_when_combo.clear()
        self._edge_when_combo.addItem("(none)")
        for name in src_returns or []:
            self._edge_when_combo.addItem(name)
        if transition.when and self._edge_when_combo.findText(transition.when) < 0:
            self._edge_when_combo.addItem(transition.when)
        if transition.when:
            idx = self._edge_when_combo.findText(transition.when)
            if idx >= 0:
                self._edge_when_combo.setCurrentIndex(idx)
        else:
            self._edge_when_combo.setCurrentIndex(0)

        # Flag conditions widget
        self._flag_conditions_widget.set_conditions(
            transition.flags,
            transition.flag_op,
            option_names or [],
        )

        self._suppress = False
        self._stack.setCurrentIndex(_PAGE_EDGE)

    def _on_node_label_changed(self, text: str) -> None:
        if self._suppress or self._state is None:
            return
        self._state.label = text or None
        self.properties_changed.emit()

    def _on_node_type_changed(self, text: str) -> None:
        if self._suppress or self._state is None:
            return
        self._cancel_record()
        self._state.node_type = text
        self._state.params = {}
        self._state.option_overrides = {}
        schema = NODE_PARAM_SCHEMAS.get(text, [])
        self._param_form.load_schema(schema, {})
        self._state.params = self._param_form.get_params()
        self._rebuild_overrides(self._state)
        self._rebuild_returns(text)
        self._record_row.setVisible(text in recording.RECORDABLE_NODES)
        self.properties_changed.emit()

    def _on_param_form_changed(self) -> None:
        if self._state is None:
            return
        self._state.params = self._param_form.get_params()
        self.properties_changed.emit()

    def _on_edge_when_changed(self, text: str) -> None:
        if self._suppress or self._transition is None:
            return
        self._transition.when = None if text == "(none)" else text
        self.properties_changed.emit()

    def _on_edge_src_changed(self, new_src: str) -> None:
        if self._suppress or self._transition is None or not new_src:
            return
        old_src = self._transition.source
        if new_src == old_src:
            return
        old_when = self._transition.when
        old_dst = self._transition.dest
        # Duplicate check: another transition with the same (new_src, dest, when) must not exist
        if any(
            t is not self._transition and t.source == new_src and t.dest == old_dst and t.when == old_when
            for t in self._all_transitions
        ):
            QMessageBox.warning(
                self,
                "Duplicate Transition",
                f"A transition from '{new_src}' to '{old_dst}' with the same When condition already exists.",
            )
            self._suppress = True
            self._edge_src_combo.setCurrentText(old_src)
            self._suppress = False
            return
        self._transition.source = new_src
        self.edge_endpoints_changed.emit(old_src, old_when, old_dst, new_src, old_dst)
        self.properties_changed.emit()

    def _on_edge_dst_changed(self, new_dst: str) -> None:
        if self._suppress or self._transition is None or not new_dst:
            return
        old_dst = self._transition.dest
        if new_dst == old_dst:
            return
        old_src = self._transition.source
        old_when = self._transition.when
        # Duplicate check
        if any(
            t is not self._transition and t.source == old_src and t.dest == new_dst and t.when == old_when
            for t in self._all_transitions
        ):
            QMessageBox.warning(
                self,
                "Duplicate Transition",
                f"A transition from '{old_src}' to '{new_dst}' with the same When condition already exists.",
            )
            self._suppress = True
            self._edge_dst_combo.setCurrentText(old_dst)
            self._suppress = False
            return
        self._transition.dest = new_dst
        self.edge_endpoints_changed.emit(old_src, old_when, old_dst, old_src, new_dst)
        self.properties_changed.emit()

    def _on_flag_conditions_changed(self) -> None:
        if self._suppress or self._transition is None:
            return
        flags, flag_op = self._flag_conditions_widget.get_conditions()
        self._transition.flags = flags
        self._transition.flag_op = flag_op
        self.properties_changed.emit()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _cancel_record(self) -> None:
        if self._record_task is not None and not self._record_task.done():
            self._record_task.cancel()
        self._record_task = None
        self._record_btn.setText("Record")
        self._record_status_lbl.setText("")

    def _on_record_clicked(self) -> None:
        if self._record_task is not None and not self._record_task.done():
            self._cancel_record()
            return
        if self._state is None:
            return
        clients = self._get_clients()
        if not clients:
            self._record_status_lbl.setText("No client connected.")
            return
        if self._state.node_type not in recording.RECORDABLE_NODES:
            return
        self._record_task = asyncio.ensure_future(
            recording.run_recording(
                self._state.node_type,
                clients[0],
                self._param_form,
                self._record_status_lbl.setText,
                self._record_btn.setText,
                self._try_set_label,
            )
        )

    def _try_set_label(self, label: str) -> None:
        """Auto-populate the node label if it is currently empty."""
        if self._state is None or (self._state.label or "").strip():
            return
        self._state.label = label
        self._suppress = True
        self._node_label_edit.setText(label)
        self._suppress = False
        self.properties_changed.emit()

    def _rebuild_overrides(self, state: StateModel) -> None:
        for row in self._override_rows:
            self._overrides_body.removeWidget(row)
            row.deleteLater()
        self._override_rows.clear()
        self._override_widgets.clear()

        spec = NODE_SPECS.get(state.node_type)
        bot_options = spec.required_bot_options if spec else []
        if not bot_options:
            self._overrides_section.setVisible(False)
            return

        overrides = state.option_overrides
        for bot_opt in bot_options:
            name = bot_opt.name
            is_overriding = name in overrides
            current_val = overrides.get(name, bot_opt.default)

            val_widget = _make_override_widget(bot_opt.type, current_val, bot_opt.description or "")
            val_widget.setEnabled(is_overriding)

            cb = QCheckBox(f"Override {name}:")
            cb.setChecked(is_overriding)
            if bot_opt.description:
                cb.setToolTip(bot_opt.description)

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            row_layout.addWidget(cb)
            row_layout.addWidget(val_widget, 1)

            def _on_cb_toggled(checked: bool, n=name, w=val_widget, s=state) -> None:
                if self._suppress:
                    return
                w.setEnabled(checked)
                if checked:
                    s.option_overrides[n] = _read_override_widget(w)
                else:
                    s.option_overrides.pop(n, None)
                self.properties_changed.emit()

            def _on_val_changed(n=name, cb_ref=cb, w=val_widget, s=state) -> None:
                if self._suppress or not cb_ref.isChecked():
                    return
                s.option_overrides[n] = _read_override_widget(w)
                self.properties_changed.emit()

            cb.toggled.connect(_on_cb_toggled)
            _connect_override_widget(val_widget, _on_val_changed)

            self._overrides_body.addWidget(row)
            self._override_rows.append(row)
            self._override_widgets[name] = (cb, val_widget)

        self._overrides_section.setVisible(True)

    def _rebuild_returns(self, node_type: str) -> None:
        while self._returns_body_layout.count():
            item = self._returns_body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        spec = NODE_SPECS.get(node_type)
        if spec and spec.returns:
            for ret in spec.returns:
                if ret.description:
                    lbl = QLabel(f"<b>{ret.name}</b> — {ret.description}")
                else:
                    lbl = QLabel(f"<b>{ret.name}</b>")
                lbl.setWordWrap(True)
                lbl.setStyleSheet("font-size: 11px;")
                self._returns_body_layout.addWidget(lbl)
            self._returns_section.setVisible(True)
        else:
            self._returns_section.setVisible(False)
