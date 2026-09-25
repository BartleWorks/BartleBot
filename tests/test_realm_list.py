"""Unit tests for bartlebot/nodes/utility/realm_list.py, driven directly.

``test_change_realm.py`` covers the node's two modes, and to do that it stubs
``poll_until`` to succeed at once -- which means the checks this module actually waits
on never run there: that a page really flipped, that the list really rendered, that the
cooldown really cleared. These tests keep the real ``poll_until`` and fake the game UI
underneath it instead, so each of those checks has to see the state it is looking for.
"""

import logging
from unittest.mock import AsyncMock, patch

import pytest

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.utility.realm_list as rl
from bartlebot.nodes.paths import (
    BACKPACK_CLOSE_BUTTON_PATH,
    REALM_CURRENT_NAME_PATH,
    REALM_GO_TO_BUTTON_PATH,
    REALM_NEXT_PAGE_PATH,
    REALM_PREV_PAGE_PATH,
    realm_button_path,
    realm_name_path,
)
from tests.conftest import FakeClient, FakeMachine, message_box_patches

# Wide enough that a check which *can* succeed always does, even on a loaded runner;
# the tests that time out pay it once.
_TIMEOUT = 0.1

_PAGES = [["Ambrose", "Balestrom"], ["Diego", "Drake"], ["Wraith"]]


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch):
    monkeypatch.setattr(rl, "POLL_INTERVAL", 0.001)


class _Panel:
    """The realm panel: pages of names, arrows that move between them, a current-name label.

    ``arrows_work=False`` is a panel whose arrows stay clickable but never change the
    page -- the lag or dropped click the page-change check exists to catch.
    """

    def __init__(self, pages, page=0, *, arrows_work=True, current_name_text=""):
        self.pages = pages
        self.page = page
        self.arrows_work = arrows_work
        self.current_name_text = current_name_text
        self.clicked: list = []

    async def text(self, _client, path):
        if path == REALM_CURRENT_NAME_PATH:
            return self.current_name_text
        for row in range(rl.REALMS_PER_PAGE):
            if path == realm_name_path(row):
                names = self.pages[self.page]
                return names[row] if row < len(names) else ""
        return ""

    async def enabled(self, _client, path):
        if path == REALM_NEXT_PAGE_PATH:
            return self.page < len(self.pages) - 1
        if path == REALM_PREV_PAGE_PATH:
            return self.page > 0
        return True

    async def click(self, _client, path):
        self.clicked.append(path)
        if self.arrows_work:
            if path == REALM_NEXT_PAGE_PATH and self.page < len(self.pages) - 1:
                self.page += 1
            elif path == REALM_PREV_PAGE_PATH and self.page > 0:
                self.page -= 1
        return True

    def patches(self):
        return (
            patch.object(nh, "get_text_by_path", new=AsyncMock(side_effect=self.text)),
            patch.object(nh, "is_enabled_by_path", new=AsyncMock(side_effect=self.enabled)),
            patch.object(nh, "click_by_path", new=AsyncMock(side_effect=self.click)),
            patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True)),
        )


def _warned(machine: FakeMachine, text: str) -> bool:
    return any(text in msg and lvl == logging.WARNING for msg, lvl in machine.logs)


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------


async def test_navigate_pages_steps_right_one_confirmed_page_at_a_time():
    panel = _Panel(_PAGES)
    text, enabled, click, visible = panel.patches()

    with text, enabled, click, visible:
        assert await rl.navigate_pages(FakeMachine(), FakeClient(), 2, _TIMEOUT, "t") is True

    assert panel.page == 2
    assert panel.clicked == [REALM_NEXT_PAGE_PATH, REALM_NEXT_PAGE_PATH]


async def test_navigate_pages_steps_left_for_a_negative_count():
    panel = _Panel(_PAGES, page=2)
    text, enabled, click, visible = panel.patches()

    with text, enabled, click, visible:
        assert await rl.navigate_pages(FakeMachine(), FakeClient(), -1, _TIMEOUT, "t") is True

    assert panel.page == 1
    assert panel.clicked == [REALM_PREV_PAGE_PATH]


async def test_navigate_pages_fails_when_the_page_does_not_change():
    """A click that did not flip the page must stop the walk, not count as a step."""
    panel = _Panel(_PAGES, arrows_work=False)
    machine = FakeMachine()
    text, enabled, click, visible = panel.patches()

    with text, enabled, click, visible:
        assert await rl.navigate_pages(machine, FakeClient(), 2, _TIMEOUT, "t") is False

    assert panel.clicked == [REALM_NEXT_PAGE_PATH]  # gave up after the first step
    assert _warned(machine, "page did not change on step 1/2 (direction=right)")


async def test_a_blank_page_mid_render_is_not_a_changed_page():
    """Row 0 reading '' differs from the old name, but it is a list still drawing, not a new page."""
    panel = _Panel([["Ambrose"], []])
    text, enabled, click, visible = panel.patches()

    with text, enabled, click, visible:
        assert await rl.navigate_pages(FakeMachine(), FakeClient(), 1, _TIMEOUT, "t") is False


async def test_navigate_to_page_1_walks_left_until_the_arrow_grays():
    """The panel opens wherever it was last left, so page 1 is found, not assumed."""
    panel = _Panel(_PAGES, page=2)
    text, enabled, click, visible = panel.patches()

    with text, enabled, click, visible:
        assert await rl.navigate_to_page_1(FakeMachine(), FakeClient(), _TIMEOUT, "t") is True

    assert panel.page == 0
    assert panel.clicked == [REALM_PREV_PAGE_PATH, REALM_PREV_PAGE_PATH]


