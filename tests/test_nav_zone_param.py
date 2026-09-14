"""Widget tests for the 'nav_zone' param, driven through the real Go To Zone schema (QApplication required).

The point of the type is that the bot file stores a zone path and nothing else: the button shows
the zone's name and its world, but a world written back into the bot would be a second value
free to disagree with the first.
"""

from bartlebot.gui.widgets.picker_popup import apply_toggle

_ENTRANCE = "Krokotopia/KT_Krokosphinx/KT_EntranceHall"


def _form(params):
    from bartlebot.gui.canvas.param_panel import _DynamicParamForm
    from bartlebot.nodes.teleportation import go_to_zone

    form = _DynamicParamForm()
    form.load_schema(go_to_zone.PARAM_SCHEMA, params)
    return form


def test_the_zone_path_round_trips(qapp):
    assert _form({"zone": _ENTRANCE}).get_params() == {"zone": _ENTRANCE}


def test_the_button_names_the_zone_and_its_world_but_stores_only_the_path(qapp):
    form = _form({"zone": _ENTRANCE})

    assert form.widget("zone").text() == "Entrance Hall · Krokotopia"
    assert form.get_params()["zone"] == _ENTRANCE


def test_an_unset_zone_stays_unset(qapp):
    form = _form({})

    assert form.widget("zone").text() == "Choose a zone…"
    assert form.get_params()["zone"] == ""


def test_a_zone_no_longer_on_the_map_is_kept_and_marked(qapp):
    """A bot is not silently rewritten to some other zone because the map changed under it."""
    form = _form({"zone": "Krokotopia/KT_Gone"})

    assert form.widget("zone").text() == "Krokotopia/KT_Gone (not on the map)"
    assert form.get_params()["zone"] == "Krokotopia/KT_Gone"


def test_single_select_makes_the_clicked_row_the_whole_selection():
    assert apply_toggle({"a"}, "b", True, single_select=True) == {"b"}
    # Clicking the row that is already chosen keeps it chosen; the popup just closes.
    assert apply_toggle({"b"}, "b", False, single_select=True) == {"b"}


def test_multi_select_toggles_one_row_at_a_time():
    assert apply_toggle({"a"}, "b", True) == {"a", "b"}
    assert apply_toggle({"a", "b"}, "a", False) == {"b"}
