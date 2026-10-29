"""Unit tests for the quicksell node."""

import logging
from unittest.mock import AsyncMock, patch

from bartleby.runtime.paths import (
    QUICKSELL_JEWEL_OPEN_PATH,
    QUICKSELL_NEXT_PAGE_PATH,
    QUICKSELL_PET_TAB_PATH,
    quicksell_item_checkbox_path,
    quicksell_item_title_path,
)

import bartlebot.nodes.utility.quicksell as qs
from bartlebot.bot.schema import BotConfig, BotOptionModel
from bartlebot.nodes import node_helpers
from bartlebot.settings_manager import SettingsManager
from tests.conftest import FakeClient, FakeMachine
from tests.node_helper_patches import patch_helper


def _machine(**options) -> FakeMachine:
    """A FakeMachine answering quicksell's options by their user-facing names.

    Quicksell reads bot options rather than params now, so a test says what the
    *bot* is set to do rather than what one node's params hold. The keyword
    arguments are the internal keys the pure helpers speak; this maps them across.
    Quicksell is on unless a test says otherwise, since it is off by default (#262)
    and a node that sells nothing has nothing to test.
    """
    answers: dict = {}
    for key, value in ({"enabled": True} | options).items():
        if key == "keep_list":
            answers[qs.OPTION_KEEP_LIST] = value
        elif key == "enabled":
            answers[qs.OPTION_ENABLED] = value
        else:
            answers[qs.SELL_FLAG_OPTIONS[key]] = value
    return FakeMachine(options=answers)


def _make_patchers(
    *,
    poll_until_returns=True,
    is_enabled_by_path_returns=False,
    get_window_text_returns="",
    is_jewel_full_returns=False,
):
    """Return a dict of patch context managers for the common quicksell dependencies."""
    return {
        "poll_until": patch_helper("poll_until", new_callable=AsyncMock, return_value=poll_until_returns),
        "click_by_path": patch_helper("click_by_path", new_callable=AsyncMock, return_value=True),
        "is_enabled": patch_helper(
            "is_enabled_by_path", new_callable=AsyncMock, return_value=is_enabled_by_path_returns
        ),
        "get_text": patch_helper("get_text_by_path", new_callable=AsyncMock, return_value=get_window_text_returns),
        "jewel_full": patch.object(
            qs, "_is_jewel_backpack_full", new_callable=AsyncMock, return_value=is_jewel_full_returns
        ),
        "poll_interval": patch.object(qs, "POLL_INTERVAL", 0),
    }


async def test_returns_success_when_neither_backpack_full():
    """Both backpacks have space → no quicksell UI opened, returns SUCCESS."""
    client = FakeClient()
    client.backpack_space.return_value = (50, 100)

    p = _make_patchers(is_jewel_full_returns=False)
    with (
        p["poll_until"],
        p["click_by_path"] as mock_click,
        p["is_enabled"],
        p["get_text"],
        p["jewel_full"],
        p["poll_interval"],
    ):
        result = await qs.run(_machine(), [client], {})

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert QUICKSELL_JEWEL_OPEN_PATH not in clicked_paths


async def test_quicksells_when_main_backpack_full():
    """Main backpack full, jewel not full → quicksell UI opened via jewel path."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    p = _make_patchers(is_jewel_full_returns=False)
    with (
        p["poll_until"],
        p["click_by_path"] as mock_click,
        p["is_enabled"],
        p["get_text"],
        p["jewel_full"],
        p["poll_interval"],
    ):
        result = await qs.run(_machine(), [client], {})

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert QUICKSELL_JEWEL_OPEN_PATH in clicked_paths


async def test_quicksells_when_jewel_full():
    """Main not full, jewel full → quicksell UI opened via jewel path."""
    client = FakeClient()
    client.backpack_space.return_value = (50, 100)

    p = _make_patchers(is_jewel_full_returns=True)
    with (
        p["poll_until"],
        p["click_by_path"] as mock_click,
        p["is_enabled"],
        p["get_text"],
        p["jewel_full"],
        p["poll_interval"],
    ):
        result = await qs.run(_machine(), [client], {})

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert QUICKSELL_JEWEL_OPEN_PATH in clicked_paths


async def test_skips_quicksell_when_not_full():
    """Neither backpack full → quicksell UI never opened."""
    client = FakeClient()
    client.backpack_space.return_value = (0, 100)

    p = _make_patchers(is_jewel_full_returns=False)
    with (
        p["poll_until"],
        p["click_by_path"] as mock_click,
        p["is_enabled"],
        p["get_text"],
        p["jewel_full"],
        p["poll_interval"],
    ):
        result = await qs.run(_machine(), [client], {})

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert QUICKSELL_JEWEL_OPEN_PATH not in clicked_paths


async def test_quicksells_once_when_both_full():
    """Both backpacks full → quicksell UI opened exactly once via jewel path."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    p = _make_patchers(is_jewel_full_returns=True)
    with (
        p["poll_until"],
        p["click_by_path"] as mock_click,
        p["is_enabled"],
        p["get_text"],
        p["jewel_full"],
        p["poll_interval"],
    ):
        result = await qs.run(_machine(), [client], {})

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    jewel_open_clicks = [p for p in clicked_paths if p == QUICKSELL_JEWEL_OPEN_PATH]
    assert len(jewel_open_clicks) == 1


