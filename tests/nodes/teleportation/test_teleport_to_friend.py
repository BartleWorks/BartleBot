"""Unit tests for the teleport_to_friend node."""

from unittest.mock import AsyncMock, patch

import pytest
from bartleby.runtime.game import wizard_names
from bartleby.runtime.paths import MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.teleportation.teleport_to_friend as ttf
from tests.conftest import FakeClient, FakeMachine, message_box_patches
from tests.node_helper_patches import patch_helper, set_helper
from tests.port_sim import HOME, PortSim

_HUB = "HubZone"
_HOUSE = "Housing_AR_Dormroom/Interior"
_AT_FRIEND = (10.0, 20.0, 0.0)
_FAR = (900.0, -400.0, 0.0)
_FRIEND = "WizardFriend"

# Friend-name mode through the deprecated literal: the same code path as the Friend Name
# option, without standing up the option resolution this file is not about.
_PARAMS = {"friend_name": _FRIEND, "to_zone": ""}

# node_helpers owns the shared "port to a friend" mechanics (port_to_friend /
# teleport_via_friends_list); these stand in for the friends list and the profile's button.
_OPEN_OK = patch_helper("open_friend_profile", new=AsyncMock())
_TELEPORT_OK = patch_helper("_click_go_to_friend", new=AsyncMock(return_value=True))

_NO_DIALOG = patch_helper("is_visible_by_path", new=AsyncMock(return_value=False))


def _caption(text: str):
    """Patch the modal caption read, which is how dialogs are told apart."""
    return patch_helper("get_text_by_path", new=AsyncMock(return_value=text))


@pytest.fixture(autouse=True)
def _forget_names():
    yield
    wizard_names.clear_cache()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(ttf, "POLL_INTERVAL", 0.001)
    monkeypatch.setattr(ttf, "TRIGGER_TIMEOUT", 0.05)
    set_helper(monkeypatch, "TRIGGER_TIMEOUT", 0.05)
    set_helper(monkeypatch, "PORT_QUIET_GRACE", 0.05)


def _friend_sim() -> PortSim:
    return PortSim(friend=(_HOUSE, _AT_FRIEND, HOME))


async def _run(sim: PortSim, clients: list, params: dict = _PARAMS) -> tuple[str, FakeMachine]:
    machine = FakeMachine()
    with (
        patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)),
        patch_helper("resolve_modal", new=AsyncMock(return_value=nh.ModalOutcome(answered=False))),
    ):
        return await ttf.run(machine, clients, params), machine


# ---------------------------------------------------------------------------
# Friend by name (#14)
# ---------------------------------------------------------------------------


async def test_everyone_already_at_the_friend_succeeds_without_a_loading_screen():
    """The case that motivated #14: logged out in the host's house, nobody has anywhere to go."""
    sim = _friend_sim()
    clients = [sim.player(_HOUSE, _AT_FRIEND), sim.player(_HOUSE, _AT_FRIEND)]

    result, machine = await _run(sim, clients)

    assert result == "SUCCESS"
    presumed = [msg for msg, _ in machine.logs if "presumed already with the target" in msg]
    assert len(presumed) == 2, "a player presumed there is always visible after the fact"


async def test_some_port_in_and_some_are_already_there():
    sim = _friend_sim()
    there = sim.player(_HOUSE, _AT_FRIEND)
    hub = sim.player(_HUB, _FAR)
    same_house_elsewhere = sim.player(_HOUSE, _FAR)

    result, _ = await _run(sim, [there, hub, same_house_elsewhere])

    assert result == "SUCCESS"
    assert hub.where[0] == same_house_elsewhere.where[0] == _HOUSE
    # XYZ has no value equality, so compare the coordinates.
    assert nh.distance(same_house_elsewhere.where[1], hub.where[1]) == 0
    assert sim.times_ported(same_house_elsewhere) == 1, "the jump is the port; it is not re-sent"


