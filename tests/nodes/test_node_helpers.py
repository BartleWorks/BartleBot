"""Tests for node_helpers zone matching utilities."""

import asyncio
import itertools
import logging
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
from tests.conftest import FakeClient, FakeEntity, FakeMachine, message_box_patches, prompt_at


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("25", 25.0), ("0:25", 25.0), ("1:05", 65.0), ("<center>12</center>", 12.0), ("", None), ("Ready", None)],
)
def test_parse_countdown_reads_seconds_or_minutes_and_seconds(text, seconds):
    assert nh.parse_countdown(text) == seconds


async def test_teleport_cooldown_is_zero_while_the_hub_button_is_enabled():
    with (
        patch.object(nh, "is_enabled_by_path", AsyncMock(return_value=True)),
        patch.object(nh, "get_text_by_path", AsyncMock(return_value="25")) as read_timer,
    ):
        assert await nh.teleport_cooldown(FakeClient()) == 0.0
    read_timer.assert_not_called()


@pytest.mark.parametrize(("timer", "left"), [("25", 25.0), ("", None)])
async def test_teleport_cooldown_reads_the_timer_while_the_buttons_are_disabled(timer, left):
    with (
        patch.object(nh, "is_enabled_by_path", AsyncMock(return_value=False)),
        patch.object(nh, "get_text_by_path", AsyncMock(return_value=timer)),
    ):
        assert await nh.teleport_cooldown(FakeClient()) == left


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


# A dungeon and its Challenge Mode copy are one zone to a bot (#143). One case per suffix the
# game uses, since no dungeon shares another's.
_COPIES = [
    ("Aquila/AQ_Z02_Atlantea", "Aquila/AQ_Z02_Atlantea_Hard"),
    ("Marleybone/G14_Gauntlet/MB_Z06_Rooftop", "Marleybone/G14_Gauntlet/MB_Z06_Rooftop_Challenge"),
    ("Karamelle/Interiors/KM_Z10_ThroneRoom_B", "Karamelle/Interiors/KM_Z10_ThroneRoom_B_Adv"),
    ("Darkmoor/Interiors/DM_Z04I08_BogOfVengeance", "Darkmoor/Interiors/DM_Z04I08_BogOfVengeanceAdv"),
]


@pytest.mark.parametrize(("ordinary", "copy"), _COPIES)
def test_zone_matches_a_challenge_mode_copy_of_a_named_zone(ordinary, copy):
    assert zone_matches(copy, ordinary) is True


@pytest.mark.parametrize(("ordinary", "copy"), _COPIES)
def test_zone_matches_a_named_copy_against_its_ordinary_zone(ordinary, copy):
    """Both ways: a bot recorded in Challenge Mode still runs in normal mode."""
    assert zone_matches(ordinary, copy) is True


def test_zone_matches_only_the_copy_of_that_zone():
    """Atlantea02_Hard is the copy of Atlantea02, not of Atlantea, though it starts with it."""
    assert zone_matches("Aquila/AQ_Z02_Atlantea02_Hard", "Aquila/AQ_Z02_Atlantea") is False
    assert zone_matches("Aquila/AQ_Z02_Atlantea_Hard", "Aquila/AQ_Z02_Atlantea02") is False


def test_zone_matches_does_not_take_an_easy_copy_for_a_challenge_copy():
    easy = "Wallaru/Interiors/WL_Z02_I10_OutbackDungeon_Easy"
    assert zone_matches(easy, "Wallaru/Interiors/WL_Z02_I10_OutbackDungeon") is False


def test_zone_matches_a_glob_against_a_copy_through_its_ordinary_zone():
    assert zone_matches("Aquila/AQ_Z02_Atlantea_Hard", "Aquila/AQ_Z02_Atlant?a") is True


_PA_01_03 = "Housing_PA_Gauntlet/PA_Gauntlet_01/PA_Gauntlet_01_03"
_PA_40_03 = "Housing_PA_Gauntlet/PA_Gauntlet_40/PA_Gauntlet_40_03"