async def test_keeps_items_matching_keyword():
    """Row 0 contains 'Rare Hat' and keep_list=['hat'] → row 0 checkbox is clicked to uncheck."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    def _text_for_path(client_arg, path):
        if path == quicksell_item_title_path(0):
            return "Rare Hat"
        return ""

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"] as mock_click, p["is_enabled"], p["jewel_full"], p["poll_interval"]:
        with patch_helper("get_text_by_path", side_effect=_text_for_path, new_callable=AsyncMock):
            result = await qs.run(_machine(keep_list=["hat"]), [client], {})

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert quicksell_item_checkbox_path(0) in clicked_paths


async def test_a_ticked_item_spares_only_itself():
    """keep_list=['=Couch Potatoes'] unchecks that row and leaves Red Couch Potatoes to be sold."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    def _text_for_path(client_arg, path):
        if path == quicksell_item_title_path(0):
            return "Red Couch Potatoes"
        if path == quicksell_item_title_path(1):
            return "<center>Couch Potatoes"
        return ""

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"] as mock_click, p["is_enabled"], p["jewel_full"], p["poll_interval"]:
        with patch_helper("get_text_by_path", side_effect=_text_for_path, new_callable=AsyncMock):
            result = await qs.run(_machine(keep_list=["=Couch Potatoes"]), [client], {})

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert quicksell_item_checkbox_path(1) in clicked_paths
    assert quicksell_item_checkbox_path(0) not in clicked_paths


async def test_case_insensitive_keep_match():
    """keep_list=['STAFF'] matches 'Firebrand Staff' (case-insensitive) → checkbox unchecked."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    def _text_for_path(client_arg, path):
        if path == quicksell_item_title_path(3):
            return "Firebrand Staff"
        return ""

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"] as mock_click, p["is_enabled"], p["jewel_full"], p["poll_interval"]:
        with patch_helper("get_text_by_path", side_effect=_text_for_path, new_callable=AsyncMock):
            result = await qs.run(_machine(keep_list=["STAFF"]), [client], {})

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert quicksell_item_checkbox_path(3) in clicked_paths


async def test_non_matching_items_not_unchecked():
    """Item name does not match any keyword → checkbox is NOT clicked."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    def _text_for_path(client_arg, path):
        if path == quicksell_item_title_path(0):
            return "Common Boots"
        return ""

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"] as mock_click, p["is_enabled"], p["jewel_full"], p["poll_interval"]:
        with patch_helper("get_text_by_path", side_effect=_text_for_path, new_callable=AsyncMock):
            result = await qs.run(_machine(keep_list=["hat"]), [client], {})

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert quicksell_item_checkbox_path(0) not in clicked_paths


