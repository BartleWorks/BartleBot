"""The 'xyz' param field, and the validation that reads what it stores (#100).

A coordinate used to be three spinboxes, which have no "no value" state: an optional
coordinate left unset was saved as (0, 0, 0) on the first unrelated edit, and a
required one could never look unfilled. Blank is now absent, a half-filled field is
invalid rather than saved, and a field nobody touched writes back what it loaded.
"""

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QValidator
from PyQt6.QtWidgets import QApplication

from bartlebot.gui.canvas.edit_scene import _required_field_empty
from bartlebot.nodes.param_schema import ParamField
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS

_OPTIONAL = ParamField(key="spot", type="xyz", label="Spot", default=None, required=False)
_REQUIRED = ParamField(key="spot", type="xyz", label="Spot", default=None, required=True)
_NAMES = ParamField(key="names", type="str", label="Names", default="")


def _form(fields, params):
    from bartlebot.gui.canvas.param_panel import _DynamicParamForm

    form = _DynamicParamForm()
    form.load_schema(fields, params)
    return form


def _type(field, x: str, y: str, z: str) -> None:
    for axis, text in zip("xyz", (x, y, z)):
        field.axis_edit(axis).setText(text)


class _FakeClipboard:
    def __init__(self, text: str = "") -> None:
        self._text = text

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802 - mirrors QClipboard
        self._text = text


@pytest.fixture
def clipboard(monkeypatch):
    """A stand-in for the system clipboard, which every xdist worker on the machine shares."""
    fake = _FakeClipboard()
    monkeypatch.setattr(QApplication, "clipboard", staticmethod(lambda: fake))
    return fake


# ---------------------------------------------------------------------------
# Blank is absent
# ---------------------------------------------------------------------------


def test_an_unset_optional_coordinate_loads_blank_and_is_not_written(qapp):
    form = _form([_OPTIONAL], {})
    field = form.widget("spot")

    assert [field.axis_edit(a).text() for a in "xyz"] == ["", "", ""]
    assert "spot" not in form.get_params()


def test_editing_another_field_leaves_an_unset_coordinate_absent(qapp):
    """The reproduction from #100: this used to save {"x": 0.0, "y": 0.0, "z": 0.0}."""
    form = _form([_NAMES, _OPTIONAL], {"names": ""})
    form.widget("names").setText("Mandrake")

    assert form.get_params() == {"names": "Mandrake"}


def test_clearing_every_box_removes_the_coordinate(qapp):
    form = _form([_OPTIONAL], {"spot": {"x": 1.0, "y": 2.0, "z": 3.0}})
    _type(form.widget("spot"), "", "", "")

    assert "spot" not in form.get_params()


# ---------------------------------------------------------------------------
# Untouched writes back what it loaded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored",
    [
        {"x": -6704.81, "y": 6382.21, "z": 29.85},
        {"x": 1234.5, "y": 0.0, "z": -12.0},
        {"x": 1, "y": 2, "z": 3},  # ints stay ints
        {"x": 0.0, "y": 0.0, "z": 0.0},  # a legacy placeholder is left for #102 to remove
    ],
)
def test_an_untouched_coordinate_writes_back_exactly_what_it_loaded(qapp, stored):
    form = _form([_OPTIONAL], {"spot": dict(stored)})

    written = form.get_params()["spot"]
    assert written == stored
    assert [type(v) for v in written.values()] == [type(v) for v in stored.values()]


def test_a_loaded_value_shows_at_most_two_decimals_without_padding(qapp):
    field = _form([_OPTIONAL], {"spot": {"x": 1234.5, "y": -0.25, "z": 7.0}}).widget("spot")

    assert [field.axis_edit(a).text() for a in "xyz"] == ["1234.5", "-0.25", "7"]


def test_a_typed_coordinate_is_written_as_floats(qapp):
    form = _form([_OPTIONAL], {})
    _type(form.widget("spot"), "10", "-2.5", ".75")

    assert form.get_params()["spot"] == {"x": 10.0, "y": -2.5, "z": 0.75}


# ---------------------------------------------------------------------------
# Invalid: partly filled, or an unfinished number
# ---------------------------------------------------------------------------


