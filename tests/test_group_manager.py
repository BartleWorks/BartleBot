"""Unit tests for the group formation engine.

Group membership is read from the UI (the Leave Group button on the social window), not
from memory — see the module docstring in bartlebot/group_manager.py for why. These tests
therefore drive everything through a fake that models that UI.
"""

import asyncio
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
    GROUP_ROSTER_LINES,
    GROUP_WINDOW_CLOSE_PATH,
    INVITE_TO_GROUP_BUTTON_PATH,
    LEAVE_GROUP_BUTTON_PATH,
    SOCIAL_BUTTON_PATH,
    SOCIAL_GROUP_TAB_PATH,
    SOCIAL_WINDOW_PATH,
    group_member_name_path,
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
        # Which group each client is in, as a set of member names. The game shows every
        # member the same roster, so modelling groups by identity (rather than per-client
        # name lists) is what makes a SPLIT group expressible: two clients can both be
        # grouped while their rosters disagree, which is the case the Leave Group button
        # alone cannot see.
        self.group_of: dict[FakeClient, tuple[str, ...]] = {}
        self.name_of: dict[FakeClient, str] = {}
        # Wraps every roster name in KI colour markup, as the game does.
        self.roster_markup = True
        # The first EMPTY roster slot does not read blank. It is an invitation prompt, and
        # its NameText carries the game's leftover placeholder "Left" — on every client, in
        # every group. Modelled because a constant entry is what would defeat the "is this
        # roster readable?" guard in same_group.
        self.roster_empty_slot_text = "Left"
        self.social_open: dict[FakeClient, bool] = {}
        # High-water mark of how many clients had the social window open at the same
        # moment. read_group_flags surveys concurrently, so this reaching N is the
        # observable proof that the checks overlapped rather than ran one after another.
        self.max_social_open_at_once = 0
        self.group_tab_clicks: list[FakeClient] = []
        self.leave_clicks: list[FakeClient] = []
        # When False the Leave Group click lands but membership never changes, modelling a
        # click the game ignored.
        self.leaving_works = True
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
        self._members: dict[FakeClient, list[FakeClient]] = {}
        # Clients that, on accepting our invitation, end up in somebody ELSE's group —
        # modelling a competing invite accepted in the gap between our click and our read.
        self.joins_foreign_group_on_accept: set[FakeClient] = set()
        self._awaiting: dict[FakeClient, FakeClient] = {}  # target -> inviting leader
        self._pending_name: str | None = None

    def _set_social_open(self, client, value: bool) -> None:
        self.social_open[client] = value
        self.max_social_open_at_once = max(self.max_social_open_at_once, sum(self.social_open.values()))

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

    def put_in_group(self, *clients: FakeClient) -> None:
        """Put these clients (and anyone already grouped with them) into one group."""
        members: list[FakeClient] = []
        for client in clients:
            for member in self._members.get(client, [client]):
                if member not in members:
                    members.append(member)
        roster = tuple(self.name_of[m] for m in members)
        for member in members:
            self._members[member] = members
            self.group_of[member] = roster
            self.grouped[member] = True

    def remove_from_group(self, client: FakeClient) -> None:
        """Take a client out of its group, dissolving the group if one member would remain.

        The dissolve is the game's own behaviour and the reason a 2+2 split needs only ONE
        leave click: the partner is dropped out with it.
        """
        members = self._members.get(client, [client])
        remaining = [m for m in members if m is not client]
        for gone in (client, *(remaining if len(remaining) <= 1 else ())):
            self.grouped[gone] = False
            self.group_of[gone] = ()
            self._members[gone] = [gone]
        if len(remaining) > 1:
            roster = tuple(self.name_of[m] for m in remaining)
            for member in remaining:
                self._members[member] = remaining
                self.group_of[member] = roster

    def put_in_foreign_group(self, client: FakeClient, *strangers: str) -> None:
        """Put this client in a group with players that are not clients of ours.

        The case the Leave Group button cannot see: grouped=True, but with the wrong
        people.
        """
        self._members[client] = [client]
        self.group_of[client] = (self.name_of[client], *strangers)
        self.grouped[client] = True

    async def get_text_by_path(self, client, path) -> str:
        """Answer the roster reads, modelling the game's markup and self-first ordering.

        Returns nothing unless the social window is open, so a roster read that happens
        after the window is closed shows up as an empty roster rather than passing.
        """
        if not self.social_open.get(client, False):
            return ""
        for line in range(1, GROUP_ROSTER_LINES + 1):
            if path != group_member_name_path(line):
                continue
            roster = self.group_of.get(client, ())
            # Every member sees the same set, but sees *itself* on Line1. Modelling that
            # means each client reads the roster in a different order, which is what
            # forces same_group to compare sets rather than sequences.
            me = self.name_of.get(client)
            if me and me in roster:
                roster = (me,) + tuple(n for n in roster if n != me)
            if line <= len(roster):
                return self._render(roster[line - 1])
            if line == len(roster) + 1:
                return self._render(self.roster_empty_slot_text)
            return ""
        return ""

    def _render(self, name: str) -> str:
        return f"<Color;255,255,255>{name}</Color>" if self.roster_markup else name

    def make_client(self, name: str | None = None, *, grouped: bool = False) -> FakeClient:
        client = FakeClient()
        name = name or f"Wizard{len(self.name_of) + 1}"
        self.name_of[client] = name
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
        self._by_name[name] = client
        if grouped:
            # Everything made grouped joins ONE group, which is what the older tests mean
            # by "already grouped". A split group has to be built explicitly, with
            # put_in_group.
            self.put_in_group(*[c for c, g in self.grouped.items() if g])
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
        if path == LEAVE_GROUP_BUTTON_PATH:
            self.leave_clicks.append(client)
            if not self.leaving_works:
                return True
            self.remove_from_group(client)
            return True
        if path == SOCIAL_GROUP_TAB_PATH:
            self.group_tab_clicks.append(client)
            return True
        if path == SOCIAL_BUTTON_PATH:
            self.social_button_clicks.append(client)
            if self.friends_list_stage.get(client, "closed") != "closed":
                # The friends list sits on top of the social HUD button, so the click lands
                # on the list and the social window never opens. This is what a run started
                # with the large friends list up actually did.
                return True
            if self.social_button_works:
                self._set_social_open(client, not self.social_open.get(client, False))
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
            # Forming a group puts the INVITER in it as well, not just the invitee, and
            # both end up on the SAME roster — which is what makes the post-invite roster
            # confirmation in _invite_and_accept pass.
            if client in self.joins_foreign_group_on_accept:
                self.put_in_foreign_group(client, "Stranger One")
            else:
                self.put_in_group(leader, client)
            for member in (client, leader):
                self._set_social_open(member, True)  # the game opens it on joining
            return True
        if path == ALREADY_IN_GROUP_DISMISS_PATH:
            self.popup_showing[client] = False
            return True
        if path == GROUP_WINDOW_CLOSE_PATH:
            self.closed_group_window_on.append(client)
            if not self._swallow(client, path):
                self._set_social_open(client, False)
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

    async def _stub_close_friends_list(self, client) -> bool:
        """Stand in for the real F-cycling close, succeeding at once.

        It must still update the modelled state: the friends list covers the social button
        here, so a stub that reported success without closing anything would leave every
        later social-window open blocked.
        """
        self.friends_list_stage[client] = "closed"
        return True

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
            patch.object(nh, "get_text_by_path", new=AsyncMock(side_effect=self.get_text_by_path)),
            patch.object(nh, "click_until_gone", new=AsyncMock(side_effect=self._fast_click_until_gone)),
            patch.object(nh, "press_key_until_gone", new=AsyncMock(side_effect=self._fast_press_key_until_gone)),
            patch.object(nh, "open_friend_profile", new=AsyncMock(side_effect=self.open_friend_profile)),
        )
        if not real_friends_list_close:
            base += (patch.object(nh, "close_friends_list", new=AsyncMock(side_effect=self._stub_close_friends_list)),)
        return base + (
            patch.object(gm, "GROUP_UI_SETTLE", 0),
            patch.object(gm, "POLL_INTERVAL", 0),
            patch.object(gm, "GROUP_INVITE_TIMEOUT", 0.05),
            patch.object(gm, "GROUP_VERIFY_TIMEOUT", 0.05),
            patch.object(gm, "GROUP_PROFILE_TIMEOUT", 0.05),
            patch.object(gm, "GROUP_JOIN_HINT_TIMEOUT", 0.05),
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


@pytest.mark.parametrize("stage", ["compact", "large"])
async def test_an_open_friends_list_is_closed_before_the_social_button_is_clicked(ui, stage):
    """Regression (issue #96): starting a run with the friends list up failed the grouping.

    The list sits on top of the social HUD button, so the click landed on the list, the
    social window never opened, and every client read as ungrouped. Both list windows do
    it — the large one is how it was hit in the wild, since nothing else leaves that up.
    """
    client = ui.make_client(grouped=True)
    ui.friends_list_stage[client] = stage

    assert await _run_with(ui, lambda: gm.is_in_group(client)) is True
    assert ui.friends_list_stage[client] == "closed"


async def test_the_friends_list_is_not_touched_when_it_is_already_closed(ui):
    """F is a toggle, so pressing it on a closed list would open one."""
    client = ui.make_client(grouped=True)

    with ExitStack() as stack:
        for p in ui.patches():
            stack.enter_context(p)
        closer = AsyncMock(side_effect=ui._stub_close_friends_list)
        stack.enter_context(patch.object(nh, "close_friends_list", new=closer))
        await gm.is_in_group(client)

    closer.assert_not_called()


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


async def test_is_in_group_selects_the_group_tab(ui):
    """Membership is read off the Group page, so the window must be switched to it.

    No assertion that the switch is SKIPPED when the window is already on that tab: the
    game gives us no way to tell which tab is showing (see tools/probe_group_tab.py), so
    the click is unconditional by necessity.
    """
    client = ui.make_client(grouped=True)

    await _run_with(ui, lambda: gm.is_in_group(client))
    assert ui.group_tab_clicks == [client]


async def test_read_group_flags_is_positionally_aligned_with_clients(ui):
    """The flags index into `names` in form_group, so gather's ordering guarantee matters."""
    a = ui.make_client(grouped=False)
    b = ui.make_client(grouped=True)
    c = ui.make_client(grouped=False)

    assert await _run_with(ui, lambda: gm.read_group_flags([a, b, c])) == [False, True, False]


async def test_read_group_flags_checks_clients_concurrently(ui):
    """Sequential checks would never have two social windows open at the same instant.

    Overlap only exists where a check actually WAITS. Here that is the GROUP_UI_SETTLE
    after the tab click — patched to 0 in the fixture, but asyncio.sleep(0) still yields,
    which is all the overlap needs.
    """
    clients = [ui.make_client(grouped=True) for _ in range(4)]

    await _run_with(ui, lambda: gm.read_group_flags(clients))

    assert ui.max_social_open_at_once == len(clients)
    # And every window it opened was closed again, despite the overlap.
    assert not any(ui.social_open.values())


async def test_read_group_flags_survives_one_unreadable_client(ui):
    """One dead client must not cancel the survey for the rest — see is_in_group."""
    good = ui.make_client(grouped=True)
    bad = ui.make_client(grouped=True)

    real_is_visible = ui.is_visible_by_path

    async def explode_for_bad(client, path):
        if client is bad:
            raise RuntimeError("client gone")
        return await real_is_visible(client, path)

    with ExitStack() as stack:
        for p in ui.patches():
            stack.enter_context(p)
        stack.enter_context(patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=explode_for_bad)))
        assert await gm.read_group_flags([good, bad]) == [True, False]