async def test_iterates_multiple_pages():
    """Next-page button enabled first time, disabled second → advances exactly one page."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    p = _make_patchers(is_jewel_full_returns=False)
    # is_enabled_by_path calls: next-page True (advance), next-page False (stop), sell-button False (grayed).
    with p["poll_until"], p["click_by_path"] as mock_click, p["get_text"], p["jewel_full"], p["poll_interval"]:
        with patch_helper("is_enabled_by_path", new_callable=AsyncMock, side_effect=[True, False, False]):
            result = await qs.run(_machine(), [client], {})

    assert result == "SUCCESS"
    next_page_clicks = [c for c in mock_click.call_args_list if c.args[1] == QUICKSELL_NEXT_PAGE_PATH]
    assert len(next_page_clicks) == 1


async def test_returns_failure_when_quicksell_ui_never_opens():
    """poll_until returns False (shop UI never appeared) → FAILURE."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    p = _make_patchers(poll_until_returns=False, is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"], p["is_enabled"], p["get_text"], p["jewel_full"], p["poll_interval"]:
        result = await qs.run(_machine(), [client], {})

    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# The sale itself
#
# The tests above either find nothing full or stop at a grayed Sell button. These drive
# the UI the rest of the way, against a screen whose clicks change what is showing, so
# the real poll_until waits run rather than a patched answer that ignores the order.
# ---------------------------------------------------------------------------


# The captions a real sale showed, as the client read them.
_CROWNS = "<center>You are selling 3 Crowns Items. Are you sure you want to sell these Crowns Items?</center>"
_PET = "<center>You are selling a Pet. Are you sure you want to sell a Pet?</center>"
_GOLD = (
    "<center>You are close to the gold limit! <br>13534 gold from this sale will be lost! <br><br>"
    "Are you sure that you want to SELL for 19221 gold?</center>"
)
_INCLUDE_CROWNS = "Would you like to include Crowns Items in the selected items?"
# Something no sale raises, standing in for an invitation that arrives mid-sale.
_STRANGER = "<center>Someone has invited you to be their friend.</center>"


class _SellScreen:
    """The quicksell window from Select All onwards, as the node can see it.

    Dialogs are read by caption, as the node reads them. `dialogs` come up after the sale
    is confirmed, one at a time; `dropped` says how many clicks on each path the game
    loses before one lands. The backpack's close button is hidden while the shop is up.
    """

    def __init__(
        self,
        *,
        confirm_appears: bool = True,
        dialogs: tuple[str, ...] = (),
        include_crowns_question: bool = False,
        keep_everything: bool = False,
        dropped: dict | None = None,
    ):
        self.confirm_appears = confirm_appears
        self.pending_dialogs = list(dialogs)
        self.include_crowns_question = include_crowns_question
        self.keep_everything = keep_everything
        self.dropped = dict(dropped or {})
        self.dialogs: list[str] = []
        self.shop_open = True
        self.sold = False
        self.backpack_open = True
        self.clicks: list[list[str]] = []

    async def _is_visible(self, _client, path):
        if path in (qs.MESSAGE_BOX_CENTER, qs.MESSAGE_BOX_RIGHT):
            return bool(self.dialogs)
        if path == qs.MESSAGE_BOX_LEFT:
            return False
        if path == qs.QUICKSELL_CONFIRM_SELL_PATH:
            return self.confirm_appears and qs.QUICKSELL_SELL_BUTTON_PATH in self.clicks
        if path == qs.QUICKSELL_SHOP_PATH:
            return self.shop_open
        if path == qs.BACKPACK_CLOSE_BUTTON_PATH:
            return not self.shop_open
        if path == node_helpers.DECK_CONFIGURATION_PATH:
            return self.backpack_open
        return True

    async def _is_enabled(self, _client, path):
        # One page of items, and something on it left to sell unless everything is kept.
        return path == qs.QUICKSELL_SELL_BUTTON_PATH and not self.keep_everything

    async def _text(self, _client, path):
        if path == node_helpers.MESSAGE_BOX_CAPTION_PATH:
            return self.dialogs[0] if self.dialogs else ""
        return ""

    def _lands(self, path) -> bool:
        key = tuple(path)
        if self.dropped.get(key, 0) > 0:
            self.dropped[key] -= 1
            return False
        return True

    async def _click(self, _client, path):
        self.clicks.append(path)
        if not self._lands(path):
            return True
        if path == qs.QUICKSELL_ALL_BUTTON_PATH and self.include_crowns_question:
            self.include_crowns_question = False
            self.dialogs.append(_INCLUDE_CROWNS)
        elif path == qs.QUICKSELL_CONFIRM_SELL_PATH:
            self.dialogs.extend(self.pending_dialogs)
            self._finish_if_answered()
        elif path in (qs.MESSAGE_BOX_CENTER, qs.MESSAGE_BOX_RIGHT) and self.dialogs:
            self.dialogs.pop(0)
            self._finish_if_answered()
        elif path == qs.QUICKSELL_CLOSE_BUTTON_PATH:
            self.shop_open = False
        elif path == qs.BACKPACK_CLOSE_BUTTON_PATH:
            self.backpack_open = False
        return True

    def _finish_if_answered(self):
        if qs.QUICKSELL_CONFIRM_SELL_PATH in self.clicks and not self.dialogs:
            self.sold, self.shop_open = True, False

    async def sell(self, machine, *, complete_timeout: float = 1.0) -> str:
        with (
            patch_helper("is_visible_by_path", new=AsyncMock(side_effect=self._is_visible)),
            patch_helper("is_enabled_by_path", new=AsyncMock(side_effect=self._is_enabled)),
            patch_helper("click_by_path", new=AsyncMock(side_effect=self._click)),
            patch_helper("get_text_by_path", new=AsyncMock(side_effect=self._text)),
            patch.object(qs, "_is_jewel_backpack_full", new=AsyncMock(return_value=False)),
            patch.object(qs, "POLL_INTERVAL", 0),
            patch.object(qs, "QUICKSELL_COMPLETE_TIMEOUT", complete_timeout),
            patch_helper("WINDOW_CLOSE_RETRY_INTERVAL", 0.01),
            patch_helper("WINDOW_CLOSE_CONFIRM_INTERVAL", 0.001),
            patch_helper("WINDOW_CLOSE_TIMEOUT", 0.5),
        ):
            return await qs.run(machine, [_full_backpack()], {})

    def clicks_after(self, path) -> list[list[str]]:
        return self.clicks[self.clicks.index(path) + 1 :]


