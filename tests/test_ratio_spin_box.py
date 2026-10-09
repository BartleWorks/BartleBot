"""A ratio param: stored 0.0 to 1.0, shown and edited as a whole percent.

Real widget tests (QApplication required). The bot file is not migrated: what these
hold is that the control shows a percent, cannot leave 0% to 100%, and hands back the
same float the file had unless the user moved it.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QDoubleSpinBox

from bartlebot.gui.canvas.param_panel import _DynamicParamForm
from bartlebot.gui.widgets.inputs.ratio_spin_box import RatioSpinBox
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS

RATIO_FIELDS = [
    ("check_health_and_mana", "health_threshold"),
    ("check_health_and_mana", "mana_threshold"),
    ("collect_wisps", "if_health_below"),
    ("collect_wisps", "if_mana_below"),
    ("collect_wisps", "fail_if_health_below"),
    ("collect_wisps", "fail_if_mana_below"),
    ("handle_potions", "if_health_below"),
    ("handle_potions", "if_mana_below"),
]


def test_a_ratio_is_shown_as_a_percent(qapp):
    box = RatioSpinBox(0.15)

    assert (box.value(), box.suffix(), box.text()) == (15, "%", "15%")
    assert box.ratio() == 0.15


def test_it_cannot_leave_zero_to_a_hundred_percent(qapp):
    box = RatioSpinBox(0.5)
    assert (box.minimum(), box.maximum()) == (0, 100)

    box.setValue(250)
    assert box.ratio() == 1.0
    box.setValue(-5)
    assert box.ratio() == 0.0


def test_a_value_out_of_range_in_a_file_is_shown_held_to_the_range(qapp):
    assert RatioSpinBox(5.0).value() == 100
    assert RatioSpinBox(-1.0).value() == 0


def test_an_edit_is_handed_back_as_a_ratio(qapp):
    box = RatioSpinBox(0.5)

    box.setValue(80)

    assert box.ratio() == 0.8


def test_reading_it_back_untouched_does_not_round_a_finer_value(qapp):
    box = RatioSpinBox(0.125)

    assert box.text() == "12%" or box.text() == "13%"
    assert box.ratio() == 0.125


def test_it_is_only_as_wide_as_its_widest_value_in_its_own_font(qapp):
    box, plain = RatioSpinBox(1.0), QDoubleSpinBox()
    plain.setRange(-999_999.0, 999_999.0)
    plain.setDecimals(3)
    assert box.sizeHint().width() < plain.sizeHint().width()
    narrow = box.sizeHint().width()

    font = box.font()
    font.setPixelSize(font.pixelSize() * 2 if font.pixelSize() > 0 else 28)
    box.setFont(font)

    assert box.sizeHint().width() > narrow, "it follows the font, with no width spelled out"


@pytest.mark.parametrize(("node_type", "key"), RATIO_FIELDS)
def test_every_threshold_is_a_ratio_field_drawn_as_a_percent(qapp, node_type, key):
    schema = NODE_PARAM_SCHEMAS[node_type]
    field = next(f for f in schema if f.key == key)
    assert field.type == "ratio" and 0.0 <= field.default <= 1.0
    assert "0.0" not in field.help and "ratio" not in field.help, "the help speaks in percent too"
    form = _DynamicParamForm()
    form.load_schema(schema, {key: 0.25})

    widget = form.widget(key)

    assert isinstance(widget, RatioSpinBox) and widget.text() == "25%"
    assert form.get_params()[key] == 0.25
    widget.setValue(40)
    assert form.get_params()[key] == 0.4


def test_no_other_param_is_left_typed_as_a_bare_share(qapp):
    """A float whose default hint or help talks in percent would be the next one to forget."""
    typed = {(n, f.key) for n, schema in NODE_PARAM_SCHEMAS.items() for f in schema if f.type == "ratio"}

    assert typed == set(RATIO_FIELDS)
