"""Unit tests for the mark_location node.

The game is a small fake compass HUD per client (`_Hud`): what MarkButton, RecallButton and
recall's timer read, and what a MarkButton click does to them. Reads can lag a click, which
is the case that used to toggle a freshly set mark straight off again.
"""

import asyncio
import logging
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from wizwalker import XYZ

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.teleportation.mark_location as ml
from bartlebot.nodes.paths import MARK_BUTTON_PATH, RECALL_BUTTON_PATH, RECALL_TIMER_PATH
from tests.conftest import FakeClient, FakeMachine, zone_unreadable_at_first

_ZONE = "TestZone"  # matches FakeClient.zone_name default
_PARAMS = {
    "zone": _ZONE,
    "xyz": {"x": 0.0, "y": 0.0, "z": 0.0},
}
# Short enough that a test waiting out a whole window stays fast.
_SAFETY_DELAY = 0.05


class _Hud:
    """One client's compass HUD, and what a click of MarkButton does to it.

    *marked*: a mark is set somewhere. *here*: it is where the client stands, so a click
    clears it. *lag*: RecallButton reads that still show the old state after a click.
    *cooldown*: reads of recall's timer that still show it running (RecallButton grayed).
    *lands*: whether a click reaches the game at all. *refused*: a click spends mana but
    sets no mark.
    """

    def __init__(
        self,
        client: FakeClient,
        *,
        marked: bool = False,
        here: bool = False,
        can_mark: bool = True,
        lag: int = 0,
        cooldown: int = 0,
        lands: bool = True,
        refused: bool = False,
    ):
        self.marked, self.here, self.can_mark = marked, here, can_mark
        self.lag, self.cooldown, self.lands, self.refused = lag, cooldown, lands, refused
        self.shown, self.pending = marked, 0
        self.mana = 100
        self.clicks = 0
        self.clicked_at: list[float] = []  # the event loop's clock at each click
        client.stats.current_mana = AsyncMock(side_effect=lambda: self.mana)

    def click(self) -> None:
        self.clicks += 1
        self.clicked_at.append(asyncio.get_running_loop().time())
        if not self.lands:
            return
        self.mana -= 1
        if self.marked and self.here:
            self.marked = self.here = False
        elif not self.refused:
            self.marked = self.here = True
        self.pending = self.lag

    def recall_enabled(self) -> bool:
        if self.cooldown:
            return False
        if self.pending:
            self.pending -= 1
            return self.shown
        self.shown = self.marked
        return self.marked

    def timer_visible(self) -> bool:
        if self.cooldown:
            self.cooldown -= 1
            return True
        return False


@contextmanager
def _game(huds: dict):
    """Route the node's reads and clicks to each client's `_Hud`."""

    async def is_enabled(client, path):
        hud = huds[client]
        if path == MARK_BUTTON_PATH:
            return hud.can_mark
        assert path == RECALL_BUTTON_PATH
        return hud.recall_enabled()

    async def is_visible(client, path):
        assert path == RECALL_TIMER_PATH
        return huds[client].timer_visible()

    async def click(client, path):
        assert path == MARK_BUTTON_PATH
        huds[client].click()

    with (
        patch.object(nh, "is_enabled_by_path", new=is_enabled),
        patch.object(nh, "is_visible_by_path", new=is_visible),
        patch.object(nh, "click_by_path", new=click),
        patch.object(ml, "POLL_INTERVAL", 0),
    ):
        yield


def _machine() -> FakeMachine:
    return FakeMachine(options={"Safety Delay": _SAFETY_DELAY})


def _ready_client() -> FakeClient:
    """Client in the correct zone at the target XYZ."""
    c = FakeClient()
    # body.position at target XYZ → no teleport needed before marking.
    c.body.position = AsyncMock(return_value=XYZ(0.0, 0.0, 0.0))
    return c


# ---------------------------------------------------------------------------
# Who cannot mark
# ---------------------------------------------------------------------------


async def test_a_grayed_mark_button_fails_without_a_click():
    """MarkButton grayed (no mana, combat, a house): FAILURE before anyone clicks."""
    ok, barred = _ready_client(), _ready_client()
    huds = {ok: _Hud(ok), barred: _Hud(barred, can_mark=False)}

    with _game(huds):
        result = await ml.run(_machine(), [ok, barred], _PARAMS)

    assert result == "FAILURE"
    assert huds[ok].clicks == huds[barred].clicks == 0


