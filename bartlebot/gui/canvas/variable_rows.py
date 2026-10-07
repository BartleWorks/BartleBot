"""The two row editors that read and write variables: an edge's condition rows, and a node's assignments (#187).

**A condition row** (:class:`ConditionRow`) picks an option or a variable from one
grouped list. An option row is ``[NOT] option``, as it always was. A variable row is
``variable compare value``: the comparisons offered are the ones its type takes
(``variables.comparisons_for``), and the value is a :class:`VariableValueField`, so
neither offers anything validation would refuse. A variable cannot be negated -- that is
``!=`` -- so the NOT box is hidden on its row, keeping its slot so the lists line up.

**An assignment row** (:class:`AssignmentRow`) is ``variable op value``, the ops limited
to the type's (``variables.operations_for``), and no value for a toggle.
:class:`AssignmentsField` is the ``assignments`` param type's control: the rows of a
``set_variables`` node now, and the same rows on an edge in Stage 4 (#189).

Both read the declarations they are handed, never the config: the panel passes the
bot's current ones each time it shows a node or an edge. A row naming something the bot
does not declare keeps it, listed and handed back as it was read, so opening an edge or a
node never rewrites a row. Validation is what says it is wrong.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QVBoxLayout, QWidget

from bartlebot import variables
from bartlebot.bot.schema import AssignmentModel, FlagCondition, VariableModel
from bartlebot.gui.widgets.action_button import ActionButton
from bartlebot.gui.widgets.discard import discard_widget
from bartlebot.gui.widgets.icon_button import IconButton, IconTint
from bartlebot.gui.widgets.themed_label import ThemedTextLabel
from bartlebot.gui.widgets.variable_widgets import OPTION, VARIABLE, ConditionSourceCombo, VariableValueField

_NOT_W = 52
_REM_W = 24
_OP_W = 56

#: What an assignment's op reads as in its combo.
OP_LABELS = {"set": "=", "add": "+", "toggle": "toggle"}

NO_VARIABLES_HINT = "Declare a variable in the Variables section, on the Options tab, to set it here."


def _keep_slot(widget: QWidget) -> None:
    policy = widget.sizePolicy()
    policy.setRetainSizeWhenHidden(True)
    widget.setSizePolicy(policy)


def _remove_button(tooltip: str) -> IconButton:
    return IconButton("dismiss", tooltip=tooltip, size=_REM_W, icon_size=12, tint=IconTint.DANGER)


# ---------------------------------------------------------------------------
# Condition rows
# ---------------------------------------------------------------------------


class ConditionRow(QWidget):
    """One condition on an edge: an option, NOT or not, or a variable compared with a value."""

    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)

    def __init__(
        self,
        option_names: Sequence[str],
        decls: Sequence[VariableModel],
        condition: FlagCondition | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._decls = {d.name: d for d in decls}
        self._loading = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.not_box = QCheckBox("NOT")
        self.not_box.setFixedWidth(_NOT_W)
        self.not_box.setToolTip("True when the option is off")
        _keep_slot(self.not_box)
        self.source = ConditionSourceCombo()
        self.source.setToolTip("An option the user sets before the run, or a variable the bot sets during it")
        self.compare = QComboBox()
        self.compare.setFixedWidth(_OP_W)
        self.value = VariableValueField()
        self.remove_btn = _remove_button("Remove this condition")

        layout.addWidget(self.not_box)
        layout.addWidget(self.source, 1)
        layout.addWidget(self.compare)
        layout.addWidget(self.value, 1)
        layout.addWidget(self.remove_btn)

        current = None
        if condition is not None:
            current = (VARIABLE, condition.variable) if condition.is_variable else (OPTION, condition.option or "")
        self.source.set_sources(list(option_names), [d.name for d in decls], current)
        if condition is not None and not condition.is_variable:
            self.not_box.setChecked(condition.negate)
        self._show_kind(condition)
        self._loading = False

        self.not_box.checkStateChanged.connect(lambda _s: self._emit())
        self.source.currentIndexChanged.connect(lambda _i: self._on_source())
        self.compare.currentIndexChanged.connect(lambda _i: self._emit())
        self.value.changed.connect(self._emit)
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))

    @property
    def is_variable(self) -> bool:
        source = self.source.current_source()
        return source is not None and source[0] == VARIABLE

    def condition(self) -> FlagCondition | None:
        """The row as stored, or None while it names nothing."""
        source = self.source.current_source()
        if source is None or not source[1]:
            return None
        kind, name = source
        if kind == OPTION:
            return FlagCondition(option=name, negate=self.not_box.isChecked())
        value = self.value.value()
        if value is None:
            decl = self._decls.get(name)
            value = decl.initial_value() if decl is not None else None
        if value is None:
            return None  # an enum with no values: nothing to compare with
        return FlagCondition(variable=name, compare=self.compare.currentText() or "==", value=value)

    # ------------------------------------------------------------------

    def _show_kind(self, condition: FlagCondition | None) -> None:
        """Lay the row out for what its list has picked, showing ``condition``'s comparison and value if it has them."""
        is_var = self.is_variable
        self.not_box.setVisible(not is_var)
        self.compare.setVisible(is_var)
        self.value.setVisible(is_var)
        if not is_var:
            return
        name = self.source.current_source()[1]
        decl = self._decls.get(name)
        # A variable the bot does not declare has no type to limit the comparisons by.
        offered = list(variables.comparisons_for(decl.type) if decl is not None else variables.ALL_COMPARES)
        held = condition is not None and condition.is_variable and condition.variable == name
        op = (condition.compare or "==") if held else "=="
        if op not in offered:
            offered.append(op)  # read in from the file, so kept, and validation names it
        self.compare.blockSignals(True)
        self.compare.clear()
        self.compare.addItems(offered)
        self.compare.setCurrentText(op)
        self.compare.blockSignals(False)
        if held:
            self.value.set_declaration(decl, condition.value)
        else:
            self.value.set_declaration(decl, decl.initial_value() if decl is not None else None)

    def _on_source(self) -> None:
        if self._loading:
            return
        self._show_kind(None)
        self._emit()

    def _emit(self) -> None:
        if not self._loading:
            self.changed.emit()


