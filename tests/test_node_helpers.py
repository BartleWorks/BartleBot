"""Tests for node_helpers zone matching utilities."""

import asyncio
import itertools
from unittest.mock import AsyncMock, patch

import pytest
from wizwalker import XYZ, MemoryReadError

import bartlebot.nodes.node_helpers as nh
from bartlebot.nodes.node_helpers import (
    _get_window_from_path,
    all_in_zone,
    all_left_zone,
    entity_name_matches,
    entity_name_predicate,
    find_closest_entity_by_name,
    find_entities_by_name,
    find_windows_by_path,
    resolve_path_match,
    strip_html,
    window_text_matches,
    zone_matches,
)
from bartlebot.nodes.paths import MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT
from tests.conftest import FakeClient, FakeEntity, FakeMachine, message_box_patches

#: Somewhere and somewhere else — reach_sigil_prompt only ever passes these through.
_SIGIL = XYZ(0.0, 0.0, 0.0)
_RESET = XYZ(1.0, 0.0, 0.0)

# ---------------------------------------------------------------------------
# zone_matches — pure function, no async
# ---------------------------------------------------------------------------


def test_zone_matches_exact_hit():
    assert zone_matches("WizardCity/WC_Hub", "WizardCity/WC_Hub") is True


def test_zone_matches_exact_miss():
    assert zone_matches("WizardCity/WC_Ravenwood", "WizardCity/WC_Hub") is False


def test_zone_matches_star_wildcard():
    assert zone_matches("WizardCity/WC_Hub", "WizardCity/*") is True


def test_zone_matches_star_wildcard_different_world():
    assert zone_matches("Krokotopia/KT_Hub", "WizardCity/*") is False


def test_zone_matches_star_any_world():
    assert zone_matches("Krokotopia/KT_Hub", "*/KT_Hub") is True


def test_zone_matches_question_mark():
    assert zone_matches("WizardCity/WC_Hub", "WizardCity/WC_Hu?") is True
    assert zone_matches("WizardCity/WC_Hub", "WizardCity/WC_Hu??") is False


def test_zone_matches_star_all_zones():
    assert zone_matches("WizardCity/WC_Hub", "*") is True
    assert zone_matches("Krokotopia/KT_Market", "*") is True


def test_zone_matches_case_sensitive():
    assert zone_matches("wizardcity/wc_hub", "WizardCity/*") is False


def test_zone_matches_none_actual_returns_false():
    # zone_name() returns None during loading screens; must not raise
    assert zone_matches(None, "WizardCity/WC_Hub") is False
    assert zone_matches(None, "WizardCity/*") is False


def test_zone_matches_malformed_pattern_is_literal_not_an_error():
    # fnmatch treats an unbalanced bracket as a literal, so this must not raise
    # and matches only the zone of that exact name.
    assert zone_matches("WizardCity/WC_Hub", "[unclosed") is False
    assert zone_matches("[unclosed", "[unclosed") is True


# ---------------------------------------------------------------------------
# entity_name_matches — pure function, no async
# ---------------------------------------------------------------------------


def test_entity_name_plain_is_substring():
    # The long-standing behaviour every existing bot file depends on.
    assert entity_name_matches("Cyclops/CyclopsMinion", "Cyclops") is True
    assert entity_name_matches("Cyclops/CyclopsMinion", "Minion") is True


def test_entity_name_plain_is_case_insensitive():
    assert entity_name_matches("Cyclops/CyclopsMinion", "cyclops") is True


def test_entity_name_plain_miss():
    assert entity_name_matches("Cyclops/CyclopsMinion", "Kraken") is False


def test_entity_name_wildcard_anchors_the_whole_name():
    # The bug behind the enemy-wildcard fix: a wildcard used to be a literal character that matched
    # nothing. It now globs, and anchored, so a leading "*" is load-bearing.
    assert entity_name_matches("Bandit/BanditThug", "*Bandit*") is True
    assert entity_name_matches("Bandit/BanditThug", "Bandit*") is True
    assert entity_name_matches("Krokotopia/BanditThug", "Bandit*") is False


def test_entity_name_wildcard_is_case_insensitive():
    assert entity_name_matches("Bandit/BanditThug", "*bandit*") is True


def test_entity_name_question_mark():
    assert entity_name_matches("Rank3", "Rank?") is True
    assert entity_name_matches("Rank30", "Rank?") is False


def test_entity_name_none_actual_returns_false():
    assert entity_name_matches(None, "Cyclops") is False
    assert entity_name_matches(None, "*Cyclops*") is False


def test_entity_name_malformed_pattern_is_literal_not_an_error():
    # fnmatch treats an unbalanced bracket as a literal, so this must not raise
    # and must behave like any other anchored pattern.
    assert entity_name_matches("[unclosed", "[unclosed") is True
    assert entity_name_matches("a [unclosed name", "[unclosed") is False
    assert entity_name_matches("a [unclosed name", "*[unclosed*") is True


# ---------------------------------------------------------------------------
# find_closest_entity_by_name — async
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_closest_plain_name_uses_vague_name():
    """A plain name must keep taking WizSprinter's own path, untouched."""
    client = FakeClient()
    client.find_closest_by_vague_name.return_value = FakeEntity("Cyclops/CyclopsMinion")

    found = await find_closest_entity_by_name(client, "Cyclops")

    assert found is not None
    client.find_closest_by_vague_name.assert_awaited_once_with("Cyclops")
    client.find_closest_by_predicate.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_closest_wildcard_uses_predicate():
    client = FakeClient()
    client.find_closest_by_predicate.return_value = FakeEntity("Bandit/BanditThug")

    found = await find_closest_entity_by_name(client, "*Bandit*")

    assert found is not None
    client.find_closest_by_vague_name.assert_not_awaited()
    client.find_closest_by_predicate.assert_awaited_once()


@pytest.mark.asyncio
async def test_find_entities_plain_name_uses_vague_name():
    client = FakeClient()
    client.get_base_entities_with_vague_name.return_value = [FakeEntity("FungusWort")]

    found = await find_entities_by_name(client, "Fungi")

    assert len(found) == 1
    client.get_base_entities_with_vague_name.assert_awaited_once_with("Fungi")
    client.get_base_entities_with_predicate.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_entities_wildcard_uses_predicate():
    client = FakeClient()
    client.get_base_entities_with_predicate.return_value = [FakeEntity("FungusWort")]

    found = await find_entities_by_name(client, "*Fungi*")

    assert len(found) == 1
    client.get_base_entities_with_vague_name.assert_not_awaited()
    client.get_base_entities_with_predicate.assert_awaited_once()


