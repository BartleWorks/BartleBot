"""Pressing through NPC dialogue declines a side quest instead of accepting it.

``node_helpers.advance_dialog_once`` is the one place dialogue is pressed -- the run's
ambient watcher, ``advance_dialogue_until_clear`` (interact, wait_for_dialogue) and the
Auto-dialogue authoring toggle all go through it. A side quest's offer page puts ACCEPT
where SPACEBAR lands, and shows a DECLINE on the left that no other page has.
"""

from __future__ import annotations

import asyncio

import pytest
from bartleby.runtime.paths import ADVANCE_DIALOG_PATH, DECLINE_QUEST_PATH
from wizwalker import Keycode

from bartlebot.nodes import node_helpers as nh
from tests.conftest import FakeClient
from tests.node_helper_patches import set_helper

#: One dialogue page: the right button (advance, or ACCEPT on an offer), the left (DECLINE).
TALK = {"right": True, "left": False}
OFFER = {"right": True, "left": True}


class Dialogue:
    """A client's dialogue window, paged forward by SPACEBAR or DECLINE.

    Records what was pressed, so a test can say ACCEPT was never pressed on an offer.
    """

    def __init__(self, client: FakeClient, pages: list[dict], *, decline_clickable: bool = True):
        self.pages = list(pages)
        self.accepted = 0
        self.declined = 0
        self.decline_clickable = decline_clickable
        client.dialogue = self
        client.send_key.side_effect = self._key

    @property
    def page(self) -> dict | None:
        return self.pages[0] if self.pages else None

    async def _key(self, key, _seconds=0.1):
        assert key == Keycode.SPACEBAR
        if self.page is None:
            return
        if self.page["left"]:
            self.accepted += 1
        self.pages.pop(0)

    def visible(self, path) -> bool:
        page = self.page
        if page is None:
            return False
        return page["right"] if path == ADVANCE_DIALOG_PATH else page["left"]

    def click(self, path) -> bool:
        assert path == DECLINE_QUEST_PATH
        if not self.decline_clickable:
            return False
        self.declined += 1
        self.pages.pop(0)
        return True


@pytest.fixture(autouse=True)
def fake_ui(monkeypatch):
    async def visible(client, path):
        await asyncio.sleep(0)  # a real read yields, which is what lets two callers interleave
        return client.dialogue.visible(path) if hasattr(client, "dialogue") else False

    async def click(client, path):
        return client.dialogue.click(path)

    set_helper(monkeypatch, "is_visible_by_path", visible)
    set_helper(monkeypatch, "click_by_path", click)
    set_helper(monkeypatch, "DIALOG_PAGE_SETTLE", 0.0)
    set_helper(monkeypatch, "DIALOG_FAST_POLL_INTERVAL", 0.001)
    set_helper(monkeypatch, "POLL_INTERVAL", 0.001)


async def test_an_ordinary_page_is_advanced_with_spacebar():
    client = FakeClient()
    dialogue = Dialogue(client, [TALK, TALK])

    assert await nh.advance_dialog_once(client)
    client.send_key.assert_awaited_once_with(Keycode.SPACEBAR, 0.1)
    assert len(dialogue.pages) == 1


async def test_a_side_quest_offer_is_declined_and_never_accepted():
    client = FakeClient()
    dialogue = Dialogue(client, [OFFER])

    assert await nh.advance_dialog_once(client)
    assert dialogue.declined == 1
    client.send_key.assert_not_awaited()


async def test_no_dialogue_presses_nothing():
    client = FakeClient()
    Dialogue(client, [])

    assert not await nh.advance_dialog_once(client)
    client.send_key.assert_not_awaited()


async def test_a_left_button_without_an_open_dialogue_is_not_clicked():
    """A child's visibility is its own flag, so a stale DECLINE must not read as an offer."""
    client = FakeClient()
    dialogue = Dialogue(client, [{"right": False, "left": True}])

    assert not await nh.advance_dialog_once(client)
    assert dialogue.declined == 0
    client.send_key.assert_not_awaited()


async def test_a_decline_that_cannot_be_clicked_is_retried_rather_than_accepted():
    client = FakeClient()
    dialogue = Dialogue(client, [OFFER], decline_clickable=False)

    for _ in range(3):
        assert await nh.advance_dialog_once(client)
    assert dialogue.accepted == 0
    client.send_key.assert_not_awaited()


async def test_two_callers_on_the_page_before_an_offer_do_not_both_press():
    """The ambient watcher and a node's advance loop run at once. Both reading the page before
    an offer and both pressing would put the second press on ACCEPT."""
    client = FakeClient()
    dialogue = Dialogue(client, [TALK, OFFER])

    await asyncio.gather(nh.advance_dialog_once(client), nh.advance_dialog_once(client))

    assert dialogue.accepted == 0
    assert dialogue.declined == 1
    assert dialogue.pages == []


async def test_advancing_until_clear_declines_an_offer_mid_conversation():
    talker, quest_giver = FakeClient(), FakeClient()
    Dialogue(talker, [TALK, TALK])
    offer = Dialogue(quest_giver, [TALK, OFFER, TALK])

    assert await nh.advance_dialogue_until_clear([talker, quest_giver], 0.01, 5.0)
    assert offer.accepted == 0
    assert offer.declined == 1
    assert talker.dialogue.pages == [] and offer.pages == []


# ---------------------------------------------------------------------------
# first_wait — the longer wait for the first window, and only the first
# ---------------------------------------------------------------------------


@pytest.fixture
def _fast_polls(monkeypatch):
    set_helper(monkeypatch, "POLL_INTERVAL", 0.0)
    set_helper(monkeypatch, "DIALOG_FAST_POLL_INTERVAL", 0.0)


async def test_first_wait_is_the_quiet_period_when_no_dialogue_opens(_fast_polls):
    """An animation that never opens a window still gets its full wait."""
    client = FakeClient()
    Dialogue(client, [])
    loop = asyncio.get_running_loop()

    start = loop.time()
    assert await nh.advance_dialogue_until_clear([client], 0.01, 5.0, first_wait=0.2)
    assert loop.time() - start >= 0.2


async def test_first_wait_no_longer_applies_once_dialogue_has_opened(_fast_polls):
    """After the last page only the Safety Delay is waited: the animation played before it."""
    client = FakeClient()
    Dialogue(client, [TALK, TALK])
    loop = asyncio.get_running_loop()

    start = loop.time()
    assert await nh.advance_dialogue_until_clear([client], 0.01, 10.0, first_wait=5.0)
    assert loop.time() - start < 2.5
    assert client.dialogue.pages == []


async def test_each_player_gets_the_first_wait_for_its_own_window(_fast_polls):
    """One player's window opening early does not cut short another's animation."""
    early, late = FakeClient(), FakeClient()
    Dialogue(early, [TALK])
    Dialogue(late, [])
    loop = asyncio.get_running_loop()
    loop.call_later(0.15, late.dialogue.pages.append, TALK)

    assert await nh.advance_dialogue_until_clear([early, late], 0.01, 5.0, first_wait=0.3)
    assert late.send_key.called, "the late window was seen and advanced, not missed"
    assert late.dialogue.pages == []


async def test_a_player_whose_window_never_opens_still_gets_the_whole_first_wait(_fast_polls):
    talker, silent = FakeClient(), FakeClient()
    Dialogue(talker, [TALK])
    Dialogue(silent, [])
    loop = asyncio.get_running_loop()

    start = loop.time()
    assert await nh.advance_dialogue_until_clear([talker, silent], 0.01, 5.0, first_wait=0.2)
    assert loop.time() - start >= 0.2