def _warned(machine, text: str) -> bool:
    return any(level == logging.WARNING and text in msg for msg, level in machine.logs)


async def test_a_full_backpack_is_sold_and_the_backpack_closed():
    screen = _SellScreen()

    result = await screen.sell(_machine())

    assert result == "SUCCESS"
    assert screen.clicks_after(qs.QUICKSELL_SELL_BUTTON_PATH)[0] == qs.QUICKSELL_CONFIRM_SELL_PATH
    assert screen.clicks_after(qs.QUICKSELL_CONFIRM_SELL_PATH) == [qs.BACKPACK_CLOSE_BUTTON_PATH]


async def test_a_dropped_close_click_is_sent_again_until_the_backpack_is_gone():
    """The game drops clicks sent while it is busy after a sale; one click left the backpack open."""
    screen = _SellScreen(dropped={tuple(qs.BACKPACK_CLOSE_BUTTON_PATH): 2})

    result = await screen.sell(_machine())

    assert result == "SUCCESS"
    assert screen.clicks_after(qs.QUICKSELL_CONFIRM_SELL_PATH) == [qs.BACKPACK_CLOSE_BUTTON_PATH] * 3
    assert not screen.backpack_open


async def test_a_backpack_that_stays_open_fails_the_node_and_says_so():
    screen = _SellScreen(dropped={tuple(qs.BACKPACK_CLOSE_BUTTON_PATH): float("inf")})
    machine = _machine()

    result = await screen.sell(machine)

    assert result == "FAILURE"
    assert _warned(machine, "did not close")


async def test_every_question_the_sale_raises_is_confirmed_before_the_backpack_closes():
    """Answering No to any of them cancels the whole sale, so each one gets the center button.
    These three are the ones a real sale raised, which was every one the old loop allowed for."""
    screen = _SellScreen(dialogs=(_CROWNS, _PET, _GOLD))

    result = await screen.sell(_machine())

    assert result == "SUCCESS"
    assert screen.clicks_after(qs.QUICKSELL_CONFIRM_SELL_PATH) == [
        qs.MESSAGE_BOX_CENTER,
        qs.MESSAGE_BOX_CENTER,
        qs.MESSAGE_BOX_CENTER,
        qs.BACKPACK_CLOSE_BUTTON_PATH,
    ]