async def test_wrong_zone_returns_failure():
    """FAILURE when client is not in the expected zone."""
    client = FakeClient()
    client.zone_name.return_value = "WrongZone"

    with _game({client: _Hud(client)}):
        result = await ml.run(_machine(), [client], _PARAMS)

    assert result == "FAILURE"
    client.teleport.assert_not_called()


async def test_a_zone_unreadable_at_first_is_waited_for_not_failed():
    """Just off a loading screen the zone reads None for a moment (#282)."""
    client = _ready_client()
    zone_unreadable_at_first(client, _ZONE)

    with _game({client: _Hud(client)}):
        result = await ml.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"


async def test_one_client_wrong_zone_fails():
    """FAILURE when one of two clients is in the wrong zone."""
    good, bad = _ready_client(), FakeClient()
    bad.zone_name.return_value = "WrongZone"
    huds = {good: _Hud(good), bad: _Hud(bad)}

    with _game(huds):
        result = await ml.run(_machine(), [good, bad], _PARAMS)

    assert result == "FAILURE"
    assert huds[good].clicks == 0


# ---------------------------------------------------------------------------
# XYZ pre-positioning
# ---------------------------------------------------------------------------


async def test_correct_zone_wrong_xyz_teleports_to_target():
    """If in the correct zone but away from the mark XYZ, the client is teleported first."""
    client = _ready_client()
    client.body.position = AsyncMock(return_value=XYZ(100.0, 0.0, 0.0))  # far from target

    with _game({client: _Hud(client)}):
        result = await ml.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_called_once()
    tp_target: XYZ = client.teleport.call_args.args[0]
    assert tp_target.x == pytest.approx(0.0)
    assert tp_target.y == pytest.approx(0.0)


async def test_correct_zone_correct_xyz_no_teleport():
    """No teleport when the client is already at the target XYZ."""
    client = _ready_client()

    with _game({client: _Hud(client)}):
        result = await ml.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"
    client.teleport.assert_not_called()


# ---------------------------------------------------------------------------
# Waiting for the server to catch up with a teleport
# ---------------------------------------------------------------------------

_WAITED = "Giving the server"


async def test_the_first_click_waits_out_the_safety_delay():
    """A click before the server hears of a teleport marks the spot the client left."""
    delay = 0.2
    client = _ready_client()
    client.body.position = AsyncMock(return_value=XYZ(100.0, 0.0, 0.0))  # teleports first
    hud = _Hud(client)
    started = asyncio.get_running_loop().time()

    with _game({client: hud}):
        result = await ml.run(FakeMachine(options={"Safety Delay": delay}), [client], _PARAMS)

    assert result == "SUCCESS"
    # Less a little: the loop may fire a timer up to its clock resolution early.
    assert hud.clicked_at[0] - started >= delay - 0.03


async def test_a_player_the_node_did_not_teleport_still_waits():
    """The node before may have teleported the client here, which this node cannot see."""
    machine, client = _machine(), _ready_client()

    with _game({client: _Hud(client)}):
        await ml.run(machine, [client], _PARAMS)

    client.teleport.assert_not_called()
    assert any(_WAITED in msg for msg, _lvl in machine.logs)


async def test_a_recall_cooldown_counts_toward_the_wait():
    """Waiting out recall's timer already gave the server its time: no further sleep."""
    machine, client = _machine(), _ready_client()

    async def slow_had_mark(machine, client, safety_delay):
        await asyncio.sleep(safety_delay * 2)
        return False

    with _game({client: _Hud(client)}), patch.object(ml, "_had_mark", new=slow_had_mark):
        assert await ml.run(machine, [client], _PARAMS) == "SUCCESS"

    assert not any(_WAITED in msg for msg, _lvl in machine.logs)


async def test_marking_again_after_a_clear_does_not_wait_again():
    """The second click is from the same spot, which the server already has."""
    machine, client = _machine(), _ready_client()
    hud = _Hud(client, marked=True, here=True)

    with _game({client: hud}):
        assert await ml.run(machine, [client], _PARAMS) == "SUCCESS"

    assert hud.clicks == 2
    assert sum(_WAITED in msg for msg, _lvl in machine.logs) == 1


# ---------------------------------------------------------------------------
# The three ways a click succeeds
# ---------------------------------------------------------------------------


