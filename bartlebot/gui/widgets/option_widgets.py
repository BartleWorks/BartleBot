"""The one option-type -> widget mapping.

A bot option's type decides which control edits it, and that mapping used to be
written out three times: once in the Config tab's row builder, once in the
properties panel's ``_make_widget``, and once in the per-node override renderer
that Phase 3 deleted.  Three copies is how ``_AddOptionDialog`` came to hardcode
``type="bool"`` while the row builder happily rendered floats -- pressing Edit on
Safety Delay rewrote it as a checkbox.

Both option surfaces now share this module: the Setup values view, the Options
declaration editor, and the Add/Edit dialog inside it.  ``_make_widget`` stays
where it is -- it maps *param* fields, a much larger vocabulary (zone, xyz,
wiki_page, drop_keep_list) that no bot option can have.

Four operations, all dispatching on the widget rather than on a remembered type
string, so a caller holding only the widget can still read and write it:
``make_option_widget``, ``widget_value``, ``set_widget_value``, ``connect_widget``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from bartlebot.gui.widgets.list_text import format_list_text, parse_list_text
from bartlebot.gui.widgets.player_list_widget import PlayerListWidget

#: Every type a bot option may declare, in the order the Add/Edit dialog lists them.
OPTION_TYPES: tuple[str, ...] = ("bool", "int", "float", "str", "str_list", "player_list")

#: What to call each type to a user who has never read the schema.
TYPE_LABELS: dict[str, str] = {
    "bool": "Yes / No",
    "int": "Whole number",
    "float": "Decimal number",
    "str": "Text",
    "str_list": "List of text",
    "player_list": "Players",
}


class StrListLineEdit(QLineEdit):
    """A comma-separated list of strings, identified by type rather than by flag.

    ``QLineEdit`` alone cannot say whether its text is one string or several, and
    ``widget_value`` dispatches on the widget -- so the distinction has to be a
    class.  The properties panel carries its own copy for the same reason.
    """


def empty_value(opt_type: str) -> Any:
    """The value to show for a declaration of ``None`` -- "the author has no opinion".

    There is still a control to fill, and every type has an empty-handed value to
    fill it with.  This is *display* only: filling a widget is not storing a value,
    and nothing here reaches the bot file or the override store.
    """
    if opt_type == "bool":
        return False
    if opt_type == "int":
        return 0
    if opt_type == "float":
        return 0.0
    if opt_type in ("str_list", "player_list"):
        return []
    return ""


def make_option_widget(
    opt_type: str,
    value: Any = None,
    *,
    connected: int = 0,
    tooltip: str = "",
    label: str = "",
) -> QWidget:
    """Build the control that edits an option of *opt_type*, showing *value*.

    *connected* is how many clients are hooked right now; it only greys the player
    checkboxes that have nobody behind them.  *label* is the checkbox caption a
    ``bool`` carries instead of a separate field label.
    """
    widget: QWidget
    if opt_type == "bool":
        widget = QCheckBox(label)
    elif opt_type == "int":
        widget = QSpinBox()
        widget.setRange(-2_147_483_648, 2_147_483_647)
    elif opt_type == "float":
        widget = QDoubleSpinBox()
        widget.setDecimals(4)
        widget.setRange(-1e12, 1e12)
    elif opt_type == "player_list":
        widget = PlayerListWidget()
        widget.set_available_players(connected)
    elif opt_type == "str_list":
        widget = StrListLineEdit()
        widget.setPlaceholderText("e.g. ItemA, ItemB")
    else:  # str, and any type a future schema adds that this build cannot name
        widget = QLineEdit()

    set_widget_value(widget, value if value is not None else empty_value(opt_type))
    if tooltip:
        widget.setToolTip(tooltip)
    return widget


def set_widget_value(widget: QWidget, value: Any) -> None:
    """Show *value* in *widget*, coercing a value of the wrong shape rather than raising.

    A stored value can be the wrong type -- an author who changes an option's type
    strands every value written under the old one -- and a control that refuses to
    build is worse than one showing an empty-handed value the user can correct.
    """
    if isinstance(widget, QCheckBox):
        widget.setChecked(bool(value))
    elif isinstance(widget, QSpinBox):
        widget.setValue(_as_int(value))
    elif isinstance(widget, QDoubleSpinBox):
        widget.setValue(_as_float(value))
    elif isinstance(widget, PlayerListWidget):
        widget.set_value([v for v in value if isinstance(v, int)] if isinstance(value, list) else [])
    elif isinstance(widget, StrListLineEdit):
        widget.setText(format_list_text([str(v) for v in value]) if isinstance(value, list) else str(value or ""))
    elif isinstance(widget, QLineEdit):
        widget.setText("" if value is None else str(value))


def widget_value(widget: QWidget) -> Any:
    """Read the current value out of *widget*, in the type the option declares."""
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        return widget.value()
    if isinstance(widget, PlayerListWidget):
        return widget.get_value()
    if isinstance(widget, StrListLineEdit):
        return parse_list_text(widget.text())
    if isinstance(widget, QLineEdit):
        return widget.text()
    return None


def connect_widget(widget: QWidget, callback: Callable[[Any], None]) -> None:
    """Call *callback* with the new value whenever the user changes *widget*.

    Line edits report on ``editingFinished`` rather than per keystroke: a value
    edit writes an override row to ``settings.json``, and a save per character
    typed is a disk write per character typed.
    """
    if isinstance(widget, QCheckBox):
        widget.toggled.connect(lambda _checked: callback(widget_value(widget)))
    elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        widget.valueChanged.connect(lambda _v: callback(widget_value(widget)))
    elif isinstance(widget, PlayerListWidget):
        widget.changed.connect(lambda: callback(widget_value(widget)))
    elif isinstance(widget, QLineEdit):
        widget.editingFinished.connect(lambda: callback(widget_value(widget)))


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
