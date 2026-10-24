"""Unit tests for bartlebot/nodes/housing.py.

The dangerous failure here is not a crash, it is a silent wrong answer: equipping a castle
for a wizard who deliberately has none, or restoring the wrong castle because the list
resorted. Both are changes to a real account that no error message would announce, so those
are the cases pinned hardest.
"""

import logging
from unittest.mock import AsyncMock, patch

import bartlebot.nodes.housing as housing
import bartlebot.nodes.node_helpers as nh
from tests.conftest import FakeClient

# A page of castles: row -> (name, equipped)
_TWO_CASTLES = {1: ("Arcanum Apartment", True), 2: ("Red Barn Farm", False)}
_NONE_EQUIPPED = {1: ("Arcanum Apartment", False), 2: ("Red Barn Farm", False)}


def _page(rows: dict[int, tuple[str, bool]], pages: list[dict] | None = None):
    """Patch the node_helpers reads that housing uses, backed by a fake castle list.

    `pages`, when given, is consumed one page per rightscroll click, so paging can be
    exercised without a game client.
    """
    remaining = list(pages or [])
    current = {"rows": rows, "spellbook": True}

    async def get_text(client, path):
        row = int(path[-1].removeprefix("Item_"))
        name, _ = current["rows"].get(row, ("", False))
        return name

    async def is_visible(client, path):
        if path == nh.DECK_CONFIGURATION_PATH:
            return current["spellbook"]
        if path[-1] == "fist":
            row = int(path[-2].removeprefix("Item_"))
            return current["rows"].get(row, ("", False))[1]
        if path[-1] == "rightscroll":
            return bool(remaining)
        return True  # tab / equip button present

    async def is_enabled(client, path):
        return True

    async def click(client, path):
        if path[-1] == "rightscroll" and remaining:
            current["rows"] = remaining.pop(0)
        elif path == housing.BACKPACK_CLOSE_BUTTON_PATH:
            current["spellbook"] = False
        return True

    return (
        patch.object(nh, "get_text_by_path", new=AsyncMock(side_effect=get_text)),
        patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=is_visible)),
        patch.object(nh, "is_enabled_by_path", new=AsyncMock(side_effect=is_enabled)),
        patch.object(nh, "click_by_path", new=AsyncMock(side_effect=click)),
    )


async def test_read_page_skips_empty_slots():
    """Rows 3-8 are empty slots reading '' — they are not castles."""
    text, visible, enabled, click = _page(_TWO_CASTLES)

    with text, visible, enabled, click:
        rows = await housing.read_page(FakeClient())

    assert rows == [(1, "Arcanum Apartment", True), (2, "Red Barn Farm", False)]


async def test_find_equipped_row_returns_the_row_with_the_fist():
    text, visible, enabled, click = _page(_TWO_CASTLES)

    with text, visible, enabled, click:
        assert await housing.find_equipped_row(FakeClient()) == (1, "Arcanum Apartment")


async def test_find_equipped_row_returns_none_when_nothing_is_equipped():
    """The case the whole fist indicator exists for — must NOT fall back to row 1."""
    text, visible, enabled, click = _page(_NONE_EQUIPPED)

    with text, visible, enabled, click:
        assert await housing.find_equipped_row(FakeClient()) is None


async def test_find_row_by_name_is_case_and_space_insensitive():
    text, visible, enabled, click = _page(_TWO_CASTLES)

    with text, visible, enabled, click:
        assert await housing.find_row_by_name(FakeClient(), "  red barn farm ") == 2


async def test_find_row_by_name_returns_none_when_absent():
    text, visible, enabled, click = _page(_TWO_CASTLES)

    with text, visible, enabled, click:
        assert await housing.find_row_by_name(FakeClient(), "Celestial Observatory") is None


async def test_find_row_by_name_pages_through_the_list():
    """A wizard with more than 8 castles has the one we want on a later page."""
    page2 = {1: ("Celestial Observatory", False)}
    text, visible, enabled, click = _page(_TWO_CASTLES, pages=[page2])

    with text, visible, enabled, click:
        assert await housing.find_row_by_name(FakeClient(), "Celestial Observatory") == 1