def test_zone_matches_a_gauntlet_room_in_any_level_band():
    """The players' levels pick the band; a bot recorded in one runs in every other."""
    assert zone_matches(_PA_40_03, _PA_01_03) is True
    assert zone_matches(_PA_01_03, _PA_40_03) is True
    assert zone_matches("Housing_PA_Gauntlet/PA_Gauntlet_80/PA_Gauntlet_80_03", _PA_40_03) is True


def test_zone_matches_only_the_same_room_across_bands():
    assert zone_matches("Housing_PA_Gauntlet/PA_Gauntlet_40/PA_Gauntlet_40_04", _PA_01_03) is False


def test_zone_matches_a_glob_over_the_lowest_band_takes_in_every_band():
    assert zone_matches(_PA_40_03, "Housing_PA_Gauntlet/PA_Gauntlet_01/*") is True


def test_zone_matches_does_not_merge_numbered_rooms():
    """_T1 to _T3 are three rooms of one dungeon, not three bands."""
    sunken = "WizardCity/WC_Streets/Interiors/WC_Sunken_City_T"
    assert zone_matches(sunken + "2", sunken + "1") is False


def test_zone_matches_a_glob_naming_only_the_copy_still_refuses_the_ordinary_zone():
    """A glob is the one way left to tell the two apart."""
    assert zone_matches("Aquila/AQ_Z02_Atlantea_Hard", "*_Hard") is True
    assert zone_matches("Aquila/AQ_Z02_Atlantea", "*_Hard") is False


# ---------------------------------------------------------------------------
# entity_name_matches — pure function, no async
# ---------------------------------------------------------------------------


def test_entity_name_plain_is_the_whole_id():
    assert entity_name_matches("Stone", "Stone") is True


def test_entity_name_plain_is_case_insensitive():
    assert entity_name_matches("Stone", "stone") is True
    assert entity_name_matches("Black Lotus", "BLACK LOTUS") is True


def test_entity_name_plain_never_matches_part_of_an_id():
    # #144: "Stone" sent collect_reagent to an NPC and to two other reagents.
    assert entity_name_matches("ZF-STONE-NPC10", "Stone") is False
    assert entity_name_matches("Sandstone", "Stone") is False
    assert entity_name_matches("AetherOre", "Ore") is False


def test_entity_name_plain_miss():
    assert entity_name_matches("Stone", "Kraken") is False


def test_entity_name_wildcard_is_how_to_ask_for_part_of_an_id():
    assert entity_name_matches("Sandstone", "*Stone*") is True
    assert entity_name_matches("ZF-STONE-NPC10", "*Stone*") is True


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
async def test_find_closest_plain_name_never_uses_vague_name():
    """WizSprinter's vague-name search is a substring match, which a plain name no longer is."""
    client = FakeClient()
    client.find_closest_by_predicate.return_value = FakeEntity("Stone")

    found = await find_closest_entity_by_name(client, "Stone")

    assert found is not None
    client.find_closest_by_vague_name.assert_not_awaited()
    client.find_closest_by_predicate.assert_awaited_once()


@pytest.mark.asyncio
async def test_find_closest_wildcard_uses_predicate():
    client = FakeClient()
    client.find_closest_by_predicate.return_value = FakeEntity("Bandit/BanditThug")

    found = await find_closest_entity_by_name(client, "*Bandit*")

    assert found is not None
    client.find_closest_by_vague_name.assert_not_awaited()
    client.find_closest_by_predicate.assert_awaited_once()


@pytest.mark.asyncio
async def test_find_entities_plain_name_keeps_only_the_exact_id():
    client = FakeClient()
    entities = [FakeEntity("Stone"), FakeEntity("ZF-STONE-NPC10"), FakeEntity("Sandstone"), FakeEntity("stone")]

    async def _filter(predicate):
        return [e for e in entities if await predicate(e)]

    client.get_base_entities_with_predicate.side_effect = _filter

    found = await find_entities_by_name(client, "Stone")

    assert [await e.object_name() for e in found] == ["Stone", "stone"]
    client.get_base_entities_with_vague_name.assert_not_awaited()


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


