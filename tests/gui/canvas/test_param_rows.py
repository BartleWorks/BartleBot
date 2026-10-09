"""The compact param row (#142): a head line and a control, behind the ``[bar][reset][?]`` gutter.

One row builder draws a node's params and a subgraph node's inputs, so everything here
holds on both pages. What changed from the rows #142 replaced: actions sit at the end of
the control line instead of in a full-width row under it, the glob hint and the default
hint fold behind ``?``, and an empty required field turns red only once it has been
touched and left empty, or after a refused save.
"""

from PyQt6.QtWidgets import QLabel

from bartlebot.gui.canvas.param_panel import (
    ZONE_GLOB_HELP,
    ZONE_PLACEHOLDER,
    _DynamicParamForm,
    _NavZoneField,
    _ZoneWidget,
)
from bartlebot.gui.theming.theme import THEME
from bartlebot.nodes.param_schema import ParamField

ZONE = ParamField(key="from_zone", type="zone", label="From Zone", required=True, default="")
NAME = ParamField(key="name", type="str", label="Name", default="", help="As the sigil shows it.")
TIMEOUT = ParamField(key="timeout", type="float", label="Timeout", default=30.0)
TEAM = ParamField(key="team_up", type="bool", label="Offers Team-Up", default=False)
SPOT = ParamField(key="spot", type="xyz", label="Spot", required=True)
DEST = ParamField(key="dest", type="nav_zone", label="Zone", required=True, default="")
PAGE = ParamField(key="wiki_page", type="wiki_page", label="Wiki page", default="")


def _form(schema, params=None, **kwargs) -> _DynamicParamForm:
    form = _DynamicParamForm()
    form.load_schema(schema, params or {}, **kwargs)
    return form


# -- the gutter -----------------------------------------------------------------------


def test_every_row_has_the_gutter(qapp):
    form = _form([ZONE, NAME, TIMEOUT, TEAM, SPOT])

    for key in ("from_zone", "name", "timeout", "team_up", "spot"):
        assert form.reset_button(key) is not None
        assert form.override_bar(key) is not None
        assert form.help_button(key) is not None


def test_on_a_node_reset_puts_the_node_types_default_back(qapp):
    form = _form([TIMEOUT], {"timeout": 45.0})
    reset = form.reset_button("timeout")
    assert reset.isEnabled()
    assert "30.0" in reset.toolTip()

    reset.click()

    assert form.get_params()["timeout"] == 30.0
    assert not reset.isEnabled()
    assert reset.toolTip() == "Already the default"


def test_on_a_node_the_bar_is_never_lit(qapp):
    form = _form([TIMEOUT], {"timeout": 45.0})

    assert not form.override_bar("timeout").active


def test_a_required_param_with_no_default_has_nothing_to_reset_to(qapp):
    form = _form([ZONE], {"from_zone": "Aquila/AQ_Z00_Hub"})

    assert not form.reset_button("from_zone").isEnabled()
    assert form.reset_button("from_zone").toolTip() == "No default: this param has to be set"


def test_an_edit_enables_reset_without_rebuilding_the_row(qapp):
    form = _form([TIMEOUT])
    spin = form.widget("timeout")
    row = form.row("timeout")

    spin.setValue(12.0)

    assert form.reset_button("timeout").isEnabled()
    assert form.widget("timeout") is spin and form.row("timeout") is row


def test_a_linked_row_has_nothing_to_reset(qapp):
    form = _form([TIMEOUT], {"timeout": 45.0}, linkable=True, links={"timeout": "Delay"})

    assert not form.reset_button("timeout").isEnabled()


def test_on_a_subgraph_node_reset_drops_this_nodes_value(qapp):
    form = _form([NAME], {"name": "Mine"}, resettable={"name"})
    asked = []
    form.reset_requested.connect(asked.append)

    assert form.override_bar("name").active
    form.reset_button("name").click()

    assert asked == ["name"]


# -- descriptions ----------------------------------------------------------------------