async def test_find_row_by_name_gives_up_rather_than_paging_forever():
    """A rightscroll that stays clickable but never advances must not spin."""
    stuck = [dict(_TWO_CASTLES) for _ in range(housing.HOUSING_PAGE_MAX + 5)]
    text, visible, enabled, click = _page(_TWO_CASTLES, pages=stuck)

    with text, visible, enabled, click:
        assert await housing.find_row_by_name(FakeClient(), "Nowhere Manor") is None


async def test_unequip_returns_the_castle_name():
    text, visible, enabled, click = _page(_TWO_CASTLES)
    # The fist flips off once Equip_Item is clicked.
    flip = patch.object(housing, "_set_equipped", new=AsyncMock(return_value=True))
    opened = patch.object(housing, "open_page", new=AsyncMock(return_value=True))
    tab = patch.object(housing, "select_castles_tab", new=AsyncMock(return_value=True))
    close = patch.object(housing, "close_page", new=AsyncMock())

    with text, visible, enabled, click, flip, opened, tab, close:
        assert await housing.unequip_castle(FakeClient()) == "Arcanum Apartment"


async def test_unequip_returns_none_when_no_castle_is_equipped():
    """Nothing to do — and critically, Equip_Item must never be clicked here."""
    text, visible, enabled, click = _page(_NONE_EQUIPPED)
    flip = patch.object(housing, "_set_equipped", new=AsyncMock(return_value=True))
    opened = patch.object(housing, "open_page", new=AsyncMock(return_value=True))
    tab = patch.object(housing, "select_castles_tab", new=AsyncMock(return_value=True))
    close = patch.object(housing, "close_page", new=AsyncMock())

    with text, visible, enabled, click, flip as f, opened, tab, close:
        assert await housing.unequip_castle(FakeClient()) is None

    f.assert_not_awaited()


async def test_unequip_returns_none_when_the_page_will_not_open():
    """Falls back to the slow HOME path rather than pressing on blindly."""
    opened = patch.object(housing, "open_page", new=AsyncMock(return_value=False))

    with opened:
        assert await housing.unequip_castle(FakeClient()) is None


async def test_unequip_closes_the_page_even_on_failure():
    opened = patch.object(housing, "open_page", new=AsyncMock(return_value=True))
    tab = patch.object(housing, "select_castles_tab", new=AsyncMock(return_value=False))
    close = patch.object(housing, "close_page", new=AsyncMock())

    with opened, tab, close as c:
        await housing.unequip_castle(FakeClient())

    c.assert_awaited_once()


async def test_equip_castle_finds_it_after_the_list_resorted():
    """The castle moved rows while the page was shut; it is found by name, not position."""
    resorted = {1: ("Amber Estate", False), 2: ("Red Barn Farm", False)}
    text, visible, enabled, click = _page(resorted)
    opened = patch.object(housing, "open_page", new=AsyncMock(return_value=True))
    tab = patch.object(housing, "select_castles_tab", new=AsyncMock(return_value=True))
    close = patch.object(housing, "close_page", new=AsyncMock())
    setter = patch.object(housing, "_set_equipped", new=AsyncMock(return_value=True))

    with text, visible, enabled, click, opened, tab, close, setter as s:
        assert await housing.equip_castle(FakeClient(), "Red Barn Farm") is True

    assert s.await_args.args[1] == 2  # row 2, not the remembered row 1


async def test_equip_castle_fails_loudly_when_the_castle_is_gone():
    text, visible, enabled, click = _page(_NONE_EQUIPPED)
    opened = patch.object(housing, "open_page", new=AsyncMock(return_value=True))
    tab = patch.object(housing, "select_castles_tab", new=AsyncMock(return_value=True))
    close = patch.object(housing, "close_page", new=AsyncMock())
    setter = patch.object(housing, "_set_equipped", new=AsyncMock(return_value=True))

    with text, visible, enabled, click, opened, tab, close, setter as s:
        assert await housing.equip_castle(FakeClient(), "Sold Last Week Manor") is False

    s.assert_not_awaited()