async def test_a_dropped_answer_is_clicked_again_until_that_dialog_goes():
    screen = _SellScreen(dialogs=(_CROWNS, _PET, _GOLD), dropped={tuple(qs.MESSAGE_BOX_CENTER): 1})

    result = await screen.sell(_machine())

    assert result == "SUCCESS"
    assert screen.sold
    assert screen.clicks_after(qs.QUICKSELL_CONFIRM_SELL_PATH).count(qs.MESSAGE_BOX_CENTER) == 4


async def test_a_dialog_the_sale_did_not_raise_is_cancelled_and_the_sale_goes_on():
    machine = _machine()
    screen = _SellScreen(dialogs=(_CROWNS, _STRANGER, _GOLD))

    result = await screen.sell(machine)

    assert result == "SUCCESS"
    assert screen.sold
    assert screen.clicks_after(qs.QUICKSELL_CONFIRM_SELL_PATH)[:3] == [
        qs.MESSAGE_BOX_CENTER,
        qs.MESSAGE_BOX_RIGHT,
        qs.MESSAGE_BOX_CENTER,
    ]
    # Named by what it is, never by its caption, which can carry a wizard's name.
    assert not any("invited" in msg for msg, _level in machine.logs)


async def test_a_dialog_that_will_not_close_fails_the_sale():
    machine = _machine()
    screen = _SellScreen(dialogs=(_GOLD,), dropped={tuple(qs.MESSAGE_BOX_CENTER): float("inf")})

    result = await screen.sell(machine)

    assert result == "FAILURE"
    assert _warned(machine, "would not close")
    assert qs.BACKPACK_CLOSE_BUTTON_PATH not in screen.clicks


async def test_endless_dialogs_fail_the_sale_rather_than_answer_forever():
    machine = _machine()
    # Alternating: the same caption twice running is one dialog answered twice, as it should be.
    screen = _SellScreen(dialogs=(_CROWNS, _PET) * qs._MAX_DIALOGS)

    result = await screen.sell(machine)

    assert result == "FAILURE"
    assert _warned(machine, "dialogs came up")


async def test_a_backpack_that_never_comes_back_after_the_sale_fails():
    machine = _machine()
    screen = _SellScreen(dialogs=(_GOLD,))
    screen._finish_if_answered = lambda: None  # the shop never goes

    result = await screen.sell(machine, complete_timeout=0.05)

    assert result == "FAILURE"
    assert _warned(machine, "did not come back")


async def test_the_crowns_question_after_select_all_is_answered_yes():
    screen = _SellScreen(include_crowns_question=True)

    result = await screen.sell(_machine())

    assert result == "SUCCESS"
    assert screen.clicks_after(qs.QUICKSELL_ALL_BUTTON_PATH)[0] == qs.MESSAGE_BOX_CENTER


async def test_a_sale_the_game_never_asks_to_confirm_fails():
    screen = _SellScreen(confirm_appears=False)

    result = await screen.sell(_machine())

    assert result == "FAILURE"
    assert screen.clicks[-1] == qs.QUICKSELL_SELL_BUTTON_PATH


async def test_with_everything_kept_the_shop_is_closed_until_gone_before_the_backpack():
    """The backpack's close button is behind the shop until the shop is gone."""
    screen = _SellScreen(keep_everything=True, dropped={tuple(qs.QUICKSELL_CLOSE_BUTTON_PATH): 1})

    result = await screen.sell(_machine())

    assert result == "SUCCESS"
    assert qs.QUICKSELL_SELL_BUTTON_PATH not in screen.clicks
    assert screen.clicks_after(qs.QUICKSELL_ALL_BUTTON_PATH)[-3:] == [
        qs.QUICKSELL_CLOSE_BUTTON_PATH,
        qs.QUICKSELL_CLOSE_BUTTON_PATH,
        qs.BACKPACK_CLOSE_BUTTON_PATH,
    ]


async def test_with_everything_kept_a_shop_that_will_not_close_fails():
    machine = _machine()
    screen = _SellScreen(keep_everything=True, dropped={tuple(qs.QUICKSELL_CLOSE_BUTTON_PATH): float("inf")})

    result = await screen.sell(machine)

    assert result == "FAILURE"
    assert _warned(machine, "quicksell window did not close")
    assert qs.BACKPACK_CLOSE_BUTTON_PATH not in screen.clicks