# ---------------------------------------------------------------------------
# entity_name_predicate — the matcher both search helpers hand to WizSprinter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_predicate_accepts_and_rejects_by_name():
    predicate = entity_name_predicate("Bandit*")

    assert await predicate(FakeEntity("BanditThug")) is True
    assert await predicate(FakeEntity("Krokotopia/BanditThug")) is False


@pytest.mark.asyncio
async def test_predicate_skips_entity_that_despawned():
    """A dead entity mid-scan is skipped, not allowed to abort the whole search."""
    predicate = entity_name_predicate("Bandit*")

    gone = FakeEntity("BanditThug")
    gone.object_template = AsyncMock(side_effect=MemoryReadError("read failed"))
    assert await predicate(gone) is False


@pytest.mark.asyncio
async def test_predicate_skips_entity_without_a_template():
    predicate = entity_name_predicate("Bandit*")

    templateless = FakeEntity("BanditThug")
    templateless.object_template = AsyncMock(return_value=None)
    assert await predicate(templateless) is False


# ---------------------------------------------------------------------------
# all_in_zone — async, uses FakeClient-style mocks
# ---------------------------------------------------------------------------


def _make_client(zone: str):
    c = AsyncMock()
    c.zone_name = AsyncMock(return_value=zone)
    return c


@pytest.mark.asyncio
async def test_all_in_zone_exact_all_match():
    clients = [_make_client("WizardCity/WC_Hub"), _make_client("WizardCity/WC_Hub")]
    assert await all_in_zone(clients, "WizardCity/WC_Hub") is True


@pytest.mark.asyncio
async def test_all_in_zone_exact_partial_match():
    clients = [_make_client("WizardCity/WC_Hub"), _make_client("WizardCity/WC_Ravenwood")]
    assert await all_in_zone(clients, "WizardCity/WC_Hub") is False


@pytest.mark.asyncio
async def test_all_in_zone_wildcard_all_match():
    clients = [_make_client("WizardCity/WC_Hub"), _make_client("WizardCity/WC_Ravenwood")]
    assert await all_in_zone(clients, "WizardCity/*") is True


@pytest.mark.asyncio
async def test_all_in_zone_wildcard_partial_match():
    clients = [_make_client("WizardCity/WC_Hub"), _make_client("Krokotopia/KT_Hub")]
    assert await all_in_zone(clients, "WizardCity/*") is False


# ---------------------------------------------------------------------------
# all_left_zone — async, uses FakeClient-style mocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_left_zone_exact_all_left():
    clients = [_make_client("WizardCity/WC_Ravenwood"), _make_client("Krokotopia/KT_Hub")]
    assert await all_left_zone(clients, "WizardCity/WC_Hub") is True


@pytest.mark.asyncio
async def test_all_left_zone_exact_still_present():
    clients = [_make_client("WizardCity/WC_Hub"), _make_client("WizardCity/WC_Ravenwood")]
    assert await all_left_zone(clients, "WizardCity/WC_Hub") is False


@pytest.mark.asyncio
async def test_all_left_zone_wildcard_all_left():
    clients = [_make_client("Krokotopia/KT_Hub"), _make_client("Krokotopia/KT_Market")]
    assert await all_left_zone(clients, "WizardCity/*") is True


@pytest.mark.asyncio
async def test_all_left_zone_wildcard_one_still_matches():
    # One client is still in a WizardCity zone — should NOT be considered "left"
    clients = [_make_client("WizardCity/WC_Ravenwood"), _make_client("Krokotopia/KT_Hub")]
    assert await all_left_zone(clients, "WizardCity/*") is False


@pytest.mark.asyncio
async def test_all_left_zone_wildcard_same_prefix_as_destination():
    # Documents the known hazard: if from_zone wildcard matches the destination
    # zone too, all_left_zone stays False even after the zone transition.
    clients = [_make_client("WizardCity/WC_GreatSpyre_DF3"), _make_client("WizardCity/WC_GreatSpyre_DF3")]
    assert await all_left_zone(clients, "WizardCity/*") is False


# ---------------------------------------------------------------------------
# _get_window_from_path — traversal across ambiguously named siblings
#
# Wizard101 reuses window names between siblings: wndCharacter has two children both
# named "ButtonLayout", only one of which holds the group invite button. These tests
# pin the backtracking that makes INVITE_TO_GROUP_BUTTON_PATH resolve correctly.
# ---------------------------------------------------------------------------


def _make_window(name: str, children: list | None = None, text: str = ""):
    from unittest.mock import MagicMock

    w = MagicMock()
    w.name = AsyncMock(return_value=name)
    w.children = AsyncMock(return_value=list(children or []))
    w.maybe_text = AsyncMock(return_value=text)
    return w


async def test_get_window_backtracks_past_a_dead_end_sibling():
    """The first same-named sibling does not lead anywhere; the second does."""
    target = _make_window("btnInvite")
    root = _make_window(
        "root",
        [
            _make_window(
                "wndCharacter",
                [
                    _make_window("ButtonLayout", [_make_window("btnSomethingElse")]),
                    _make_window("ButtonLayout", [target]),
                ],
            )
        ],
    )

    found = await _get_window_from_path(root, ["wndCharacter", "ButtonLayout", "btnInvite"])
    assert found is target


async def test_get_window_returns_none_when_no_sibling_completes_the_path():
    root = _make_window(
        "root",
        [
            _make_window(
                "wndCharacter",
                [
                    _make_window("ButtonLayout", [_make_window("btnOther")]),
                    _make_window("ButtonLayout", [_make_window("btnAnother")]),
                ],
            )
        ],
    )

    assert await _get_window_from_path(root, ["wndCharacter", "ButtonLayout", "btnInvite"]) is None


# ---------------------------------------------------------------------------
# find_windows_by_path / resolve_path_match — paths that match several elements
#
# A list menu whose rows are structurally identical is ambiguous by path: every row
# completes it. The first match wins silently unless the caller narrows it by the text
# the row displays or by its position.
# ---------------------------------------------------------------------------


