"""The controls a variable is picked and valued in, shared by every editor that writes one.

Three surfaces edit a variable's value: the Variables section's dialog (its initial
value), the edge panel's condition rows, and the ``assignments`` editor of a
``set_variables`` node. They share :class:`VariableValueField`, so a value control is
built from the declaration in one place: a checkbox for a bool, a spin box for an int,
the enum's own values for an enum. **It never offers a value the type refuses**, which
is the whole of what keeps a condition from comparing ``swords`` with ``"Moon"``.

A row read from a hand-edited file can still hold such a value. The field keeps it --
shown as a blank, never as a value it is not -- and hands it back unchanged until the
user picks one, so opening an edge never rewrites it, and validation is what says it
is wrong.

:class:`ConditionSourceCombo` is the one grouped list a condition row picks from:
the bot's options, then its variables, under a heading each.
"""

from __future__ import annotations

from typing import Any

from bartleby.qt.widgets.layout.discard import discard_widget
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QSpinBox, QWidget

from bartlebot.bot import variables
from bartlebot.bot.schema import VariableModel

#: A spin box's range: wide enough for any counter a bot keeps.
INT_RANGE = (-999_999, 999_999)

#: What a control shows in place of a stored value its type cannot hold.
NOT_A_VALUE = "—"

_NOTHING = object()


class VariableValueField(QWidget):
    """One value for one variable, in the control its type calls for.

    :meth:`set_declaration` rebuilds the control, so a row whose variable changes keeps
    the same field. With no declaration -- a row naming a variable the bot does not
    declare -- the stored value is shown as text and handed back as it came.
    """

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._decl: VariableModel | None = None
        self._control: QWidget | None = None
        # A stored value the control cannot show, kept until the user picks another.
        self._held: Any = _NOTHING
        self._loading = False

    @property
    def control(self) -> QWidget | None:
        """The checkbox, spin box, combo or label the value is shown in."""
        return self._control

    @property
    def holds_a_stored_value(self) -> bool:
        """Whether the field is handing back a value its type refuses, untouched, as it was read."""
        return self._held is not _NOTHING

    def set_declaration(self, decl: VariableModel | None, value: Any = None) -> None:
        """Show ``value`` for a variable declared as ``decl``, in a control built for its type."""
        if self._control is not None:
            discard_widget(self._control, self._layout)
            self._control = None
        self._decl = decl
        self._held = _NOTHING
        self._loading = True
        try:
            if decl is None:
                self._held = value
                control: QWidget = QLabel(variables.format_value(value) if value is not None else NOT_A_VALUE)
                control.setToolTip("This row names a variable the bot does not declare.")
            elif not variables.value_fits(decl, value):
                self._held = value
                control = self._build(decl, None)
            else:
                control = self._build(decl, value)
            self._layout.addWidget(control, 1)
            self._control = control
        finally:
            self._loading = False

    def value(self) -> Any:
        """The value to store: the control's, or the one read in while it is still untouched."""
        if self._held is not _NOTHING:
            return self._held
        control = self._control
        if isinstance(control, QCheckBox):
            return control.isChecked()
        if isinstance(control, QSpinBox):
            return control.value()
        if isinstance(control, QComboBox):
            return control.currentText() if control.currentIndex() >= 0 else None
        return None

    def set_value(self, value: Any) -> None:
        """Show ``value`` in the control there is, as though it had been read in."""
        self.set_declaration(self._decl, value)

    # ------------------------------------------------------------------

    def _build(self, decl: VariableModel, value: Any) -> QWidget:
        """The control for ``decl``'s type showing ``value``, or showing nothing while ``value`` is None."""
        if decl.type == "bool":
            box = QCheckBox()
            if value is None:
                # Neither: tristate is the one way a checkbox can say so.
                box.setTristate(True)
                box.setCheckState(Qt.CheckState.PartiallyChecked)
                box.setText(NOT_A_VALUE)
            else:
                box.setChecked(bool(value))
                box.setText(variables.format_value(bool(value)))
            box.checkStateChanged.connect(lambda _s, b=box: self._on_bool(b))
            return box
        if decl.type == "int":
            spin = QSpinBox()
            low, high = INT_RANGE
            if value is None:
                # One step below the range, shown as a dash, is the only blank a spin box has.
                spin.setRange(low - 1, high)
                spin.setSpecialValueText(NOT_A_VALUE)
                spin.setValue(low - 1)
            else:
                spin.setRange(low, high)
                spin.setValue(int(value))
            spin.valueChanged.connect(lambda _v, s=spin: self._on_int(s))
            return spin
        combo = QComboBox()
        combo.addItems(list(decl.values or []))
        if value is None:
            combo.setPlaceholderText(NOT_A_VALUE)
            combo.setCurrentIndex(-1)
        else:
            combo.setCurrentText(str(value))
        combo.currentIndexChanged.connect(lambda _i: self._on_picked())
        return combo

    def _on_bool(self, box: QCheckBox) -> None:
        if self._loading:
            return
        if box.isTristate():
            # The first click leaves "neither" for good: only true and false are values.
            box.blockSignals(True)
            box.setTristate(False)
            box.setChecked(box.checkState() != Qt.CheckState.Unchecked)
            box.blockSignals(False)
        box.setText(variables.format_value(box.isChecked()))
        self._on_picked()

    def _on_int(self, spin: QSpinBox) -> None:
        if self._loading:
            return
        low, high = INT_RANGE
        if spin.minimum() < low:
            spin.blockSignals(True)
            spin.setSpecialValueText("")
            spin.setRange(low, high)
            spin.blockSignals(False)
        self._on_picked()

    def _on_picked(self) -> None:
        if self._loading:
            return
        self._held = _NOTHING
        self.changed.emit()