async def test_set_equipped_is_a_noop_when_already_in_the_wanted_state():
    """Equip_Item is a toggle — clicking it when the state already matches would undo it."""
    text, visible, enabled, click = _page(_TWO_CASTLES)

    with text, visible, enabled, click as c:
        assert await housing._set_equipped(FakeClient(), 1, True) is True

    c.assert_not_awaited()


async def test_set_equipped_logs_when_it_skips_the_click(caplog):
    """The no-op is the one read nothing re-checks, so it has to leave a trace (#15)."""
    text, visible, enabled, click = _page(_TWO_CASTLES)
    client = FakeClient()

    with text, visible, enabled, click, caplog.at_level(logging.INFO, logger=housing.logger.name):
        await housing._set_equipped(client, 1, True)

    assert f"hwnd {client.window_handle}: castle row 1 already reads equipped" in caplog.text


async def test_set_equipped_reports_failure_when_the_icon_never_changes():
    """A click that did not land leaves the opposite state; saying it worked would be worse."""
    text, visible, enabled, click = _page(_TWO_CASTLES)

    with text, visible, enabled, click, patch.object(housing, "HOUSING_EQUIP_TIMEOUT", 0):
        # Row 1 is equipped and the fake never flips it, so asking to unequip must fail.
        assert await housing._set_equipped(FakeClient(), 1, False) is False


async def test_set_equipped_fails_when_equip_item_never_enables(monkeypatch):
    """Selecting the row is what ungrays Equip_Item; without it the toggle is never pressed."""
    _fast(monkeypatch)
    text, visible, _enabled, click = _page(_TWO_CASTLES)
    never = patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=False))

    with text, visible, never, click as c:
        assert await housing._set_equipped(FakeClient(), 2, True) is False

    assert all(call.args[1] != housing.HOUSING_EQUIP_BUTTON_PATH for call in c.await_args_list)


# ---------------------------------------------------------------------------
# Opening, closing and navigating the page
# ---------------------------------------------------------------------------


def _fast(monkeypatch):
    """Compress the waits housing reads, for the tests whose subject is giving up."""
    monkeypatch.setattr(housing, "TRIGGER_TIMEOUT", 0.01)
    monkeypatch.setattr(housing, "POLL_INTERVAL", 0.001)


def _spellbook(client, *, blocked_by_other_page: bool):
    """A fake spellbook: U opens the housing page unless another page already owns the window.

    ESC closes whatever page that was, after which U works. Every key press is recorded on
    ``client.send_key`` for the tests to read the order back.
    """
    state = {"open": False, "blocked": blocked_by_other_page}

    async def send_key(key, _seconds):
        if key == housing.Keycode.ESC:
            state["blocked"] = False
        elif key == housing.Keycode.U and not state["blocked"]:
            state["open"] = True

    async def is_visible(_client, path):
        return path == housing.HOUSING_CASTLES_TAB_PATH and state["open"]

    client.send_key.side_effect = send_key
    return patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=is_visible))


def _keys(client) -> list:
    return [call.args[0] for call in client.send_key.await_args_list]


async def test_open_page_presses_u():
    client = FakeClient()

    with _spellbook(client, blocked_by_other_page=False):
        assert await housing.open_page(client) is True

    assert _keys(client) == [housing.Keycode.U]


async def test_open_page_presses_nothing_when_already_open():
    client = FakeClient()

    with patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True)):
        assert await housing.open_page(client) is True

    client.send_key.assert_not_awaited()


async def test_open_page_backs_out_of_another_page_and_retries(monkeypatch):
    """U is swallowed while another spellbook page is up; ESC clears it, the next U works."""
    _fast(monkeypatch)
    client = FakeClient()

    with _spellbook(client, blocked_by_other_page=True):
        assert await housing.open_page(client) is True

    assert _keys(client) == [housing.Keycode.U, housing.Keycode.ESC, housing.Keycode.U]


async def test_open_page_gives_up_after_its_retries(monkeypatch):
    _fast(monkeypatch)
    client = FakeClient()

    with patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=False)):
        assert await housing.open_page(client) is False

    assert _keys(client).count(housing.Keycode.U) == housing.HOUSING_OPEN_RETRIES