async def test_is_grouped_requires_every_client(ui):
    a = ui.make_client(grouped=True)
    b = ui.make_client(grouped=False)
    assert await _run_with(ui, lambda: gm.is_grouped([a, b])) is False

    ui.put_in_group(a, b)
    assert await _run_with(ui, lambda: gm.is_grouped([a, b])) is True


async def test_is_grouped_false_for_single_client(ui):
    """A group of one confers no benefit, so it must not suppress a regroup."""
    client = ui.make_client(grouped=True)
    assert await _run_with(ui, lambda: gm.is_grouped([client])) is False


# ---------------------------------------------------------------------------
# Reading the roster, and telling one group from two
#
# The Leave Group button answers "am I in a group?" — two clients in two DIFFERENT groups
# both read True. These cover the roster read that distinguishes them.
# ---------------------------------------------------------------------------


async def test_read_group_state_returns_the_roster(ui):
    a = ui.make_client("Wolf Deathblade", grouped=True)
    b = ui.make_client("Iridian Spellthief", grouped=True)

    state = await _run_with(ui, lambda: gm.read_group_state(a))

    assert state.grouped is True
    assert set(state.roster) == {"Wolf Deathblade", "Iridian Spellthief"}
    assert b is not None


async def test_roster_names_have_markup_stripped(ui):
    """Names arrive wrapped in KI colour tags; comparing them raw would never match."""
    a = ui.make_client("Wolf Deathblade", grouped=True)
    ui.make_client("Iridian Spellthief", grouped=True)

    state = await _run_with(ui, lambda: gm.read_group_state(a))

    assert all("<" not in name for name in state.roster), state.roster