def test_a_partly_filled_coordinate_is_not_written_and_marks_the_blank_boxes(qapp):
    form = _form([_OPTIONAL], {})
    field = form.widget("spot")
    _type(field, "10", "", "30")

    assert "spot" not in form.get_params()
    assert field.marked_axes() == {"y"}


def test_emptying_one_box_of_a_stored_coordinate_drops_it(qapp):
    """What is saved always matches what the boxes show: no stale previous value."""
    form = _form([_OPTIONAL], {"spot": {"x": 1.0, "y": 2.0, "z": 3.0}})
    form.widget("spot").axis_edit("z").setText("")

    assert "spot" not in form.get_params()


def test_an_unfinished_number_is_invalid_and_marked(qapp):
    form = _form([_OPTIONAL], {})
    field = form.widget("spot")
    _type(field, "1", "-", "3")

    assert field.value() is None
    assert field.marked_axes() == {"y"}


@pytest.mark.parametrize(
    ("text", "state"),
    [
        ("-12.5", QValidator.State.Acceptable),
        ("999999", QValidator.State.Acceptable),
        ("abc", QValidator.State.Invalid),
        ("1.234", QValidator.State.Invalid),  # two decimals at most
        ("1,5", QValidator.State.Invalid),  # "." whatever the Windows region says
        ("1e5", QValidator.State.Invalid),
    ],
)
def test_a_box_accepts_only_a_number_with_two_decimals(qapp, text, state):
    edit = _form([_OPTIONAL], {}).widget("spot").axis_edit("x")

    assert edit.validator().validate(text, 0)[0] == state


# ---------------------------------------------------------------------------
# Required
# ---------------------------------------------------------------------------


def test_a_blank_required_coordinate_is_not_marked_before_it_is_left_empty(qapp):
    """A freshly placed node is not a wall of red before anyone has touched it (#142)."""
    field = _form([_REQUIRED], {}).widget("spot")

    assert field.marked_axes() == set()


def test_a_blank_required_coordinate_marks_every_box_once_shown(qapp):
    form = _form([_REQUIRED], {})
    form.show_required_errors()

    assert form.widget("spot").marked_axes() == {"x", "y", "z"}


def test_an_all_zero_required_coordinate_is_marked(qapp):
    """(0, 0, 0) is what an unset coordinate was saved as before; never a real position."""
    form = _form([_REQUIRED], {"spot": {"x": 0.0, "y": 0.0, "z": 0.0}})
    form.show_required_errors()

    assert form.widget("spot").marked_axes() == {"x", "y", "z"}


def test_a_filled_required_coordinate_is_not_marked(qapp):
    field = _form([_REQUIRED], {"spot": {"x": 0.0, "y": 5.0, "z": 0.0}}).widget("spot")

    assert field.marked_axes() == set()


def test_a_blank_optional_coordinate_is_not_marked(qapp):
    assert _form([_OPTIONAL], {}).widget("spot").marked_axes() == set()


# ---------------------------------------------------------------------------
# Setting it: Record, Fill current, Paste
# ---------------------------------------------------------------------------


def test_set_xyz_rounds_to_two_decimals_and_emits_once(qapp):
    form = _form([_OPTIONAL], {})
    emitted = []
    form.changed.connect(lambda: emitted.append(1))

    form.set_xyz("spot", 1.23456, -7.891, 3.0)

    assert form.get_params()["spot"] == {"x": 1.23, "y": -7.89, "z": 3.0}
    assert len(emitted) == 1


@pytest.mark.parametrize(
    "stored",
    [
        {"x": -6704.8123, "y": 6382.2177, "z": 29.8531},  # more precise than the boxes show
        None,  # blank
    ],
)
def test_restore_puts_a_coordinate_back_exactly_after_record_overwrote_it(qapp, stored):
    """A cancelled recording restores what was there. Put back through set_xyz, a
    stored coordinate would lose everything past two decimals, and a blank one could
    not be blank again."""
    form = _form([_OPTIONAL], {"spot": dict(stored)} if stored else {})
    saved = form.snapshot(["spot"])

    form.set_xyz("spot", 1.0, 2.0, 3.0)
    form.restore(saved)

    assert form.get_params().get("spot") == stored