async def test_handles_multiple_clients():
    """Two clients both have full main backpacks → both are quicksold, SUCCESS."""
    client1 = FakeClient()
    client1.backpack_space.return_value = (100, 100)
    client2 = FakeClient()
    client2.backpack_space.return_value = (100, 100)

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"], p["is_enabled"], p["get_text"], p["jewel_full"], p["poll_interval"]:
        result = await qs.run(_machine(), [client1, client2], {})

    assert result == "SUCCESS"
    assert client1.send_key.called
    assert client2.send_key.called


async def test_is_jewel_backpack_full_parses_text():
    """_is_jewel_backpack_full returns True when text is 'X/Y' with X >= Y."""
    with patch_helper("get_text_by_path", new_callable=AsyncMock) as mock_text:
        mock_text.return_value = "100/100"
        assert await qs._is_jewel_backpack_full(FakeClient()) is True

        mock_text.return_value = "50/100"
        assert await qs._is_jewel_backpack_full(FakeClient()) is False

        mock_text.return_value = "bad text"
        assert await qs._is_jewel_backpack_full(FakeClient()) is False

        mock_text.return_value = ""
        assert await qs._is_jewel_backpack_full(FakeClient()) is False


async def test_warns_that_a_keep_list_cannot_spare_a_pet():
    """Sell Pets on plus a keep list is the case where a pet is silently sold."""
    client = FakeClient()
    client.backpack_space.return_value = (50, 100)
    machine = _machine(keep_list=["hat"], sell_pets=True)

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"], p["is_enabled"], p["get_text"], p["jewel_full"], p["poll_interval"]:
        await qs.run(machine, [client], {})

    # INFO, not WARNING: the notice is advisory — Sell Pets with a keep list is a legitimate
    # configuration, just one whose keywords cannot match a pet. Keyed on the message rather
    # than on "any INFO log" because quicksell narrates its progress at INFO too.
    assert any("nickname" in msg for msg, level in machine.logs if level == logging.INFO)


async def test_no_pet_warning_without_a_keep_list():
    """Nothing was asked to be kept, so there is nothing to be surprised by."""
    client = FakeClient()
    client.backpack_space.return_value = (50, 100)
    machine = _machine()

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"], p["is_enabled"], p["get_text"], p["jewel_full"], p["poll_interval"]:
        await qs.run(machine, [client], {})

    assert not [msg for msg, level in machine.logs if level == logging.INFO and "nickname" in msg]


async def test_no_pet_warning_when_pets_are_not_being_sold():
    client = FakeClient()
    client.backpack_space.return_value = (50, 100)
    machine = _machine(keep_list=["hat"], sell_pets=False)

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"], p["is_enabled"], p["get_text"], p["jewel_full"], p["poll_interval"]:
        await qs.run(machine, [client], {})

    assert not [msg for msg, level in machine.logs if level == logging.INFO and "nickname" in msg]


# ---------------------------------------------------------------------------
# Bot options, and the levels behind them
#
# The node asks ``machine.get_option`` and does what it is told; which of the four
# levels answered is ``bot_options``' business, and is logged there. What is worth
# pinning down here is the round trip: that a value set at each level reaches the
# clicks this node makes, and that Quicksell off stops it doing anything.
#
# The settings used are the per-test isolated ones from conftest, not the
# developer's.
# ---------------------------------------------------------------------------


def _full_backpack() -> FakeClient:
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)
    return client


def _bot(*options: BotOptionModel) -> BotConfig:
    """A one-node bot whose only state is the quicksell this is all about."""
    return BotConfig.model_validate(
        {
            "id": "quicksell-bot",
            "name": "Quicksell Bot",
            "initial": "s0",
            "recovery_state": "s0",
            "states": [{"id": "s0", "node_type": "quicksell"}],
            "transitions": [],
            "options": [o.model_dump() for o in options],
        }
    )


async def test_enable_quicksell_off_is_a_no_op_that_succeeds():
    """A user who turned selling off has not broken the run, so this is not a failure —
    and nothing may be clicked, because the backpack is full and it would sell."""
    client = _full_backpack()

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"] as mock_click, p["is_enabled"], p["get_text"], p["jewel_full"]:
        result = await qs.run(_machine(enabled=False), [client], {})

    assert result == "SUCCESS"
    assert mock_click.call_args_list == []
    client.send_key.assert_not_called()