async def test_roster_is_read_before_the_window_closes(ui):
    """The fake only answers roster reads while the social window is open.

    read_group_state closes that window in a `finally`, so a roster read placed after the
    read of the Leave Group button — or worse, after cleanup — comes back empty. That
    ordering is invisible in production until a split group goes undetected.
    """
    a = ui.make_client("Wolf Deathblade", grouped=True)
    ui.make_client("Iridian Spellthief", grouped=True)

    state = await _run_with(ui, lambda: gm.read_group_state(a))

    assert state.roster, "the roster was read after the social window had closed"
    assert ui.social_open[a] is False


async def test_empty_slot_text_is_not_read_as_a_member(ui):
    """The first empty roster slot reads "Left" in game, on every client in every group.

    It is not a player. Seen in the wild as a phantom third member of a two-wizard group.
    """
    a = ui.make_client("Wolf Deathblade", grouped=True)
    ui.make_client("Iridian Spellthief", grouped=True)

    state = await _run_with(ui, lambda: gm.read_group_state(a))

    assert set(state.roster) == {"Wolf Deathblade", "Iridian Spellthief"}


async def test_a_wizard_with_no_surname_stays_on_the_roster(ui):
    """A wizard can have a given name and nothing else, so single-word entries are real.

    Regression: the placeholder was first filtered by "has a space", on the wrong theory
    that every name is given-plus-surname. That rule dropped players like this one.
    """
    a = ui.make_client("Wolf", grouped=True)
    ui.make_client("Iridian Spellthief", grouped=True)

    state = await _run_with(ui, lambda: gm.read_group_state(a))

    assert set(state.roster) == {"Wolf", "Iridian Spellthief"}


async def test_empty_slot_text_does_not_make_an_empty_roster_look_readable(ui):
    """The dangerous half of the "Left" entry, and the reason it could not just be left in.

    same_group treats a non-empty roster as one it managed to read. A constant entry pads
    every roster to one member, so two clients whose real member lines failed would compare
    equal on the junk alone — a confident "same group" from two failed reads.
    """
    a = ui.make_client("Wolf Deathblade", grouped=True)
    b = ui.make_client("Iridian Spellthief", grouped=True)
    ui.group_of[a] = ()
    ui.group_of[b] = ()

    states = await _run_with(ui, lambda: gm.read_group_states([a, b]))

    assert all(state.roster == () for state in states)
    assert gm.same_group(states) is None