@pytest.mark.parametrize("sent", [True, False], ids=["clicks-sent", "button-grayed"])
async def test_click_until_gone_says_how_many_clicks_were_never_sent(sent, caplog):
    """A grayed button and a covered one time out alike; only the count tells them apart."""
    from unittest.mock import patch

    import bartlebot.nodes.node_helpers as nh_mod

    clicks = {"count": 0}

    async def click(_client, _path):
        clicks["count"] += 1
        return sent

    async def window_is_open():
        return True

    with (
        patch.object(nh_mod, "click_by_path", new=AsyncMock(side_effect=click)),
        caplog.at_level(logging.WARNING, logger=nh_mod.__name__),
    ):
        result = await nh_mod.click_until_gone(
            None, ["Some", "Close"], is_open=window_is_open, timeout=0.05, poll_interval=0.01
        )

    assert result is False
    unsent = 0 if sent else clicks["count"]
    assert f"({unsent} of {clicks['count']} clicks not sent" in caplog.text


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
    from bartlebot.game import dialog_captions

    with _caption_patch(_EXIT_CAPTION):
        assert await nh_mod.identify_modal(FakeClient()) == dialog_captions.INSTANCE_EXIT


async def test_identify_modal_returns_none_for_an_unknown_caption():
    import bartlebot.nodes.node_helpers as nh_mod

    with _caption_patch("A dialog nobody has cataloged"):
        assert await nh_mod.identify_modal(FakeClient()) is None


async def test_modal_showing_is_true_for_an_unrecognized_dialog():
    """identify_modal() conflates 'nothing up' with 'not in the snapshot'; this separates them."""
    import bartlebot.nodes.node_helpers as nh_mod

    with _caption_patch("A dialog nobody has cataloged"):
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
    from bartlebot.game import dialog_captions

    outcome, clicked = await _resolve_with("Your friend is busy right now.", (MESSAGE_BOX_RIGHT,))

    assert outcome.refusal == dialog_captions.FRIEND_BUSY
    assert clicked == [MESSAGE_BOX_RIGHT]
    assert "not accepting teleports" in dialog_captions.describe(outcome.refusal)


