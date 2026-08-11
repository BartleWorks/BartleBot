"""Unit tests for the group formation engine.

Group membership is read from the UI (the Leave Group button on the social window), not
from memory — see the module docstring in bartlebot/group_manager.py for why. These tests
therefore drive everything through a fake that models that UI.
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from wizwalker import Keycode

import bartlebot.group_manager as gm
import bartlebot.nodes.node_helpers as nh
from bartlebot.nodes.paths import (
    ALREADY_IN_GROUP_DISMISS_PATH,
    CHARACTER_PROFILE_CLOSE_PATH,
    CHARACTER_PROFILE_PATH,
    GROUP_INVITE_ACCEPT_PATH,
    GROUP_WINDOW_CLOSE_PATH,
    INVITE_TO_GROUP_BUTTON_PATH,
    LEAVE_GROUP_BUTTON_PATH,
    SOCIAL_BUTTON_PATH,
    SOCIAL_WINDOW_PATH,
)
from tests.conftest import FakeClient

# Captured before any patching so the fake can delegate to the real retry loops.
_REAL_CLICK_UNTIL_GONE = nh.click_until_gone
_REAL_PRESS_KEY_UNTIL_GONE = nh.press_key_until_gone


class FakeUI:
    """Models the group-related UI across several clients.

    Tracks per-client group membership and answers the window queries group_manager makes,
    so tests can assert on real behaviour (who got invited, what got closed) rather than on
    call counts against opaque mocks.

    Clicking the social HUD button *toggles* the window, as in the game. That toggle is
    load-bearing: is_in_group() closes the window after reading it, so every later check
    has to reopen it.
    """

    def __init__(self):
        self.grouped: dict[FakeClient, bool] = {}
        self.social_open: dict[FakeClient, bool] = {}
        self.already_in_other_group: set[FakeClient] = set()
        self.popup_showing: dict[FakeClient, bool] = {}
        self.closed_group_window_on: list[FakeClient] = []
        self.social_button_clicks: list[FakeClient] = []
        # True while the leader's friend profile popup is open. The social window opens on
        # top of it and covers its close button, so a close click aimed at the profile
        # while the social window is open lands on the social window instead.
        self.profile_open: dict[FakeClient, bool] = {}
        # Set once the first invite is sent. Membership checks also open and close the
        # social window, so tests about cleanup ordering must ignore anything before this.
        self.invite_sent = False
        # Number of close clicks to swallow before a window actually closes, modelling a
        # click absorbed by an overlapping window or dropped mid-animation.
        self.swallow_closes = 0
        # The game has TWO separate friends-list windows and F cycles between them:
        # closed -> "compact" (NewFriendsListWindow) -> "large" (wndFriendsList) -> closed.
        # Modelling only one of them would let a close that checks a single window name
        # pass here while leaving the other list up in the game.
        self.friends_list_stage: dict[FakeClient, str] = {}
        self.friends_list_close_clicks = 0
        self.invited_names: list[str | None] = []
        self.accepts_invites = True
        # When False the social button does nothing, modelling a click that never lands.
        self.social_button_works = True
        self._swallowed: dict[tuple[int, str], int] = {}
        self._by_name: dict[str, FakeClient] = {}
        self._awaiting: dict[FakeClient, FakeClient] = {}  # target -> inviting leader
        self._pending_name: str | None = None

    def _swallow(self, client, path) -> bool:
        """Return True if this close click should be discarded rather than take effect."""
        if not self.swallow_closes:
            return False
        key = (id(client), "/".join(path))
        seen = self._swallowed.get(key, 0)
        if seen < self.swallow_closes:
            self._swallowed[key] = seen + 1
            return True
        return False

    def make_client(self, name: str | None = None, *, grouped: bool = False) -> FakeClient:
        client = FakeClient()
        self.grouped[client] = grouped
        self.social_open[client] = False

        _NEXT_STAGE = {"closed": "compact", "compact": "large", "large": "closed"}

        async def send_key(key, _seconds=0):
            # F cycles through the two friends-list windows, as in the game.
            if key is Keycode.F:
                self.friends_list_close_clicks += 1
                if not self._swallow(client, ("F",)):
                    stage = self.friends_list_stage.get(client, "closed")
                    self.friends_list_stage[client] = _NEXT_STAGE[stage]

        client.send_key = AsyncMock(side_effect=send_key)
        if name:
            self._by_name[name] = client
        return client

    async def is_visible_by_path(self, client, path) -> bool:
        # Close buttons report visible=True at ALL times — a control's visibility flag
        # ignores its parents. Anything polling a close button to decide whether its
        # window shut will therefore never terminate. This is not an artifact of the
        # fake; it is what the game does (tools/probe_friends_list.py).
        if path in (CHARACTER_PROFILE_CLOSE_PATH, GROUP_WINDOW_CLOSE_PATH):
            return True
        if path == CHARACTER_PROFILE_PATH:
            return self.profile_open.get(client, False)
        if path == SOCIAL_WINDOW_PATH:
            return self.social_open.get(client, False)
        if path == LEAVE_GROUP_BUTTON_PATH:
            # The game HIDES this button when ungrouped rather than graying it.
            return self.grouped.get(client, False)
        if path == GROUP_INVITE_ACCEPT_PATH:
            return client in self._awaiting
        if path == ALREADY_IN_GROUP_DISMISS_PATH:
            return self.popup_showing.get(client, False)
        return False

    async def is_enabled_by_path(self, client, path) -> bool:
        # Mirrors the real helper: True for any window that exists and is not grayed.
        # The Leave Group button exists in the tree in BOTH states, so this says nothing
        # about group membership — which is exactly the bug this fake now pins.
        return True

    async def click_by_path(self, client, path) -> bool:
        if path == INVITE_TO_GROUP_BUTTON_PATH:
            self.invite_sent = True
        if path == SOCIAL_BUTTON_PATH:
            self.social_button_clicks.append(client)
            if self.social_button_works:
                self.social_open[client] = not self.social_open.get(client, False)
            return True
        if path == INVITE_TO_GROUP_BUTTON_PATH:
            self.invited_names.append(self._pending_name)
            target = self._by_name.get(self._pending_name)
            if target is None:
                return True
            if target in self.already_in_other_group:
                self.popup_showing[client] = True
            elif self.accepts_invites:
                self._awaiting[target] = client
            return True
        if path == GROUP_INVITE_ACCEPT_PATH and client in self._awaiting:
            leader = self._awaiting.pop(client)
            # Forming a group puts the INVITER in it as well, not just the invitee.
            for member in (client, leader):
                self.grouped[member] = True
                self.social_open[member] = True  # the game opens it on joining
            return True
        if path == ALREADY_IN_GROUP_DISMISS_PATH:
            self.popup_showing[client] = False
            return True
        if path == GROUP_WINDOW_CLOSE_PATH:
            self.closed_group_window_on.append(client)
            if not self._swallow(client, path):
                self.social_open[client] = False
            return True
        if path == CHARACTER_PROFILE_CLOSE_PATH:
            if self.social_open.get(client, False):
                # The social window is on top; the click never reaches the profile.
                return False
            if not self._swallow(client, path):
                self.profile_open[client] = False
            return True
        return True

    async def open_friend_profile(self, client, name):
        self._pending_name = name
        self.profile_open[client] = True
        self.friends_list_stage[client] = "compact"
        return None

    async def _is_named_window_visible(self, client, name: str) -> bool:
        """Stands in for is_named_window_visible; only the friends lists are modelled.

        The two lists answer to different names, so a caller that asks about just one of
        them will see "closed" while the other is still on screen.
        """
        stage = self.friends_list_stage.get(client, "closed")
        if name == "NewFriendsListWindow":
            return stage == "compact"
        if name == "wndFriendsList":
            return stage == "large"
        return False

    async def _fast_click_until_gone(self, client, path, is_open=None, timeout=0.5, poll_interval=0.0):
        """The real retry loop, with the production 0.4 s pause removed."""
        return await _REAL_CLICK_UNTIL_GONE(client, path, is_open=is_open, timeout=timeout, poll_interval=poll_interval)

    async def _fast_press_key_until_gone(self, client, key, is_open, timeout=0.5, poll_interval=0.0):
        """The real key-retry loop, with the production 0.4 s pause removed."""
        return await _REAL_PRESS_KEY_UNTIL_GONE(
            client, key, is_open=is_open, timeout=timeout, poll_interval=poll_interval
        )

    def patches(self, *, real_friends_list_close: bool = False):
        """Patch the window layer.

        With real_friends_list_close, node_helpers.close_friends_list is NOT stubbed — the
        real function runs against this fake, so its choice of openness predicate is under
        test. Stubbing it would make a regression there invisible.
        """
        base = (
            patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=self.is_visible_by_path)),
            patch.object(nh, "is_enabled_by_path", new=AsyncMock(side_effect=self.is_enabled_by_path)),
            patch.object(nh, "is_named_window_visible", new=AsyncMock(side_effect=self._is_named_window_visible)),
            patch.object(nh, "click_by_path", new=AsyncMock(side_effect=self.click_by_path)),
            patch.object(nh, "click_until_gone", new=AsyncMock(side_effect=self._fast_click_until_gone)),
            patch.object(nh, "press_key_until_gone", new=AsyncMock(side_effect=self._fast_press_key_until_gone)),
            patch.object(nh, "open_friend_profile", new=AsyncMock(side_effect=self.open_friend_profile)),
        )
        if not real_friends_list_close:
            base += (patch.object(nh, "close_friends_list", new=AsyncMock(return_value=True)),)
        return base + (
            patch.object(gm, "GROUP_UI_SETTLE", 0),
            patch.object(gm, "POLL_INTERVAL", 0),
            patch.object(gm, "GROUP_INVITE_TIMEOUT", 0.05),
            patch.object(gm, "GROUP_VERIFY_TIMEOUT", 0.05),
            patch.object(gm, "TRIGGER_TIMEOUT", 0.05),
        )


@pytest.fixture
def ui():
    return FakeUI()


def _run_with(ui_obj, coro_factory):
    """Apply every FakeUI patch around an awaitable factory."""

    async def runner():
        with ExitStack() as stack:
            for p in ui_obj.patches():
                stack.enter_context(p)
            return await coro_factory()

    return runner()


# ---------------------------------------------------------------------------
# Reading membership
# ---------------------------------------------------------------------------


async def test_is_in_group_true_when_leave_button_enabled(ui):
    client = ui.make_client(grouped=True)
    assert await _run_with(ui, lambda: gm.is_in_group(client)) is True


async def test_is_in_group_false_when_leave_button_hidden(ui):
    client = ui.make_client(grouped=False)
    assert await _run_with(ui, lambda: gm.is_in_group(client)) is False


async def test_is_in_group_uses_visibility_not_enabled_state(ui):
    """Regression: is_enabled_by_path returns True for any window that merely EXISTS and
    is not grayed, and the whole SocialSystemsWindow subtree exists whether or not the
    window is open. Reading enabled-ness here reported every client as grouped, always."""
    client = ui.make_client(grouped=False)

    with ExitStack() as stack:
        for p in ui.patches():
            stack.enter_context(p)
        # Stand in for the real helper's behaviour on a hidden-but-present button.
        enabled = AsyncMock(return_value=True)
        stack.enter_context(patch.object(nh, "is_enabled_by_path", new=enabled))
        assert await gm.is_in_group(client) is False

    assert not any(call.args[1] == LEAVE_GROUP_BUTTON_PATH for call in enabled.call_args_list), (
        "group membership must not be read with is_enabled_by_path"
    )


async def test_is_in_group_clicks_the_social_button_to_open_the_window(ui):
    """The HUD button is used, not Ctrl+A — send_hotkey drops the modifier and the
    character strafes on a bare 'A' instead of the window opening."""
    client = ui.make_client(grouped=True)

    await _run_with(ui, lambda: gm.is_in_group(client))

    assert ui.social_button_clicks == [client]
    assert not hasattr(client, "send_hotkey"), "the hotkey route must not be used"


async def test_is_in_group_does_not_toggle_an_already_open_window(ui):
    """The social button toggles, so clicking it on an open window would close it."""
    client = ui.make_client(grouped=True)
    ui.social_open[client] = True

    await _run_with(ui, lambda: gm.is_in_group(client))
    assert ui.social_button_clicks == []


async def test_is_in_group_closes_the_window_afterwards(ui):
    """Leaving the social window open would cover the HUD for later nodes."""
    client = ui.make_client(grouped=True)

    await _run_with(ui, lambda: gm.is_in_group(client))
    assert client in ui.closed_group_window_on
    assert ui.social_open[client] is False


async def test_is_in_group_false_when_the_window_never_opens(ui):
    client = ui.make_client(grouped=True)
    ui.social_button_works = False  # the click lands but the window never appears

    assert await _run_with(ui, lambda: gm.is_in_group(client)) is False


async def test_is_in_group_never_raises(ui):
    client = ui.make_client(grouped=True)

    with ExitStack() as stack:
        for p in ui.patches():
            stack.enter_context(p)
        stack.enter_context(
            patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=RuntimeError("client gone")))
        )
        assert await gm.is_in_group(client) is False


async def test_is_grouped_requires_every_client(ui):
    a = ui.make_client(grouped=True)
    b = ui.make_client(grouped=False)
    assert await _run_with(ui, lambda: gm.is_grouped([a, b])) is False

    ui.grouped[b] = True
    assert await _run_with(ui, lambda: gm.is_grouped([a, b])) is True


async def test_is_grouped_false_for_single_client(ui):
    """A group of one confers no benefit, so it must not suppress a regroup."""
    client = ui.make_client(grouped=True)
    assert await _run_with(ui, lambda: gm.is_grouped([client])) is False


# ---------------------------------------------------------------------------
# Leader selection
# ---------------------------------------------------------------------------


def test_leader_is_a_client_already_in_a_group():
    """A group that merely lost a member is added to, not rebuilt from the default leader."""
    assert gm._choose_leader([False, True, True], leader_index=0) == 1


def test_leader_index_wins_when_it_is_already_grouped():
    assert gm._choose_leader([True, True, False], leader_index=0) == 0


def test_leader_falls_back_to_leader_index_when_nobody_grouped():
    assert gm._choose_leader([False, False, False], leader_index=2) == 2


# ---------------------------------------------------------------------------
# form_group
# ---------------------------------------------------------------------------


async def test_already_grouped_makes_no_invites(ui):
    a = ui.make_client("Alice", grouped=True)
    b = ui.make_client("Bob", grouped=True)

    result = await _run_with(ui, lambda: gm.form_group([a, b], ["Alice", "Bob"]))

    assert result.success is True
    assert result.added == []
    assert ui.invited_names == []


async def test_single_client_is_a_no_op(ui):
    result = await _run_with(ui, lambda: gm.form_group([ui.make_client("Alice")], ["Alice"]))
    assert result.success is True
    assert "fewer than two clients" in result.reason


async def test_mismatched_names_length_fails_without_inviting(ui):
    clients = [ui.make_client("Alice"), ui.make_client("Bob")]
    result = await _run_with(ui, lambda: gm.form_group(clients, ["OnlyOne"]))

    assert result.success is False
    assert ui.invited_names == []


async def test_ungrouped_client_is_invited_and_confirmed(ui):
    leader = ui.make_client("Alice")
    joiner = ui.make_client("Bob")

    result = await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice", "Bob"]))

    assert result.success is True
    assert result.added == ["Bob"]
    assert ui.invited_names == ["Bob"]
    assert ui.grouped[joiner] is True


async def test_client_with_unknown_name_is_reported_not_skipped(ui):
    leader = ui.make_client("Alice")
    joiner = ui.make_client()

    result = await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice", None]))

    assert result.success is False
    assert result.failed == ["client 1"]
    assert ui.invited_names == []


async def test_invite_never_accepted_fails(ui):
    """The invitation prompt never appears on the target."""
    leader = ui.make_client("Alice")
    joiner = ui.make_client("Bob")
    ui.accepts_invites = False

    result = await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice", "Bob"]))

    assert result.success is False
    assert result.failed == ["Bob"]


async def test_player_already_in_another_group_is_not_retried(ui):
    """Retrying cannot help — they must leave their other group first."""
    leader = ui.make_client("Alice")
    joiner = ui.make_client("Bob")
    ui.already_in_other_group.add(joiner)

    result = await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice", "Bob"]))

    assert result.success is False
    assert result.failed == ["Bob"]
    # One attempt only, despite GROUP_ADD_RETRIES being greater than 1.
    assert ui.invited_names == ["Bob"]


async def test_already_in_group_popup_is_dismissed(ui):
    """An undismissed popup would block the next invite."""
    leader = ui.make_client("Alice")
    joiner = ui.make_client("Bob")
    ui.already_in_other_group.add(joiner)

    await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice", "Bob"]))

    assert ui.popup_showing.get(leader) is False


async def test_friend_not_in_list_fails_without_retrying(ui):
    leader = ui.make_client("Alice")
    joiner = ui.make_client("Bob")
    profile = AsyncMock(side_effect=ValueError("Could not find friend"))

    with ExitStack() as stack:
        for p in ui.patches():
            stack.enter_context(p)
        stack.enter_context(patch.object(nh, "open_friend_profile", new=profile))
        result = await gm.form_group([leader, joiner], ["Alice", "Bob"])

    assert result.success is False
    assert result.failed == ["Bob"]
    assert profile.call_count == 1


async def test_group_window_is_closed_on_both_players(ui):
    """The game opens it on the inviter and the invitee, and neither closes itself."""
    leader = ui.make_client("Alice")
    joiner = ui.make_client("Bob")

    await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice", "Bob"]))

    assert leader in ui.closed_group_window_on
    assert joiner in ui.closed_group_window_on


async def test_friend_profile_is_closed_after_the_social_window(ui):
    """The social window opens on top of the profile and covers its close button, so
    closing the profile first leaves it open — the click lands on the social window."""
    leader = ui.make_client("Alice")
    joiner = ui.make_client("Bob")

    await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice", "Bob"]))

    assert ui.profile_open.get(leader) is False, "the friend profile was left open on the leader"


async def test_profile_close_is_attempted_only_once_the_social_window_is_gone(ui):
    """Pins the ordering directly, independent of the fake's overlap modelling.

    Only clicks made after the invite are considered: the membership checks that run
    beforehand also close the social window, and counting those hides the ordering.
    """
    leader = ui.make_client("Alice")
    joiner = ui.make_client("Bob")
    order: list[str] = []

    async def record(client, path) -> bool:
        result = await ui.click_by_path(client, path)
        if ui.invite_sent and client is leader:
            if path == GROUP_WINDOW_CLOSE_PATH:
                order.append("social")
            elif path == CHARACTER_PROFILE_CLOSE_PATH:
                order.append("profile")
        return result

    with ExitStack() as stack:
        for p in ui.patches():
            stack.enter_context(p)
        stack.enter_context(patch.object(nh, "click_by_path", new=AsyncMock(side_effect=record)))
        await gm.form_group([leader, joiner], ["Alice", "Bob"])

    assert "social" in order and "profile" in order, f"expected both closes on the leader, got {order}"
    assert order.index("social") < order.index("profile"), f"profile closed before the social window: {order}"


async def test_friends_list_is_closed_once_for_the_whole_regroup(ui):
    """It is reused between invites, so closing per-invite would force a reopen each time."""
    leader = ui.make_client("Alice")
    b = ui.make_client("Bob")
    c = ui.make_client("Carol")
    close = AsyncMock(return_value=True)

    with ExitStack() as stack:
        for p in ui.patches():
            stack.enter_context(p)
        stack.enter_context(patch.object(nh, "close_friends_list", new=close))
        result = await gm.form_group([leader, b, c], ["Alice", "Bob", "Carol"])

    assert result.added == ["Bob", "Carol"]
    assert close.await_count == 1


# ---------------------------------------------------------------------------
# Concurrency
#
# Two form_group calls can overlap in production: machine.pause() only suspends the FSM
# after the current node finishes, so a form_group node keeps running while recovery
# regroups; and set_machine() precedes pre-run setup, so a crash during deck apply queues
# recovery alongside pre-run grouping. With different leader_index values each call would
# elect a different leader and build a SEPARATE group — a split the UI cannot detect,
# because membership is only readable per client as a yes/no.
# ---------------------------------------------------------------------------


async def test_concurrent_calls_do_not_create_two_groups(ui):
    """The exact four-client split: two leaders each inviting their own partner."""
    import asyncio

    a = ui.make_client("Alice")
    b = ui.make_client("Bob")
    c = ui.make_client("Carol")
    d = ui.make_client("Dave")
    clients = [a, b, c, d]
    names = ["Alice", "Bob", "Carol", "Dave"]

    with ExitStack() as stack:
        for p in ui.patches():
            stack.enter_context(p)
        # Different leaders, as a form_group node with leader_index=2 racing the
        # recovery hook's default of 0 would produce.
        await asyncio.gather(
            gm.form_group(clients, names, leader_index=0),
            gm.form_group(clients, names, leader_index=2),
        )

    # Exactly one leader did the inviting, so there is one group, not two.
    assert sorted(ui.invited_names) == ["Bob", "Carol", "Dave"]
    assert all(ui.grouped[client] for client in clients)


async def test_second_concurrent_call_finds_the_group_already_built(ui):
    """The loser of the race must no-op, not re-invite everyone."""
    import asyncio

    a = ui.make_client("Alice")
    b = ui.make_client("Bob")

    with ExitStack() as stack:
        for p in ui.patches():
            stack.enter_context(p)
        first, second = await asyncio.gather(
            gm.form_group([a, b], ["Alice", "Bob"]),
            gm.form_group([a, b], ["Alice", "Bob"]),
        )

    assert first.success and second.success
    # One of the two did the work; the other found everyone already grouped.
    assert sorted([len(first.added), len(second.added)]) == [0, 1]
    assert ui.invited_names == ["Bob"]


async def test_friends_list_close_is_retried_until_the_list_is_gone(ui):
    """The reported failure: the first close click is swallowed, the next click lands on
    the social button behind the list, and the list is left open."""
    leader = ui.make_client("Alice")
    joiner = ui.make_client("Bob")
    ui.swallow_closes = 1

    with ExitStack() as stack:
        for p in ui.patches(real_friends_list_close=True):
            stack.enter_context(p)
        result = await gm.form_group([leader, joiner], ["Alice", "Bob"])

    assert result.success is True
    assert ui.friends_list_stage.get(leader) == "closed", "the friends list was left open"
    # One swallowed press, then compact -> large -> closed. More than that means the loop
    # could not tell the list had closed and kept pressing until the timeout — which is
    # what happens if openness is judged from the permanently-visible close button.
    assert ui.friends_list_close_clicks == 3, (
        f"expected 3 close presses, got {ui.friends_list_close_clicks} — "
        "the retry loop is not detecting that the list closed"
    )


async def test_close_friends_list_closes_the_larger_list_too(ui):
    """The large list (wndFriendsList) is a separate window, not a state of the compact one.

    F cycles compact -> large -> closed, so a close that only watches
    NewFriendsListWindow stops after one press and leaves the large list on screen.
    """
    client = ui.make_client("Alice")
    ui.friends_list_stage[client] = "large"

    with ExitStack() as stack:
        for p in ui.patches(real_friends_list_close=True):
            stack.enter_context(p)
        closed = await nh.close_friends_list(client)

    assert closed is True
    assert ui.friends_list_stage[client] == "closed"
    assert ui.friends_list_close_clicks == 1


async def test_close_friends_list_cycles_past_the_larger_list(ui):
    """Starting from the compact list, F opens the large one before closing anything."""
    client = ui.make_client("Alice")
    ui.friends_list_stage[client] = "compact"

    with ExitStack() as stack:
        for p in ui.patches(real_friends_list_close=True):
            stack.enter_context(p)
        closed = await nh.close_friends_list(client)

    assert closed is True
    assert ui.friends_list_stage[client] == "closed"
    # compact -> large -> closed. A one-press close would report success with the large
    # list still up.
    assert ui.friends_list_close_clicks == 2


async def test_window_closes_survive_a_swallowed_click(ui):
    """Every close in the cleanup retries, not just the friends list."""
    leader = ui.make_client("Alice")
    joiner = ui.make_client("Bob")
    ui.swallow_closes = 1

    with ExitStack() as stack:
        for p in ui.patches(real_friends_list_close=True):
            stack.enter_context(p)
        await gm.form_group([leader, joiner], ["Alice", "Bob"])

    assert ui.profile_open.get(leader) is False, "the friend profile was left open"
    assert ui.social_open.get(leader) is False, "the leader's social window was left open"
    assert ui.social_open.get(joiner) is False, "the joiner's social window was left open"


async def test_already_grouped_client_is_not_reinvited(ui):
    """Only the client missing from the group is invited."""
    leader = ui.make_client("Alice", grouped=True)
    already = ui.make_client("Bob", grouped=True)
    missing = ui.make_client("Carol", grouped=False)

    result = await _run_with(ui, lambda: gm.form_group([leader, already, missing], ["Alice", "Bob", "Carol"]))

    assert result.success is True
    assert result.added == ["Carol"]
    assert ui.invited_names == ["Carol"]
