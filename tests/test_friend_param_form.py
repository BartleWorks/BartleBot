"""Widget tests for teleport_to_friend's two-mode parameter form.

Real widget tests (QApplication required), in the style of test_canvas_property_edits.
They cover the two mechanisms the design leans on and nothing else: that the friend name
cannot reach a bot file through the panel, and that the two target modes visibly exclude
each other.
"""

from bartlebot.gui.canvas.param_panel import _DynamicParamForm
from bartlebot.nodes.teleportation.teleport_to_friend import PARAM_SCHEMA


def _form(params: dict) -> _DynamicParamForm:
    form = _DynamicParamForm()
    form.load_schema(PARAM_SCHEMA, params)
    return form


def test_the_form_never_writes_a_friend_name_into_the_bot(qapp):
    """This is what makes test_no_bot_stores_a_friend_name hold going forward: not a
    convention authors have to remember, but a panel with no such field to write from.
    The name is a bot option now, valued in Option Values and stored in settings.json."""
    form = _form({"target_hooked_player": False, "to_zone": "Z"})

    assert "friend_name" not in form.get_params()


def test_a_legacy_friend_name_is_dropped_rather_than_round_tripped(qapp):
    """Opening and saving an older bot is how the literal leaves the file."""
    form = _form({"friend_name": "Alice StormBlade", "to_zone": "Z"})

    params = form.get_params()

    assert "friend_name" not in params
    assert params["to_zone"] == "Z"


def test_the_target_player_survives_a_round_trip(qapp):
    form = _form({"target_hooked_player": True, "target_player": 3, "to_zone": "Z"})

    assert form.get_params()["target_player"] == 3


def test_hooked_player_mode_offers_the_player_picker(qapp):
    form = _form({"target_hooked_player": True, "target_player": 1, "to_zone": "Z"})

    assert form.widget("target_player").isEnabled()


def test_name_mode_grays_the_player_picker(qapp):
    """The two targets are mutually exclusive, and graying is the loudest way the panel
    can say so. Only one side of the pair is a param now -- the name is a bot option,
    edited in Option Values -- so this is the half the panel can still show."""
    form = _form({"target_hooked_player": False, "to_zone": "Z"})

    assert not form.widget("target_player").isEnabled()
    assert "friend_name" not in form.get_params()