async def test_sell_pets_off_deselects_the_pet_tab():
    client = _full_backpack()

    p = _make_patchers(is_jewel_full_returns=False)
    with (
        p["poll_until"],
        p["click_by_path"] as mock_click,
        p["is_enabled"],
        p["get_text"],
        p["jewel_full"],
        p["poll_interval"],
    ):
        await qs.run(_machine(sell_pets=False), [client], {})

    assert any(call.args[1] == QUICKSELL_PET_TAB_PATH for call in mock_click.call_args_list)


async def test_sell_pets_on_leaves_the_pet_tab_alone():
    client = _full_backpack()

    p = _make_patchers(is_jewel_full_returns=False)
    with (
        p["poll_until"],
        p["click_by_path"] as mock_click,
        p["is_enabled"],
        p["get_text"],
        p["jewel_full"],
        p["poll_interval"],
    ):
        await qs.run(_machine(sell_pets=True), [client], {})

    assert not any(call.args[1] == QUICKSELL_PET_TAB_PATH for call in mock_click.call_args_list)


async def test_sell_wrenches_off_adds_the_keyword_to_the_keep_list():
    """Keep is expressed as a keep-list keyword, so the bool has to reach the list —
    the union of the two mechanisms happens in one place or they drift apart."""
    machine = _machine(sell_socket_wrenches=False)
    client = _full_backpack()

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"], p["is_enabled"], p["get_text"], p["jewel_full"], p["poll_interval"]:
        await qs.run(machine, [client], {})

    assert any("socket wrench" in msg for msg, _ in machine.logs)


async def test_the_log_names_every_resolved_value():
    """The bot file does not show what will be sold, so the log has to."""
    machine = _machine(keep_list=["hat"], sell_pets=True, sell_socket_wrenches=False)
    client = _full_backpack()

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"], p["is_enabled"], p["get_text"], p["jewel_full"], p["poll_interval"]:
        await qs.run(machine, [client], {})

    line = next(msg for msg, _ in machine.logs if msg.startswith("quicksell: enabled="))
    assert "sell_pets=True" in line and "sell_socket_wrenches=False" in line
    assert "'hat'" in line and "'socket wrench'" in line


# --- the four levels, through the editor's own resolver ---------------------------
#
# resolve_options_for is what the Option Values picker and the safety dialog read, and it
# goes through the same four levels the run does. Asserting on it rather than on a
# live BotMachine keeps these about precedence rather than about the FSM.


def test_an_unset_flag_follows_the_app_setting():
    SettingsManager.instance().set_general("default_quicksell_sell_pets", False)

    assert qs.resolve_options_for(_bot())["sell_pets"] is False


def test_a_declared_flag_outranks_the_app_setting():
    """An author who needs pets sold in this dungeon still gets them sold."""
    SettingsManager.instance().set_general("default_quicksell_sell_pets", False)

    config = _bot(BotOptionModel(name=qs.SELL_FLAG_OPTIONS["sell_pets"], type="bool", default=True))

    assert qs.resolve_options_for(config)["sell_pets"] is True


def test_the_users_own_value_outranks_the_declaration():
    config = _bot(BotOptionModel(name=qs.SELL_FLAG_OPTIONS["sell_pets"], type="bool", default=True))
    SettingsManager.instance().set_option_override(config.id, qs.SELL_FLAG_OPTIONS["sell_pets"], False)

    assert qs.resolve_options_for(config)["sell_pets"] is False


def test_a_users_keep_list_replaces_the_authors_rather_than_adding_to_it():
    """Replace, not merge: the widget shows the resolved list and writes its whole
    state, so a user who unticks something has actually unticked it."""
    config = _bot(BotOptionModel(name=qs.OPTION_KEEP_LIST, type="drop_keep_list", default=["Amber"]))
    SettingsManager.instance().set_option_override(config.id, qs.OPTION_KEEP_LIST, ["Onyx"])

    assert qs.resolve_options_for(config)["keep_list"] == ["Onyx"]


def test_enable_quicksell_defaults_to_off():
    """A player who never locks their items would lose them on a first run (#262)."""
    assert qs.resolve_options_for(_bot())["enabled"] is False


