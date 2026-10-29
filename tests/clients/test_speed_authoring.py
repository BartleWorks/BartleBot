"""Speed-authoring (#85): keys 1-0 in a game window add a hotbar node after the anchor and record it.

The pieces, each tested on its own: where the node goes (``authoring_anchor`` and
``BotCanvas.speed_insert``), when the keys are live (``slot_keys_armed``, the toggle, and
a hotkey set of their own), and what a key press does (``AppController._on_slot_hotkey``,
driven against stand-ins, since building the whole controller needs a live game).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from bartleby.qt.global_hotkeys import ID_BASE_AUTHORING, ID_BASE_SPEED_AUTHORING, GlobalHotkeys

from bartlebot import app_controller
from bartlebot.app_controller import AppController
from bartlebot.bot.schema import BotConfig, StateModel, TransitionModel
from bartlebot.clients.authoring_tools import SLOT_KEYS, AuthoringAction, AuthoringTools, slot_keys_armed
from bartlebot.game.game_keybindings import GameBinding
from bartlebot.gui.canvas import recording
from bartlebot.gui.canvas.edit_scene import authoring_anchor
from bartlebot.gui.settings.authoring_tab import slot_key_notes
from bartlebot.settings_manager import SettingsManager


def _state(sid: str, node_type: str = "change_realm", **kw) -> StateModel:
    return StateModel(id=sid, node_type=node_type, params={}, **kw)


def _edge(source: str, dest: str, when: str | None = "SUCCESS") -> TransitionModel:
    return TransitionModel(trigger="advance", source=source, dest=dest, when=when)


def _config(states, transitions, initial="start") -> BotConfig:
    return BotConfig(id="t", name="t", initial=initial, recovery_state=initial, states=states, transitions=transitions)


def _template() -> BotConfig:
    """A skeleton with its Exit already drawn, far right and unconnected: the issue's case."""
    return _config(
        [_state("start"), _state("enter", "enter_dungeon"), _state("exit", "teleport_to_world_hub")],
        [_edge("start", "enter"), _edge("exit", "start")],
    )


# ---------------------------------------------------------------------------
# authoring_anchor
# ---------------------------------------------------------------------------


def test_the_anchor_is_the_first_unrouted_happy_port():
    assert authoring_anchor(_template()) == ("enter", "SUCCESS")


def test_a_node_reachable_by_no_edge_is_never_the_anchor():
    """The template's Exit is further right, but nothing reaches it yet."""
    anchor = authoring_anchor(_template())

    assert anchor is not None and anchor[0] != "exit"


def test_a_failure_edge_does_not_route_the_happy_port():
    config = _config([_state("start"), _state("b")], [_edge("start", "b", "FAILURE")])

    assert authoring_anchor(config) == ("start", "SUCCESS")


def test_an_unconditional_edge_routes_the_happy_port():
    config = _config([_state("start"), _state("b")], [_edge("start", "b", None)])

    assert authoring_anchor(config) == ("b", "SUCCESS")


def test_a_condition_is_followed_by_its_true_port():
    config = _config(
        [_state("start", "check_health_and_mana"), _state("b")],
        [_edge("start", "b", "TRUE")],
    )

    assert authoring_anchor(config) == ("b", "SUCCESS")


def test_a_loop_has_no_anchor():
    config = _config([_state("start"), _state("b")], [_edge("start", "b"), _edge("b", "start")])

    assert authoring_anchor(config) is None


def test_a_path_ending_in_a_stop_has_no_anchor():
    """A node after a stop could never run."""
    config = _config([_state("start", stops={"SUCCESS": "done"})], [])

    assert authoring_anchor(config) is None


# ---------------------------------------------------------------------------
# BotCanvas.speed_insert
# ---------------------------------------------------------------------------


@pytest.fixture
def canvas(qapp):
    from bartlebot.gui.canvas.bot_canvas import BotCanvas

    built = []

    def _build(config=None, *, edit=True):
        c = BotCanvas()
        c.setup(config or _template())
        if edit:
            c.enter_edit_mode()
        built.append(c)
        return c

    yield _build
    for c in built:
        c.prepare_for_shutdown()


@pytest.fixture
def slots():
    sm = SettingsManager.instance()
    saved = sm.favorited_hotkey_nodes
    sm.set("favorited_hotkey_nodes", value=["sleep", "enter_zone", ""] + [""] * 7)
    yield
    sm.set("favorited_hotkey_nodes", value=saved)


def _out(config, sid):
    return {(t.dest, t.when) for t in config.transitions if t.source == sid}


def test_with_nothing_selected_the_node_fills_the_gap(canvas, slots):
    c = canvas()

    new_id, refusal = c.speed_insert(0)

    assert refusal == ""
    assert _out(c.config, "enter") == {(new_id, "SUCCESS")}
    assert next(s for s in c.config.states if s.id == new_id).node_type == "sleep"