# ---------------------------------------------------------------------------
# Assignment rows
# ---------------------------------------------------------------------------


class AssignmentRow(QWidget):
    """One write: a variable, set / add / toggle as its type allows, and a value unless it toggles."""

    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)

    def __init__(
        self, decls: Sequence[VariableModel], row: AssignmentModel | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._decls = {d.name: d for d in decls}
        self._loading = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.variable = QComboBox()
        names = [d.name for d in decls]
        if row is not None and row.variable not in names:
            names.append(row.variable)
        self.variable.addItems(names)
        self.op = QComboBox()
        self.op.setFixedWidth(_OP_W + 12)
        self.value = VariableValueField()
        _keep_slot(self.value)
        self.remove_btn = _remove_button("Remove this assignment")
        layout.addWidget(self.variable, 1)
        layout.addWidget(self.op)
        layout.addWidget(self.value, 1)
        layout.addWidget(self.remove_btn)

        if row is not None:
            self.variable.setCurrentText(row.variable)
        self._show_variable(row)
        self._loading = False

        self.variable.currentIndexChanged.connect(lambda _i: self._on_variable())
        self.op.currentIndexChanged.connect(lambda _i: self._on_op())
        self.value.changed.connect(self._emit)
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))

    def assignment(self) -> dict | None:
        """The row as the param stores it, ``{"variable", "op", "value"}`` with no value for a toggle."""
        name = self.variable.currentText()
        if not name:
            return None
        op = self.op.currentData() or "set"
        if op == "toggle":
            return {"variable": name, "op": op}
        value = self.value.value()
        if value is None:
            decl = self._decls.get(name)
            value = 0 if op == "add" else (decl.initial_value() if decl is not None else None)
        return {"variable": name, "op": op, "value": value}

    # ------------------------------------------------------------------

    def _value_decl(self, decl: VariableModel | None, op: str) -> VariableModel | None:
        """What the value field holds: the variable's own type for a set, a whole number for an add."""
        if op == "add":
            return VariableModel(name=decl.name if decl else "amount", type="int")
        return decl

    def _show_variable(self, row: AssignmentModel | None) -> None:
        name = self.variable.currentText()
        decl = self._decls.get(name)
        offered = list(variables.operations_for(decl.type) if decl is not None else ("set", "add", "toggle"))
        held = row is not None and row.variable == name
        op = row.op if held else "set"
        if op not in offered:
            offered.append(op)  # from the file; validation names it
        self.op.blockSignals(True)
        self.op.clear()
        for item in offered:
            self.op.addItem(OP_LABELS.get(item, item), item)
        self.op.setCurrentIndex(self.op.findData(op))
        self.op.blockSignals(False)
        if held:
            self.value.set_declaration(self._value_decl(decl, op), row.value)
        else:
            self.value.set_declaration(decl, decl.initial_value() if decl is not None else None)
        self.value.setVisible(op != "toggle")

    def _on_variable(self) -> None:
        if self._loading:
            return
        self._show_variable(None)
        self._emit()

    def _on_op(self) -> None:
        if self._loading:
            return
        op = self.op.currentData() or "set"
        decl = self._decls.get(self.variable.currentText())
        start = 1 if op == "add" else (decl.initial_value() if decl is not None else None)
        self.value.set_declaration(self._value_decl(decl, op), start)
        self.value.setVisible(op != "toggle")
        self._emit()

    def _emit(self) -> None:
        if not self._loading:
            self.changed.emit()