# --- the user's own keep list (#34) -------------------------------------------------


async def test_the_users_own_keep_list_spares_an_item_the_bot_would_sell():
    SettingsManager.instance().set_general(qs.USER_KEEP_LIST_KEY, ["hat"])
    client = _full_backpack()

    def _text_for_path(client_arg, path):
        return "Rare Hat" if path == quicksell_item_title_path(0) else ""

    p = _make_patchers(is_jewel_full_returns=False)
    with p["poll_until"], p["click_by_path"] as mock_click, p["is_enabled"], p["jewel_full"], p["poll_interval"]:
        with patch_helper("get_text_by_path", side_effect=_text_for_path, new_callable=AsyncMock):
            result = await qs.run(_machine(keep_list=[]), [client], {})

    assert result == "SUCCESS"
    assert quicksell_item_checkbox_path(0) in [c.args[1] for c in mock_click.call_args_list]


def test_the_users_own_keep_list_survives_a_bot_value_replacing_the_authors():
    """Not a level of the keep list option: a bot's value replaces the author's list,
    and the user's own list is kept whatever that resolves to."""
    config = _bot(BotOptionModel(name=qs.OPTION_KEEP_LIST, type="drop_keep_list", default=["Amber"]))
    sm = SettingsManager.instance()
    sm.set_option_override(config.id, qs.OPTION_KEEP_LIST, ["Onyx"])
    sm.set_general(qs.USER_KEEP_LIST_KEY, ["Pearl"])

    options = qs.resolve_options_for(config)

    assert options["keep_list"] == ["Onyx"]
    assert {"onyx", "pearl"} <= set(qs.effective_keep_list(options))
    assert "amber" not in qs.effective_keep_list(options)


def test_a_keep_list_setting_of_the_wrong_shape_keeps_nothing_extra():
    SettingsManager.instance().set_general(qs.USER_KEEP_LIST_KEY, "Amber")
    assert qs.user_keep_list() == []


# --- what a bot's own file sells beyond the user's settings (#145) -----------------


def _declaring(name: str, value: bool) -> BotConfig:
    return _bot(BotOptionModel(name=name, type="bool", default=value))


def test_a_bot_that_sells_what_the_users_settings_keep_declares_a_sale():
    """Tree of Life's case: Sell Skeleton Keys on, against an app setting that keeps them."""
    config = _declaring("Sell Skeleton Keys", True)
    assert qs.declared_sales(config) == ["Sell Skeleton Keys"]


def test_a_declaration_the_user_already_agrees_with_is_not_a_declared_sale():
    SettingsManager.instance().set_general("default_quicksell_sell_skeleton_keys", True)
    assert qs.declared_sales(_declaring("Sell Skeleton Keys", True)) == []


def test_a_declaration_that_keeps_more_is_not_a_declared_sale():
    assert qs.declared_sales(_declaring("Sell Pets", False)) == []


def test_the_users_own_value_for_the_bot_is_not_a_declared_sale():
    config = _declaring("Sell Skeleton Keys", True)
    SettingsManager.instance().set_option_override(config.id, "Sell Skeleton Keys", True)
    assert qs.declared_sales(config) == []


def test_a_bot_that_declares_nothing_declares_no_sale():
    assert qs.declared_sales(_bot()) == []


# ---------------------------------------------------------------------------
# The category vocabulary
#
# DROP_CATEGORIES is what the Possible Drops editor offers when a category is set
# by hand, and it is derived from the two classification sets rather than written
# out again. These pin down that it stays derived: a third hand-maintained copy
# would be the one that drifts, and a category missing from it is one a user
# cannot choose.
# ---------------------------------------------------------------------------


def test_the_vocabulary_is_both_classification_sets():
    assert set(qs.DROP_CATEGORIES) == qs.QUICKSELLABLE_CATEGORIES | set(qs.NEVER_QUICKSOLD_CATEGORIES)


def test_no_category_is_classified_both_ways():
    assert not qs.QUICKSELLABLE_CATEGORIES & set(qs.NEVER_QUICKSOLD_CATEGORIES)


def test_the_vocabulary_is_ordered_for_a_dropdown():
    assert list(qs.DROP_CATEGORIES) == sorted(qs.DROP_CATEGORIES)
