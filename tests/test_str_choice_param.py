"""Widget tests for the 'str_choice' param field (QApplication required).

The point of the type is that what the bot file stores and what the user reads are
different strings, such as a world stored as "DragonSpire" and shown as "Dragonspyre". A dropdown
that wrote its label back into the bot would produce a world key the zone glob never
matches, and it would do it silently, so the round trip is worth pinning down.
"""

from bartlebot.nodes.param_schema import ParamField


def _form(fields, params):
    from bartlebot.gui.canvas.param_panel import _DynamicParamForm

    form = _DynamicParamForm()
    form.load_schema(fields, params)
    return form


_WORLD = ParamField(
    key="world",
    type="str_choice",
    label="World",
    default="WizardCity",
    choices=[("WizardCity", "Wizard City"), ("DragonSpire", "Dragonspyre")],
    required=True,
)


def test_stored_value_round_trips(qapp):
    form = _form([_WORLD], {"world": "DragonSpire"})

    assert form.get_params()["world"] == "DragonSpire"


def test_the_label_is_shown_but_never_stored(qapp):
    """ "Dragonspyre" is not a zone prefix; storing it would break the arrival check."""
    form = _form([_WORLD], {"world": "DragonSpire"})
    widget = form.widget("world")

    assert widget.currentText() == "Dragonspyre"
    assert form.get_params()["world"] == "DragonSpire"


def test_an_unknown_stored_value_falls_back_to_the_first_choice(qapp):
    """A world dropped from the game leaves a bot unrunnable either way; do not crash."""
    form = _form([_WORLD], {"world": "Atlantis"})

    assert form.get_params()["world"] == "WizardCity"


def test_a_bare_string_choice_list_works(qapp):
    """The (value, label) pair is optional — plain strings are their own label."""
    field = ParamField(key="pick", type="str_choice", label="Pick", default="a", choices=["a", "b"])
    form = _form([field], {"pick": "b"})

    assert form.widget("pick").currentText() == "b"
    assert form.get_params()["pick"] == "b"
