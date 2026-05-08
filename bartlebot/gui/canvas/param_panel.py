"""Properties panel widgets for the bot canvas node editor.

_DynamicParamForm  — generic schema-driven form used for node parameters.
_ParamPanel        — stacked panel showing node, edge, or idle views; owns the
                     Record button and delegates recording to ``recording.py``.
"""

import asyncio
import json
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QSize, QStringListModel, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPen, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from bartlebot.bot.schema import FlagCondition, FlagGroup, StateModel, TransitionModel
from bartlebot.gui.canvas import recording
from bartlebot.gui.theme import THEME
from bartlebot.nodes.registry import NODE_CATEGORIES, NODE_PARAM_SCHEMAS, NODE_REGISTRY, NODE_SPECS

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


class _FillButton(QPushButton):
    """QPushButton that updates its label on hover to reflect fill options.

    - 0 players connected  → "Fill current"  (no-op click)
    - 1 player connected   → "Fill current"  (direct fill, no menu)
    - 2+ players connected → "Fill current ▾"  (dropdown)
    """

    def __init__(self, get_clients: Callable[[], list], parent=None):
        super().__init__(parent)
        self._get_clients = get_clients
        self._refresh_label()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._refresh_label()
        super().enterEvent(event)

    def _refresh_label(self) -> None:
        n = len(self._get_clients())
        self.setText("Fill current" if n == 1 else "Fill current ▾")