class AssignmentsField(QWidget):
    """The ``assignments`` param: rows applied in order, and Add assignment under them.

    ``get_decls`` returns the bot's declarations when asked, so a row added after a
    variable was declared offers it.
    """

    changed = pyqtSignal()

    def __init__(self, get_decls: Callable[[], Sequence[VariableModel]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._get_decls = get_decls
        self._rows: list[AssignmentRow] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(2)
        outer.addLayout(self._rows_layout)
        self.hint = ThemedTextLabel(NO_VARIABLES_HINT, color="text_muted")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)
        self.add_btn = ActionButton("plus", "Add assignment")
        self.add_btn.clicked.connect(self._on_add)
        outer.addWidget(self.add_btn)

    @property
    def rows(self) -> list[AssignmentRow]:
        return list(self._rows)

    def set_rows(self, rows: Sequence[AssignmentModel]) -> None:
        for row in self._rows:
            discard_widget(row, self._rows_layout)
        self._rows.clear()
        decls = list(self._get_decls())
        for model in rows:
            self._add_row(decls, model)
        self._refresh_hint(decls)

    def value(self) -> list[dict]:
        """The rows as the param stores them, in order, leaving out any that names nothing."""
        return [a for row in self._rows if (a := row.assignment()) is not None]

    def add_row(self) -> AssignmentRow | None:
        """Add a row for the first variable, as Add assignment does; None while there is none to write."""
        decls = list(self._get_decls())
        if not decls:
            return None
        row = self._add_row(decls, None)
        self.changed.emit()
        return row

    # ------------------------------------------------------------------

    def _add_row(self, decls: Sequence[VariableModel], model: AssignmentModel | None) -> AssignmentRow:
        row = AssignmentRow(decls, model)
        self._rows_layout.addWidget(row)
        self._rows.append(row)
        row.changed.connect(self.changed)
        row.remove_requested.connect(self._on_remove)
        return row

    def _refresh_hint(self, decls: Sequence[VariableModel]) -> None:
        self.hint.setVisible(not decls)
        self.add_btn.setEnabled(bool(decls))
        self.add_btn.setToolTip("" if decls else NO_VARIABLES_HINT)

    def _on_add(self) -> None:
        self.add_row()

    def _on_remove(self, row: AssignmentRow) -> None:
        if row in self._rows:
            self._rows.remove(row)
            discard_widget(row, self._rows_layout)
            self.changed.emit()


def parse_rows(raw: Any) -> list[AssignmentModel] | None:
    """A stored ``assignments`` value as rows, or None when it is not rows -- which the form then keeps untouched."""
    try:
        return variables.parse_assignments(raw)
    except ValueError:
        return None