async def test_same_group_true_when_rosters_match(ui):
    """Each client sees ITSELF on Line1, so the rosters differ in order but not in members.

    Comparing sequences here would report a split for a perfectly ordinary group.
    """
    a = ui.make_client("Wolf Deathblade", grouped=True)
    b = ui.make_client("Iridian Spellthief", grouped=True)

    states = await _run_with(ui, lambda: gm.read_group_states([a, b]))

    assert states[0].roster != states[1].roster, "the fake should model self-first ordering"
    assert gm.same_group(states) is True


async def test_same_group_false_when_clients_are_in_different_groups(ui):
    """The blind spot this whole feature exists for: everyone grouped, nobody together."""
    a = ui.make_client("Wolf Deathblade")
    b = ui.make_client("Iridian Spellthief")
    ui.put_in_foreign_group(a, "Stranger One")
    ui.put_in_foreign_group(b, "Stranger Two")

    states = await _run_with(ui, lambda: gm.read_group_states([a, b]))

    assert all(s.grouped for s in states), "both must look grouped, or this proves nothing"
    assert gm.same_group(states) is False


async def test_same_group_is_none_when_a_roster_cannot_be_read(ui):
    """Empty sets compare equal, so an unreadable roster must not read as agreement."""
    a = ui.make_client("Wolf Deathblade", grouped=True)
    b = ui.make_client("Iridian Spellthief", grouped=True)
    ui.group_of[b] = ()

    states = await _run_with(ui, lambda: gm.read_group_states([a, b]))

    assert gm.same_group(states) is None


async def test_same_group_ignores_name_case(ui):
    a = ui.make_client("Wolf Deathblade")
    b = ui.make_client("Iridian Spellthief")
    ui.put_in_group(a, b)
    ui.group_of[b] = ("iridian spellthief", "WOLF DEATHBLADE")

    states = await _run_with(ui, lambda: gm.read_group_states([a, b]))

    assert gm.same_group(states) is True


async def test_same_group_is_none_for_fewer_than_two_clients(ui):
    a = ui.make_client("Wolf Deathblade", grouped=True)
    states = await _run_with(ui, lambda: gm.read_group_states([a]))
    assert gm.same_group(states) is None


async def test_is_grouped_false_when_clients_are_split(ui):
    """Both report grouped, so the old per-client check said True and skipped the regroup."""
    a = ui.make_client("Wolf Deathblade")
    b = ui.make_client("Iridian Spellthief")
    ui.put_in_foreign_group(a, "Stranger One")
    ui.put_in_foreign_group(b, "Stranger Two")

    assert await _run_with(ui, lambda: gm.is_grouped([a, b])) is False


async def test_is_grouped_falls_back_to_flags_when_rosters_are_unreadable(ui):
    """If a game update breaks the roster path, behaviour must degrade to the old check,
    not to a permanent "everyone is split" that regroups on every single run."""
    a = ui.make_client("Wolf Deathblade", grouped=True)
    b = ui.make_client("Iridian Spellthief", grouped=True)
    ui.group_of[a] = ()
    ui.group_of[b] = ()

    assert await _run_with(ui, lambda: gm.is_grouped([a, b])) is True


# ---------------------------------------------------------------------------
# Leader selection
# ---------------------------------------------------------------------------


def _states(*specs: tuple[str, ...] | None) -> list[gm.GroupState]:
    """Build states from rosters; None means ungrouped, () means grouped but unreadable."""
    return [
        gm.GroupState() if spec is None else gm.GroupState(grouped=True, roster=spec)  # type: ignore[arg-type]
        for spec in specs
    ]


def test_leader_is_a_client_already_in_a_group():
    """A group that merely lost a member is added to, not rebuilt from the default leader."""
    pair = ("B", "C")
    assert gm._choose_leader(_states(None, pair, pair), leader_index=0) == 1


def test_leader_index_wins_when_it_is_already_grouped():
    pair = ("A", "B")
    assert gm._choose_leader(_states(pair, pair, None), leader_index=0) == 0


def test_leader_falls_back_to_leader_index_when_nobody_grouped():
    assert gm._choose_leader(_states(None, None, None), leader_index=2) == 2


def test_leader_comes_from_the_largest_group_so_the_fewest_wizards_have_to_move():
    """A 3+1 split: keeping the group of three means one wizard leaves and rejoins, not three.

    leader_index points into the group of one, so this only comes out right if group size
    outranks the caller's preference.
    """
    trio = ("A", "B", "C")
    solo = ("D", "Stranger One")
    assert gm._choose_leader(_states(trio, trio, trio, solo), leader_index=3) == 0


def test_leader_index_still_wins_when_the_groups_are_the_same_size():
    """2+2: either choice moves two wizards, so the caller's preference is free to decide."""
    first = ("A", "B")
    second = ("C", "D")
    assert gm._choose_leader(_states(first, first, second, second), leader_index=2) == 2


def test_leader_falls_back_to_any_grouped_client_when_rosters_are_unreadable():
    """No roster means no group sizes to compare, so this degrades to the older rule."""
    assert gm._choose_leader(_states(None, (), ()), leader_index=0) == 1