async def test_players_in_their_own_houses_are_not_mistaken_for_arrived():
    """What `*Housing*` did: every house matched, so a player at home counted as at the host's."""
    sim = _friend_sim()
    at_home = sim.player("Housing_Wizard_City/Interior", _FAR)
    also_at_home = sim.player("Housing_MooShu/Interior", _FAR)

    result, _ = await _run(sim, [at_home, also_at_home])

    assert result == "SUCCESS"
    assert at_home.where[0] == also_at_home.where[0] == _HOUSE


async def test_players_who_end_up_apart_fail(monkeypatch):
    monkeypatch.setattr(ttf, "ZONE_CHANGE_TIMEOUT", 0.2)
    sim = _friend_sim()
    clients = [sim.player(_HUB, _FAR), sim.player(_HUB, _FAR, port="lost")]

    result, machine = await _run(sim, clients)

    assert result == "FAILURE"
    assert any("did not end up together" in msg for msg, _ in machine.logs)


async def test_players_in_one_zone_on_different_realms_fail(monkeypatch):
    """One zone is not one realm: a player whose port never registered, standing in the friend's
    house on its own realm, used to count as with the others (#130)."""
    monkeypatch.setattr(ttf, "ZONE_CHANGE_TIMEOUT", 0.2)
    sim = _friend_sim()
    clients = [sim.player(_HUB, _FAR), sim.player(_HOUSE, _AT_FRIEND, realm="elsewhere", port="ignored")]

    result, machine = await _run(sim, clients)

    assert result == "FAILURE"
    assert any("on one realm" in msg for msg, _ in machine.logs)


async def test_to_zone_when_set_must_match_where_everyone_ended_up(monkeypatch):
    monkeypatch.setattr(ttf, "ZONE_CHANGE_TIMEOUT", 0.2)
    sim = _friend_sim()
    clients = [sim.player(_HUB, _FAR), sim.player(_HUB, _FAR)]

    result, _ = await _run(sim, clients, {"friend_name": _FRIEND, "to_zone": "SomeOther/Zone"})

    assert result == "FAILURE"


async def test_to_zone_when_set_and_matching_succeeds():
    sim = _friend_sim()
    clients = [sim.player(_HUB, _FAR), sim.player(_HUB, _FAR)]

    result, _ = await _run(sim, clients, {"friend_name": _FRIEND, "to_zone": _HOUSE})

    assert result == "SUCCESS"


async def test_a_port_that_never_registers_cannot_pass_a_to_zone_it_is_not_in(monkeypatch):
    """The residual hole: a silent failure reads as already there. to_zone, when set, still catches it."""
    monkeypatch.setattr(ttf, "ZONE_CHANGE_TIMEOUT", 0.2)
    sim = _friend_sim()
    stuck = sim.player(_HUB, _FAR, port="ignored")

    result, _ = await _run(sim, [stuck], {"friend_name": _FRIEND, "to_zone": _HOUSE})

    assert result == "FAILURE"


async def test_friend_not_found_fails_without_naming_them():
    sim = _friend_sim()
    sim.fail_from_call = 1

    result, machine = await _run(sim, [sim.player(_HUB, _FAR)])

    assert result == "FAILURE"
    assert not any(_FRIEND.casefold() in msg.casefold() for msg, level in machine.logs if level > 10)


async def test_straggler_is_reissued_and_succeeds():
    sim = _friend_sim()
    prompt = sim.player(_HUB, _FAR)
    straggler = sim.player(_HUB, _FAR, port="ignored_once")

    result, _ = await _run(sim, [prompt, straggler])

    assert result == "SUCCESS"
    assert sim.times_ported(prompt) == 1 and sim.times_ported(straggler) == 2


async def test_straggler_reissue_that_cannot_find_the_friend_fails():
    sim = _friend_sim()
    sim.fail_from_call = 3  # both first ports start; the re-issue cannot find the friend
    clients = [sim.player(_HUB, _FAR), sim.player(_HUB, _FAR, port="ignored")]

    result, _ = await _run(sim, clients)

    assert result == "FAILURE"