def test_the_new_node_is_selected_so_the_next_key_builds_on_it(canvas, slots):
    c = canvas()

    first, _ = c.speed_insert(0)
    second, _ = c.speed_insert(0)

    assert [i.state_id for i in c.scene.selectedItems()] == [second]
    assert _out(c.config, first) == {(second, "SUCCESS")}


def test_a_selected_node_is_built_on_and_its_successor_moves_after(canvas, slots):
    """The Exit that was already wired stays last."""
    config = _template()
    config.transitions.append(_edge("enter", "exit"))
    c = canvas(config)
    c.select_state("enter")

    new_id, _ = c.speed_insert(0)

    assert _out(c.config, "enter") == {(new_id, "SUCCESS")}
    assert _out(c.config, new_id) == {("exit", "SUCCESS")}


def test_an_action_after_a_condition_leaves_on_success(canvas, slots):
    config = _config(
        [_state("start", "check_health_and_mana"), _state("b")],
        [_edge("start", "b", "TRUE")],
    )
    c = canvas(config)
    c.select_state("start")

    new_id, _ = c.speed_insert(0)

    assert _out(c.config, "start") == {(new_id, "TRUE")}
    assert _out(c.config, new_id) == {("b", "SUCCESS")}


def test_an_enter_zone_staggers(canvas, slots):
    c = canvas()

    new_id, _ = c.speed_insert(1)

    assert next(s for s in c.config.states if s.id == new_id).params["stagger"] is True


def test_one_undo_takes_the_insert_back(canvas, slots):
    c = canvas()
    before = {s.id for s in c.config.states}

    c.speed_insert(1)
    c.undo()

    assert {s.id for s in c.config.states} == before


@pytest.mark.parametrize(
    ("setup", "slot", "phrase"),
    [
        ("run_mode", 0, "Edit mode"),
        ("none", 2, "no node on the favorites bar"),
        ("two_selected", 0, "Several nodes"),
        ("loop", 0, "no gap to fill"),
        ("stop", 0, "stops the bot"),
    ],
)
def test_a_refusal_says_what_to_do_and_adds_nothing(canvas, slots, setup, slot, phrase):
    if setup == "loop":
        c = canvas(_config([_state("start"), _state("b")], [_edge("start", "b"), _edge("b", "start")]))
    elif setup == "stop":
        c = canvas(_config([_state("start", stops={"SUCCESS": ""})], []))
        c.select_state("start")
    else:
        c = canvas(edit=setup != "run_mode")
    if setup == "two_selected":
        for sid in ("start", "enter"):
            c.scene.node_items[sid].setSelected(True)
    before = len(c.config.states)

    new_id, refusal = c.speed_insert(slot)

    assert new_id is None
    assert phrase in refusal
    assert "press" in refusal or "Assign" in refusal or "Select" in refusal
    assert len(c.config.states) == before


# ---------------------------------------------------------------------------
# When the keys are live
# ---------------------------------------------------------------------------


def test_the_slot_keys_are_the_favorites_bar_order():
    assert SLOT_KEYS == ("1", "2", "3", "4", "5", "6", "7", "8", "9", "0")


@pytest.mark.parametrize("missing", ["speed_authoring", "game_focused", "editing"])
def test_the_keys_arm_only_with_every_condition_met(missing):
    conditions = {"speed_authoring": True, "game_focused": True, "editing": True}
    assert slot_keys_armed(**conditions, run_active=False)

    conditions[missing] = False
    assert not slot_keys_armed(**conditions, run_active=False)


def test_a_run_disarms_the_keys():
    assert not slot_keys_armed(speed_authoring=True, game_focused=True, editing=True, run_active=True)


def _tools() -> AuthoringTools:
    return AuthoringTools(clients=lambda: [], bot_config=lambda: None, speed_multiplier=lambda: 5.0)


async def test_the_toggle_flips_through_perform():
    tools = _tools()

    await tools.perform(AuthoringAction.TOGGLE_SPEED_AUTHORING, None)
    assert tools.speed_authoring_on
    await tools.perform(AuthoringAction.TOGGLE_SPEED_AUTHORING, None)
    assert not tools.speed_authoring_on


async def test_a_run_switches_speed_authoring_off_and_refuses_it():
    tools = _tools()
    tools.set_speed_authoring(True)

    await tools.suspend_for_run()
    assert not tools.speed_authoring_on
    tools.set_speed_authoring(True)
    assert not tools.speed_authoring_on

    tools.resume_after_run()
    tools.set_speed_authoring(True)
    assert tools.speed_authoring_on


def _registered_ids(id_base: int, bindings: dict[str, str]) -> set[int]:
    hk = GlobalHotkeys(lambda _a: None, id_base=id_base)
    hk._user32 = MagicMock()
    hk._user32.RegisterHotKey.return_value = 1
    hk.set_bindings(bindings)
    hk.set_armed(True)
    return {c.args[1] for c in hk._user32.RegisterHotKey.call_args_list}