def _row_menu():
    """A menu of three identically-named rows, told apart only by their label text."""
    rows = [
        _make_window("Row", [_make_window("Label", text="<center>Krokotopia")]),
        _make_window("Row", [_make_window("Label", text="<center>Marleybone")]),
        _make_window("Row", [_make_window("Label", text="<center>Mooshu")]),
    ]
    return _make_window("root", [_make_window("wndMenu", rows)]), rows


def _client_with(root):
    from unittest.mock import MagicMock

    client = MagicMock()
    client.root_window = root
    return client


async def test_find_windows_returns_every_match_in_tree_order():
    root, rows = _row_menu()
    found = await find_windows_by_path(root, ["wndMenu", "Row"])
    assert found == rows


async def test_find_windows_returns_empty_list_when_nothing_matches():
    root, _ = _row_menu()
    assert await find_windows_by_path(root, ["wndMenu", "Missing"]) == []


async def test_get_window_still_takes_the_first_of_several_matches():
    """The single-result walker is unchanged by the multi-match resolver."""
    root, rows = _row_menu()
    assert await _get_window_from_path(root, ["wndMenu", "Row"]) is rows[0]


async def test_window_text_matches_reads_a_descendants_label():
    _, rows = _row_menu()
    assert await window_text_matches(rows[1], "Marleybone")
    assert not await window_text_matches(rows[1], "Krokotopia")


async def test_window_text_matches_is_case_insensitive_and_strips_html():
    """The game wraps labels in HTML; a user typing the visible text must still match."""
    _, rows = _row_menu()
    assert await window_text_matches(rows[0], "krokoTOPIA")
    assert await window_text_matches(rows[0], "Krokotopia", exact=True)


async def test_window_text_matches_shallow_ignores_children():
    _, rows = _row_menu()
    assert not await window_text_matches(rows[0], "Krokotopia", deep=False)


async def test_window_text_exact_rejects_a_substring():
    _, rows = _row_menu()
    assert await window_text_matches(rows[2], "Moo")
    assert not await window_text_matches(rows[2], "Moo", exact=True)


async def test_resolve_path_match_defaults_to_the_first_match_with_a_count():
    root, rows = _row_menu()
    window, count = await resolve_path_match(_client_with(root), ["wndMenu", "Row"])
    assert window is rows[0]
    assert count == 3


async def test_resolve_path_match_selects_by_text():
    root, rows = _row_menu()
    window, count = await resolve_path_match(_client_with(root), ["wndMenu", "Row"], match_text="Marleybone")
    assert window is rows[1]
    assert count == 3


async def test_resolve_path_match_selects_by_index():
    root, rows = _row_menu()
    window, _ = await resolve_path_match(_client_with(root), ["wndMenu", "Row"], match_index=2)
    assert window is rows[2]


async def test_resolve_path_match_index_counts_from_the_end():
    root, rows = _row_menu()
    window, _ = await resolve_path_match(_client_with(root), ["wndMenu", "Row"], match_index=-1)
    assert window is rows[2]


async def test_resolve_path_match_indexes_into_the_text_filtered_matches():
    """Match Index applies after Match Text, not to the raw match list."""
    root, rows = _row_menu()
    window, _ = await resolve_path_match(_client_with(root), ["wndMenu", "Row"], match_text="Moo", match_index=0)
    assert window is rows[2]


async def test_resolve_path_match_reports_the_count_when_text_rejects_everything():
    """The count is what the PATH matched, so the caller can say 'none of the 3 rows'."""
    root, _ = _row_menu()
    window, count = await resolve_path_match(_client_with(root), ["wndMenu", "Row"], match_text="Dragonspyre")
    assert window is None
    assert count == 3


async def test_resolve_path_match_returns_none_when_index_is_out_of_range():
    root, _ = _row_menu()
    window, count = await resolve_path_match(_client_with(root), ["wndMenu", "Row"], match_index=7)
    assert window is None
    assert count == 3


async def test_resolve_path_match_returns_zero_count_when_path_matches_nothing():
    root, _ = _row_menu()
    window, count = await resolve_path_match(_client_with(root), ["wndMenu", "Missing"])
    assert window is None
    assert count == 0


def test_strip_html_removes_the_games_wrapping():
    assert strip_html("<center>Unicorn") == "Unicorn"
    assert strip_html("  plain  ") == "plain"
    assert strip_html("") == ""


# ---------------------------------------------------------------------------
# click_until_gone — close buttons need retrying
#
# A single close click is unreliable: an overlapping window can swallow it, and clicks
# sent mid-animation get dropped. Retrying until the button itself disappears turns
# "we clicked it" into "it actually closed".
# ---------------------------------------------------------------------------


class _Closeable:
    """A window that needs `clicks_needed` clicks before it disappears."""

    def __init__(self, clicks_needed: int):
        self.clicks_needed = clicks_needed
        self.clicks = 0

    @property
    def visible(self) -> bool:
        return self.clicks < self.clicks_needed


def _closeable_patches(target: _Closeable):
    from unittest.mock import patch

    import bartlebot.nodes.node_helpers as nh_mod

    async def is_visible(_client, _path):
        return target.visible

    async def click(_client, _path):
        target.clicks += 1
        return True

    return (
        patch.object(nh_mod, "is_visible_by_path", new=AsyncMock(side_effect=is_visible)),
        patch.object(nh_mod, "click_by_path", new=AsyncMock(side_effect=click)),
    )


async def test_click_until_gone_retries_until_the_window_closes():
    from contextlib import ExitStack

    import bartlebot.nodes.node_helpers as nh_mod

    window = _Closeable(clicks_needed=3)
    with ExitStack() as stack:
        for p in _closeable_patches(window):
            stack.enter_context(p)
        result = await nh_mod.click_until_gone(None, ["Some", "Close"], timeout=5, poll_interval=0)

    assert result is True
    assert window.clicks == 3


async def test_click_until_gone_does_not_click_an_already_closed_window():
    """These paths are often toggles — a stray click would reopen what we just closed."""
    from contextlib import ExitStack

    import bartlebot.nodes.node_helpers as nh_mod

    window = _Closeable(clicks_needed=0)
    with ExitStack() as stack:
        for p in _closeable_patches(window):
            stack.enter_context(p)
        result = await nh_mod.click_until_gone(None, ["Some", "Close"], timeout=5, poll_interval=0)

    assert result is True
    assert window.clicks == 0


