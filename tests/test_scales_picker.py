"""The 'scales' param type: the picker's sections, its button, the form field and the node error."""

from wizwalker import XYZ

from bartlebot import scales
from bartlebot.bot.schema import BotConfig
from bartlebot.gui.widgets.picker_popup import filter_sections
from bartlebot.gui.widgets.scales_picker import PLACEHOLDER, ScalesButton, describe_key, scales_sections
from bartlebot.nodes.param_schema import ParamField
from bartlebot.nodes.registry import NODE_PARAM_SCHEMAS
from bartlebot.setup_steps import value_empty


def _entry(key: str, name: str, world: str, level: int | None) -> scales.Scales:
    point = XYZ(0.0, 0.0, 0.0)
    step = scales.Step(scales.SIGIL, "A", "B", point, point)
    return scales.Scales(key, name, world, level, "A", (step,), "B", point, point)


_TABLE = {
    e.key: e
    for e in (
        _entry("tartarus", "Tartarus", "Aquila", 90),
        _entry("olympus", "Mount Olympus", "Aquila", 30),
        _entry("unleveled", "Somewhere", "Aquila", None),
        _entry("spyre", "Great Spyre", "DragonSpire", 50),
    )
}


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def test_sections_are_one_per_world_by_display_name_with_rows_by_level():
    sections = scales_sections(_TABLE)

    assert [title for title, _ in sections] == ["Aquila", "Dragonspyre"]
    # An entry with no recorded level sorts last, not first.
    assert [row.key for row in sections[0][1]] == ["olympus", "tartarus", "unleveled"]


def test_a_row_shows_its_level_and_stores_the_key():
    row = scales_sections(_TABLE)[0][1][0]

    assert (row.key, row.label, row.meta) == ("olympus", "Mount Olympus", "level 30")


def test_typing_a_world_narrows_the_list_to_that_world():
    visible = filter_sections(scales_sections(_TABLE), "dragonspyre", broad=True)

    assert [row.key for _, rows in visible for row in rows] == ["spyre"]


def test_typing_a_level_finds_the_scales_of_that_level():
    visible = filter_sections(scales_sections(_TABLE), "level 90", broad=True)

    assert [row.key for _, rows in visible for row in rows] == ["tartarus"]


def test_describe_key_marks_a_key_the_table_does_not_have():
    assert describe_key(_TABLE, "tartarus") == "Aquila · Tartarus (level 90)"
    assert describe_key(_TABLE, "gone") == "gone (not a known Scales)"


# ---------------------------------------------------------------------------
# The button and the form
# ---------------------------------------------------------------------------


def test_the_button_shows_the_scales_and_stores_only_its_key(qapp):
    button = ScalesButton(_TABLE)

    button.set_value("tartarus")

    assert button.value() == "tartarus"
    assert button.text() == "Aquila · Tartarus (level 90)"


def test_an_unset_button_stays_unset(qapp):
    button = ScalesButton(_TABLE)

    assert button.value() == ""
    assert button.text() == PLACEHOLDER


def test_a_key_no_longer_in_the_table_is_kept_and_marked(qapp):
    """Opening a bot must not drop its param just because this build's table lacks the entry."""
    button = ScalesButton(_TABLE)

    button.set_value("gone")

    assert button.value() == "gone"
    assert "not a known Scales" in button.text()


def test_the_form_gives_a_scales_param_the_picker_and_reads_its_key_back(qapp):
    from bartlebot.gui.canvas.param_panel import _DynamicParamForm

    form = _DynamicParamForm()
    key = next(iter(scales.load()))
    form.load_schema([ParamField(key="scales", type="scales", label="Scales")], {"scales": key})

    assert isinstance(form.widget("scales"), ScalesButton)
    assert form.get_params()["scales"] == key


def test_handle_level_scaling_takes_one_param_and_it_is_a_scales():
    """The point of #141: which Scales is the only thing an author chooses."""
    assert [(f.key, f.type, f.required) for f in NODE_PARAM_SCHEMAS["handle_level_scaling"]] == [
        ("scales", "scales", True)
    ]


def test_an_unset_scales_is_empty():
    assert value_empty("scales", "") is True
    assert value_empty("scales", "tartarus") is False


# ---------------------------------------------------------------------------
# Validation on the node
# ---------------------------------------------------------------------------


def _errors(scales_key: str) -> list[str]:
    from bartlebot.gui.canvas.edit_scene import _validate_node

    config = BotConfig.model_validate(
        {
            "id": "b",
            "name": "B",
            "initial": "s",
            "recovery_state": "s",
            "states": [{"id": "s", "node_type": "handle_level_scaling", "params": {"scales": scales_key}}],
            "transitions": [],
        }
    )
    return _validate_node(config.states[0], config)[2]


def test_a_key_the_table_lacks_is_an_error_on_the_node():
    assert any("not a Scales of Knowledge" in reason for reason in _errors("no_such_scales"))


def test_a_known_key_raises_no_scales_error():
    key = next(iter(scales.load()))

    assert not any("scales" in reason for reason in _errors(key))


def test_an_unset_scales_is_the_required_param_error():
    assert any("Required param 'scales'" in reason for reason in _errors(""))