async def test_no_mark_before_sets_one_with_one_click():
    """RecallButton grayed before, lit after: a new mark, one click."""
    clients = [_ready_client(), _ready_client()]
    huds = {c: _Hud(c) for c in clients}

    with _game(huds):
        result = await ml.run(_machine(), clients, _PARAMS)

    assert result == "SUCCESS"
    assert all(h.clicks == 1 and h.marked for h in huds.values())


async def test_a_mark_elsewhere_is_moved_with_one_click():
    """RecallButton lit before and after, mana spent: the mark moved here."""
    client = _ready_client()
    hud = _Hud(client, marked=True)

    with _game({client: hud}):
        result = await ml.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert hud.clicks == 1 and hud.marked


async def test_standing_on_the_mark_clears_it_then_marks_again():
    """Lit before, mana spent, then grayed: the click cleared the mark, so a second click sets it."""
    client = _ready_client()
    hud = _Hud(client, marked=True, here=True)

    with _game({client: hud}):
        result = await ml.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert hud.clicks == 2 and hud.marked


async def test_a_slow_recall_button_does_not_earn_a_second_click():
    """The old bug: a new mark whose RecallButton lights late was clicked again, clearing it."""
    client = _ready_client()
    hud = _Hud(client, lag=3)

    with _game({client: hud}):
        result = await ml.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert hud.clicks == 1 and hud.marked


async def test_a_clear_that_grays_the_button_late_is_still_seen():
    """Standing on the mark with a lagging button: the clear shows late, and is still re-marked."""
    client = _ready_client()
    hud = _Hud(client, marked=True, here=True, lag=3)

    with _game({client: hud}):
        result = await ml.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"
    assert hud.clicks == 2 and hud.marked


# ---------------------------------------------------------------------------
# A click the game does not answer
# ---------------------------------------------------------------------------


async def test_a_click_that_spends_no_mana_is_not_repeated():
    """Mana unchanged after the Safety Delay: the click did not land, and is not tried again."""
    client = _ready_client()
    hud = _Hud(client, lands=False)

    with _game({client: hud}):
        result = await ml.run(_machine(), [client], _PARAMS)

    assert result == "FAILURE"
    assert hud.clicks == 1


async def test_mana_spent_but_no_mark_fails():
    """Mana spent but RecallButton never lights: no mark, FAILURE, no second click."""
    client = _ready_client()
    hud = _Hud(client, refused=True)

    with _game({client: hud}):
        result = await ml.run(_machine(), [client], _PARAMS)

    assert result == "FAILURE"
    assert hud.clicks == 1


# ---------------------------------------------------------------------------
# Recall's cooldown
# ---------------------------------------------------------------------------


async def test_recall_cooldown_is_waited_out_before_reading_the_mark():
    """Timer running: RecallButton grayed says nothing, so the node waits, then sees the mark here."""
    client = _ready_client()
    hud = _Hud(client, marked=True, here=True, cooldown=3)

    with _game({client: hud}):
        result = await ml.run(_machine(), [client], _PARAMS)

    assert result == "SUCCESS"
    # Read as "no mark", the first click would have cleared it and then failed.
    assert hud.clicks == 2 and hud.marked


async def test_a_cooldown_that_never_ends_fails_without_a_click():
    """The timer outlasts COMPASS_TELEPORT_TIMEOUT: FAILURE, nothing clicked."""
    client = _ready_client()
    hud = _Hud(client, marked=True, cooldown=10**9)

    with _game({client: hud}), patch.object(ml, "COMPASS_TELEPORT_TIMEOUT", 0.05):
        result = await ml.run(_machine(), [client], _PARAMS)

    assert result == "FAILURE"
    assert hud.clicks == 0


# ---------------------------------------------------------------------------
# The run's record of marks
# ---------------------------------------------------------------------------


async def test_a_mark_is_remembered_for_the_rest_of_the_run():
    """mark_client records where the client stood, for this node and the recall to read later."""
    machine, client = _machine(), _ready_client()

    with _game({client: _Hud(client)}):
        await ml.run(machine, [client], _PARAMS)

    mark = ml.marked_at(machine, client)
    assert mark.zone == _ZONE
    assert (mark.position.x, mark.position.y, mark.position.z) == (0.0, 0.0, 0.0)
    assert ml.marked_at(machine, _ready_client()) is None


async def test_a_mark_is_remembered_by_its_zone_once_that_is_readable():
    """A mark kept with a None zone could never match a recall, so the zone is waited for (#282)."""
    machine, client = _machine(), _ready_client()
    zone_unreadable_at_first(client, _ZONE)

    with _game({client: _Hud(client)}):
        assert await ml.mark_client(machine, client) is True

    assert ml.marked_at(machine, client).zone == _ZONE