# ---------------------------------------------------------------------------
# form_group
# ---------------------------------------------------------------------------


async def test_already_grouped_makes_no_invites(ui):
    a = ui.make_client("Alice Ashblade", grouped=True)
    b = ui.make_client("Bob Battlecaster", grouped=True)

    result = await _run_with(ui, lambda: gm.form_group([a, b], ["Alice Ashblade", "Bob Battlecaster"]))

    assert result.success is True
    assert result.added == []
    assert ui.invited_names == []


async def test_split_group_is_repaired_by_leaving_and_rejoining(ui):
    """Every client grouped, none together. This used to return success and do nothing.

    An invite alone cannot fix it — the game refuses one to a player already in a group —
    so the losing group is dissolved first and its members are then invited across.
    """
    a = ui.make_client("Alice Ashblade")
    b = ui.make_client("Bob Battlecaster")
    ui.put_in_foreign_group(a, "Stranger One")
    ui.put_in_foreign_group(b, "Stranger Two")

    result = await _run_with(ui, lambda: gm.form_group([a, b], ["Alice Ashblade", "Bob Battlecaster"]))

    assert result.success is True
    assert result.left == ["Bob Battlecaster"]
    assert result.added == ["Bob Battlecaster"]
    assert ui.grouped[a] and ui.grouped[b]
    assert gm.same_group([gm.GroupState(True, ui.group_of[a]), gm.GroupState(True, ui.group_of[b])]) is True


async def test_two_pairs_need_only_one_wizard_to_leave(ui):
    """Wizard101 dissolves a two-player group when either player leaves, so the partner is
    dropped out with it and never has to click anything itself."""
    a, b, c, d = (ui.make_client(n) for n in ("Alice A", "Bob B", "Carol C", "Dave D"))
    ui.put_in_group(a, b)
    ui.put_in_group(c, d)

    result = await _run_with(ui, lambda: gm.form_group([a, b, c, d], ["Alice A", "Bob B", "Carol C", "Dave D"]))

    assert result.success is True
    assert ui.leave_clicks == [c], f"only one wizard should have left, got {ui.leave_clicks}"
    assert sorted(result.left) == ["Carol C", "Dave D"], "both are freed by the single leave"
    assert sorted(result.added) == ["Carol C", "Dave D"]


async def test_the_larger_group_is_the_one_kept(ui):
    """3+1: the wizard on its own moves, not the three."""
    a, b, c = (ui.make_client(n) for n in ("Alice A", "Bob B", "Carol C"))
    stray = ui.make_client("Dave D")
    ui.put_in_group(a, b, c)
    ui.put_in_foreign_group(stray, "Stranger One")

    result = await _run_with(
        ui, lambda: gm.form_group([a, b, c, stray], ["Alice A", "Bob B", "Carol C", "Dave D"], leader_index=3)
    )

    assert result.success is True
    assert ui.leave_clicks == [stray]
    assert result.left == ["Dave D"]


async def test_a_client_that_cannot_leave_is_reported_once(ui):
    """A failed leave must not be counted as a failure twice — the invite it then fails is
    where the failure is recorded."""
    a = ui.make_client("Alice Ashblade")
    b = ui.make_client("Bob Battlecaster")
    ui.put_in_foreign_group(a, "Stranger One")
    ui.put_in_foreign_group(b, "Stranger Two")
    ui.leaving_works = False
    ui.already_in_other_group.add(b)

    result = await _run_with(ui, lambda: gm.form_group([a, b], ["Alice Ashblade", "Bob Battlecaster"]))

    assert result.success is False
    assert result.failed == ["Bob Battlecaster"]
    assert result.left == []


async def test_nothing_is_dissolved_when_the_leaders_roster_is_unreadable(ui):
    """Without the leader's roster there is no way to tell which group is being kept, and
    dissolving the wrong one is worse than leaving the split for the invite loop to fail on."""
    a = ui.make_client("Alice Ashblade", grouped=True)
    b = ui.make_client("Bob Battlecaster")
    ui.put_in_foreign_group(b, "Stranger Two")
    ui.group_of[a] = ()

    await _run_with(ui, lambda: gm.form_group([a, b], ["Alice Ashblade", "Bob Battlecaster"]))

    assert ui.leave_clicks == []


async def test_split_group_report_lists_each_group_once(ui):
    """Every member of a group reads the same roster, so one line per CLIENT showed four
    rosters for two groups and buried the shape of the problem."""
    a, b, c, d = (ui.make_client(n) for n in ("Alice A", "Bob B", "Carol C", "Dave D"))
    ui.put_in_group(a, b)
    ui.put_in_group(c, d)

    states = await _run_with(ui, lambda: gm.read_group_states([a, b, c, d]))

    assert gm.describe_groups(states).count("[") == 2


async def test_all_grouped_together_still_makes_no_invites(ui):
    """The other side of the split check: a genuine single group must stay a no-op."""
    a = ui.make_client("Alice Ashblade")
    b = ui.make_client("Bob Battlecaster")
    ui.put_in_group(a, b)

    result = await _run_with(ui, lambda: gm.form_group([a, b], ["Alice Ashblade", "Bob Battlecaster"]))

    assert result.success is True
    assert ui.invited_names == []