async def test_click_until_gone_returns_false_when_it_never_closes():
    from contextlib import ExitStack

    import bartlebot.nodes.node_helpers as nh_mod

    window = _Closeable(clicks_needed=999)
    with ExitStack() as stack:
        for p in _closeable_patches(window):
            stack.enter_context(p)
        result = await nh_mod.click_until_gone(None, ["Some", "Close"], timeout=0.05, poll_interval=0.01)

    assert result is False
    assert window.clicks > 0


async def test_click_until_gone_returns_as_soon_as_the_window_goes():
    """poll_interval bounds the next CLICK, not the next look.

    A close that took effect immediately — the common case — used to be invisible until the
    full retry interval had elapsed, which put a floor under every window close in the app.
    Group formation closes several per invite, so that was among the largest fixed costs in
    a regroup.
    """
    from contextlib import ExitStack

    import bartlebot.nodes.node_helpers as nh_mod

    window = _Closeable(clicks_needed=1)
    loop = asyncio.get_event_loop()
    with ExitStack() as stack:
        for p in _closeable_patches(window):
            stack.enter_context(p)
        start = loop.time()
        result = await nh_mod.click_until_gone(None, ["Some", "Close"], timeout=5, poll_interval=2.0)
        elapsed = loop.time() - start

    assert result is True
    assert window.clicks == 1
    assert elapsed < 1.0, f"waited out the retry interval after the window had already closed ({elapsed:.2f}s)"


async def test_press_key_until_gone_returns_as_soon_as_the_panel_goes():
    """The keyboard counterpart of the same rule — see click_until_gone above."""
    from wizwalker import Keycode

    import bartlebot.nodes.node_helpers as nh_mod

    panel = _Closeable(clicks_needed=1)

    async def send_key(_key, _seconds=0):
        panel.clicks += 1

    client = FakeClient()
    client.send_key = AsyncMock(side_effect=send_key)

    async def is_open():
        return panel.visible

    loop = asyncio.get_event_loop()
    start = loop.time()
    result = await nh_mod.press_key_until_gone(client, Keycode.F, is_open=is_open, timeout=5, poll_interval=2.0)
    elapsed = loop.time() - start

    assert result is True
    assert panel.clicks == 1
    assert elapsed < 1.0, f"waited out the retry interval after the panel had already closed ({elapsed:.2f}s)"


async def test_click_until_gone_uses_the_supplied_predicate_not_the_button():
    """A close button reports visible=True even inside a hidden window, so polling the
    button never terminates. The caller must be able to poll the window instead."""
    from contextlib import ExitStack
    from unittest.mock import patch

    import bartlebot.nodes.node_helpers as nh_mod

    window_open = {"value": True}
    clicks = {"count": 0}

    async def button_always_visible(_client, _path):
        return True  # the trap: the button never disappears

    async def click(_client, _path):
        clicks["count"] += 1
        window_open["value"] = False
        return True

    async def window_is_open():
        return window_open["value"]

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(nh_mod, "is_visible_by_path", new=AsyncMock(side_effect=button_always_visible))
        )
        stack.enter_context(patch.object(nh_mod, "click_by_path", new=AsyncMock(side_effect=click)))
        result = await nh_mod.click_until_gone(
            None, ["Some", "Close"], is_open=window_is_open, timeout=5, poll_interval=0
        )

    assert result is True
    assert clicks["count"] == 1, "should stop as soon as the WINDOW closed"


async def test_click_until_gone_without_a_predicate_polls_the_button_and_times_out():
    """Documents why the predicate exists: the default is wrong for permanently-visible
    close buttons, which is the situation in the friends list."""
    from contextlib import ExitStack
    from unittest.mock import patch

    import bartlebot.nodes.node_helpers as nh_mod

    async def button_always_visible(_client, _path):
        return True

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(nh_mod, "is_visible_by_path", new=AsyncMock(side_effect=button_always_visible))
        )
        stack.enter_context(patch.object(nh_mod, "click_by_path", new=AsyncMock(return_value=True)))
        result = await nh_mod.click_until_gone(None, ["Some", "Close"], timeout=0.05, poll_interval=0.01)

    assert result is False


# ---------------------------------------------------------------------------
# open_friends_list and the two friends-list windows
# ---------------------------------------------------------------------------


async def _open_friends_list_with(visible_names: set[str]) -> list[str]:
    """Run open_friends_list against a fake tree and return the actions it took."""
    from contextlib import ExitStack
    from unittest.mock import MagicMock, patch

    import bartlebot.nodes.node_helpers as nh_mod

    actions: list[str] = []
    client = MagicMock()
    client.root_window = MagicMock()

    compact = MagicMock()
    compact.is_visible = AsyncMock(return_value="NewFriendsListWindow" in visible_names)

    async def named_visible(_client, name):
        return name in visible_names

    async def closer(_client):
        actions.append("close")
        visible_names.clear()
        compact.is_visible = AsyncMock(return_value=False)
        return True

    async def maybe_get(_root, name):
        return compact if name == "NewFriendsListWindow" else MagicMock()

    async def click_window(_btn):
        actions.append("click btnFriends")

    client.mouse_handler.click_window = AsyncMock(side_effect=click_window)

    with ExitStack() as stack:
        stack.enter_context(patch.object(nh_mod, "is_named_window_visible", new=AsyncMock(side_effect=named_visible)))
        stack.enter_context(patch.object(nh_mod, "close_friends_list", new=AsyncMock(side_effect=closer)))
        stack.enter_context(
            patch("wizwalker.extensions.scripting.utils._maybe_get_named_window", new=AsyncMock(side_effect=maybe_get))
        )
        await nh_mod.open_friends_list(client)

    return actions


async def test_open_friends_list_closes_the_large_list_first():
    """Visibility is judged from the compact window alone, so from the LARGE list
    this read "closed" and clicked btnFriends anyway — and what the game does with that
    click was never established. Close first, so the state is known."""
    actions = await _open_friends_list_with({"wndFriendsList"})
    assert actions == ["close", "click btnFriends"]


async def test_open_friends_list_does_not_touch_a_list_that_is_already_compact():
    """The normal path must stay one read and no input: the list is reused between invites."""
    actions = await _open_friends_list_with({"NewFriendsListWindow"})
    assert actions == []


async def test_open_friends_list_clicks_when_nothing_is_open():
    actions = await _open_friends_list_with(set())
    assert actions == ["click btnFriends"]


# ---------------------------------------------------------------------------
# identify_modal / modal_showing — caption first, buttons as fallback
# ---------------------------------------------------------------------------