def test_the_two_hotkey_sets_never_share_an_id(qapp):
    """Both register on one thread with no window, so a shared id replaces the other's key."""
    authoring = _registered_ids(ID_BASE_AUTHORING, {a.value: f"F{i + 1}" for i, a in enumerate(AuthoringAction)})
    slots = _registered_ids(ID_BASE_SPEED_AUTHORING, {f"slot_{i}": k for i, k in enumerate(SLOT_KEYS)})

    assert len(authoring) == len(AuthoringAction)
    assert len(slots) == 10
    assert not authoring & slots


def test_a_digit_the_game_binds_is_noted():
    notes = slot_key_notes([GameBinding("DerbyTalent1", "1"), GameBinding("EquipSet1", "Ctrl+1")])

    assert "1 (Derby Talent1)" in notes
    assert "Ctrl+1" not in notes


def test_no_note_when_the_game_binds_no_digit():
    assert slot_key_notes([GameBinding("Quit", "Alt+F4")]) == ""


# ---------------------------------------------------------------------------
# A key press, through AppController
# ---------------------------------------------------------------------------


class _Panel:
    def __init__(self) -> None:
        self.recorded: list[object] = []

    def start_record(self, client) -> bool:
        self.recorded.append(client)
        return True


def _controller(insert_result=("state_9", ""), *, focused=222):
    canvas = SimpleNamespace(speed_insert=MagicMock(return_value=insert_result), param_panel=_Panel())
    clients = [SimpleNamespace(window_handle=111), SimpleNamespace(window_handle=222)]
    return SimpleNamespace(
        _window=SimpleNamespace(
            bot_canvas=canvas, game_view=SimpleNamespace(focused_hwnd=focused, last_focused_hwnd=None)
        ),
        _client_manager=SimpleNamespace(clients=clients),
    )


@pytest.fixture
def deferred(monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(app_controller.QTimer, "singleShot", lambda _ms, fn: calls.append(fn))
    monkeypatch.setattr(recording, "is_recording", lambda: False)
    return calls


def test_a_key_inserts_its_slot_and_records_from_the_focused_window(deferred):
    fake = _controller()

    AppController._on_slot_hotkey(fake, "slot_3")

    fake._window.bot_canvas.speed_insert.assert_called_once_with(3)
    assert fake._window.bot_canvas.param_panel.recorded == [fake._client_manager.clients[1]]
    assert deferred == []


def test_a_key_while_recording_is_refused_without_a_popup(deferred, monkeypatch, caplog):
    """A popup would take the keyboard mid-step; the log says it instead."""
    monkeypatch.setattr(recording, "is_recording", lambda: True)
    fake = _controller()

    AppController._on_slot_hotkey(fake, "slot_0")

    fake._window.bot_canvas.speed_insert.assert_not_called()
    assert deferred == []
    assert "still recording" in caplog.text


def test_a_refused_insert_explains_itself_in_a_deferred_popup(deferred):
    """Opened from inside Qt's native dispatch, so never directly."""
    fake = _controller(insert_result=(None, "Select the node to build from."))

    AppController._on_slot_hotkey(fake, "slot_0")

    assert len(deferred) == 1
    assert fake._window.bot_canvas.param_panel.recorded == []


def _slot_keys_controller(*, speed_authoring=True, focused=1, mode=None):
    from bartlebot.gui.canvas.bot_canvas import BotMode

    hk = GlobalHotkeys(lambda _a: None, id_base=ID_BASE_SPEED_AUTHORING)
    hk._user32 = MagicMock()
    hk._user32.RegisterHotKey.return_value = 1
    tools = SimpleNamespace(speed_authoring_on=speed_authoring, run_active=False)
    canvas = SimpleNamespace(mode=mode or BotMode.EDIT, config=object())
    return SimpleNamespace(
        _authoring=tools,
        _slot_hotkeys=hk,
        _window=SimpleNamespace(bot_canvas=canvas, game_view=SimpleNamespace(focused_hwnd=focused)),
    )


def test_arming_binds_only_the_assigned_slots(qapp, slots):
    """An empty slot leaves its digit to the game."""
    fake = _slot_keys_controller()

    AppController._refresh_slot_keys(fake)

    assert fake._slot_hotkeys.registered_actions == ["slot_0", "slot_1"]


def test_leaving_the_game_window_disarms_the_keys(qapp, slots):
    fake = _slot_keys_controller()
    AppController._refresh_slot_keys(fake)

    fake._window.game_view.focused_hwnd = None
    AppController._refresh_slot_keys(fake)

    assert not fake._slot_hotkeys.armed
    assert fake._slot_hotkeys.registered_actions == []