async def test_all_grouped_with_unreadable_rosters_stays_a_no_op(ui):
    """Degrade to the pre-roster behaviour rather than regrouping on every run."""
    a = ui.make_client("Alice Ashblade", grouped=True)
    b = ui.make_client("Bob Battlecaster", grouped=True)
    ui.group_of[a] = ()
    ui.group_of[b] = ()

    result = await _run_with(ui, lambda: gm.form_group([a, b], ["Alice Ashblade", "Bob Battlecaster"]))

    assert result.success is True
    assert ui.invited_names == []


async def test_client_grouped_with_strangers_is_invited_not_skipped(ui):
    """A grouped client in the WRONG group must not be skipped as already done.

    Skipping it is what left a split group in place. The invite it now gets is rejected by
    the game, but that turns a silent wrong result into a named failure.
    """
    leader = ui.make_client("Alice Ashblade")
    partner = ui.make_client("Bob Battlecaster")
    stray = ui.make_client("Carol Cloudrider")
    # Ungrouped, so the "everyone is grouped" short circuit does not fire and the invite
    # loop is actually reached.
    spare = ui.make_client("Dave Dawnwhisper")
    ui.put_in_group(leader, partner)
    ui.put_in_foreign_group(stray, "Stranger One")
    ui.already_in_other_group.add(stray)

    result = await _run_with(
        ui,
        lambda: gm.form_group(
            [leader, partner, stray, spare],
            ["Alice Ashblade", "Bob Battlecaster", "Carol Cloudrider", "Dave Dawnwhisper"],
        ),
    )

    assert "Carol Cloudrider" in ui.invited_names, "the client in the foreign group was skipped"
    assert result.success is False
    assert "Carol Cloudrider" in result.failed


async def test_join_into_a_group_without_the_leader_is_not_confirmed(ui):
    """grouped=True is not the same as grouped WITH US.

    A target that accepts somebody else's invitation between our click and our read looks
    joined to the Leave Group button. The roster is what catches it.
    """
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client("Bob Battlecaster")
    ui.joins_foreign_group_on_accept.add(joiner)

    result = await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice Ashblade", "Bob Battlecaster"]))

    assert result.success is False
    assert result.failed == ["Bob Battlecaster"]


async def test_single_client_is_a_no_op(ui):
    result = await _run_with(ui, lambda: gm.form_group([ui.make_client("Alice Ashblade")], ["Alice Ashblade"]))
    assert result.success is True
    assert "fewer than two clients" in result.reason


async def test_mismatched_names_length_fails_without_inviting(ui):
    clients = [ui.make_client("Alice Ashblade"), ui.make_client("Bob Battlecaster")]
    result = await _run_with(ui, lambda: gm.form_group(clients, ["OnlyOne"]))

    assert result.success is False
    assert ui.invited_names == []


async def test_ungrouped_client_is_invited_and_confirmed(ui):
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client("Bob Battlecaster")

    result = await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice Ashblade", "Bob Battlecaster"]))

    assert result.success is True
    assert result.added == ["Bob Battlecaster"]
    assert ui.invited_names == ["Bob Battlecaster"]
    assert ui.grouped[joiner] is True


async def test_client_with_unknown_name_is_reported_not_skipped(ui):
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client()

    result = await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice Ashblade", None]))

    assert result.success is False
    assert result.failed == ["client 1"]
    assert ui.invited_names == []


async def test_invite_never_accepted_fails(ui):
    """The invitation prompt never appears on the target."""
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client("Bob Battlecaster")
    ui.accepts_invites = False

    result = await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice Ashblade", "Bob Battlecaster"]))

    assert result.success is False
    assert result.failed == ["Bob Battlecaster"]


async def test_player_already_in_another_group_is_not_retried(ui):
    """Retrying cannot help — they must leave their other group first."""
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client("Bob Battlecaster")
    ui.already_in_other_group.add(joiner)

    result = await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice Ashblade", "Bob Battlecaster"]))

    assert result.success is False
    assert result.failed == ["Bob Battlecaster"]
    # One attempt only, despite GROUP_ADD_RETRIES being greater than 1.
    assert ui.invited_names == ["Bob Battlecaster"]


async def test_already_in_group_popup_is_dismissed(ui):
    """An undismissed popup would block the next invite."""
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client("Bob Battlecaster")
    ui.already_in_other_group.add(joiner)

    await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice Ashblade", "Bob Battlecaster"]))

    assert ui.popup_showing.get(leader) is False


async def test_friend_not_in_list_fails_without_retrying(ui):
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client("Bob Battlecaster")
    profile = AsyncMock(side_effect=ValueError("Could not find friend"))

    with ExitStack() as stack:
        for p in ui.patches():
            stack.enter_context(p)
        stack.enter_context(patch.object(nh, "open_friend_profile", new=profile))
        result = await gm.form_group([leader, joiner], ["Alice Ashblade", "Bob Battlecaster"])

    assert result.success is False
    assert result.failed == ["Bob Battlecaster"]
    assert profile.call_count == 1


async def test_group_window_is_closed_on_both_players(ui):
    """The game opens it on the inviter and the invitee, and neither closes itself."""
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client("Bob Battlecaster")

    await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice Ashblade", "Bob Battlecaster"]))

    assert leader in ui.closed_group_window_on
    assert joiner in ui.closed_group_window_on