class _Spellbook:
    """The spellbook as close_page sees it: open until a close lands, after `dropped` are lost."""

    def __init__(self, *, close_enabled: bool = True, dropped: float = 0):
        self.close_enabled = close_enabled
        self.dropped = dropped
        self.open = True
        self.clicks: list = []

    async def _visible(self, _client, path):
        return path == nh.DECK_CONFIGURATION_PATH and self.open

    async def _enabled(self, _client, path):
        return self.close_enabled

    async def _click(self, _client, path):
        self.clicks.append(path)
        self._lands()
        return True

    async def _key(self, *_args):
        self._lands()

    def _lands(self):
        if self.dropped > 0:
            self.dropped -= 1
        else:
            self.open = False

    def patches(self, client):
        client.send_key.side_effect = self._key
        return (
            patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=self._visible)),
            patch.object(nh, "is_enabled_by_path", new=AsyncMock(side_effect=self._enabled)),
            patch.object(nh, "click_by_path", new=AsyncMock(side_effect=self._click)),
        )


def _fast_close(monkeypatch):
    monkeypatch.setattr(nh, "WINDOW_CLOSE_RETRY_INTERVAL", 0.01)
    monkeypatch.setattr(nh, "WINDOW_CLOSE_CONFIRM_INTERVAL", 0.001)
    monkeypatch.setattr(nh, "WINDOW_CLOSE_TIMEOUT", 0.5)


async def test_close_page_clicks_the_close_button_when_there_is_one(monkeypatch):
    _fast_close(monkeypatch)
    client = FakeClient()
    book = _Spellbook()
    visible, enabled, click = book.patches(client)

    with visible, enabled, click:
        await housing.close_page(client)

    assert book.clicks == [housing.BACKPACK_CLOSE_BUTTON_PATH]
    assert not book.open
    client.send_key.assert_not_awaited()


async def test_close_page_clicks_again_when_a_click_is_dropped(monkeypatch):
    _fast_close(monkeypatch)
    client = FakeClient()
    book = _Spellbook(dropped=2)
    visible, enabled, click = book.patches(client)

    with visible, enabled, click:
        await housing.close_page(client)

    assert book.clicks == [housing.BACKPACK_CLOSE_BUTTON_PATH] * 3
    assert not book.open


async def test_close_page_falls_back_to_esc(monkeypatch):
    _fast_close(monkeypatch)
    client = FakeClient()
    book = _Spellbook(close_enabled=False)
    visible, enabled, click = book.patches(client)

    with visible, enabled, click:
        await housing.close_page(client)

    assert book.clicks == []
    assert _keys(client) == [housing.Keycode.ESC]
    assert not book.open


async def test_close_page_warns_and_returns_when_the_page_stays_open(monkeypatch, caplog):
    """Callers check where they ended up themselves, so a stuck page is a warning, not a raise."""
    _fast_close(monkeypatch)
    client = FakeClient()
    book = _Spellbook(dropped=float("inf"))
    visible, enabled, click = book.patches(client)

    with visible, enabled, click, caplog.at_level(logging.WARNING):
        await housing.close_page(client)

    assert book.open
    assert any("still open" in r.getMessage() for r in caplog.records)


async def test_select_castles_tab_clicks_the_tab_until_equip_item_shows():
    client = FakeClient()
    state = {"on_castles": False}

    async def is_visible(_client, path):
        return path == housing.HOUSING_EQUIP_BUTTON_PATH and state["on_castles"]

    async def click(_client, path):
        state["on_castles"] = path == housing.HOUSING_CASTLES_TAB_PATH
        return True

    visible = patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=is_visible))
    clicked = patch.object(nh, "click_by_path", new=AsyncMock(side_effect=click))

    with visible, clicked as c:
        assert await housing.select_castles_tab(client) is True

    c.assert_awaited_once_with(client, housing.HOUSING_CASTLES_TAB_PATH)


async def test_select_castles_tab_does_not_click_when_already_there():
    visible = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True))
    clicked = patch.object(nh, "click_by_path", new=AsyncMock(return_value=True))

    with visible, clicked as c:
        assert await housing.select_castles_tab(FakeClient()) is True

    c.assert_not_awaited()


