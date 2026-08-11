"""Tests for node_helpers zone matching utilities."""

from unittest.mock import AsyncMock

import pytest

from bartlebot.nodes.node_helpers import _get_window_from_path, all_in_zone, all_left_zone, zone_matches

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


def test_zone_matches_malformed_pattern_falls_back_to_exact():
    # "[unclosed" is an invalid glob — should not raise, falls back to equality
    assert zone_matches("WizardCity/WC_Hub", "[unclosed") is False
    assert zone_matches("[unclosed", "[unclosed") is True


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


def _make_window(name: str, children: list | None = None):
    from unittest.mock import MagicMock

    w = MagicMock()
    w.name = AsyncMock(return_value=name)
    w.children = AsyncMock(return_value=list(children or []))
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