async def test_friend_profile_is_closed_after_the_social_window(ui):
    """The social window opens on top of the profile and covers its close button, so
    closing the profile first leaves it open — the click lands on the social window."""
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client("Bob Battlecaster")

    await _run_with(ui, lambda: gm.form_group([leader, joiner], ["Alice Ashblade", "Bob Battlecaster"]))

    assert ui.profile_open.get(leader) is False, "the friend profile was left open on the leader"


async def test_profile_close_is_attempted_only_once_the_social_window_is_gone(ui):
    """Pins the ordering directly, independent of the fake's overlap modelling.

    Only clicks made after the invite are considered: the membership checks that run
    beforehand also close the social window, and counting those hides the ordering.
    """
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client("Bob Battlecaster")
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
        await gm.form_group([leader, joiner], ["Alice Ashblade", "Bob Battlecaster"])

    assert "social" in order and "profile" in order, f"expected both closes on the leader, got {order}"
    assert order.index("social") < order.index("profile"), f"profile closed before the social window: {order}"


async def test_friends_list_is_closed_once_for_the_whole_regroup(ui):
    """It is reused between invites, so closing per-invite would force a reopen each time."""
    leader = ui.make_client("Alice Ashblade")
    b = ui.make_client("Bob Battlecaster")
    c = ui.make_client("Carol Cloudrider")
    close = AsyncMock(return_value=True)

    with ExitStack() as stack:
        for p in ui.patches():
            stack.enter_context(p)
        stack.enter_context(patch.object(nh, "close_friends_list", new=close))
        result = await gm.form_group([leader, b, c], ["Alice Ashblade", "Bob Battlecaster", "Carol Cloudrider"])

    assert result.added == ["Bob Battlecaster", "Carol Cloudrider"]
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

    a = ui.make_client("Alice Ashblade")
    b = ui.make_client("Bob Battlecaster")
    c = ui.make_client("Carol Cloudrider")
    d = ui.make_client("Dave Dawnwhisper")
    clients = [a, b, c, d]
    names = ["Alice Ashblade", "Bob Battlecaster", "Carol Cloudrider", "Dave Dawnwhisper"]

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
    assert sorted(ui.invited_names) == ["Bob Battlecaster", "Carol Cloudrider", "Dave Dawnwhisper"]
    assert all(ui.grouped[client] for client in clients)


async def test_second_concurrent_call_finds_the_group_already_built(ui):
    """The loser of the race must no-op, not re-invite everyone."""
    import asyncio

    a = ui.make_client("Alice Ashblade")
    b = ui.make_client("Bob Battlecaster")

    with ExitStack() as stack:
        for p in ui.patches():
            stack.enter_context(p)
        first, second = await asyncio.gather(
            gm.form_group([a, b], ["Alice Ashblade", "Bob Battlecaster"]),
            gm.form_group([a, b], ["Alice Ashblade", "Bob Battlecaster"]),
        )

    assert first.success and second.success
    # One of the two did the work; the other found everyone already grouped.
    assert sorted([len(first.added), len(second.added)]) == [0, 1]
    assert ui.invited_names == ["Bob Battlecaster"]


async def test_friends_list_close_is_retried_until_the_list_is_gone(ui):
    """The reported failure: the first close click is swallowed, the next click lands on
    the social button behind the list, and the list is left open."""
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client("Bob Battlecaster")
    ui.swallow_closes = 1

    with ExitStack() as stack:
        for p in ui.patches(real_friends_list_close=True):
            stack.enter_context(p)
        result = await gm.form_group([leader, joiner], ["Alice Ashblade", "Bob Battlecaster"])

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
    client = ui.make_client("Alice Ashblade")
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
    client = ui.make_client("Alice Ashblade")
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
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client("Bob Battlecaster")
    ui.swallow_closes = 1

    with ExitStack() as stack:
        for p in ui.patches(real_friends_list_close=True):
            stack.enter_context(p)
        await gm.form_group([leader, joiner], ["Alice Ashblade", "Bob Battlecaster"])

    assert ui.profile_open.get(leader) is False, "the friend profile was left open"
    assert ui.social_open.get(leader) is False, "the leader's social window was left open"
    assert ui.social_open.get(joiner) is False, "the joiner's social window was left open"


# ---------------------------------------------------------------------------
# Polling rather than waiting (#114)
#
# These are wall-clock assertions, which is unusual — but the thing under test IS elapsed
# time. Each one leaves a production-sized delay in place and asserts the happy path does
# not pay it, so a fixed sleep reintroduced anywhere in the invite exchange fails here
# rather than quietly costing a second per client on every regroup.
# ---------------------------------------------------------------------------


def _patches_except(ui_obj, *attributes: str, extra=()):
    """FakeUI's patches with some constants' overrides dropped, plus replacements.

    The fixture zeroes the timings so tests run fast, which also makes a sleep and a poll
    look identical. Restoring one of them to a visible value is what lets these tests tell
    the two apart.
    """
    kept = tuple(p for p in ui_obj.patches() if getattr(p, "attribute", None) not in attributes)
    return kept + tuple(extra)


