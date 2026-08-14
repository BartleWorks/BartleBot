"""Tests for node_helpers zone matching utilities."""

from unittest.mock import AsyncMock

import pytest
from wizwalker import MemoryReadError

from bartlebot.nodes.node_helpers import (
    _get_window_from_path,
    all_in_zone,
    all_left_zone,
    entity_name_matches,
    entity_name_predicate,
    find_closest_entity_by_name,
    find_entities_by_name,
    zone_matches,
)
from bartlebot.nodes.paths import MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT
from tests.conftest import FakeClient, FakeEntity, message_box_patches

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
    # The bug behind #58: a wildcard used to be a literal character that matched
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
    """Issue #96: visibility is judged from the compact window alone, so from the LARGE list
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
