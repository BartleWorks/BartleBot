"""Unit tests for bartlebot/nodes/housing.py.

The dangerous failure here is not a crash, it is a silent wrong answer: equipping a castle
for a wizard who deliberately has none, or restoring the wrong castle because the list
resorted. Both are changes to a real account that no error message would announce, so those
are the cases pinned hardest.
"""

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
    current = {"rows": rows}

    async def get_text(client, path):
        row = int(path[-1].removeprefix("Item_"))
        name, _ = current["rows"].get(row, ("", False))
        return name

    async def is_visible(client, path):
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


async def test_set_equipped_reports_failure_when_the_icon_never_changes():
    """A click that did not land leaves the opposite state; saying it worked would be worse."""
    text, visible, enabled, click = _page(_TWO_CASTLES)

    with text, visible, enabled, click, patch.object(housing, "HOUSING_EQUIP_TIMEOUT", 0):
        # Row 1 is equipped and the fake never flips it, so asking to unequip must fail.
        assert await housing._set_equipped(FakeClient(), 1, False) is False