async def _timed(coro_factory, patches):
    """Run coro_factory under `patches`, returning (result, seconds elapsed)."""
    loop = asyncio.get_event_loop()
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        start = loop.time()
        result = await coro_factory()
        return result, loop.time() - start


# The one settle a regroup still cannot poll away: nothing on the Group tab distinguishes
# it from the other tabs, so the opening membership survey has to wait a fixed time before
# reading. It is concurrent across clients, so a pair costs one of these, not two.
#
# Only the ratio to _BUDGET below matters, not the absolute value — the tests ask whether
# the exchange paid one settle or three, and both sides scale together. Kept well above the
# few milliseconds these fakes actually spend working, so scheduling noise cannot pass for
# a settle, and low enough that the two tests do not dominate the file's runtime.
_SETTLE = 0.5

# Everything an invite exchange used to add on top of that — a sleep for the friend profile
# to draw, a sleep to see whether the refusal popup appeared, and a second full settle to
# confirm the join. Three settles' worth; the budget below allows for well under one.
_BUDGET = _SETTLE * 2


async def test_a_successful_invite_pays_only_the_settle_that_cannot_be_polled_away(ui):
    """Every wait in the invite exchange itself has something specific to watch for.

    The profile popup, the invitation prompt and the joined-a-group confirmation are all
    observable, so all three are polled and a fast game ends each wait immediately. Only the
    opening survey is blind, because there both answers are plausible and neither has a
    signal — see the GROUP_UI_SETTLE note in timing.py.
    """
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client("Bob Battlecaster")

    result, elapsed = await _timed(
        lambda: gm.form_group([leader, joiner], ["Alice Ashblade", "Bob Battlecaster"]),
        _patches_except(ui, "GROUP_UI_SETTLE", extra=(patch.object(gm, "GROUP_UI_SETTLE", _SETTLE),)),
    )

    assert result.success is True
    assert result.added == ["Bob Battlecaster"]
    assert elapsed < _BUDGET, f"the invite exchange slept through a fixed delay ({elapsed:.1f}s)"


async def test_a_refusal_is_noticed_as_soon_as_the_popup_is_drawn(ui):
    """The refusal is raced against the invitation prompt rather than checked after a sleep.

    Absence of the popup is the expected outcome, so on its own there is nothing to poll
    for — which is why this used to be a flat sleep on every invite, successful or not.
    Polling both windows together keeps the check while charging nothing for it, and this
    pins that racing them did not lose the refusal: it is the one branch that must not
    retry, since the player has to leave their other group first.
    """
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client("Bob Battlecaster")
    ui.already_in_other_group.add(joiner)

    result, elapsed = await _timed(
        lambda: gm.form_group([leader, joiner], ["Alice Ashblade", "Bob Battlecaster"]),
        _patches_except(
            ui,
            "GROUP_UI_SETTLE",
            "GROUP_INVITE_TIMEOUT",
            extra=(
                patch.object(gm, "GROUP_UI_SETTLE", _SETTLE),
                # Generous, so a refusal that is only noticed when the prompt wait gives up
                # shows as a timeout rather than as a slightly slow pass.
                patch.object(gm, "GROUP_INVITE_TIMEOUT", 30.0),
            ),
        ),
    )

    assert result.success is False
    assert result.failed == ["Bob Battlecaster"]
    assert ui.invited_names == ["Bob Battlecaster"], "a refusal must not be retried"
    assert ui.popup_showing.get(leader) is False, "the refusal popup was left blocking the next invite"
    assert elapsed < _BUDGET, f"the refusal was not acted on until a wait expired ({elapsed:.1f}s)"


async def test_invite_cleanup_runs_on_both_clients_at_once(ui):
    """Ordering matters within a client, not between them.

    The leader's social-window-then-profile order is load-bearing — the social window covers
    the profile's close button — but the target shares nothing with it, so making the target
    wait for the leader is pure latency, once per invite.
    """
    leader = ui.make_client("Alice Ashblade")
    joiner = ui.make_client("Bob Battlecaster")

    async def _slow(_client):
        await asyncio.sleep(0.3)

    loop = asyncio.get_event_loop()
    with ExitStack() as stack:
        stack.enter_context(patch.object(gm, "close_social_window", new=AsyncMock(side_effect=_slow)))
        stack.enter_context(patch.object(gm, "_close_overlapping_windows", new=AsyncMock(side_effect=_slow)))
        start = loop.time()
        await gm._close_invite_windows(leader, joiner)
        elapsed = loop.time() - start

    assert elapsed < 0.5, f"the two clients were cleaned up one after the other ({elapsed:.2f}s)"


async def test_already_grouped_client_is_not_reinvited(ui):
    """Only the client missing from the group is invited."""
    leader = ui.make_client("Alice Ashblade", grouped=True)
    already = ui.make_client("Bob Battlecaster", grouped=True)
    missing = ui.make_client("Carol Cloudrider", grouped=False)

    result = await _run_with(
        ui,
        lambda: gm.form_group([leader, already, missing], ["Alice Ashblade", "Bob Battlecaster", "Carol Cloudrider"]),
    )

    assert result.success is True
    assert result.added == ["Carol Cloudrider"]
    assert ui.invited_names == ["Carol Cloudrider"]