async def test_friend_name_is_lowercased():
    """friend_name is lowercased before the friends list is searched for it."""
    client = _friend_sim().player(_HOUSE, _AT_FRIEND)

    with _TELEPORT_OK, _NO_DIALOG, patch_helper("open_friend_profile", new=AsyncMock()) as profile:
        await ttf.run(FakeMachine(), [client], _PARAMS)

    assert profile.call_args[0][1] == "wizardfriend"


async def test_mouse_handler_context_activated():
    """The mouse handler context manager is entered for each teleporting client."""
    client = _friend_sim().player(_HOUSE, _AT_FRIEND)

    with _TELEPORT_OK, _OPEN_OK, _NO_DIALOG:
        await ttf.run(FakeMachine(), [client], _PARAMS)

    client.mouse_handler.__aenter__.assert_called()
    client.mouse_handler.__aexit__.assert_called()


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------


async def test_friend_busy_dialog_returns_failure():
    sim = _friend_sim()
    client = sim.player(_HUB, _FAR, port="ignored")
    patches, clicked = message_box_patches((MESSAGE_BOX_RIGHT,))
    machine = FakeMachine()

    with (
        patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)),
        patches[0],
        patches[1],
        _caption("Your friend is busy right now."),
    ):
        result = await ttf.run(machine, [client], _PARAMS)

    assert result == "FAILURE"
    # Dismissed, and nothing else touched — no stray confirm on the way past.
    assert clicked == [MESSAGE_BOX_RIGHT]
    assert any("not accepting teleports" in msg for msg, _lvl in machine.logs)


async def test_unrecognized_dismiss_only_dialog_still_fails_fast():
    """A dialog reworded by a game patch must not quietly become a presumed arrival.

    Caption matching finds nothing, so the button layout decides: a right button with no
    center offers no way to proceed, which is a refusal whatever it says.
    """
    sim = _friend_sim()
    client = sim.player(_HUB, _FAR, port="ignored")
    patches, clicked = message_box_patches((MESSAGE_BOX_RIGHT,))
    machine = FakeMachine()

    with (
        patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)),
        patches[0],
        patches[1],
        _caption("Some brand new refusal from a future patch"),
    ):
        result = await ttf.run(machine, [client], _PARAMS)

    assert result == "FAILURE"
    assert clicked == [MESSAGE_BOX_RIGHT]
    assert any("does not recognize" in msg for msg, _lvl in machine.logs)


async def test_friend_busy_dialog_only_checked_for_not_yet_ported():
    """Once a client has been seen loading, the dialog check is skipped for it."""
    sim = _friend_sim()
    c1 = sim.player(_HUB, _FAR)
    c2 = sim.player(_HUB, _FAR, port="ignored")

    dialog_for_c2 = AsyncMock(side_effect=lambda client, path: client is c2 and path == MESSAGE_BOX_RIGHT)
    caption_for_c2 = AsyncMock(
        side_effect=lambda client, path: "Your friend is busy right now." if client is c2 else ""
    )

    with (
        patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)),
        patch_helper("is_visible_by_path", dialog_for_c2),
        patch_helper("get_text_by_path", caption_for_c2),
        patch_helper("click_by_path", new=AsyncMock(return_value=True)),
    ):
        result = await ttf.run(FakeMachine(), [c1, c2], _PARAMS)

    assert result == "FAILURE"