def _caption_patch(text: str):
    """Patch the caption read so the client appears to be showing `text`."""
    from unittest.mock import patch

    import bartlebot.nodes.node_helpers as nh_mod

    return patch.object(nh_mod, "get_text_by_path", new=AsyncMock(return_value=text))


_EXIT_CAPTION = (
    "If you leave a Dungeon you will lose all your progress in the Dungeon and will "
    "have to start over at the beginning.<br>Are you sure you want to leave now?"
)


async def test_identify_modal_names_the_dialog_on_screen():
    import bartlebot.nodes.node_helpers as nh_mod
    from bartlebot import dialog_captions

    with _caption_patch(_EXIT_CAPTION):
        assert await nh_mod.identify_modal(FakeClient()) == dialog_captions.INSTANCE_EXIT


async def test_identify_modal_returns_none_for_an_unknown_caption():
    import bartlebot.nodes.node_helpers as nh_mod

    with _caption_patch("A dialog nobody has catalogued"):
        assert await nh_mod.identify_modal(FakeClient()) is None


async def test_modal_showing_is_true_for_an_unrecognised_dialog():
    """identify_modal() conflates 'nothing up' with 'not in the snapshot'; this separates them."""
    import bartlebot.nodes.node_helpers as nh_mod

    with _caption_patch("A dialog nobody has catalogued"):
        assert await nh_mod.modal_showing(FakeClient()) is True
    with _caption_patch(""):
        assert await nh_mod.modal_showing(FakeClient()) is False


# ---------------------------------------------------------------------------
# resolve_modal — confirm questions, dismiss refusals, report the refusals
# ---------------------------------------------------------------------------


async def _resolve_with(caption: str, buttons):
    """Run resolve_modal against a stated dialog. Returns (outcome, clicked paths)."""

    import bartlebot.nodes.node_helpers as nh_mod
    from tests.conftest import message_box_patches as mbp

    patches, clicked = mbp(buttons)
    with patches[0], patches[1], _caption_patch(caption):
        outcome = await nh_mod.resolve_modal(FakeClient())
    return outcome, clicked


async def test_resolve_modal_confirms_a_question():
    outcome, clicked = await _resolve_with(_EXIT_CAPTION, (MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))

    assert outcome.answered is True
    assert outcome.refusal is None
    assert clicked == [MESSAGE_BOX_CENTER]


async def test_resolve_modal_dismisses_a_refusal_and_names_it():
    """The node needs the key so it can fail with the reason a player would have read."""
    from bartlebot import dialog_captions

    outcome, clicked = await _resolve_with("Your friend is busy right now.", (MESSAGE_BOX_RIGHT,))

    assert outcome.refusal == dialog_captions.FRIEND_BUSY
    assert clicked == [MESSAGE_BOX_RIGHT]
    assert "not accepting teleports" in dialog_captions.describe(outcome.refusal)


async def test_resolve_modal_dismisses_a_one_button_refusal_with_the_centre():
    """A refusal with only an OK button puts it in the centre, not the right."""
    from bartlebot import dialog_captions

    outcome, clicked = await _resolve_with(
        "You are not allowed to switch Realms from this location", (MESSAGE_BOX_CENTER,)
    )

    assert outcome.refusal == dialog_captions.REALM_NOT_ALLOWED_HERE
    assert clicked == [MESSAGE_BOX_CENTER]


async def test_resolve_modal_reports_nothing_when_no_dialog_is_up():
    outcome, clicked = await _resolve_with("", ())

    assert outcome == (False, None)
    assert clicked == []


async def test_resolve_modal_treats_an_unknown_dismiss_only_dialog_as_a_refusal():
    """A dialog reworded by a patch must keep failing fast rather than start timing out.

    No centre button means the game offers no way to proceed, whatever the words say.
    """
    from bartlebot import dialog_captions

    outcome, clicked = await _resolve_with("Wording nobody has catalogued", (MESSAGE_BOX_RIGHT,))

    assert outcome.refusal == dialog_captions.UNRECOGNISED_REFUSAL
    assert clicked == [MESSAGE_BOX_RIGHT]


async def test_resolve_modal_confirms_an_unknown_dialog_that_offers_a_way_forward():
    outcome, clicked = await _resolve_with("Wording nobody has catalogued", (MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))

    assert outcome.answered is True
    assert outcome.refusal is None
    assert clicked == [MESSAGE_BOX_CENTER]


async def test_resolve_modal_leaves_another_handlers_dialog_alone():
    """A recognised dialog that belongs to somebody else is not clicked at all.

    "already in a group" is group_manager's to answer. Being recognised is what protects
    it: an unknown dismiss-only dialog would be treated as a refusal and dismissed, which
    is the right default but the wrong answer here.
    """
    from bartlebot import dialog_captions

    outcome, clicked = await _resolve_with("Somebody is already in a group.", (MESSAGE_BOX_RIGHT,))

    assert dialog_captions.identify_caption("Somebody is already in a group.") == dialog_captions.ALREADY_IN_GROUP
    assert outcome == (False, None)
    assert clicked == []


# ---------------------------------------------------------------------------
# ModalAwareRetry and await_instance_prompt
# ---------------------------------------------------------------------------


async def test_retry_answers_the_dialog_instead_of_pressing_the_button():
    """A client behind a modal is not lagging — the button it would press is unreachable."""
    import bartlebot.nodes.node_helpers as nh_mod

    patches, clicked = message_box_patches((MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))
    action = AsyncMock()
    with patches[0], patches[1], _caption_patch(_EXIT_CAPTION):
        await nh_mod.ModalAwareRetry(action)(FakeClient())

    assert clicked == [MESSAGE_BOX_CENTER]
    action.assert_not_called()


async def test_retry_falls_through_when_nothing_is_on_screen():
    """Outside a dungeon the wrapper must be invisible — stragglers still get retried."""
    import bartlebot.nodes.node_helpers as nh_mod

    patches, _ = message_box_patches(())
    action = AsyncMock()
    client = FakeClient()
    with patches[0], patches[1], _caption_patch(""):
        await nh_mod.ModalAwareRetry(action)(client)

    action.assert_awaited_once_with(client)