async def test_resolve_modal_dismisses_a_one_button_refusal_with_the_center():
    """A refusal with only an OK button puts it in the center, not the right."""
    from bartlebot.game import dialog_captions

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

    No center button means the game offers no way to proceed, whatever the words say.
    """
    from bartlebot.game import dialog_captions

    outcome, clicked = await _resolve_with("Wording nobody has cataloged", (MESSAGE_BOX_RIGHT,))

    assert outcome.refusal == dialog_captions.UNRECOGNIZED_REFUSAL
    assert clicked == [MESSAGE_BOX_RIGHT]


async def test_resolve_modal_confirms_an_unknown_dialog_that_offers_a_way_forward():
    outcome, clicked = await _resolve_with("Wording nobody has cataloged", (MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))

    assert outcome.answered is True
    assert outcome.refusal is None
    assert clicked == [MESSAGE_BOX_CENTER]


async def test_resolve_modal_leaves_another_handlers_dialog_alone():
    """A recognized dialog that belongs to somebody else is not clicked at all.

    "already in a group" is group_manager's to answer. Being recognized is what protects
    it: an unknown dismiss-only dialog would be treated as a refusal and dismissed, which
    is the right default but the wrong answer here.
    """
    from bartlebot.game import dialog_captions

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
    from bartlebot.game import dialog_captions

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
    from bartlebot.game import dialog_captions

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

    with pytest.raises(ValueError, match="Could not find friend") as raised:
        await nh_mod.resolve_friend_name(object(), "bob frostgem")
    # Callers log this message, so it must not carry the wizard name (#121).
    assert "frostgem" not in str(raised.value).casefold()


# ---------------------------------------------------------------------------
# reach_prompt / clear_stale_prompt
#
# The reset-and-retry routine every prompt-raising node shares. Driven here with
# INTERACT_PROMPT, whose checks read FakeClient.is_in_npc_range directly.
# ---------------------------------------------------------------------------


async def test_reach_prompt_stands_at_reset_first_even_with_no_prompt_showing():
    """A prompt reading absent may only be on its way: a client just off a loading screen
    beside another object raises that object's prompt a moment later, and approaching
    straight from there presses X on it (#152). So the reset is unconditional."""
    client = FakeClient()
    prompt_at(client, _SIGIL)

    reached = await nh.reach_prompt(FakeMachine(), [client], _SIGIL, _RESET, nh.INTERACT_PROMPT, 0.01, 0)

    assert reached is True
    assert [call.args[0] for call in client.teleport.call_args_list] == [_RESET, _SIGIL]


async def test_reach_prompt_clears_a_stale_prompt_first():
    """A prompt already on screen is not the one this run raised, and pressing X on it
    would use whatever the client happens to be standing next to."""
    client = FakeClient()
    prompt_at(client, _SIGIL, initially=True)
    machine = FakeMachine()

    reached = await nh.reach_prompt(machine, [client], _SIGIL, _RESET, nh.INTERACT_PROMPT, 0.01, 0)

    assert reached is True
    assert [call.args[0] for call in client.teleport.call_args_list] == [_RESET, _SIGIL]
    assert any("Pre-existing interact prompt on client(s) [1]" in msg for msg, _ in machine.logs)


async def test_reach_prompt_waits_out_a_prompt_that_arrives_late_at_reset():
    """The #152 race itself: the stale prompt is not up when the node starts, appears once
    the client is at reset, and then clears. The approach waits for all of that."""
    client = FakeClient()
    reads = iter([False, True, True, False])  # node start, then at reset: late, late, gone

    async def _showing():
        last = client.teleport.call_args_list[-1].args[0] if client.teleport.call_args_list else None
        if last == _SIGIL:
            return True
        return next(reads, False)

    client.is_in_npc_range.side_effect = _showing

    reached = await nh.reach_prompt(FakeMachine(), [client], _SIGIL, _RESET, nh.INTERACT_PROMPT, 0.5, 0)

    assert reached is True
    assert [call.args[0] for call in client.teleport.call_args_list] == [_RESET, _SIGIL]
    assert next(reads, "spent") == "spent"


async def test_settle_prompt_clear_restarts_when_the_prompt_flickers_back():
    """One absent read is not a settled one: a prompt that reappears restarts the count."""
    client = FakeClient()
    client.is_in_npc_range.side_effect = itertools.chain([False, True, False], itertools.repeat(False))

    with patch.object(nh, "PROMPT_CLEAR_SETTLE", 0):
        still = await nh.settle_prompt_clear([client], nh.INTERACT_PROMPT, 0.5, 0)

    assert still == [False]
    # Absent, back, then two absent reads in a row.
    assert client.is_in_npc_range.call_count == 4


async def test_reach_prompt_fails_without_approaching_when_reset_never_clears(monkeypatch):
    monkeypatch.setattr(nh, "PROMPT_CLEAR_SETTLE", 0)
    """A prompt still up at reset_xyz means reset is in range of something; approaching
    from there would carry that prompt to the object, so the node fails instead."""
    client = FakeClient()
    client.is_in_npc_range.return_value = True
    machine = FakeMachine()

    reached = await nh.reach_prompt(machine, [client], _SIGIL, _RESET, nh.INTERACT_PROMPT, 0.01, 0, fail_on_miss=False)

    assert reached is False
    client.teleport.assert_called_once_with(_RESET)
    assert any(
        msg.startswith("FAILURE: interact prompt would not clear at reset_xyz on client(s) [1]")
        and level == logging.WARNING
        for msg, level in machine.logs
    )


async def test_reach_prompt_resets_and_retries_after_a_miss():
    client = FakeClient()
    # The prompt shows only once the client is back at the point for the second time:
    # reset, point, reset, point.
    client.is_in_npc_range.side_effect = lambda: client.teleport.call_count >= 4

    reached = await nh.reach_prompt(FakeMachine(), [client], _SIGIL, _RESET, nh.INTERACT_PROMPT, 0.05, 0)

    assert reached is True
    assert [call.args[0] for call in client.teleport.call_args_list] == [_RESET, _SIGIL, _RESET, _SIGIL]


async def test_reach_prompt_gives_up_after_max_retries_and_logs_the_failure():
    client = FakeClient()
    machine = FakeMachine()

    reached = await nh.reach_prompt(machine, [client], _SIGIL, _RESET, nh.INTERACT_PROMPT, 0.01, 0)

    assert reached is False
    # The first reset, then one teleport to the point and one reset per attempt.
    assert client.teleport.call_count == 1 + nh.MAX_RETRIES * 2
    assert any(
        msg == f"FAILURE: interact prompt never appeared after {nh.MAX_RETRIES} attempts" and level == logging.WARNING
        for msg, level in machine.logs
    )


async def test_reach_prompt_leaves_a_miss_to_the_caller_when_asked():
    """enter_zone_via_object carries on with whichever clients did get the prompt, so a
    miss there is not the node's failure and must not be logged as one. Those clients have
    to still be at the point for that: back at reset, X would reach no object, or another."""
    machine = FakeMachine()
    client = FakeClient()

    reached = await nh.reach_prompt(machine, [client], _SIGIL, _RESET, nh.INTERACT_PROMPT, 0.01, 0, fail_on_miss=False)

    assert reached is False
    assert not any(level >= logging.WARNING for _, level in machine.logs)
    assert client.teleport.call_args_list[-1].args[0] is _SIGIL


async def test_reach_prompt_skips_the_clear_when_the_caller_has_done_it():
    client = FakeClient()
    prompt_at(client, _SIGIL)

    reached = await nh.reach_prompt(
        FakeMachine(), [client], _SIGIL, _RESET, nh.INTERACT_PROMPT, 0.01, 0, clear_first=False
    )

    assert reached is True
    client.teleport.assert_called_once_with(_SIGIL)


async def test_clear_stale_prompt_labels_clients_by_the_numbers_given():
    """A node working on a subset of the party names its clients by party position."""
    idle, stale = FakeClient(), FakeClient()
    prompt_at(stale, initially=True)
    machine = FakeMachine()

    cleared = await nh.clear_stale_prompt(machine, [idle, stale], _RESET, nh.INTERACT_PROMPT, 0.01, 0, numbers=[2, 4])

    assert cleared is True
    assert any("client(s) [4]" in msg for msg, _ in machine.logs)
    idle.teleport.assert_called_once_with(_RESET)
    stale.teleport.assert_called_once_with(_RESET)


async def test_clear_stale_prompt_resets_quietly_without_one():
    """Nothing was showing, so nothing is worth a log line, but the reset still happens."""
    client = FakeClient()
    machine = FakeMachine()

    cleared = await nh.clear_stale_prompt(machine, [client], _RESET, nh.INTERACT_PROMPT, 0.01, 0)

    assert cleared is True
    client.teleport.assert_called_once_with(_RESET)
    assert machine.logs == []


async def test_clear_stale_prompt_names_only_the_clients_still_showing(monkeypatch):
    monkeypatch.setattr(nh, "PROMPT_CLEAR_SETTLE", 0)
    clear, stuck = FakeClient(), FakeClient()
    stuck.is_in_npc_range.return_value = True
    machine = FakeMachine()

    cleared = await nh.clear_stale_prompt(machine, [clear, stuck], _RESET, nh.INTERACT_PROMPT, 0.01, 0)

    assert cleared is False
    assert any("on client(s) [2]" in msg and level == logging.WARNING for msg, level in machine.logs)


# ---------------------------------------------------------------------------
# reach_sigil_prompt
#
# The half enter_dungeon shares between its normal and Team-Up tails. It lived
# twice, once per node, until the two were merged — the node tests cover
# it through each tail; these cover it directly.
# ---------------------------------------------------------------------------


async def test_reach_sigil_prompt_resets_then_teleports_once_when_the_prompt_appears():
    client = FakeClient()
    client.zone_name.return_value = "Hub"

    with patch.object(nh, "all_at_dungeon_entrance", new=AsyncMock(return_value=True)):
        reached = await nh.reach_sigil_prompt(FakeMachine(), [client], "Hub", _SIGIL, _RESET, 0.01, 0)

    assert reached is True
    assert [call.args[0] for call in client.teleport.call_args_list] == [_RESET, _SIGIL]


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
    # The first reset, then one sigil teleport and one reset teleport per attempt.
    assert client.teleport.call_count == 1 + nh.MAX_RETRIES * 2


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
    deadline is honored and the final zones are reported."""
    arrived = FakeClient()
    arrived.zone_name.return_value = "Dst"
    straggler = FakeClient()
    straggler.zone_name.return_value = "Elsewhere"  # never changes, even after "recovery"

    with patch.object(nh, "_attempt_zone_recovery", new=AsyncMock()) as recovery:
        result = await nh.wait_for_zone_with_recovery(FakeMachine(), [arrived, straggler], "Dst", 0.01, 0)

    assert result == ["Dst", "Elsewhere"]
    recovery.assert_awaited_once()  # not retried on every subsequent poll


async def test_wait_for_zone_with_recovery_never_ports_when_recovery_is_off():
    """Zone navigation sorts out its own stragglers after the step, so it only wants the wait."""
    arrived = FakeClient()
    arrived.zone_name.return_value = "Dst"
    straggler = FakeClient()
    straggler.zone_name.return_value = "Elsewhere"

    with patch.object(nh, "_attempt_zone_recovery", new=AsyncMock()) as recovery:
        result = await nh.wait_for_zone_with_recovery(
            FakeMachine(), [arrived, straggler], "Dst", 0.01, 0, settle_grace=0, recover=False
        )

    assert result == ["Dst", "Elsewhere"]
    recovery.assert_not_awaited()


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

    find_target = AsyncMock(return_value=nh.ZoneTarget(index=0, name="Luke SkullHunter"))
    port = AsyncMock(return_value=nh.PortToFriendResult(seen_loading={0}))

    with (
        patch.object(nh, "find_target_in_zone", find_target),
        patch.object(nh, "port_to_friend", port),
        patch.object(nh, "none_loading", new=AsyncMock(return_value=True)),
    ):
        await nh._attempt_zone_recovery(machine, clients, "Dst", zones, 0)

    find_target.assert_awaited_once_with(clients, "Dst", zones=zones)
    port.assert_awaited_once()
    assert port.await_args[0][0] == [straggler]
    assert port.await_args[0][1] == "luke skullhunter"


async def test_find_target_in_zone_picks_the_first_client_there_by_player_order():
    from bartlebot.game import wizard_names  # noqa: PLC0415

    clients = [FakeClient(), FakeClient(), FakeClient()]
    try:
        wizard_names.remember(clients[1], "Luke SkullHunter")
        wizard_names.remember(clients[2], "Other Name")
        target = await nh.find_target_in_zone(clients, "Dst", zones=["Elsewhere", "Dst", "Dst"])
    finally:
        wizard_names.clear_cache()

    assert (target.index, target.name, target.error) == (1, "Luke SkullHunter", "")


async def test_find_target_in_zone_reports_nobody_there():
    clients = [FakeClient(), FakeClient()]
    target = await nh.find_target_in_zone(clients, "Dst", zones=["Elsewhere", "Elsewhere"])

    assert target.index is None
    assert "no hooked client" in target.error


async def test_find_target_in_zone_reports_a_target_whose_name_is_not_known_yet():
    clients = [FakeClient(), FakeClient()]
    for c, z in zip(clients, ("Dst", "Elsewhere")):
        c.zone_name.return_value = z
    target = await nh.find_target_in_zone(clients, "Dst")

    assert target.index is None
    assert "P1" in target.error and "not been detected" in target.error


async def test_attempt_zone_recovery_gives_up_quietly_when_no_target_found():
    """Nobody is in the target zone at all — there is nothing to port anyone to, and this
    must not attempt a teleport or raise."""
    machine = FakeMachine()
    clients = [FakeClient(), FakeClient()]
    zones = ["Elsewhere", "Elsewhere"]

    find_target = AsyncMock(return_value=nh.ZoneTarget(error="no hooked client is currently in 'Dst'"))
    port = AsyncMock()

    with patch.object(nh, "find_target_in_zone", find_target), patch.object(nh, "port_to_friend", port):
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

    find_target = AsyncMock(return_value=nh.ZoneTarget(index=0, name="Luke SkullHunter"))
    port = AsyncMock(side_effect=ValueError("Could not find friend"))

    with patch.object(nh, "find_target_in_zone", find_target), patch.object(nh, "port_to_friend", port):
        await nh._attempt_zone_recovery(machine, clients, "Dst", zones, 0)  # must not raise


# ---------------------------------------------------------------------------
# fight_target_rank / find_closest_fight_target — which entity a fight's enemy name means
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actual", "pattern", "rank"),
    [
        ("AQ-Piranha-Guard", "AQ-Piranha-Guard", 0),
        ("aq-piranha-guard", "AQ-Piranha-Guard", 0),
        ("Adv_AQ-Piranha-Guard", "AQ-Piranha-Guard", 1),  # advanced mode's copy
        ("LM_Dog_Hero_A_01_Hard", "LM_Dog_Hero_A_01", 1),
        ("AQ-Piranha-Guard_StandIn", "AQ-Piranha-Guard", None),  # the pre-battle speaker
        ("AR-DeathSpell170-Standin-Ilga_01", "Ilga", None),
        ("Oni-War-MSBoss-L50-mini", "Oni-War-MSBoss-L50", None),  # the pet copy
        ("AQ-Piranha-Guard_StandIn", "AQ-Piranha-Guard_StandIn", 0),  # named on purpose
        ("AQ-Piranha-Guard_StandIn", "Guard_StandIn", 1),
        ("AQ-Shark-Lamia", "AQ-Piranha-Guard", None),
        (None, "AQ-Piranha-Guard", None),
    ],
)
def test_fight_target_rank(actual, pattern, rank):
    assert nh.fight_target_rank(actual, pattern) == rank


def _world(client: FakeClient, *entities: tuple[str, float]) -> dict:
    """Give *client* a world of named entities at distances, searched as WizSprinter would."""
    placed = {}
    for name, distance in entities:
        entity = FakeEntity(name)
        placed[name] = (entity, distance)

    async def closest(pred):
        matching = [(d, e) for e, d in placed.values() if await pred(e)]
        return min(matching, key=lambda m: m[0])[1] if matching else None

    client.find_closest_by_predicate.side_effect = closest
    return {name: entity for name, (entity, _d) in placed.items()}


async def test_an_exact_name_wins_over_a_closer_different_boss():
    """Golem-Steel-MBBoss-L50-1 is Mr. Incinerator; the Bellows fight must not port to it."""
    client = FakeClient()
    world = _world(client, ("Golem-Steel-MBBoss-L50-1", 10.0), ("Golem-Steel-MBBoss-L50", 500.0))
    assert await nh.find_closest_fight_target(client, "Golem-Steel-MBBoss-L50") is world["Golem-Steel-MBBoss-L50"]


async def test_a_closer_stand_in_is_never_the_fight():
    client = FakeClient()
    world = _world(client, ("AQ-Piranha-Guard_StandIn", 5.0), ("AQ-Piranha-Guard", 300.0))
    assert await nh.find_closest_fight_target(client, "AQ-Piranha-Guard") is world["AQ-Piranha-Guard"]


async def test_advanced_mode_falls_back_to_the_copy_but_not_its_stand_in_or_pet():
    client = FakeClient()
    world = _world(
        client,
        ("AQ-Piranha-Guard_StandIn", 5.0),
        ("AQ-Piranha-Guard-mini", 6.0),
        ("Adv_AQ-Piranha-Guard", 300.0),
    )
    assert await nh.find_closest_fight_target(client, "AQ-Piranha-Guard") is world["Adv_AQ-Piranha-Guard"]


async def test_only_a_stand_in_means_no_target():
    client = FakeClient()
    _world(client, ("AQ-Piranha-Guard_StandIn", 5.0))
    assert await nh.find_closest_fight_target(client, "AQ-Piranha-Guard") is None


async def test_a_wildcard_fight_name_matches_as_written():
    """A glob names its own anchoring, stand-in or not; exact-first does not apply to it."""
    client = FakeClient()
    world = _world(client, ("AQ-Piranha-Guard_StandIn", 5.0), ("AQ-Piranha-Guard", 300.0))
    assert await nh.find_closest_fight_target(client, "AQ-Piranha-Guard*") is world["AQ-Piranha-Guard_StandIn"]


# ---------------------------------------------------------------------------
# clear_modals — back out of whatever is up, never confirm it
# ---------------------------------------------------------------------------


class _Screen:
    """A stack of modals, each a (caption, buttons) pair, closed by a click on one of its buttons."""

    def __init__(self, *modals, stubborn: bool = False):
        self.modals = list(modals)
        self.stubborn = stubborn
        self.clicked: list[list[str]] = []

    def patches(self):
        import bartlebot.nodes.node_helpers as nh_mod
        from bartlebot.nodes.paths import MESSAGE_BOX_CAPTION_PATH

        async def _text(_client, path):
            return self.modals[0][0] if self.modals and list(path) == MESSAGE_BOX_CAPTION_PATH else ""

        async def _visible(_client, path):
            return bool(self.modals) and list(path) in self.modals[0][1]

        async def _click(_client, path):
            self.clicked.append(list(path))
            if self.modals and list(path) in self.modals[0][1] and not self.stubborn:
                self.modals.pop(0)

        return (
            patch.object(nh_mod, "get_text_by_path", new=_text),
            patch.object(nh_mod, "is_visible_by_path", new=_visible),
            patch.object(nh_mod, "click_by_path", new=_click),
        )


async def _clear(screen: _Screen, timeout: float = 0.2):
    import bartlebot.nodes.node_helpers as nh_mod

    a, b, c = screen.patches()
    with a, b, c:
        return await nh_mod.clear_modals(FakeClient(), timeout, 0.001)


async def test_clear_modals_with_nothing_up_clicks_nothing():
    screen = _Screen()
    result = await _clear(screen)

    assert result.cleared is True
    assert result.dismissed == ()
    assert screen.clicked == []


async def test_clear_modals_cancels_a_question_instead_of_confirming_it():
    """Confirming the exit prompt would leave the dungeon; the caller wanted a menu, not that."""
    from bartlebot.game import dialog_captions

    screen = _Screen((_EXIT_CAPTION, [MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT]))
    result = await _clear(screen)

    assert result.cleared is True
    assert result.dismissed == (dialog_captions.INSTANCE_EXIT,)
    assert screen.clicked == [MESSAGE_BOX_RIGHT]


async def test_clear_modals_closes_a_one_button_dialog_with_its_center():
    screen = _Screen(("Your friend is busy right now.", [MESSAGE_BOX_CENTER]))
    result = await _clear(screen)

    assert result.cleared is True
    assert screen.clicked == [MESSAGE_BOX_CENTER]


async def test_clear_modals_closes_a_dialog_the_snapshot_does_not_know():
    screen = _Screen(("A dialog nobody has cataloged", [MESSAGE_BOX_RIGHT]))
    result = await _clear(screen)

    assert result.cleared is True
    assert result.dismissed == (None,)


async def test_clear_modals_closes_every_dialog_in_a_stack():
    screen = _Screen(
        (_EXIT_CAPTION, [MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT]),
        ("Your friend is busy right now.", [MESSAGE_BOX_RIGHT]),
    )
    result = await _clear(screen)

    assert result.cleared is True
    assert len(result.dismissed) == 2
    assert screen.modals == []


async def test_clear_modals_gives_up_on_a_dialog_that_stays_and_names_it():
    from bartlebot.game import dialog_captions

    screen = _Screen((_EXIT_CAPTION, [MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT]), stubborn=True)
    result = await _clear(screen, timeout=0.05)

    assert result.cleared is False
    assert result.disconnected is False
    assert result.key == dialog_captions.INSTANCE_EXIT


async def test_clear_modals_leaves_a_disconnect_alone():
    """Nothing on a disconnect modal helps, and the health monitor recovers the client."""
    screen = _Screen(("Connection Lost!", [MESSAGE_BOX_CENTER]))
    result = await _clear(screen)

    assert result.cleared is False
    assert result.disconnected is True
    assert screen.clicked == []