class _ZoneLineEdit(QLineEdit):
    """QLineEdit with zone-path autocomplete for 'str' fields whose key contains 'zone'.

    Refreshes suggestions from a caller-supplied getter on focus so the dropdown
    always reflects currently connected players and recently visited zones.
    """

    def __init__(self, get_suggestions: Callable[[], list[str]], parent=None):
        super().__init__(parent)
        self._get_suggestions = get_suggestions
        self._model = QStringListModel(self)
        completer = QCompleter(self._model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setCompleter(completer)

    def focusInEvent(self, event) -> None:
        self._model.setStringList(self._get_suggestions())
        super().focusInEvent(event)


class _ZoneWidget(QWidget):
    """Zone string field with autocomplete + a 'Fill from Player' dropdown button.

    Wraps a ``_ZoneLineEdit`` and adds a compact button row so users can
    populate the field from a connected player's current zone without
    switching to the sidebar.
    """

    textChanged = pyqtSignal(str)

    def __init__(
        self,
        get_suggestions: Callable[[], list[str]],
        get_clients: Callable[[], list],
        parent=None,
    ):
        super().__init__(parent)
        self._get_clients = get_clients

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        self._edit = _ZoneLineEdit(get_suggestions)
        outer.addWidget(self._edit)

        fill_btn = _FillButton(get_clients)
        fill_btn.setFixedHeight(22)
        fill_btn.setToolTip("Fill from a connected player's current zone")
        fill_btn.setStyleSheet("QPushButton { font-size: 11px; padding: 1px 2px; }")
        fill_btn.clicked.connect(lambda _: self._show_fill_menu(fill_btn))
        outer.addWidget(fill_btn)

        self._edit.textChanged.connect(self.textChanged)

    # ------------------------------------------------------------------
    # Public interface (matches QLineEdit enough for form integration)
    # ------------------------------------------------------------------

    def text(self) -> str:
        return self._edit.text()

    def setText(self, text: str) -> None:
        self._edit.setText(text)

    # ------------------------------------------------------------------
    # Fill from player
    # ------------------------------------------------------------------

    def _show_fill_menu(self, btn: QPushButton) -> None:
        clients = self._get_clients()
        if not clients:
            return
        if len(clients) == 1:
            asyncio.ensure_future(self._fill_from_player(0))
            return
        menu = QMenu(btn)
        for i in range(len(clients)):
            action = menu.addAction(f"Fill from Player {i + 1}")
            action.triggered.connect(lambda _checked, idx=i: asyncio.ensure_future(self._fill_from_player(idx)))
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    async def _fill_from_player(self, idx: int) -> None:
        clients = self._get_clients()
        if idx >= len(clients):
            return
        try:
            zone = await clients[idx].zone_name()
            if zone:
                self.setText(zone)
        except Exception:
            pass


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
        self._get_clients: Callable[[], list] = lambda: []
        self._get_zone_suggestions: Callable[[], list[str]] = lambda: []

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
                hint_lbl = QLabel(f"Default: {field.default_hint}")
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
            elif isinstance(widget, _ZoneWidget):
                display_w = widget
                empty = not widget.text().strip()
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
                result[key] = [
                    x.strip().strip("'\"") for x in w.text().strip("[] ").split(",") if x.strip().strip("'\"")
                ]
            elif isinstance(w, _ZoneWidget):
                result[key] = w.text()
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
            if "zone" in key.lower():
                w: QWidget = _ZoneWidget(self._get_zone_suggestions, self._get_clients)
                w.setText(str(val) if val is not None else "")
                if help_text:
                    w.setToolTip(help_text)
                w.textChanged.connect(self._emit_changed)
                return w
            plain = QLineEdit()
            plain.setText(str(val) if val is not None else "")
            if help_text:
                plain.setToolTip(help_text)
            plain.textChanged.connect(self._emit_changed)
            return plain
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
            outer = QWidget()
            if help_text:
                outer.setToolTip(help_text)
            outer_layout = QVBoxLayout(outer)
            outer_layout.setContentsMargins(0, 0, 0, 0)
            outer_layout.setSpacing(2)
            # Row 1: X/Y/Z spinboxes
            spins_row = QHBoxLayout()
            spins_row.setContentsMargins(0, 0, 0, 0)
            spins_row.setSpacing(4)
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
                spins_row.addWidget(lbl)
                spins_row.addWidget(spin, 1)
                spins.append(spin)
            outer_layout.addLayout(spins_row)
            # Row 2: full-width segmented action bar — all buttons equal width, uniform height
            btns_row = QHBoxLayout()
            btns_row.setContentsMargins(0, 0, 0, 0)
            btns_row.setSpacing(2)
            _BTN_H = 22
            _btn_style = "QPushButton { font-size: 11px; padding: 1px 2px; }"
            copy_btn = QPushButton("Copy")
            copy_btn.setFixedHeight(_BTN_H)
            copy_btn.setToolTip("Copy XYZ to clipboard as JSON")
            copy_btn.setStyleSheet(_btn_style)
            copy_btn.clicked.connect(lambda _checked, k=key: self._copy_xyz(k))
            btns_row.addWidget(copy_btn, 1)
            paste_btn = QPushButton("Paste")
            paste_btn.setFixedHeight(_BTN_H)
            paste_btn.setToolTip("Paste XYZ from clipboard (JSON format)")
            paste_btn.setStyleSheet(_btn_style)
            paste_btn.clicked.connect(lambda _checked, k=key: self._paste_xyz(k))
            btns_row.addWidget(paste_btn, 1)
            fill_btn = _FillButton(self._get_clients)
            fill_btn.setFixedHeight(_BTN_H)
            fill_btn.setToolTip("Fill from a connected player's live position")
            fill_btn.setStyleSheet(_btn_style)
            fill_btn.clicked.connect(lambda _checked, k=key, b=fill_btn: self._show_fill_menu(k, b))
            btns_row.addWidget(fill_btn, 1)
            tp_btn = QPushButton("TP")
            tp_btn.setFixedHeight(_BTN_H)
            tp_btn.setToolTip("Teleport all connected players to these coordinates")
            tp_btn.setStyleSheet(_btn_style)
            tp_btn.clicked.connect(lambda _checked, k=key: asyncio.ensure_future(self._teleport_to_xyz(k)))
            btns_row.addWidget(tp_btn, 1)
            outer_layout.addLayout(btns_row)
            return (spins[0], spins[1], spins[2], outer)
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

    def _copy_xyz(self, key: str) -> None:
        widgets = self._widgets.get(key)
        if not isinstance(widgets, tuple):
            return
        x_s, y_s, z_s, _ = widgets
        QApplication.clipboard().setText(json.dumps({"x": x_s.value(), "y": y_s.value(), "z": z_s.value()}))

    def _show_fill_menu(self, key: str, btn: QPushButton) -> None:
        clients = self._get_clients()
        if not clients:
            return
        if len(clients) == 1:
            asyncio.ensure_future(self._fill_xyz_from_player(key, 0))
            return
        menu = QMenu(btn)
        for i in range(len(clients)):
            action = menu.addAction(f"Fill from Player {i + 1}")
            action.triggered.connect(
                lambda _checked, k=key, idx=i: asyncio.ensure_future(self._fill_xyz_from_player(k, idx))
            )
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    async def _fill_xyz_from_player(self, key: str, idx: int) -> None:
        clients = self._get_clients()
        if idx >= len(clients):
            return
        try:
            pos = await clients[idx].body.position()
            self.set_xyz(key, pos.x, pos.y, pos.z)
        except Exception:
            pass

    async def _teleport_to_xyz(self, key: str) -> None:
        from wizwalker import XYZ

        clients = self._get_clients()
        if not clients:
            return
        widgets = self._widgets.get(key)
        if not isinstance(widgets, tuple):
            return
        x_s, y_s, z_s, _ = widgets
        target = XYZ(x_s.value(), y_s.value(), z_s.value())
        try:
            await asyncio.gather(
                *[c.teleport(target, wait_on_inuse=True, purge_on_after_unuser_fixer=True) for c in clients]
            )
        except Exception:
            pass

    def set_str(self, key: str, value: str) -> None:
        """Programmatically set a 'str' field by key."""
        widget = self._widgets.get(key)
        if not isinstance(widget, (_ZoneWidget, QLineEdit)):
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
    """Widget that edits flag conditions on a TransitionModel.

    Supports two levels of boolean logic:
    - Each group combines its conditions internally with AND or OR.
    - Multiple groups are combined with the outer AND/OR selector.

    Single-group display (one group, outer selector hidden) matches the old
    flat-list layout for simple cases and backward compatibility.
    """

    changed = pyqtSignal()

    _NOT_W = 52
    _REM_W = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self._option_names: list[str] = []
        self._updating: bool = False
        # List of (group_frame, inner_op_combo, rows_layout, row_tuples_list)
        self._groups: list[tuple[QFrame, QComboBox, QVBoxLayout, list]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # Outer operator row (hidden when only one group)
        self._outer_op_row = QWidget()
        _oor = QHBoxLayout(self._outer_op_row)
        _oor.setContentsMargins(0, 0, 0, 0)
        _oor.setSpacing(4)
        _oor_lbl = QLabel("Groups combine with:")
        _oor_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px; font-weight: 600;")
        self._groups_op_combo = QComboBox()
        self._groups_op_combo.addItems(["OR", "AND"])
        self._groups_op_combo.setFixedWidth(64)
        _oor.addWidget(_oor_lbl)
        _oor.addWidget(self._groups_op_combo)
        _oor.addStretch()
        outer.addWidget(self._outer_op_row)
        self._outer_op_row.setVisible(False)

        # Groups container
        self._groups_widget = QWidget()
        self._groups_layout = QVBoxLayout(self._groups_widget)
        self._groups_layout.setContentsMargins(0, 0, 0, 0)
        self._groups_layout.setSpacing(6)
        outer.addWidget(self._groups_widget)

        # Add group button
        self._add_group_btn = QPushButton("+ Add condition group")
        self._add_group_btn.setFixedHeight(22)
        self._add_group_btn.clicked.connect(self._on_add_group)
        outer.addWidget(self._add_group_btn)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {THEME.border};")
        outer.addWidget(sep)

        self._groups_op_combo.currentTextChanged.connect(self._on_any_changed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_flag_groups(
        self,
        flag_groups: list[FlagGroup],
        flag_groups_op: str,
        option_names: list[str],
    ) -> None:
        """Load from the new grouped FlagGroup format."""
        self._updating = True
        self._option_names = list(option_names)
        self._clear_all_groups()
        idx = self._groups_op_combo.findText(flag_groups_op)
        self._groups_op_combo.setCurrentIndex(idx if idx >= 0 else 0)
        for g in flag_groups:
            self._add_group(flags=g.flags, op=g.op)
        if not self._groups:
            self._add_group()
        self._refresh_outer_op_visibility()
        self._updating = False

    def set_conditions_flat(
        self,
        flags: list[FlagCondition],
        flag_op: str,
        option_names: list[str],
    ) -> None:
        """Load from the legacy flat flags+flag_op format (single group)."""
        self._updating = True
        self._option_names = list(option_names)
        self._clear_all_groups()
        self._groups_op_combo.setCurrentIndex(0)  # OR default
        self._add_group(flags=flags, op=flag_op)
        self._refresh_outer_op_visibility()
        self._updating = False

    def get_flag_groups(self) -> tuple[list[FlagGroup], str]:
        """Return the current state as (flag_groups, flag_groups_op)."""
        result: list[FlagGroup] = []
        for _frame, inner_op, _rows_layout, rows in self._groups:
            flags = []
            for not_cb, opt_cb, _rem in rows:
                option = opt_cb.currentText()
                if option:
                    flags.append(FlagCondition(option=option, negate=not_cb.isChecked()))
            if flags:
                result.append(FlagGroup(flags=flags, op=inner_op.currentText()))
        return result, self._groups_op_combo.currentText()

    # ------------------------------------------------------------------
    # Group management
    # ------------------------------------------------------------------

    def _clear_all_groups(self) -> None:
        for frame, _, _, _ in self._groups:
            self._groups_layout.removeWidget(frame)
            frame.deleteLater()
        self._groups.clear()

    def _add_group(self, flags: list[FlagCondition] | None = None, op: str = "AND") -> None:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(f"QFrame {{ border: 1px solid {THEME.border}; border-radius: 4px; }}")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(6, 4, 6, 4)
        frame_layout.setSpacing(3)

        # Group header: inner-op selector + remove button
        header = QWidget()
        header.setStyleSheet("QWidget { border: none; }")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(4)

        op_lbl = QLabel("Combine with:")
        op_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px; font-weight: 600; border: none;")
        inner_op = QComboBox()
        inner_op.addItems(["AND", "OR"])
        inner_op.setFixedWidth(64)
        inner_op_idx = inner_op.findText(op)
        inner_op.setCurrentIndex(inner_op_idx if inner_op_idx >= 0 else 0)

        rem_group_btn = QPushButton("✕")
        rem_group_btn.setFixedWidth(self._REM_W)
        rem_group_btn.setStyleSheet(f"color: {THEME.error}; font-weight: bold; border: none; padding: 0;")

        h_layout.addWidget(op_lbl)
        h_layout.addWidget(inner_op)
        h_layout.addStretch()
        h_layout.addWidget(rem_group_btn)
        frame_layout.addWidget(header)

        # Rows container for this group
        rows_widget = QWidget()
        rows_widget.setStyleSheet("QWidget { border: none; }")
        rows_layout = QVBoxLayout(rows_widget)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(2)
        frame_layout.addWidget(rows_widget)

        # Add condition button
        add_cond_btn = QPushButton("+ Add condition")
        add_cond_btn.setStyleSheet("QPushButton { border: none; }")
        add_cond_btn.setFixedHeight(20)
        frame_layout.addWidget(add_cond_btn)

        row_tuples: list[tuple[QCheckBox, QComboBox, QPushButton]] = []
        group_entry = (frame, inner_op, rows_layout, row_tuples)
        self._groups.append(group_entry)
        self._groups_layout.addWidget(frame)

        # Add initial condition rows
        for fc in flags or []:
            self._add_condition_row(group_entry, fc.option, fc.negate)
        if not flags:
            default = self._option_names[0] if self._option_names else ""
            self._add_condition_row(group_entry, default, False)

        # Wire signals
        inner_op.currentTextChanged.connect(self._on_any_changed)
        add_cond_btn.clicked.connect(lambda _, ge=group_entry: self._on_add_condition(ge))
        rem_group_btn.clicked.connect(lambda _, f=frame, ge=group_entry: self._on_remove_group(f, ge))

    def _add_condition_row(
        self,
        group_entry: tuple,
        option: str = "",
        negate: bool = False,
    ) -> None:
        frame, inner_op, rows_layout, row_tuples = group_entry

        row = QWidget()
        row.setStyleSheet("QWidget { border: none; }")
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
        rows_layout.addWidget(row)

        t = (not_cb, opt_combo, rem_btn)
        row_tuples.append(t)

        not_cb.checkStateChanged.connect(self._on_any_changed)
        opt_combo.currentTextChanged.connect(self._on_any_changed)
        rem_btn.clicked.connect(lambda _=None, r=row, tup=t, ge=group_entry: self._on_remove_condition(r, tup, ge))

    def _refresh_outer_op_visibility(self) -> None:
        self._outer_op_row.setVisible(len(self._groups) > 1)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_add_group(self) -> None:
        self._add_group()
        self._refresh_outer_op_visibility()
        if not self._updating:
            self.changed.emit()

    def _on_remove_group(self, frame: QFrame, group_entry: tuple) -> None:
        if len(self._groups) <= 1:
            return  # always keep at least one group
        if group_entry in self._groups:
            self._groups.remove(group_entry)
        self._groups_layout.removeWidget(frame)
        frame.deleteLater()
        self._refresh_outer_op_visibility()
        if not self._updating:
            self.changed.emit()

    def _on_add_condition(self, group_entry: tuple) -> None:
        default = self._option_names[0] if self._option_names else ""
        self._add_condition_row(group_entry, default, False)
        if not self._updating:
            self.changed.emit()

    def _on_remove_condition(self, row: QWidget, tup: tuple, group_entry: tuple) -> None:
        _frame, _inner_op, rows_layout, row_tuples = group_entry
        if tup in row_tuples:
            row_tuples.remove(tup)
        rows_layout.removeWidget(row)
        row.deleteLater()
        if not self._updating:
            self.changed.emit()

    def _on_any_changed(self, *_) -> None:
        if not self._updating:
            self.changed.emit()


# ---------------------------------------------------------------------------
# _ParamPanel
# ---------------------------------------------------------------------------

_PAGE_IDLE = 0
_PAGE_NODE = 1
_PAGE_EDGE = 2


class _NodeTypeDelegate(QStyledItemDelegate):
    """Custom delegate that renders separator items as a consistent 1px line in THEME.border."""

    def paint(self, painter, option, index):
        if index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == "separator":
            painter.save()
            mid = option.rect.center().y()
            painter.setPen(QPen(QColor(THEME.border), 1))
            painter.drawLine(option.rect.left() + 4, mid, option.rect.right() - 4, mid)
            painter.restore()
            return
        super().paint(painter, option, index)

    def sizeHint(self, option, index):
        if index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == "separator":
            return QSize(0, 9)
        return super().sizeHint(option, index)


class _ParamPanel(QWidget):
    properties_changed = pyqtSignal()
    # Emitted for discrete structural property changes (node type switch) that need
    # an immediate undo snapshot rather than the debounced param-edit path.
    structural_property_changed = pyqtSignal(str)
    # Emitted when the user changes source or dest via the dropdowns.
    # Args: old_source, old_when (str|None), old_dest, new_source, new_dest
    edge_endpoints_changed = pyqtSignal(str, object, str, str, str)
    # Emitted when the user completes a wiki import from the handle_combat node panel.
    # Carries the list of selected item name strings to be added to the bot's drops.
    wiki_import_completed = pyqtSignal(list)
    # Emitted when the user clicks a connection row to navigate to an edge or state.
    select_edge_requested = pyqtSignal(object)  # TransitionModel
    select_state_requested = pyqtSignal(str)  # state ID

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: StateModel | None = None
        self._transition: TransitionModel | None = None
        self._suppress: bool = False
        self._get_clients: Callable[[], list] = lambda: []
        self._record_task: asyncio.Task | None = None
        # All states for the source/dest dropdowns; populated by show_transition()
        self._all_states: list = []
        # Other transitions in the config — for duplicate-check in endpoint change
        self._all_transitions: list[TransitionModel] = []
        # All states/transitions for connections panel; populated by show_state()
        self._all_states_for_node: list = []
        self._all_transitions_for_node: list[TransitionModel] = []

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
        _model = QStandardItemModel()
        for _i, (_category, _node_types) in enumerate(NODE_CATEGORIES):
            if _i > 0:
                _sep = QStandardItem()
                _sep.setData("separator", Qt.ItemDataRole.AccessibleDescriptionRole)
                _sep.setEnabled(False)
                _sep.setSelectable(False)
                _model.appendRow(_sep)
            _header = QStandardItem(_category)
            _header.setEnabled(False)
            _header.setSelectable(False)
            _font = _header.font()
            _font.setBold(True)
            _font.setPointSize(max(1, _font.pointSize() - 1))
            _header.setFont(_font)
            _header.setForeground(QColor(THEME.text_muted))
            _model.appendRow(_header)
            for _node_type in _node_types:
                _item = QStandardItem("    " + _node_type)
                _item.setData(_node_type, Qt.ItemDataRole.UserRole)
                _model.appendRow(_item)
        self._node_type_combo.setModel(_model)
        self._node_type_combo.setItemDelegate(_NodeTypeDelegate(self._node_type_combo))
        self._node_help_btn = QPushButton("?")
        self._node_help_btn.setFixedSize(22, 22)
        self._node_help_btn.setToolTip("View node documentation")
        self._node_help_btn.setStyleSheet(
            f"QPushButton {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
            f"border-radius: 4px; color: {THEME.accent}; font-weight: bold; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {THEME.accent}; color: #ffffff; }}"
        )
        self._node_help_btn.clicked.connect(self._on_node_help)

        _node_type_row = QWidget()
        _nt_row_layout = QHBoxLayout(_node_type_row)
        _nt_row_layout.setContentsMargins(0, 0, 0, 0)
        _nt_row_layout.setSpacing(4)
        _nt_row_layout.addWidget(self._node_type_combo, 1)
        _nt_row_layout.addWidget(self._node_help_btn)

        nf.addRow("ID", self._node_id_lbl)
        nf.addRow("Label", self._node_label_edit)
        nf.addRow("Node type", _node_type_row)

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

        # Wiki import row — shown only for handle_combat nodes
        self._wiki_import_row = QWidget()
        _wi_layout = QHBoxLayout(self._wiki_import_row)
        _wi_layout.setContentsMargins(6, 0, 6, 4)
        _wi_layout.setSpacing(6)
        self._wiki_import_btn = QPushButton("Import drops from wiki…")
        self._wiki_import_btn.setToolTip(
            "Fetch drop data from a Wizard101 wiki creature page and add selected items to the bot's drop list."
        )
        _wi_layout.addWidget(self._wiki_import_btn)
        self._wiki_import_row.setVisible(False)
        self._wiki_import_btn.clicked.connect(self._on_wiki_import)

        # Notes section — freeform per-node annotation
        self._notes_section = QWidget()
        _notes_vbox = QVBoxLayout(self._notes_section)
        _notes_vbox.setContentsMargins(6, 4, 6, 6)
        _notes_vbox.setSpacing(3)
        _notes_title = QLabel("Notes:")
        _notes_title.setStyleSheet(f"font-weight: bold; color: {THEME.text_subtle};")
        _notes_vbox.addWidget(_notes_title)
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlaceholderText("Add notes about this node…")
        self._notes_edit.setFixedHeight(72)
        self._notes_edit.setStyleSheet(
            f"QPlainTextEdit {{ background: {THEME.surface}; border: 1px solid {THEME.border}; "
            f"border-radius: 4px; color: {THEME.text}; font-size: 11px; padding: 3px; }}"
        )
        _notes_vbox.addWidget(self._notes_edit)

        # Connections section — lists outgoing/incoming transitions for this node
        self._connections_section = QWidget()
        _conn_outer = QVBoxLayout(self._connections_section)
        _conn_outer.setContentsMargins(6, 4, 6, 4)
        _conn_outer.setSpacing(4)

        _conn_header = QWidget()
        _conn_header_layout = QHBoxLayout(_conn_header)
        _conn_header_layout.setContentsMargins(0, 0, 0, 0)
        _conn_header_layout.setSpacing(4)
        self._conn_toggle_btn = QPushButton("▾ Connections")
        self._conn_toggle_btn.setFlat(True)
        self._conn_toggle_btn.setStyleSheet(
            f"QPushButton {{ text-align: left; font-weight: bold; color: {THEME.text_subtle};"
            " border: none; padding: 0; font-size: 11px; background: transparent; }}"
        )
        _conn_header_layout.addWidget(self._conn_toggle_btn)
        _conn_header_layout.addStretch()
        _conn_outer.addWidget(_conn_header)

        self._conn_body = QWidget()
        self._conn_body_layout = QVBoxLayout(self._conn_body)
        self._conn_body_layout.setContentsMargins(0, 2, 0, 2)
        self._conn_body_layout.setSpacing(2)
        _conn_outer.addWidget(self._conn_body)

        self._conn_expanded = True
        self._conn_toggle_btn.clicked.connect(self._toggle_connections)
        self._connections_section.setVisible(False)

        node_outer = QWidget()
        node_vbox = QVBoxLayout(node_outer)
        node_vbox.setContentsMargins(0, 0, 0, 0)
        node_vbox.setSpacing(0)
        node_vbox.addWidget(node_w)
        node_vbox.addWidget(self._record_row)
        node_vbox.addWidget(scroll, 1)
        node_vbox.addWidget(self._wiki_import_row)
        node_vbox.addWidget(self._overrides_section)
        node_vbox.addWidget(self._returns_section)
        node_vbox.addWidget(self._connections_section)
        node_vbox.addWidget(self._notes_section)
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
        self._node_type_combo.currentIndexChanged.connect(self._on_node_type_changed)
        self._param_form.changed.connect(self._on_param_form_changed)
        self._notes_edit.textChanged.connect(self._on_notes_changed)
        self._edge_when_combo.currentTextChanged.connect(self._on_edge_when_changed)
        self._edge_src_combo.currentTextChanged.connect(self._on_edge_src_changed)
        self._edge_dst_combo.currentTextChanged.connect(self._on_edge_dst_changed)
        self._flag_conditions_widget.changed.connect(self._on_flag_conditions_changed)

    def show_idle(self) -> None:
        self._cancel_record()
        self._state = None
        self._transition = None
        self._stack.setCurrentIndex(_PAGE_IDLE)

    def show_state(
        self,
        state: StateModel,
        all_states: list | None = None,
        all_transitions: list[TransitionModel] | None = None,
    ) -> None:
        self._cancel_record()
        self._state = state
        self._transition = None
        self._all_states_for_node = list(all_states or [])
        self._all_transitions_for_node = list(all_transitions or [])
        self._suppress = True
        self._node_id_lbl.setText(state.id)
        self._node_label_edit.setText(state.label or "")
        idx = self._node_type_combo.findData(state.node_type, Qt.ItemDataRole.UserRole)
        if idx >= 0:
            self._node_type_combo.setCurrentIndex(idx)
        schema = NODE_PARAM_SCHEMAS.get(state.node_type, [])
        self._param_form.load_schema(schema, state.params or {})
        self._rebuild_overrides(state)
        self._rebuild_returns(state.node_type)
        self._rebuild_connections(state.id)
        self._notes_edit.blockSignals(True)
        self._notes_edit.setPlainText(state.notes or "")
        self._notes_edit.blockSignals(False)
        self._suppress = False
        self._record_row.setVisible(state.node_type in recording.RECORDABLE_NODES)
        self._wiki_import_row.setVisible(state.node_type == "handle_combat")
        self._stack.setCurrentIndex(_PAGE_NODE)

    def show_transition(
        self,
        transition: TransitionModel,
        src_returns: list[str] | None = None,
        option_names: list[str] | None = None,
        states: list | None = None,
        all_transitions: list[TransitionModel] | None = None,
    ) -> None:
        self._transition = transition
        self._state = None
        self._all_states = list(states or [])
        self._all_transitions = list(all_transitions or [])
        self._suppress = True

        # Source / dest dropdowns — display label (or ID as fallback), store ID as item data
        self._edge_src_combo.clear()
        for st in self._all_states:
            self._edge_src_combo.addItem(st.label or st.id, st.id)
        src_idx = self._edge_src_combo.findData(transition.source)
        self._edge_src_combo.setCurrentIndex(src_idx if src_idx >= 0 else 0)

        self._edge_dst_combo.clear()
        for st in self._all_states:
            self._edge_dst_combo.addItem(st.label or st.id, st.id)
        dst_idx = self._edge_dst_combo.findData(transition.dest)
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

        # Flag conditions widget — prefer grouped format, fall back to flat legacy format
        if transition.flag_groups:
            self._flag_conditions_widget.set_flag_groups(
                transition.flag_groups,
                transition.flag_groups_op,
                option_names or [],
            )
        else:
            self._flag_conditions_widget.set_conditions_flat(
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

    def _on_node_help(self) -> None:
        """Open the node browser in view mode for the currently displayed node type."""
        from bartlebot.gui.canvas.node_browser_dialog import NodeBrowserDialog

        node_type: str | None = None
        if self._state is not None:
            node_type = self._state.node_type
        else:
            node_type = self._node_type_combo.currentData(Qt.ItemDataRole.UserRole)
        NodeBrowserDialog(self, mode="view", initial_node_type=node_type).exec()

    def _on_node_type_changed(self, _index: int) -> None:
        node_type: str = self._node_type_combo.currentData(Qt.ItemDataRole.UserRole)
        if self._suppress or self._state is None or node_type not in NODE_REGISTRY:
            return
        self.structural_property_changed.emit("Change Node Type")
        self._cancel_record()
        self._state.node_type = node_type
        self._state.params = {}
        self._state.option_overrides = {}
        schema = NODE_PARAM_SCHEMAS.get(node_type, [])
        self._param_form.load_schema(schema, {})
        self._state.params = self._param_form.get_params()
        self._rebuild_overrides(self._state)
        self._rebuild_returns(node_type)
        self._record_row.setVisible(node_type in recording.RECORDABLE_NODES)
        self._wiki_import_row.setVisible(node_type == "handle_combat")
        self.properties_changed.emit()

    def _on_param_form_changed(self) -> None:
        if self._state is None:
            return
        self._state.params = self._param_form.get_params()
        self.properties_changed.emit()

    def _on_notes_changed(self) -> None:
        if self._suppress or self._state is None:
            return
        text = self._notes_edit.toPlainText().strip()
        self._state.notes = text if text else None
        self.properties_changed.emit()

    def _on_edge_when_changed(self, text: str) -> None:
        if self._suppress or self._transition is None:
            return
        self._transition.when = None if text == "(none)" else text
        self.properties_changed.emit()

    def _on_edge_src_changed(self, _text: str) -> None:
        if self._suppress or self._transition is None:
            return
        new_src_id = self._edge_src_combo.currentData()
        if not new_src_id:
            return
        old_src = self._transition.source
        if new_src_id == old_src:
            return
        old_when = self._transition.when
        old_dst = self._transition.dest
        # Duplicate check: another transition with the same (new_src, dest, when) must not exist
        if any(
            t is not self._transition and t.source == new_src_id and t.dest == old_dst and t.when == old_when
            for t in self._all_transitions
        ):
            QMessageBox.warning(
                self,
                "Duplicate Transition",
                f"A transition from '{new_src_id}' to '{old_dst}' with the same When condition already exists.",
            )
            self._suppress = True
            self._edge_src_combo.setCurrentIndex(self._edge_src_combo.findData(old_src))
            self._suppress = False
            return
        self._transition.source = new_src_id
        self.edge_endpoints_changed.emit(old_src, old_when, old_dst, new_src_id, old_dst)
        self.properties_changed.emit()

    def _on_edge_dst_changed(self, _text: str) -> None:
        if self._suppress or self._transition is None:
            return
        new_dst_id = self._edge_dst_combo.currentData()
        if not new_dst_id:
            return
        old_dst = self._transition.dest
        if new_dst_id == old_dst:
            return
        old_src = self._transition.source
        old_when = self._transition.when
        # Duplicate check
        if any(
            t is not self._transition and t.source == old_src and t.dest == new_dst_id and t.when == old_when
            for t in self._all_transitions
        ):
            QMessageBox.warning(
                self,
                "Duplicate Transition",
                f"A transition from '{old_src}' to '{new_dst_id}' with the same When condition already exists.",
            )
            self._suppress = True
            self._edge_dst_combo.setCurrentIndex(self._edge_dst_combo.findData(old_dst))
            self._suppress = False
            return
        self._transition.dest = new_dst_id
        self.edge_endpoints_changed.emit(old_src, old_when, old_dst, old_src, new_dst_id)
        self.properties_changed.emit()

    def _on_flag_conditions_changed(self) -> None:
        if self._suppress or self._transition is None:
            return
        flag_groups, flag_groups_op = self._flag_conditions_widget.get_flag_groups()
        self._transition.flag_groups = flag_groups
        self._transition.flag_groups_op = flag_groups_op
        # Clear legacy flat fields — the grouped format takes precedence in to_machine_config()
        self._transition.flags = []
        self.properties_changed.emit()

    # ------------------------------------------------------------------
    # Wiki import
    # ------------------------------------------------------------------

    def _on_wiki_import(self) -> None:
        from bartlebot.gui.wiki_import_dialog import WikiImportDialog

        dialog = WikiImportDialog(parent=self.window())
        if dialog.exec():
            items = dialog.selected_items()
            if items:
                self.wiki_import_completed.emit(items)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def toggle_record(self) -> None:
        """Start or cancel recording for the currently selected node (hotkey handler)."""
        self._on_record_clicked()

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
            self._record_status_lbl.setText("No player connected.")
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

    # ------------------------------------------------------------------
    # Connections section
    # ------------------------------------------------------------------

    def _toggle_connections(self) -> None:
        self._conn_expanded = not self._conn_expanded
        self._conn_body.setVisible(self._conn_expanded)
        self._conn_toggle_btn.setText("▾ Connections" if self._conn_expanded else "▸ Connections")

    def _rebuild_connections(self, state_id: str) -> None:
        """Rebuild the outgoing/incoming connections list for the selected node."""
        # Clear existing rows
        while self._conn_body_layout.count():
            item = self._conn_body_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        all_t = self._all_transitions_for_node
        state_map = {s.id: (s.label or s.id) for s in self._all_states_for_node}

        outgoing = [t for t in all_t if t.source == state_id]
        incoming = [t for t in all_t if t.dest == state_id and t.source != state_id]

        if not outgoing and not incoming:
            placeholder = QLabel("No connections")
            placeholder.setStyleSheet(f"color: {THEME.text_muted}; font-size: 11px;")
            self._conn_body_layout.addWidget(placeholder)
            self._connections_section.setVisible(True)
            return

        # Sort outgoing: conditional first (matches evaluation order from to_machine_config)
        outgoing.sort(key=lambda t: 0 if t.when else 1)

        if outgoing:
            out_lbl = QLabel("Outgoing:")
            out_lbl.setStyleSheet(f"color: {THEME.text_muted}; font-size: 10px; font-weight: 600;")
            self._conn_body_layout.addWidget(out_lbl)
            for priority, t in enumerate(outgoing, 1):
                row = self._make_conn_row(
                    t,
                    badge_text=str(priority),
                    chip_text=t.when or "always",
                    node_label=state_map.get(t.dest, t.dest),
                    node_id=t.dest,
                    is_outgoing=True,
                )
                self._conn_body_layout.addWidget(row)

        if incoming:
            in_lbl = QLabel("Incoming:")
            in_lbl.setStyleSheet(
                f"color: {THEME.text_muted}; font-size: 10px; font-weight: 600;{'margin-top: 4px;' if outgoing else ''}"
            )
            self._conn_body_layout.addWidget(in_lbl)
            for t in incoming:
                row = self._make_conn_row(
                    t,
                    badge_text=None,
                    chip_text=t.when or "always",
                    node_label=state_map.get(t.source, t.source),
                    node_id=t.source,
                    is_outgoing=False,
                )
                self._conn_body_layout.addWidget(row)

        self._connections_section.setVisible(True)

    def _make_conn_row(
        self,
        transition: TransitionModel,
        badge_text: str | None,
        chip_text: str,
        node_label: str,
        node_id: str,
        is_outgoing: bool,
    ) -> QWidget:
        """Build a single connection row widget."""
        row = QWidget()
        row.setStyleSheet(f"QWidget {{ border-radius: 4px; }}QWidget:hover {{ background: {THEME.surface}; }}")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # Priority badge (outgoing only)
        if badge_text is not None:
            badge = QLabel(badge_text)
            badge.setFixedWidth(18)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(f"color: {THEME.text_muted}; font-size: 9px; font-weight: 600;")
            layout.addWidget(badge)

        # Condition chip
        chip_text_upper = chip_text.upper()
        if chip_text_upper in ("SUCCESS", "TRUE"):
            chip_bg = THEME.chip_success_bg
            chip_fg = "#ffffff"
        elif chip_text_upper in ("FAILURE", "FALSE"):
            chip_bg = THEME.chip_failure_bg
            chip_fg = "#ffffff"
        elif chip_text == "always":
            chip_bg = THEME.surface
            chip_fg = THEME.text_muted
        else:
            chip_bg = THEME.chip_neutral_bg
            chip_fg = "#ffffff"
        chip = QLabel(chip_text)
        chip.setStyleSheet(
            f"background: {chip_bg}; color: {chip_fg}; border-radius: 3px;"
            " font-size: 10px; font-weight: 600; padding: 1px 5px;"
        )

        # Node name link
        name_lbl = QLabel(node_label)
        name_lbl.setStyleSheet(f"color: {THEME.accent}; font-size: 11px;")
        name_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        name_lbl.setToolTip(f"Navigate to: {node_label}")

        if is_outgoing:
            layout.addWidget(chip)
            layout.addWidget(name_lbl, 1)
        else:
            layout.addWidget(name_lbl, 1)
            layout.addWidget(chip)

        # Clicking the node name navigates to that state
        def _on_name_clicked(ev, nid=node_id):
            ev.accept()
            self.select_state_requested.emit(nid)

        name_lbl.mousePressEvent = _on_name_clicked  # type: ignore[method-assign]

        # Clicking anywhere else on the row selects the edge
        def _on_row_pressed(ev, t=transition):
            self.select_edge_requested.emit(t)

        row.mousePressEvent = _on_row_pressed  # type: ignore[method-assign]

        return row