async def test_retry_records_a_refusal_and_stops_pressing():
    """poll_each_until_seen only reports who was seen, so the refusal has to be carried out."""
    import bartlebot.nodes.node_helpers as nh_mod
    from bartlebot import dialog_captions

    patches, _ = message_box_patches((MESSAGE_BOX_CENTER,))
    action = AsyncMock()
    retry = nh_mod.ModalAwareRetry(action)
    with patches[0], patches[1], _caption_patch("You cannot change Areas while in a Gauntlet."):
        await retry(FakeClient())

    assert retry.refusal == dialog_captions.GAUNTLET_NO_ZONE_CHANGE
    action.assert_not_called()


async def test_await_instance_prompt_returns_as_soon_as_it_answers():
    """No reason to sit out the rest of the Safety Delay once the dialog is gone."""
    import bartlebot.nodes.node_helpers as nh_mod

    patches, clicked = message_box_patches((MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))
    with patches[0], patches[1], _caption_patch(_EXIT_CAPTION):
        outcome = await nh_mod.await_instance_prompt(FakeClient(), delay=30, poll_interval=0)

    assert outcome.answered is True
    assert clicked == [MESSAGE_BOX_CENTER]


async def test_await_instance_prompt_gives_up_quietly_when_no_prompt_appears():
    """The common case: no dungeon involved, so the wait expires and the node carries on."""
    import bartlebot.nodes.node_helpers as nh_mod

    patches, clicked = message_box_patches(())
    with patches[0], patches[1], _caption_patch(""):
        outcome = await nh_mod.await_instance_prompt(FakeClient(), delay=0.01, poll_interval=0)

    assert outcome == (False, None)
    assert clicked == []


async def test_answer_instance_prompts_reports_one_clients_refusal_for_the_group():
    """Clients cross together, so a refusal for one condemns the move for all."""
    from unittest.mock import patch

    import bartlebot.nodes.node_helpers as nh_mod
    from bartlebot import dialog_captions

    ok, blocked = FakeClient(), FakeClient()

    async def caption(client, _path):
        return "You cannot change Areas while in a Gauntlet." if client is blocked else ""

    patches, _ = message_box_patches((MESSAGE_BOX_CENTER,))
    with patches[0], patches[1], patch.object(nh_mod, "get_text_by_path", new=AsyncMock(side_effect=caption)):
        refusal = await nh_mod.answer_instance_prompts([ok, blocked], delay=0.01, poll_interval=0)

    assert refusal == dialog_captions.GAUNTLET_NO_ZONE_CHANGE


# ---------------------------------------------------------------------------
# list_online_friends / resolve_friend_name
# ---------------------------------------------------------------------------


def _friends_list_patches(list_text, monkeypatch):
    """Stand in for the WizWalker internals list_online_friends imports lazily."""
    import sys
    import types

    import bartlebot.nodes.node_helpers as nh_mod

    async def _named_window(_parent, _name):
        window = types.SimpleNamespace()
        window.maybe_text = AsyncMock(return_value=list_text)
        return window

    utils = types.ModuleType("wizwalker.extensions.scripting.utils")
    utils._cycle_to_online_friends = AsyncMock()
    utils._maybe_get_named_window = _named_window
    # The real entry regex, which is what makes this exercise the actual parse.
    import re

    utils._friend_list_entry = re.compile(r"<icon>.*?</icon>(?P<name>[^<]+)")
    monkeypatch.setitem(sys.modules, "wizwalker.extensions.scripting.utils", utils)
    monkeypatch.setattr(nh_mod, "open_friends_list", AsyncMock(return_value=object()))


async def test_list_online_friends_returns_every_entry(monkeypatch):
    """The picker needs the whole list, not one match — this is the read resolve_friend_name
    was doing all along with the filter baked in."""
    import bartlebot.nodes.node_helpers as nh_mod

    _friends_list_patches("<icon>x</icon>Alice StormBlade<icon>x</icon>Bob FrostGem", monkeypatch)

    assert await nh_mod.list_online_friends(object()) == ["Alice StormBlade", "Bob FrostGem"]


async def test_list_online_friends_raises_when_nobody_is_online(monkeypatch):
    """An empty read and an empty list are indistinguishable from the window text, so the
    caller is told rather than handed a plausible-looking empty list."""
    import bartlebot.nodes.node_helpers as nh_mod

    _friends_list_patches("", monkeypatch)

    with pytest.raises(ValueError, match="No friends online"):
        await nh_mod.list_online_friends(object())


async def test_resolve_friend_name_matches_case_insensitively(monkeypatch):
    """WizWalker compares names case-sensitively, so this is where the exact casing has
    to be recovered."""
    import bartlebot.nodes.node_helpers as nh_mod

    _friends_list_patches("<icon>x</icon>Alice StormBlade<icon>x</icon>Bob FrostGem", monkeypatch)

    assert await nh_mod.resolve_friend_name(object(), "bob frostgem") == "Bob FrostGem"


async def test_resolve_friend_name_raises_when_the_name_is_absent(monkeypatch):
    import bartlebot.nodes.node_helpers as nh_mod

    _friends_list_patches("<icon>x</icon>Alice StormBlade", monkeypatch)

    with pytest.raises(ValueError, match="Could not find friend"):
        await nh_mod.resolve_friend_name(object(), "bob frostgem")


# ---------------------------------------------------------------------------
# reach_sigil_prompt
#
# The half enter_dungeon shares between its normal and Team-Up tails. It lived
# twice, once per node, until the two were merged — the node tests cover
# it through each tail; these cover it directly.
# ---------------------------------------------------------------------------


async def test_reach_sigil_prompt_teleports_once_when_the_prompt_appears():
    client = FakeClient()
    client.zone_name.return_value = "Hub"

    with patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)):
        reached = await nh.reach_sigil_prompt(FakeMachine(), [client], "Hub", _SIGIL, _RESET, 0.01, 0)

    assert reached is True
    client.teleport.assert_called_once_with(_SIGIL)


async def test_reach_sigil_prompt_refuses_from_the_wrong_zone():
    """The pre-condition, and the one failure that must not teleport: a client somewhere
    else is not a client whose sigil is at these coordinates."""
    client = FakeClient()
    client.zone_name.return_value = "Elsewhere"

    reached = await nh.reach_sigil_prompt(FakeMachine(), [client], "Hub", _SIGIL, _RESET, 0.01, 0)

    assert reached is False
    client.teleport.assert_not_called()


async def test_reach_sigil_prompt_gives_up_after_max_retries():
    client = FakeClient()
    client.zone_name.return_value = "Hub"

    reached = await nh.reach_sigil_prompt(FakeMachine(), [client], "Hub", _SIGIL, _RESET, 0.01, 0)

    assert reached is False
    # One sigil teleport and one reset teleport per attempt.
    assert client.teleport.call_count == nh.MAX_RETRIES * 2


