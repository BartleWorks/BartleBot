"""Unit tests for the quicksell node."""

import logging
from unittest.mock import AsyncMock, patch

import bartlebot.nodes.utility.quicksell as qs
from bartlebot.bot.schema import BotConfig, BotOptionModel
from bartlebot.nodes import node_helpers
from bartlebot.nodes.paths import (
    QUICKSELL_CLOSE_BUTTON_PATH,
    QUICKSELL_JEWEL_OPEN_PATH,
    QUICKSELL_NEXT_PAGE_PATH,
    QUICKSELL_PET_TAB_PATH,
    quicksell_item_checkbox_path,
    quicksell_item_title_path,
)
from bartlebot.settings_manager import SettingsManager
from tests.conftest import FakeClient, FakeMachine


def _machine(**options) -> FakeMachine:
    """A FakeMachine answering quicksell's options by their user-facing names.

    Quicksell reads bot options rather than params now, so a test says what the
    *bot* is set to do rather than what one node's params hold. The keyword
    arguments are the internal keys the pure helpers speak; this maps them across.
    """
    answers: dict = {}
    for key, value in options.items():
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
        "poll_until": patch.object(node_helpers, "poll_until", new_callable=AsyncMock, return_value=poll_until_returns),
        "click_by_path": patch.object(node_helpers, "click_by_path", new_callable=AsyncMock, return_value=True),
        "is_enabled": patch.object(
            node_helpers, "is_enabled_by_path", new_callable=AsyncMock, return_value=is_enabled_by_path_returns
        ),
        "get_text": patch.object(
            node_helpers, "get_text_by_path", new_callable=AsyncMock, return_value=get_window_text_returns
        ),
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


async def test_closes_shop_gui_when_all_items_kept():
    """All items match the keep list (sell button grayed) → shop GUI closed before backpack."""
    client = FakeClient()
    client.backpack_space.return_value = (100, 100)

    p = _make_patchers(is_jewel_full_returns=False, is_enabled_by_path_returns=False)
    with (
        p["poll_until"],
        p["click_by_path"] as mock_click,
        p["is_enabled"],
        p["get_text"],
        p["jewel_full"],
        p["poll_interval"],
    ):
        result = await qs.run(_machine(keep_list=["hat"]), [client], {})

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert QUICKSELL_CLOSE_BUTTON_PATH in clicked_paths


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
        with patch.object(node_helpers, "get_text_by_path", side_effect=_text_for_path, new_callable=AsyncMock):
            result = await qs.run(_machine(keep_list=["hat"]), [client], {})

    assert result == "SUCCESS"
    clicked_paths = [c.args[1] for c in mock_click.call_args_list]
    assert quicksell_item_checkbox_path(0) in clicked_paths


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
        with patch.object(node_helpers, "get_text_by_path", side_effect=_text_for_path, new_callable=AsyncMock):
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
        with patch.object(node_helpers, "get_text_by_path", side_effect=_text_for_path, new_callable=AsyncMock):
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
        with patch.object(node_helpers, "is_enabled_by_path", new_callable=AsyncMock, side_effect=[True, False, False]):
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


class _SellScreen:
    """The quicksell window from the Sell button onwards, as the node can see it."""

    def __init__(self, *, confirm_appears: bool = True, post_sell_dialogs: int = 0):
        self.confirm_appears = confirm_appears
        self.post_sell_dialogs = post_sell_dialogs
        self.dialogs_showing = 0
        self.clicks: list[list[str]] = []

    async def _is_visible(self, _client, path):
        if path == qs.MESSAGE_BOX_CENTER:
            return self.dialogs_showing > 0
        if path == qs.QUICKSELL_CONFIRM_SELL_PATH:
            return self.confirm_appears and qs.QUICKSELL_SELL_BUTTON_PATH in self.clicks
        return True

    async def _is_enabled(self, _client, path):
        # One page of items, and something on it left to sell.
        return path == qs.QUICKSELL_SELL_BUTTON_PATH

    async def _click(self, _client, path):
        self.clicks.append(path)
        if path == qs.QUICKSELL_CONFIRM_SELL_PATH:
            self.dialogs_showing = self.post_sell_dialogs
        elif path == qs.MESSAGE_BOX_CENTER and self.dialogs_showing:
            self.dialogs_showing -= 1
        return True

    async def sell(self, machine) -> str:
        with (
            patch.object(node_helpers, "is_visible_by_path", new=AsyncMock(side_effect=self._is_visible)),
            patch.object(node_helpers, "is_enabled_by_path", new=AsyncMock(side_effect=self._is_enabled)),
            patch.object(node_helpers, "click_by_path", new=AsyncMock(side_effect=self._click)),
            patch.object(node_helpers, "get_text_by_path", new=AsyncMock(return_value="")),
            patch.object(qs, "_is_jewel_backpack_full", new=AsyncMock(return_value=False)),
            patch.object(qs, "POLL_INTERVAL", 0),
        ):
            return await qs.run(machine, [_full_backpack()], {})

    def clicks_after(self, path) -> list[list[str]]:
        return self.clicks[self.clicks.index(path) + 1 :]


async def test_a_full_backpack_is_sold_and_the_backpack_closed():
    screen = _SellScreen()

    result = await screen.sell(_machine())

    assert result == "SUCCESS"
    assert screen.clicks_after(qs.QUICKSELL_SELL_BUTTON_PATH)[0] == qs.QUICKSELL_CONFIRM_SELL_PATH
    assert screen.clicks_after(qs.QUICKSELL_CONFIRM_SELL_PATH) == [qs.BACKPACK_CLOSE_BUTTON_PATH]


async def test_every_post_sell_dialog_is_confirmed_before_the_backpack_closes():
    """Answering No to any of them cancels the whole sale, so each one gets the centre button."""
    screen = _SellScreen(post_sell_dialogs=2)

    result = await screen.sell(_machine())

    assert result == "SUCCESS"
    assert screen.clicks_after(qs.QUICKSELL_CONFIRM_SELL_PATH) == [
        qs.MESSAGE_BOX_CENTER,
        qs.MESSAGE_BOX_CENTER,
        qs.BACKPACK_CLOSE_BUTTON_PATH,
    ]


async def test_a_sale_the_game_never_asks_to_confirm_fails():
    screen = _SellScreen(confirm_appears=False)

    result = await screen.sell(_machine())

    assert result == "FAILURE"
    assert screen.clicks[-1] == qs.QUICKSELL_SELL_BUTTON_PATH


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
    with patch.object(node_helpers, "get_text_by_path", new_callable=AsyncMock) as mock_text:
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
# clicks this node makes, and that Enable Quicksell off stops it doing anything.
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
# resolve_options_for is what the Setup picker and the safety dialog read, and it
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


def test_enable_quicksell_defaults_to_on():
    """Every bot sold before this option existed, and none of them opted in."""
    assert qs.resolve_options_for(_bot())["enabled"] is True


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