def test_restore_puts_back_a_half_typed_coordinate_as_it_was(qapp):
    form = _form([_OPTIONAL], {})
    field = form.widget("spot")
    _type(field, "10", "", "3")
    saved = form.snapshot(["spot"])

    form.set_xyz("spot", 1.0, 2.0, 3.0)
    form.restore(saved)

    assert field.texts() == ("10", "", "3")
    assert field.marked_axes() == {"y"}


def test_snapshot_skips_keys_the_form_does_not_have(qapp):
    form = _form([_OPTIONAL], {})

    assert list(form.snapshot(["spot", "not_a_param"])) == ["spot"]


def test_paste_button_fills_the_axes_the_clipboard_names(qapp, clipboard):
    form = _form([_OPTIONAL], {"spot": {"x": 1.0, "y": 2.0, "z": 3.0}})
    clipboard.setText('{"y": 7}')

    form.widget("spot")._paste()

    assert form.get_params()["spot"] == {"x": 1.0, "y": 7.0, "z": 3.0}


def test_ctrl_v_of_a_whole_coordinate_into_one_box_fills_all_three(qapp, qtbot, clipboard):
    form = _form([_OPTIONAL], {})
    clipboard.setText("mass tp XYZ(10.5, -20, 30.125)")

    qtbot.keyClick(form.widget("spot").axis_edit("y"), Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

    assert form.get_params()["spot"] == {"x": 10.5, "y": -20.0, "z": 30.12}


def test_copy_writes_the_coordinate_as_json(qapp, clipboard):
    form = _form([_OPTIONAL], {"spot": {"x": 1.5, "y": 2.0, "z": -3.25}})

    form.widget("spot")._copy()

    assert clipboard.text() == '{"x": 1.5, "y": 2.0, "z": -3.25}'


def _copy_action(field):
    return next(a for a in field.box_menu(field.axis_edit("x")).actions() if a.text() == "Copy XYZ")


def test_copy_and_teleport_need_a_complete_coordinate(qapp):
    field = _form([_OPTIONAL], {}).widget("spot")
    assert not _copy_action(field).isEnabled()
    assert not field.teleport_button.isEnabled()

    _type(field, "1", "2", "")
    assert not _copy_action(field).isEnabled()

    field.axis_edit("z").setText("3")
    assert _copy_action(field).isEnabled()
    assert field.teleport_button.isEnabled()


def test_copy_and_paste_are_on_each_boxs_menu_ahead_of_its_own(qapp):
    """They left the button row (#142) for the right-click menu, as Unity and Blender keep them."""
    field = _form([_OPTIONAL], {"spot": {"x": 1.0, "y": 2.0, "z": 3.0}}).widget("spot")

    texts = [a.text() for a in field.box_menu(field.axis_edit("y")).actions()]

    assert texts[:2] == ["Copy XYZ", "Paste XYZ"]
    assert len(texts) > 3, "the box's own Undo / Cut / Copy / Paste follow the separator"


def test_fill_current_and_teleport_sit_on_the_boxes_line(qapp):
    field = _form([_OPTIONAL], {}).widget("spot")
    line = field.layout()

    assert all(line.indexOf(w) >= 0 for w in (field.axis_edit("x"), field.fill_button, field.teleport_button))
    assert field.teleport_button.text() == "", "the glyph alone; the verb is in the tooltip"


# ---------------------------------------------------------------------------
# Validation: _required_field_empty
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"spot": None},
        {"spot": {"x": 0.0, "y": 0.0, "z": 0.0}},
        {"spot": {"x": 0, "y": 0, "z": 0}},
        {"spot": {"x": 5.0, "y": 1.0}},  # a hand-edited file missing an axis
        {"spot": {"x": "a", "y": 1.0, "z": 2.0}},
    ],
)
def test_a_required_coordinate_with_no_real_position_is_empty(params):
    assert _required_field_empty(_REQUIRED, params)


def test_a_required_coordinate_with_any_nonzero_axis_is_filled():
    assert not _required_field_empty(_REQUIRED, {"spot": {"x": 0.0, "y": 0.0, "z": 12.5}})


def test_no_required_coordinate_defaults_to_a_position():
    """A new node's required coordinate has to start blank, or it can never look unfilled."""
    offenders = [
        f"{node_type}.{field.key}"
        for node_type, schema in NODE_PARAM_SCHEMAS.items()
        for field in schema
        if field.type == "xyz" and field.default is not None
    ]

    assert not offenders