# ---------------------------------------------------------------------------
# The grouped list a condition row picks from
# ---------------------------------------------------------------------------

OPTION = "option"
VARIABLE = "variable"

#: The role an item's ``(kind, name)`` is kept under.
_SOURCE_ROLE = Qt.ItemDataRole.UserRole + 1


class ConditionSourceCombo(QComboBox):
    """The bot's options, then its variables, each under a heading that cannot be picked.

    An item's data is ``(kind, name)``, ``kind`` being :data:`OPTION` or :data:`VARIABLE`.
    A name the row holds and the bot does not declare is listed under its kind, so a
    row is never shown naming something else.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = QStandardItemModel(self)
        self.setModel(self._model)

    def set_sources(
        self, options: list[str], variable_names: list[str], current: tuple[str, str] | None = None
    ) -> None:
        """List ``options`` and ``variable_names``, and pick ``current`` -- added if it is in neither."""
        self.blockSignals(True)
        try:
            self._model.clear()
            options = list(options)
            variable_names = list(variable_names)
            if current is not None:
                kind, name = current
                listed = options if kind == OPTION else variable_names
                if name and name not in listed:
                    listed.append(name)
            for heading, kind, names in (("Options", OPTION, options), ("Variables", VARIABLE, variable_names)):
                if not names:
                    continue
                head = QStandardItem(heading)
                head.setFlags(Qt.ItemFlag.NoItemFlags)
                font = QFont(head.font())
                font.setBold(True)
                head.setFont(font)
                self._model.appendRow(head)
                for name in names:
                    item = QStandardItem(name)
                    item.setData((kind, name), _SOURCE_ROLE)
                    self._model.appendRow(item)
            self.setCurrentIndex(self._index_of(current) if current is not None else self._first_pickable())
        finally:
            self.blockSignals(False)

    def current_source(self) -> tuple[str, str] | None:
        """``(kind, name)`` of the picked item, or None when nothing is."""
        data = self.currentData(_SOURCE_ROLE)
        return tuple(data) if data else None

    def sources(self) -> list[tuple[str, str]]:
        """Every pickable ``(kind, name)``, in the order listed."""
        found = []
        for row in range(self._model.rowCount()):
            data = self._model.item(row).data(_SOURCE_ROLE)
            if data:
                found.append(tuple(data))
        return found

    def _index_of(self, source: tuple[str, str]) -> int:
        for row in range(self._model.rowCount()):
            data = self._model.item(row).data(_SOURCE_ROLE)
            if data and tuple(data) == tuple(source):
                return row
        return -1

    def _first_pickable(self) -> int:
        for row in range(self._model.rowCount()):
            if self._model.item(row).data(_SOURCE_ROLE):
                return row
        return -1