async def test_dungeon_exit_warning_is_not_mistaken_for_a_busy_friend():
    """Porting to a friend out of a dungeon failed outright before this.

    The warning also shows a right button, so the busy check matched it and reported an
    unreachable friend on every dungeon exit. The center button is what separates them.
    """
    sim = _friend_sim()
    client = sim.player(_HUB, _FAR, port="ignored")

    # The warning is up until its center button is clicked; confirming it lets the port go.
    showing = [MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT]
    clicked: list[list[str]] = []

    async def _is_visible(_c, path):
        return path in showing

    async def _click(_c, path):
        clicked.append(path)
        if path not in showing:
            return False
        showing.clear()
        client.port = "lands"
        await sim.teleport(client, _FRIEND.lower())
        return True

    with (
        patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)),
        patch_helper("is_visible_by_path", new=AsyncMock(side_effect=_is_visible)),
        patch_helper("click_by_path", new=AsyncMock(side_effect=_click)),
    ):
        result = await ttf.run(FakeMachine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert clicked == [MESSAGE_BOX_CENTER]
    assert client.where[0] == _HOUSE


async def test_busy_dialog_behind_the_dungeon_warning_still_fails():
    """The live-observed order: the warning comes up first, the busy dialog only after it.

    Confirming the warning must not lose the busy diagnosis that follows it — otherwise an
    unreachable friend reads as a player already standing with them.
    """
    sim = _friend_sim()
    client = sim.player(_HUB, _FAR, port="ignored")

    showing = [MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT]
    clicked: list[list[str]] = []

    async def _is_visible(_c, path):
        return path in showing

    async def _click(_c, path):
        clicked.append(path)
        if path not in showing:
            return False
        if path == MESSAGE_BOX_CENTER:
            showing.remove(MESSAGE_BOX_CENTER)
        return True

    with (
        patch_helper("teleport_via_friends_list", new=AsyncMock(side_effect=sim.teleport)),
        patch_helper("is_visible_by_path", new=AsyncMock(side_effect=_is_visible)),
        patch_helper("click_by_path", new=AsyncMock(side_effect=_click)),
    ):
        result = await ttf.run(FakeMachine(), [client], _PARAMS)

    assert result == "FAILURE"
    assert clicked == [MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT]


# ---------------------------------------------------------------------------
# Hooked player
# ---------------------------------------------------------------------------


def _hooked_sim(zone: str = _HOUSE) -> PortSim:
    sim = PortSim()
    sim.target = sim.player(zone, _AT_FRIEND, name="Luke SkullHunter")
    return sim


_HOOKED = {"target_hooked_player": True, "target_player": 1, "to_zone": ""}


async def test_the_target_player_is_never_asked_to_teleport_to_itself():
    sim = _hooked_sim()
    mover = sim.player(_HUB, _FAR)

    result, _ = await _run(sim, [sim.target, mover], _HOOKED)

    assert result == "SUCCESS"
    assert sim.ported == [mover]
    assert mover.where == sim.target.where


async def test_players_whose_zones_never_turn_readable_are_not_together(monkeypatch):
    """All None is one set of zones, and says nothing: it names no zone they share (#282)."""
    monkeypatch.setattr(ttf, "ZONE_CHANGE_TIMEOUT", 0.2)
    sim = _friend_sim()
    clients = [sim.player(_HOUSE, _AT_FRIEND), sim.player(_HOUSE, _AT_FRIEND)]
    for c in clients:
        c.zone_name = AsyncMock(return_value=None)

    result, machine = await _run(sim, clients)

    assert result == "FAILURE"
    assert any("did not end up together" in msg for msg, _ in machine.logs)


async def test_a_blank_to_zone_follows_the_target_wherever_it_is():
    sim = _hooked_sim("Anywhere/At_All")
    mover = sim.player(_HUB, _FAR)

    result, _ = await _run(sim, [sim.target, mover], _HOOKED)

    assert result == "SUCCESS"
    assert mover.where[0] == "Anywhere/At_All"


async def test_a_target_outside_to_zone_fails_before_anyone_ports():
    """Otherwise a target somewhere unexpected takes the whole group there with it."""
    sim = _hooked_sim(_HUB)
    mover = sim.player(_HUB, _FAR)

    result, machine = await _run(sim, [sim.target, mover], {**_HOOKED, "to_zone": _HOUSE})

    assert result == "FAILURE"
    assert sim.ported == []
    assert any("not" in msg and _HOUSE in msg for msg, _ in machine.logs)


async def test_a_target_whose_zone_is_unreadable_at_first_is_waited_for_not_failed():
    """Just off a loading screen the target's zone reads None for a moment (#282)."""
    sim = _hooked_sim()
    mover = sim.player(_HUB, _FAR)
    target = sim.target
    target.zone_name = AsyncMock(side_effect=lambda: None if target.zone_name.await_count <= 1 else target.where[0])

    result, _ = await _run(sim, [sim.target, mover], {**_HOOKED, "to_zone": _HOUSE})

    assert result == "SUCCESS"
    assert mover.where[0] == _HOUSE


async def test_a_player_already_in_to_zone_is_still_ported_to_the_target():
    """In the zone is not with the target: another realm, or the far side of it."""
    sim = _hooked_sim()
    in_zone_far = sim.player(_HOUSE, _FAR)

    result, _ = await _run(sim, [sim.target, in_zone_far], {**_HOOKED, "to_zone": _HOUSE})

    assert result == "SUCCESS"
    assert sim.times_ported(in_zone_far) == 1
    assert in_zone_far.where == sim.target.where


async def test_no_target_is_a_failure_before_any_client_is_touched():
    sim = _friend_sim()
    client = sim.player(_HUB, _FAR)

    with patch.object(ttf, "resolve_target", return_value=ttf.TargetResolution(error="nope")):
        result, _ = await _run(sim, [client], {"to_zone": ""})

    assert result == "FAILURE"
    assert sim.ported == []


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def test_resolve_target_uses_the_option_when_the_bot_names_nobody():
    """The Friend Name option arrives already resolved — this holds the precedence
    between it and the two things in the bot file, and reads no settings itself."""
    target = ttf.resolve_target([], {"to_zone": _HOUSE}, "Configured Wizard")

    assert target.name == "Configured Wizard"
    assert target.source == "the Friend Name option"
    assert not target.error


def test_resolve_target_fails_with_a_pointer_when_nothing_is_configured():
    """The failure has to say where to fix it — a silent teleport to nowhere is the bug
    this replaced."""
    target = ttf.resolve_target([], {"to_zone": _HOUSE}, "")

    assert not target.name
    assert "Options" in target.error and "Settings" in target.error


def test_a_literal_friend_name_still_wins_but_is_marked_deprecated():
    """Bot files written before the local friend-name work keep working; the source label is what says they
    should not stay that way."""
    target = ttf.resolve_target([], _PARAMS, "Configured Wizard")

    assert target.name == _FRIEND
    assert target.source == "the bot file (deprecated)"


def test_resolve_target_reads_the_hooked_players_name_from_memory():
    """Slot mode stores nothing: the name comes from the cache ClientManager fills."""
    clients = [FakeClient(), FakeClient()]
    wizard_names.remember(clients[1], "Luke SkullHunter")

    target = ttf.resolve_target(clients, {"target_hooked_player": True, "target_player": 2})

    assert target.name == "Luke SkullHunter"
    assert target.target_index == 1
    assert target.source == "hooked player P2"


def test_resolve_target_fails_when_the_slots_name_is_not_known_yet():
    clients = [FakeClient()]
    target = ttf.resolve_target(clients, {"target_hooked_player": True, "target_player": 1})

    assert not target.name
    assert "has not been detected" in target.error


def test_resolve_target_fails_when_the_slot_is_not_connected():
    target = ttf.resolve_target([FakeClient()], {"target_hooked_player": True, "target_player": 3})

    assert "P3 is not connected" in target.error


def test_to_zone_is_optional():
    field = next(f for f in ttf.PARAM_SCHEMA if f.key == "to_zone")
    assert not field.required