async def test_navigate_to_page_1_fails_when_the_left_arrow_does_nothing():
    panel = _Panel(_PAGES, page=2, arrows_work=False)
    machine = FakeMachine()
    text, enabled, click, visible = panel.patches()

    with text, enabled, click, visible:
        assert await rl.navigate_to_page_1(machine, FakeClient(), _TIMEOUT, "t") is False

    assert _warned(machine, "could not reach page 1")


# ---------------------------------------------------------------------------
# Reading the current realm, and waiting for the list
# ---------------------------------------------------------------------------


async def test_wait_for_current_realm_name_returns_it_with_the_markup_stripped():
    panel = _Panel(_PAGES, current_name_text="<center>Wraith</center>")
    text, enabled, click, visible = panel.patches()

    with text, enabled, click, visible:
        assert await rl.wait_for_current_realm_name(FakeClient(), _TIMEOUT) == "Wraith"


async def test_wait_for_current_realm_name_waits_for_the_label_to_fill():
    panel = _Panel(_PAGES)
    reads = {"n": 0}

    async def text(client, path):
        if path == REALM_CURRENT_NAME_PATH:
            reads["n"] += 1
            return "Wraith" if reads["n"] >= 3 else ""
        return await panel.text(client, path)

    _text, enabled, click, visible = panel.patches()
    with patch.object(nh, "get_text_by_path", new=AsyncMock(side_effect=text)), enabled, click, visible:
        assert await rl.wait_for_current_realm_name(FakeClient(), 1.0) == "Wraith"


async def test_wait_for_current_realm_name_is_empty_when_the_label_never_fills():
    """'' is the caller's cue to find the current realm by paging instead."""
    panel = _Panel(_PAGES)
    text, enabled, click, visible = panel.patches()

    with text, enabled, click, visible:
        assert await rl.wait_for_current_realm_name(FakeClient(), _TIMEOUT) == ""


async def test_open_realm_list_waits_for_a_rendered_name():
    panel = _Panel(_PAGES)
    text, enabled, click, visible = panel.patches()

    with text, enabled, click, visible:
        assert await rl.open_realm_list(FakeMachine(), FakeClient(), _TIMEOUT, "t") is True

    assert BACKPACK_CLOSE_BUTTON_PATH not in panel.clicked


async def test_open_realm_list_fails_and_closes_the_spellbook_when_the_list_never_renders():
    panel = _Panel([[]])
    machine = FakeMachine()
    text, enabled, click, visible = panel.patches()

    with text, enabled, click, visible:
        assert await rl.open_realm_list(machine, FakeClient(), _TIMEOUT, "t") is False

    assert panel.clicked[-1] == BACKPACK_CLOSE_BUTTON_PATH
    assert _warned(machine, "realm list did not render")


# ---------------------------------------------------------------------------
# Hopping
# ---------------------------------------------------------------------------


def _hop_game(client, *, cooldown_polls: int = 0, loads: bool = True):
    """Go To Realm grayed for *cooldown_polls* checks, then a loading screen after it is pressed."""
    state = {"checks": 0, "pressed": False, "loading_reads": 0}

    async def enabled(_client, path):
        if path == REALM_GO_TO_BUTTON_PATH:
            state["checks"] += 1
            return state["checks"] > cooldown_polls
        return True

    async def is_loading():
        if not (loads and state["pressed"]):
            return False
        state["loading_reads"] += 1
        return state["loading_reads"] == 1  # one read of the screen, then it clears

    patches, clicked = message_box_patches(())

    async def click(c, path):
        if path == REALM_GO_TO_BUTTON_PATH:
            state["pressed"] = True
        return await patches[1].new(c, path)

    client.is_loading.side_effect = is_loading
    return (
        patches[0],
        patch.object(nh, "click_by_path", new=AsyncMock(side_effect=click)),
        patch.object(nh, "is_enabled_by_path", new=AsyncMock(side_effect=enabled)),
        patch.object(nh, "get_text_by_path", new=AsyncMock(return_value="Wraith")),
    ), clicked


async def test_hop_to_row_waits_out_the_cooldown_reselecting_the_row_each_tick():
    """The game deselects the row when the cooldown ends, so it is re-clicked until Go To enables."""
    client = FakeClient()
    (visible, click, enabled, text), clicked = _hop_game(client, cooldown_polls=3)

    with visible, click, enabled, text:
        assert await rl.hop_to_row(FakeMachine(), client, 4, "t") == "SUCCESS"

    before_go_to = clicked[: clicked.index(REALM_GO_TO_BUTTON_PATH)]
    # The first selection, then one re-selection per grayed check.
    assert before_go_to == [realm_button_path(4)] * 4


async def test_hop_to_row_fails_when_no_loading_screen_starts(monkeypatch):
    monkeypatch.setattr(rl, "TRIGGER_TIMEOUT", _TIMEOUT)
    client = FakeClient()
    machine = FakeMachine()
    (visible, click, enabled, text), clicked = _hop_game(client, loads=False)

    with visible, click, enabled, text:
        assert await rl.hop_to_row(machine, client, 4, "t") == "FAILURE"

    assert REALM_GO_TO_BUTTON_PATH in clicked
    assert _warned(machine, "loading screen did not start")