async def test_reach_sigil_prompt_clears_a_stale_prompt_first():
    """A prompt already on screen is not the one this run raised, and entering on it
    would enter whatever the client happens to be standing on."""
    client = FakeClient()
    client.zone_name.return_value = "Hub"

    with (
        patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True)),
        patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)),
        patch.object(nh, "none_at_dungeon_entrance", new=AsyncMock(return_value=True)),
    ):
        reached = await nh.reach_sigil_prompt(FakeMachine(), [client], "Hub", _SIGIL, _RESET, 0.01, 0)

    assert reached is True
    assert [call.args[0] for call in client.teleport.call_args_list] == [_RESET, _SIGIL]


# ---------------------------------------------------------------------------
# poll_each_until_seen — stop_fn early exit
# ---------------------------------------------------------------------------


async def test_poll_each_until_seen_stops_early_when_stop_fn_fires():
    """stop_fn ends the poll as soon as it fires, without waiting out the full timeout —
    even though a straggler has not been seen and the timeout has not elapsed."""
    calls = itertools.count()

    async def check_fn(client):
        return client is _SEEN

    async def action_fn(_client):
        return None

    _SEEN = object()
    seen_at_stop = []

    async def stop_fn(seen_clients):
        seen_at_stop.append(list(seen_clients))
        next(calls)
        return True

    result = await nh.poll_each_until_seen(
        [_SEEN, object()], check_fn, action_fn, timeout=100.0, poll_interval=0, stop_fn=stop_fn
    )

    assert result == {0}
    assert seen_at_stop == [[_SEEN]]


async def test_poll_each_until_seen_ignores_stop_fn_until_someone_is_seen():
    """stop_fn is never even called while nobody has triggered — nothing to recover to."""
    stop_fn = AsyncMock(return_value=True)

    async def check_fn(_client):
        return False

    async def action_fn(_client):
        return None

    result = await nh.poll_each_until_seen(
        [object()], check_fn, action_fn, timeout=0.02, poll_interval=0, stop_fn=stop_fn
    )

    assert result == set()
    stop_fn.assert_not_called()


async def test_poll_each_until_seen_default_behavior_unaffected_by_stop_fn():
    """Without stop_fn, behavior is unchanged: polls until everyone is seen."""
    client = FakeClient()
    client.zone_name = AsyncMock(side_effect=["Src", "Dst"])

    async def check_fn(c):
        return await c.zone_name() == "Dst"

    result = await nh.poll_each_until_seen(
        [client], check_fn, action_fn=lambda c: c.teleport(None), timeout=1.0, poll_interval=0
    )

    assert result == {0}


# ---------------------------------------------------------------------------
# wait_for_zone_with_recovery
# ---------------------------------------------------------------------------

import bartlebot.nodes.teleportation.teleport_to_player_if_in_zone as tpz  # noqa: E402


async def test_wait_for_zone_with_recovery_returns_none_on_plain_success():
    """No recovery needed: everyone is already in zone on the first check."""
    client = FakeClient()
    client.zone_name.return_value = "Dst"

    with patch.object(nh, "_attempt_zone_recovery", new=AsyncMock()) as recovery:
        result = await nh.wait_for_zone_with_recovery(FakeMachine(), [client], "Dst", 0.01, 0)

    assert result is None
    recovery.assert_not_called()


async def test_wait_for_zone_with_recovery_skips_when_nobody_arrived():
    """No client reached the target zone at all — nothing to port stragglers to, so
    recovery is never attempted and the deadline is just waited out."""
    clients = [FakeClient(), FakeClient()]
    for c in clients:
        c.zone_name.return_value = "Elsewhere"

    with patch.object(nh, "_attempt_zone_recovery", new=AsyncMock()) as recovery:
        result = await nh.wait_for_zone_with_recovery(FakeMachine(), clients, "Dst", 0.01, 0)

    assert result == ["Elsewhere", "Elsewhere"]
    recovery.assert_not_called()


async def test_wait_for_zone_with_recovery_tries_recovery_as_soon_as_its_viable():
    """One client is already in zone and one is not: recovery is attempted as soon as the
    straggler has settled, without waiting out the rest of the timeout first."""
    arrived = FakeClient()
    arrived.zone_name.return_value = "Dst"
    straggler = FakeClient()
    # Still elsewhere on the first read (triggers recovery); in zone on every read after —
    # standing in for the recovery attempt itself having ported it.
    straggler.zone_name = AsyncMock(side_effect=itertools.chain(["Elsewhere"], itertools.repeat("Dst")))
    machine = FakeMachine()

    with patch.object(nh, "_attempt_zone_recovery", new=AsyncMock()) as recovery:
        result = await nh.wait_for_zone_with_recovery(machine, [arrived, straggler], "Dst", 0.01, 0, settle_grace=0)

    assert result is None
    recovery.assert_awaited_once()
    assert recovery.await_args[0][0] is machine
    assert recovery.await_args[0][1] == [arrived, straggler]
    assert recovery.await_args[0][2] == "Dst"


async def test_wait_for_zone_with_recovery_reports_failure_when_recovery_does_not_help():
    """Recovery is attempted exactly once; if the straggler still never arrives, the
    deadline is honoured and the final zones are reported."""
    arrived = FakeClient()
    arrived.zone_name.return_value = "Dst"
    straggler = FakeClient()
    straggler.zone_name.return_value = "Elsewhere"  # never changes, even after "recovery"

    with patch.object(nh, "_attempt_zone_recovery", new=AsyncMock()) as recovery:
        result = await nh.wait_for_zone_with_recovery(FakeMachine(), [arrived, straggler], "Dst", 0.01, 0)

    assert result == ["Dst", "Elsewhere"]
    recovery.assert_awaited_once()  # not retried on every subsequent poll


# ---------------------------------------------------------------------------
# wait_for_zone_with_recovery — a straggler that is still arriving (#70)
# ---------------------------------------------------------------------------


