"""Widget tests for the 'nav_zone' param, driven through the real Go To Zone schema (QApplication required).

The point of the type is that the bot file stores a zone path and nothing else: the button shows
the zone's world, name and id, but a world written back into the bot would be a second value
free to disagree with the first.
"""

import asyncio
import logging

from bartlebot.gui.widgets.picker_popup import apply_toggle

_ENTRANCE = "Krokotopia/KT_Krokosphinx/KT_EntranceHall"


def _form(params, clients=()):
    from bartlebot.gui.canvas.param_panel import _DynamicParamForm
    from bartlebot.nodes.teleportation import go_to_zone

    form = _DynamicParamForm()
    form.set_clients_getter(lambda: list(clients))
    form.load_schema(go_to_zone.PARAM_SCHEMA, params)
    return form


def test_the_zone_path_round_trips(qapp):
    assert _form({"zone": _ENTRANCE}).get_params() == {"zone": _ENTRANCE}


def test_the_button_names_the_world_the_zone_and_its_id_but_stores_only_the_path(qapp):
    form = _form({"zone": _ENTRANCE})

    assert form.widget("zone").picker.text() == "Krokotopia · Entrance Hall (KT_EntranceHall)"
    assert form.get_params()["zone"] == _ENTRANCE


def test_an_unset_zone_stays_unset(qapp):
    form = _form({})

    assert form.widget("zone").picker.text() == "Choose a zone…"
    assert form.get_params()["zone"] == ""


def test_a_zone_no_longer_on_the_map_is_kept_and_marked(qapp):
    """A bot is not silently rewritten to some other zone because the map changed under it."""
    form = _form({"zone": "Krokotopia/KT_Gone"})

    assert form.widget("zone").picker.text() == "Krokotopia/KT_Gone (not on the map)"
    assert form.get_params()["zone"] == "Krokotopia/KT_Gone"


def test_a_long_caption_does_not_hold_the_panel_open(qapp):
    from bartlebot.gui.widgets.zone_picker import MINIMUM_WIDTH

    form = _form({"zone": _ENTRANCE})  # held: the form owns the widgets below
    picker = form.widget("zone").picker

    assert picker.minimumSizeHint().width() == MINIMUM_WIDTH < picker.sizeHint().width()


async def test_go_navigates_the_connected_players_to_the_chosen_zone(qapp, monkeypatch):
    from bartlebot.gui.canvas import param_panel

    seen = []
    released = asyncio.Event()

    async def fake_navigate(machine, clients, target):
        seen.append((clients, target, machine.port_map))
        machine.log("step")
        await released.wait()

    monkeypatch.setattr(param_panel.zone_navigation, "navigate", fake_navigate)
    form = _form({"zone": _ENTRANCE}, clients=["c1", "c2"])
    field = form.widget("zone")

    field.go_btn.click()
    await asyncio.sleep(0)
    assert seen == [(["c1", "c2"], _ENTRANCE, None)]
    assert not field.go_btn.isEnabled()  # one trip at a time

    released.set()
    await asyncio.gather(*param_panel._NAV_TASKS)
    assert field.go_btn.isEnabled()


async def test_a_failed_trip_is_logged_and_gives_the_button_back(qapp, monkeypatch, caplog):
    from bartlebot.gui.canvas import param_panel

    async def broken_navigate(machine, clients, target):
        raise RuntimeError("boom")

    monkeypatch.setattr(param_panel.zone_navigation, "navigate", broken_navigate)
    form = _form({"zone": _ENTRANCE}, clients=["c1"])
    field = form.widget("zone")

    with caplog.at_level(logging.ERROR):
        field.go_btn.click()
        await asyncio.gather(*param_panel._NAV_TASKS)

    assert "Go To Zone from the properties panel failed" in caplog.text
    assert field.go_btn.isEnabled()


def test_go_does_nothing_without_players_or_a_zone(qapp, monkeypatch):
    from bartlebot.gui.canvas import param_panel

    called = []
    monkeypatch.setattr(param_panel.zone_navigation, "navigate", lambda *a: called.append(a))

    no_players, no_zone = _form({"zone": _ENTRANCE}), _form({}, clients=["c1"])
    no_players.widget("zone").go_btn.click()
    no_zone.widget("zone").go_btn.click()

    assert called == []
    assert not param_panel._NAV_TASKS


class _ZoneClient:
    def __init__(self, zone):
        self.zone = zone

    async def zone_name(self):
        return self.zone


def test_both_buttons_are_action_buttons(qapp):
    from bartlebot.gui.widgets.action_button import ActionButton

    form = _form({"zone": _ENTRANCE})
    field = form.widget("zone")

    assert isinstance(field.fill_btn, ActionButton)
    assert isinstance(field.go_btn, ActionButton)


async def test_fill_sets_the_zone_from_a_player_and_saves_it(qapp):
    form = _form({}, clients=[_ZoneClient(_ENTRANCE)])
    edits = []
    form.changed.connect(lambda: edits.append(form.get_params()["zone"]))

    await form.widget("zone")._fill_from_player(0)

    assert form.get_params()["zone"] == _ENTRANCE
    assert form.widget("zone").picker.text() == "Krokotopia · Entrance Hall (KT_EntranceHall)"
    assert edits == [_ENTRANCE]


async def test_filling_the_zone_already_chosen_does_not_dirty_the_bot(qapp):
    form = _form({"zone": _ENTRANCE}, clients=[_ZoneClient(_ENTRANCE)])
    edits = []
    form.changed.connect(lambda: edits.append(1))

    await form.widget("zone")._fill_from_player(0)

    assert edits == []


async def test_fill_leaves_the_zone_alone_when_the_read_fails_or_the_player_is_gone(qapp):
    class _Broken:
        async def zone_name(self):
            raise RuntimeError("unhooked")

    form = _form({"zone": _ENTRANCE}, clients=[_Broken()])

    await form.widget("zone")._fill_from_player(0)
    await form.widget("zone")._fill_from_player(5)

    assert form.get_params()["zone"] == _ENTRANCE


def test_the_authoring_machine_reads_safety_delay_from_the_app_setting(monkeypatch):
    from bartlebot.gui.canvas import param_panel

    class _Settings:
        default_safety_delay = 7.5

    monkeypatch.setattr(param_panel.SettingsManager, "instance", staticmethod(lambda: _Settings()))
    machine = param_panel._AuthoringMachine()

    assert machine.get_option("Safety Delay") == 7.5
    assert machine.get_option("Other", 1) == 1


def test_single_select_makes_the_clicked_row_the_whole_selection():
    assert apply_toggle({"a"}, "b", True, single_select=True) == {"b"}
    # Clicking the row that is already chosen keeps it chosen; the popup just closes.
    assert apply_toggle({"b"}, "b", False, single_select=True) == {"b"}


def test_multi_select_toggles_one_row_at_a_time():
    assert apply_toggle({"a"}, "b", True) == {"a", "b"}
    assert apply_toggle({"a", "b"}, "a", False) == {"b"}