async def test_select_castles_tab_fails_when_equip_item_never_shows(monkeypatch):
    _fast(monkeypatch)
    visible = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=False))
    clicked = patch.object(nh, "click_by_path", new=AsyncMock(return_value=True))

    with visible, clicked:
        assert await housing.select_castles_tab(FakeClient()) is False


async def test_find_equipped_row_pages_to_a_later_castle():
    """The equipped castle of a wizard with more than 8 is on a later page."""
    page2 = {1: ("Celestial Observatory", True)}
    text, visible, enabled, click = _page(_NONE_EQUIPPED, pages=[page2])

    with text, visible, enabled, click:
        assert await housing.find_equipped_row(FakeClient()) == (1, "Celestial Observatory")


async def test_find_equipped_row_gives_up_rather_than_paging_forever():
    stuck = [dict(_NONE_EQUIPPED) for _ in range(housing.HOUSING_PAGE_MAX + 5)]
    text, visible, enabled, click = _page(_NONE_EQUIPPED, pages=stuck)

    with text, visible, enabled, click:
        assert await housing.find_equipped_row(FakeClient()) is None


async def test_next_page_stops_at_a_grayed_scroll_button():
    """A visible but grayed rightscroll is the last page; clicking it would not advance."""
    visible = patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True))
    enabled = patch.object(nh, "is_enabled_by_path", new=AsyncMock(return_value=False))
    clicked = patch.object(nh, "click_by_path", new=AsyncMock(return_value=True))

    with visible, enabled, clicked as c:
        assert await housing._next_page(FakeClient()) is False

    c.assert_not_awaited()


# ---------------------------------------------------------------------------
# Unequip / equip failure paths
# ---------------------------------------------------------------------------


def _opened_on_castles():
    return (
        patch.object(housing, "open_page", new=AsyncMock(return_value=True)),
        patch.object(housing, "select_castles_tab", new=AsyncMock(return_value=True)),
        patch.object(housing, "close_page", new=AsyncMock()),
    )


async def test_unequip_returns_none_when_the_toggle_does_not_take():
    """None sends the caller down the slow HOME path; a name here would be "restored" later."""
    text, visible, enabled, click = _page(_TWO_CASTLES)
    opened, tab, close = _opened_on_castles()
    setter = patch.object(housing, "_set_equipped", new=AsyncMock(return_value=False))

    with text, visible, enabled, click, opened, tab, close as c, setter:
        assert await housing.unequip_castle(FakeClient()) is None

    c.assert_awaited_once()


async def test_equip_castle_fails_when_the_page_will_not_open():
    opened = patch.object(housing, "open_page", new=AsyncMock(return_value=False))
    close = patch.object(housing, "close_page", new=AsyncMock())

    with opened, close as c:
        assert await housing.equip_castle(FakeClient(), "Red Barn Farm") is False

    c.assert_not_awaited()  # nothing was opened, so there is nothing to close


async def test_equip_castle_fails_and_closes_when_the_tab_will_not_show():
    opened, _tab, close = _opened_on_castles()
    tab = patch.object(housing, "select_castles_tab", new=AsyncMock(return_value=False))

    with opened, tab, close as c:
        assert await housing.equip_castle(FakeClient(), "Red Barn Farm") is False

    c.assert_awaited_once()


async def test_equip_castle_fails_and_closes_when_the_toggle_does_not_take(caplog):
    """The user's castle stays off here, so the failure has to be reported, not swallowed."""
    text, visible, enabled, click = _page(_NONE_EQUIPPED)
    opened, tab, close = _opened_on_castles()
    setter = patch.object(housing, "_set_equipped", new=AsyncMock(return_value=False))

    with text, visible, enabled, click, opened, tab, close as c, setter:
        with caplog.at_level(logging.WARNING, logger=housing.logger.name):
            assert await housing.equip_castle(FakeClient(), "Red Barn Farm") is False

    c.assert_awaited_once()
    assert "could not re-equip castle 'Red Barn Farm'" in caplog.text