async def test_a_loading_straggler_is_never_ported():
    """A client on a loading screen reads the zone it left, not the one it is entering.
    Porting it there lands the teleport after it arrives and undoes the transition."""
    arrived = FakeClient()
    arrived.zone_name.return_value = "Dst"
    straggler = FakeClient()
    # Elsewhere and loading for several polls, then arrives under its own power.
    straggler.zone_name = AsyncMock(side_effect=itertools.chain(["Elsewhere"] * 3, itertools.repeat("Dst")))
    straggler.is_loading = AsyncMock(side_effect=itertools.chain([True] * 3, itertools.repeat(False)))

    with patch.object(nh, "_attempt_zone_recovery", new=AsyncMock()) as recovery:
        result = await nh.wait_for_zone_with_recovery(
            FakeMachine(), [arrived, straggler], "Dst", 0.01, 0, settle_grace=0
        )

    assert result is None
    recovery.assert_not_awaited()


async def test_a_loading_straggler_is_not_reported_as_a_timeout():
    """The deadline does not run out under a client that is mid-transition — reporting one
    as never-arrived is the same wrong call as porting it."""
    arrived = FakeClient()
    arrived.zone_name.return_value = "Dst"
    straggler = FakeClient()
    straggler.zone_name = AsyncMock(side_effect=itertools.chain(["Elsewhere"] * 4, itertools.repeat("Dst")))
    straggler.is_loading = AsyncMock(side_effect=itertools.chain([True] * 4, itertools.repeat(False)))

    with patch.object(nh, "_attempt_zone_recovery", new=AsyncMock()):
        # Deadline already spent: without the loading check this returns the stale zones.
        result = await nh.wait_for_zone_with_recovery(FakeMachine(), [arrived, straggler], "Dst", 0, 0, settle_grace=0)

    assert result is None


async def test_a_straggler_whose_loading_screen_has_not_started_gets_the_settle_grace():
    """The window between a trigger firing and its loading screen appearing reads exactly
    like a client that never triggered. The grace covers it; recovery waits."""
    arrived = FakeClient()
    arrived.zone_name.return_value = "Dst"
    straggler = FakeClient()
    # Never seen loading at all — arrival is the only evidence the trigger ever fired.
    straggler.zone_name = AsyncMock(side_effect=itertools.chain(["Elsewhere"] * 2, itertools.repeat("Dst")))

    with patch.object(nh, "_attempt_zone_recovery", new=AsyncMock()) as recovery:
        result = await nh.wait_for_zone_with_recovery(
            FakeMachine(), [arrived, straggler], "Dst", 0.01, 0, settle_grace=5.0
        )

    assert result is None
    recovery.assert_not_awaited()


async def test_a_straggler_that_stays_put_through_the_grace_is_still_recovered():
    """The grace delays recovery; it does not cancel it. A client that never triggered is
    not loading and never arrives, so it is ported once the window closes."""
    arrived = FakeClient()
    arrived.zone_name.return_value = "Dst"
    straggler = FakeClient()
    straggler.zone_name.return_value = "Elsewhere"

    with patch.object(nh, "_attempt_zone_recovery", new=AsyncMock()) as recovery:
        result = await nh.wait_for_zone_with_recovery(
            FakeMachine(), [arrived, straggler], "Dst", 0.01, 0, settle_grace=0.02
        )

    assert result == ["Dst", "Elsewhere"]
    recovery.assert_awaited_once()


# ---------------------------------------------------------------------------
# skip_while_loading
# ---------------------------------------------------------------------------


async def test_skip_while_loading_suppresses_the_retry_mid_load():
    client = FakeClient()
    client.is_loading.return_value = True
    action = AsyncMock()

    await nh.skip_while_loading(action)(client)

    action.assert_not_awaited()


async def test_skip_while_loading_passes_the_retry_through_otherwise():
    client = FakeClient()
    client.is_loading.return_value = False
    action = AsyncMock()

    await nh.skip_while_loading(action)(client)

    action.assert_awaited_once_with(client)


# ---------------------------------------------------------------------------
# _attempt_zone_recovery
# ---------------------------------------------------------------------------


async def test_attempt_zone_recovery_ports_only_the_actual_stragglers():
    """A client not in zone but not the target either is the only one ported — the target
    itself and any client already in zone are left alone."""
    target = FakeClient()
    already_there = FakeClient()
    straggler = FakeClient()
    clients = [target, already_there, straggler]
    zones = ["Dst", "Dst", "Elsewhere"]
    machine = FakeMachine()

    find_target = AsyncMock(return_value=tpz.ZoneTarget(index=0, name="Luke SkullHunter"))
    port = AsyncMock(return_value=nh.PortToFriendResult(seen_loading={0}))

    with (
        patch.object(tpz, "find_target_in_zone", find_target),
        patch.object(nh, "port_to_friend", port),
        patch.object(nh, "none_loading", new=AsyncMock(return_value=True)),
    ):
        await nh._attempt_zone_recovery(machine, clients, "Dst", zones, 0)

    find_target.assert_awaited_once_with(clients, "Dst", zones=zones)
    port.assert_awaited_once()
    assert port.await_args[0][0] == [straggler]
    assert port.await_args[0][1] == "luke skullhunter"


async def test_attempt_zone_recovery_gives_up_quietly_when_no_target_found():
    """Nobody is in the target zone at all — there is nothing to port anyone to, and this
    must not attempt a teleport or raise."""
    machine = FakeMachine()
    clients = [FakeClient(), FakeClient()]
    zones = ["Elsewhere", "Elsewhere"]

    find_target = AsyncMock(return_value=tpz.ZoneTarget(error="no hooked client is currently in 'Dst'"))
    port = AsyncMock()

    with patch.object(tpz, "find_target_in_zone", find_target), patch.object(nh, "port_to_friend", port):
        await nh._attempt_zone_recovery(machine, clients, "Dst", zones, 0)

    port.assert_not_called()


async def test_attempt_zone_recovery_swallows_a_value_error():
    """A friend that cannot be resolved (offline, not found) must not blow up the caller's
    poll loop — wait_for_zone_with_recovery's own timeout is what reports the failure."""
    target = FakeClient()
    straggler = FakeClient()
    clients = [target, straggler]
    zones = ["Dst", "Elsewhere"]
    machine = FakeMachine()

    find_target = AsyncMock(return_value=tpz.ZoneTarget(index=0, name="Luke SkullHunter"))
    port = AsyncMock(side_effect=ValueError("Could not find friend"))

    with patch.object(tpz, "find_target_in_zone", find_target), patch.object(nh, "port_to_friend", port):
        await nh._attempt_zone_recovery(machine, clients, "Dst", zones, 0)  # must not raise