def test_a_fields_help_folds_behind_its_question_mark(qapp):
    form = _form([NAME])
    description = form.description("name")
    assert description.text() == "As the sigil shows it."
    assert not description.isVisibleTo(form)

    form.help_button("name").setChecked(True)

    assert description.isVisibleTo(form)


def test_show_descriptions_opens_every_row_at_once(qapp):
    form = _form([NAME, TIMEOUT, TEAM])

    form.set_descriptions_visible(True)

    assert form.description("name").isVisibleTo(form)


# -- required fields --------------------------------------------------------------------


def test_a_fresh_form_draws_no_red(qapp):
    form = _form([ZONE, SPOT])

    assert form.to_fill() == 2
    assert not form.required_errors_shown()
    assert THEME.error not in form.widget("from_zone").line_edit.styleSheet()
    label, _ = form._field_labels[0]
    assert THEME.error not in label.text()


def test_a_required_field_turns_red_once_left_empty(qapp):
    form = _form([ZONE, NAME])
    signals = []
    form.required_shown_changed.connect(lambda: signals.append(1))
    zone_edit = form.widget("from_zone").line_edit
    name_edit = form.widget("name")

    qapp.focusChanged.emit(zone_edit, name_edit)

    assert form.required_shown("from_zone")
    assert form.required_errors_shown()
    assert THEME.error in zone_edit.styleSheet()
    assert signals == [1]


def test_moving_within_a_row_does_not_count_as_leaving_it(qapp):
    form = _form([SPOT])
    field = form.widget("spot")

    qapp.focusChanged.emit(field.axis_edit("x"), field.axis_edit("y"))

    assert not form.required_shown("spot")


def test_filling_a_red_field_clears_it_at_once(qapp):
    form = _form([ZONE])
    form.show_required_errors()
    zone = form.widget("from_zone")
    assert THEME.error in zone.line_edit.styleSheet()

    zone.setText("Aquila/AQ_Z00_Hub")

    assert THEME.error not in zone.line_edit.styleSheet()
    assert not form.required_errors_shown()
    assert form.to_fill() == 0


# -- compact rows -------------------------------------------------------------------------


def test_a_zone_row_is_a_field_with_fill_current_at_its_end(qapp):
    form = _form([ZONE])
    zone = form.widget("from_zone")

    assert isinstance(zone, _ZoneWidget)
    assert zone.line_edit.placeholderText() == ZONE_PLACEHOLDER
    assert ZONE_GLOB_HELP in zone.line_edit.toolTip()
    assert zone.layout().indexOf(zone.fill_btn) >= 0
    assert not any("Glob patterns" in lbl.text() for lbl in form.findChildren(QLabel)), "no hint line of its own"


def test_a_destination_row_keeps_fill_and_go_on_the_pickers_line(qapp):
    form = _form([DEST])
    field = form.widget("dest")

    assert isinstance(field, _NavZoneField)
    assert field.go_btn.text() == "" and field.go_btn.toolTip().startswith("Go to zone")
    line = field.layout().itemAt(0).layout()
    assert all(line.indexOf(w) >= 0 for w in (field.picker, field.fill_btn, field.go_btn))


def test_a_bool_row_is_its_checkbox(qapp):
    form = _form([TEAM])

    assert form.widget("team_up").text() == "Offers Team-Up"
    assert form._field_labels == [], "no separate label: the checkbox is the head line"


def test_a_row_action_sits_at_the_end_of_its_field(qapp):
    clicked = []
    form = _form([PAGE], row_actions={"wiki_page": ("download", "Import drops from wiki…", lambda: clicked.append(1))})

    action = form.row_action("wiki_page")
    action.click()

    assert clicked == [1]
    assert action.toolTip() == "Import drops from wiki…"


def test_building_the_rows_never_shows_a_parentless_widget(qapp, parentless_shows):
    form = _form([ZONE, NAME, TIMEOUT, TEAM, SPOT, DEST, PAGE], linkable=True, links={"name": "Name"})
    form.set_descriptions_visible(True)
    form.load_schema([ZONE, NAME], {}, resettable={"name"})

    assert parentless_shows == []