async def test_a_mark_whose_zone_never_turns_readable_is_not_remembered():
    machine, client = _machine(), _ready_client()
    client.zone_name.return_value = None

    with _game({client: _Hud(client)}):
        assert await ml.mark_client(machine, client) is False

    assert ml.marked_at(machine, client) is None
    assert any("zone could not be read" in msg and level == logging.WARNING for msg, level in machine.logs)


async def test_a_spot_already_marked_this_run_is_not_marked_again():
    """A second pass marks nothing: clicking the same spot again would only clear the mark."""
    machine, client = _machine(), _ready_client()
    hud = _Hud(client)

    with _game({client: hud}):
        assert await ml.run(machine, [client], _PARAMS) == "SUCCESS"
        client.body.position = AsyncMock(return_value=XYZ(400.0, 0.0, 0.0))  # walked off since
        assert await ml.run(machine, [client], _PARAMS) == "SUCCESS"

    assert hud.clicks == 1
    client.teleport.assert_not_called()


async def test_a_mark_kept_through_a_recall_cooldown_is_not_marked_again():
    """Just recalled: the timer grays the button, but the mark is still there and is left alone."""
    machine, client = _machine(), _ready_client()
    ml._remember(machine, client, ml.Mark(_ZONE, XYZ(0.0, 0.0, 0.0)))
    hud = _Hud(client, marked=True, here=True, cooldown=1)

    with _game({client: hud}):
        result = await ml.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    assert hud.clicks == 0


async def test_a_mark_set_since_somewhere_else_is_replaced():
    """A client whose last mark is elsewhere (say, a level scaling trip) marks this spot again."""
    machine, client = _machine(), _ready_client()
    ml._remember(machine, client, ml.Mark(_ZONE, XYZ(5000.0, 0.0, 0.0)))
    hud = _Hud(client, marked=True)

    with _game({client: hud}):
        result = await ml.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    assert hud.clicks == 1
    assert ml.marked_at(machine, client).position.x == 0.0


async def test_a_remembered_mark_that_is_gone_is_set_again():
    """The record says marked, but the RecallButton is grayed with no timer: mark again."""
    machine, client = _machine(), _ready_client()
    ml._remember(machine, client, ml.Mark(_ZONE, XYZ(0.0, 0.0, 0.0)))
    hud = _Hud(client)

    with _game({client: hud}):
        result = await ml.run(machine, [client], _PARAMS)

    assert result == "SUCCESS"
    assert hud.clicks == 1


def test_forget_drops_only_that_client():
    machine, kept, dropped = _machine(), _ready_client(), _ready_client()
    ml._remember(machine, kept, ml.Mark(_ZONE, XYZ(0.0, 0.0, 0.0)))
    ml._remember(machine, dropped, ml.Mark(_ZONE, XYZ(0.0, 0.0, 0.0)))

    ml.forget(machine, dropped)

    assert ml.marked_at(machine, dropped) is None
    assert ml.marked_at(machine, kept) is not None


def test_forget_with_no_record_leaves_the_scratch_alone():
    machine = _machine()

    ml.forget(machine, _ready_client())

    assert machine.node_scratch == {}


async def test_a_forgotten_mark_is_set_again():
    """After a recall missed it, the same spot is marked again instead of passed by."""
    machine, client = _machine(), _ready_client()
    hud = _Hud(client)

    with _game({client: hud}):
        assert await ml.run(machine, [client], _PARAMS) == "SUCCESS"
        ml.forget(machine, client)
        assert await ml.run(machine, [client], _PARAMS) == "SUCCESS"

    assert hud.clicks == 3  # set; then a clear of the mark it stands on, and the mark again


async def test_only_the_clients_without_the_mark_are_checked():
    """A client already marked here is not asked whether it can mark again."""
    machine, marked, fresh = _machine(), _ready_client(), _ready_client()
    ml._remember(machine, marked, ml.Mark(_ZONE, XYZ(0.0, 0.0, 0.0)))
    huds = {marked: _Hud(marked, marked=True, here=True, can_mark=False), fresh: _Hud(fresh)}

    with _game(huds):
        result = await ml.run(machine, [marked, fresh], _PARAMS)

    assert result == "SUCCESS"
    assert huds[marked].clicks == 0
    assert huds[fresh].clicks == 1
