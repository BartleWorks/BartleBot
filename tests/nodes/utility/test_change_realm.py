"""Unit tests for the change_realm node, in both of its modes.

The realm list is faked as pages of names: ``get_text_by_path`` answers by row from the
page currently showing, the right arrow's enabled state says whether there is another
page, and the current realm is the one whose button reads disabled -- exactly the three
reads the node relies on.
"""

import contextlib
from unittest.mock import AsyncMock, patch

import pytest
from bartleby.runtime.game import dialog_captions
from bartleby.runtime.paths import (
    BACKPACK_CLOSE_BUTTON_PATH,
    MESSAGE_BOX_CENTER,
    MESSAGE_BOX_RIGHT,
    REALM_CURRENT_NAME_PATH,
    REALM_GO_TO_BUTTON_PATH,
    REALM_NEXT_PAGE_PATH,
    REALM_PREV_PAGE_PATH,
    realm_button_path,
    realm_name_path,
)

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.utility.change_realm as cr
import bartlebot.nodes.utility.realm_list as rl
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.options import bot_options
from tests.conftest import FakeClient, FakeMachine, message_box_patches, see_each_other
from tests.node_helper_patches import patch_helper


class _RealmList:
    """Pages of realm names, with one of them marked as the realm the client is on."""

    def __init__(self, pages: list[list[str]], current: str = "", current_name_text: str = ""):
        self.pages = pages
        self.current = current
        #: What txtYourRealmName reports directly, independent of button state.
        self.current_name_text = current_name_text
        self.page = 0
        self.clicked: list[list[str]] = []

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
        for row in range(rl.REALMS_PER_PAGE):
            if path == realm_button_path(row):
                names = self.pages[self.page]
                return not (row < len(names) and names[row] == self.current)
        return True

    async def click(self, _client, path):
        self.clicked.append(path)
        if path == REALM_NEXT_PAGE_PATH and self.page < len(self.pages) - 1:
            self.page += 1
        elif path == REALM_PREV_PAGE_PATH and self.page > 0:
            self.page -= 1
        return True


async def _poll_once(condition, *_args, **_kwargs) -> bool:
    """poll_until with no waiting: ask once, so the reads behind it happen, then say it came true."""
    await condition()
    return True


@contextlib.contextmanager
def _faked(realm_list: _RealmList):
    """Stand *realm_list* in for the game's UI reads, with every wait compressed."""
    with contextlib.ExitStack() as stack:
        for p in (
            patch.object(rl, "POLL_INTERVAL", 0),
            patch_helper("poll_until", new=_poll_once),
            patch_helper("is_visible_by_path", new=AsyncMock(return_value=True)),
            patch_helper("get_text_by_path", new=AsyncMock(side_effect=realm_list.text)),
            patch_helper("is_enabled_by_path", new=AsyncMock(side_effect=realm_list.enabled)),
            patch_helper("click_by_path", new=AsyncMock(side_effect=realm_list.click)),
        ):
            stack.enter_context(p)
        yield


def _machine(favorite: str, **options) -> FakeMachine:
    return FakeMachine(options={cr.OPTION_FAVORITE_REALM: favorite, **options})


_PAGES = [
    ["Ambrose", "Balestrom", "Banshee", "Bartleby", "Bernie", "Blossom", "Centaur"],
    ["Diego", "Drake", "Dryad", "Dworgyn", "Falmea", "Ghoul", "Greyrose"],
    ["Wraith"],
]


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


async def test_no_favorite_set_fails_and_says_where_to_set_one():
    machine = _machine("")

    result = await cr.run(machine, [FakeClient()], {})

    assert result == "FAILURE"
    assert any("Settings" in msg for msg, _lvl in machine.logs)


async def test_an_unknown_realm_fails_before_touching_the_client():
    machine = _machine("Narnia")
    client = FakeClient()

    with patch_helper("click_by_path", new=AsyncMock()) as click:
        result = await cr.run(machine, [client], {})

    assert result == "FAILURE"
    click.assert_not_awaited()
    assert any("Narnia" in msg for msg, _lvl in machine.logs)


async def test_run_fails_if_any_client_fails():
    with patch.object(cr, "to_realm", new=AsyncMock(side_effect=["SUCCESS", "FAILURE"])):
        result = await cr.run(_machine("Wraith"), [FakeClient(), FakeClient()], {})

    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# to_realm
# ---------------------------------------------------------------------------


async def test_hops_to_the_favorite_on_a_later_page():
    """Wraith is on page 3: page right twice, select row 0, Go To Realm, ride out the load."""
    client = FakeClient()
    client.is_loading.side_effect = [True, False]
    realm_list = _RealmList(_PAGES, current="Ambrose")
    machine = _machine("Wraith")

    with _faked(realm_list):
        result = await cr.to_realm(machine, client, "Wraith", 0.0)

    assert result == "SUCCESS"
    assert realm_list.clicked.count(REALM_NEXT_PAGE_PATH) == 2
    assert realm_button_path(0) in realm_list.clicked
    assert REALM_GO_TO_BUTTON_PATH in realm_list.clicked
    assert any("'Wraith'" in msg for msg, _lvl in machine.logs)


async def test_matches_the_row_in_the_clients_own_locale():
    """A German client lists Hagelkorn; the stored key is still Balestrom."""
    client = FakeClient()
    client.is_loading.side_effect = [True, False]
    pages = [["Ambrose", "Hagelkorn", "Klagegeist"]]
    realm_list = _RealmList(pages, current="Ambrose")

    with _faked(realm_list):
        result = await cr.to_realm(_machine("Balestrom"), client, "Balestrom", 0.0)

    assert result == "SUCCESS"
    assert realm_button_path(1) in realm_list.clicked


async def test_already_on_the_favorite_closes_the_spellbook_without_hopping():
    client = FakeClient()
    realm_list = _RealmList(_PAGES, current="Banshee")
    machine = _machine("Banshee")

    with _faked(realm_list):
        result = await cr.to_realm(machine, client, "Banshee", 0.0)

    assert result == "SUCCESS"
    assert REALM_GO_TO_BUTTON_PATH not in realm_list.clicked
    assert realm_list.clicked[-1] == BACKPACK_CLOSE_BUTTON_PATH
    assert any("already on" in msg for msg, _lvl in machine.logs)


async def test_already_on_the_favorite_fails_when_the_spellbook_will_not_close():
    """An open spellbook takes every click the next node sends, so success needs it gone."""
    client = FakeClient()
    realm_list = _RealmList(_PAGES, current="Banshee")
    machine = _machine("Banshee")

    with _faked(realm_list), patch.object(rl, "close_spellbook", new=AsyncMock(return_value=False)):
        result = await cr.to_realm(machine, client, "Banshee", 0.0)

    assert result == "FAILURE"
    assert not any(msg.startswith("SUCCESS") for msg, _lvl in machine.logs)
    assert any("did not close" in msg for msg, _lvl in machine.logs)


async def test_already_on_the_favorite_is_detected_from_txtyourrealmname_without_paging():
    """The current realm is read straight from the settings text, skipping the scan entirely."""
    client = FakeClient()
    realm_list = _RealmList(_PAGES, current="Banshee", current_name_text="Banshee")
    realm_list.page = 2  # a scan would have to page back to find it
    machine = _machine("Banshee")

    with _faked(realm_list):
        result = await cr.to_realm(machine, client, "Banshee", 0.0)

    assert result == "SUCCESS"
    assert REALM_PREV_PAGE_PATH not in realm_list.clicked  # no page-1 anchor
    assert REALM_NEXT_PAGE_PATH not in realm_list.clicked  # no scanning
    assert not any(path == realm_button_path(row) for row in range(rl.REALMS_PER_PAGE) for path in realm_list.clicked)
    assert realm_list.clicked[-1] == BACKPACK_CLOSE_BUTTON_PATH
    assert any("already on" in msg for msg, _lvl in machine.logs)


async def test_anchors_to_page_1_before_scanning():
    """The list opens on whatever page was last visited; a favorite on page 1 must still be found."""
    client = FakeClient()
    client.is_loading.side_effect = [True, False]
    realm_list = _RealmList(_PAGES, current="Wraith")
    realm_list.page = 2

    with _faked(realm_list):
        result = await cr.to_realm(_machine("Ambrose"), client, "Ambrose", 0.0)

    assert result == "SUCCESS"
    assert realm_list.clicked.count(REALM_PREV_PAGE_PATH) == 2
    assert realm_button_path(0) in realm_list.clicked


async def test_a_realm_missing_from_every_page_fails_and_closes_the_spellbook():
    client = FakeClient()
    realm_list = _RealmList(_PAGES, current="Ambrose")
    machine = _machine("Unicorn")

    with _faked(realm_list):
        result = await cr.to_realm(machine, client, "Unicorn", 0.0)

    assert result == "FAILURE"
    assert realm_list.clicked.count(REALM_NEXT_PAGE_PATH) == 2  # every page was looked at
    assert REALM_GO_TO_BUTTON_PATH not in realm_list.clicked
    assert realm_list.clicked[-1] == BACKPACK_CLOSE_BUTTON_PATH
    assert any("not found" in msg for msg, _lvl in machine.logs)


async def test_spellbook_never_opens():
    client = FakeClient()

    with (
        patch.object(rl, "POLL_INTERVAL", 0),
        patch_helper("click_by_path", new=AsyncMock()),
        patch_helper("poll_until", new=AsyncMock(return_value=False)),
    ):
        result = await cr.to_realm(_machine("Wraith"), client, "Wraith", 0.0)

    assert result == "FAILURE"


async def test_realm_hop_timeout_fails():
    """The cooldown never clears: FAILURE within the (compressed) hop timeout."""
    client = FakeClient()
    realm_list = _RealmList(_PAGES, current="Ambrose")
    base_enabled = realm_list.enabled

    async def enabled(c, path):
        if path == REALM_GO_TO_BUTTON_PATH:
            return False
        return await base_enabled(c, path)

    realm_list.enabled = enabled

    with patch.object(rl, "REALM_HOP_TIMEOUT", -1), _faked(realm_list):
        result = await cr.to_realm(_machine("Wraith"), client, "Wraith", 0.0)

    assert result == "FAILURE"
    assert realm_list.clicked[-1] == BACKPACK_CLOSE_BUTTON_PATH


# ---------------------------------------------------------------------------
# The option
# ---------------------------------------------------------------------------


def test_the_node_declares_the_realm_with_no_opinion_of_its_own():
    """Null, always: an author cannot have a view on which realm you farm in."""
    from bartlebot.options import bot_options

    spec = next(o for o in cr.NODE_SPEC.required_bot_options if o.name == cr.OPTION_FAVORITE_REALM)

    assert spec.default is None
    assert spec.type == "realm"
    assert bot_options.is_user_owned(cr.OPTION_FAVORITE_REALM)
    assert bot_options.APP_SETTING_KEYS[cr.OPTION_FAVORITE_REALM] == "favorite_realm"


# ---------------------------------------------------------------------------
# _to_next_realm — targeted failure paths
# ---------------------------------------------------------------------------


async def test_next_realm_spellbook_never_opens():
    """FAILURE when the spellbook does not open (Preferences tab never appears)."""
    client = FakeClient()

    with (
        patch.object(rl, "POLL_INTERVAL", 0),
        patch_helper("click_by_path", new=AsyncMock()),
        patch_helper("poll_until", new=AsyncMock(return_value=False)),
    ):
        result = await cr._to_next_realm(FakeMachine(), client, 3, 5, 0.0)

    assert result == "FAILURE"


async def test_realm_panel_never_opens():
    """FAILURE when the realm panel does not open after navigating to it."""
    client = FakeClient()

    # poll_until calls in order:
    # 1. SPELLBOOK_PREFERENCES_TAB visible → True (spellbook opened)
    # 2. SPELLBOOK_REALMS_BUTTON visible   → True (not a failure gate in the code)
    # 3. REALM_CURRENT_NAME visible        → False (realm panel didn't open) → FAILURE
    with (
        patch.object(rl, "POLL_INTERVAL", 0),
        patch_helper("click_by_path", new=AsyncMock()),
        patch_helper("poll_until", new=AsyncMock(side_effect=[True, True, False])),
    ):
        result = await cr._to_next_realm(FakeMachine(), client, 3, 5, 0.0)

    assert result == "FAILURE"


async def test_realm_hop_timeout():
    """FAILURE when the realm-hop cooldown timer never expires."""
    client = FakeClient()

    async def fake_is_enabled(c, path):
        # GoTo button stays disabled; current realm detected at row 0; prev-page disabled.
        if path == REALM_GO_TO_BUTTON_PATH:
            return False
        if path == realm_button_path(0):
            return False
        return True

    with (
        patch.object(rl, "POLL_INTERVAL", 0),
        patch.object(rl, "REALM_HOP_TIMEOUT", -1),  # -1 ensures elapsed > timeout immediately
        patch.object(rl, "navigate_to_page_1", new=AsyncMock(return_value=True)),
        patch.object(rl, "navigate_pages", new=AsyncMock(return_value=True)),
        patch_helper("click_by_path", new=AsyncMock()),
        patch_helper("poll_until", new=AsyncMock(return_value=True)),
        patch_helper("is_enabled_by_path", side_effect=fake_is_enabled),
        patch_helper("get_text_by_path", new=AsyncMock(return_value="SomeRealm")),
    ):
        result = await cr._to_next_realm(FakeMachine(), client, 3, 5, 0.0)

    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# Dungeon exit warning
# ---------------------------------------------------------------------------


async def _run_realm_change_with_modal(visible_buttons) -> tuple[str, list[list[str]]]:
    """Drive the flow to the Go-To-Realm click and return (result, clicked paths)."""
    client = FakeClient()
    # Tick 1: nothing has happened yet. Tick 2: loading. Then done.
    client.is_loading.side_effect = [False, True, False]
    patches, clicked = message_box_patches(visible_buttons)

    with (
        patch.object(rl, "POLL_INTERVAL", 0),
        patch.object(rl, "navigate_to_page_1", new=AsyncMock(return_value=True)),
        patch.object(rl, "navigate_pages", new=AsyncMock(return_value=True)),
        patch_helper("poll_until", new=AsyncMock(return_value=True)),
        patch_helper("is_enabled_by_path", new=AsyncMock(return_value=True)),
        patch_helper("get_text_by_path", new=AsyncMock(return_value="SomeRealm")),
        patches[0],
        patches[1],
    ):
        result = await cr._to_next_realm(FakeMachine(), client, 3, 5, 0.0)

    return result, clicked


async def test_dungeon_exit_warning_is_confirmed_rather_than_re_clicked():
    """Hopping realms out of a dungeon raises the warning, which blocks the realm change."""
    result, clicked = await _run_realm_change_with_modal((MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))

    assert result == "SUCCESS"
    # The retry tick spent itself on the confirm, not on another Go-To-Realm press.
    assert clicked[-2:] == [REALM_GO_TO_BUTTON_PATH, MESSAGE_BOX_CENTER]


async def test_no_warning_still_re_clicks_go_to_realm():
    """Outside a dungeon the straggler retry must behave exactly as it did before."""
    result, clicked = await _run_realm_change_with_modal(())

    assert result == "SUCCESS"
    assert clicked[-2:] == [REALM_GO_TO_BUTTON_PATH, REALM_GO_TO_BUTTON_PATH]


async def test_refusal_from_inside_a_dungeon_fails_immediately():
    """Verified live: inside a dungeon the game refuses outright rather than asking.

    Re-clicking Go To Realm cannot get past it, so the node must not spend TRIGGER_TIMEOUT
    trying — and the log must name the cause, not report a missing loading screen.
    """
    client = FakeClient()
    client.is_loading.return_value = False
    patches, clicked = message_box_patches((MESSAGE_BOX_CENTER,))
    machine = FakeMachine()

    with (
        patch.object(rl, "POLL_INTERVAL", 0),
        patch.object(rl, "navigate_to_page_1", new=AsyncMock(return_value=True)),
        patch.object(rl, "navigate_pages", new=AsyncMock(return_value=True)),
        patch_helper("poll_until", new=AsyncMock(return_value=True)),
        patch_helper("is_enabled_by_path", new=AsyncMock(return_value=True)),
        patch_helper("identify_modal", new=AsyncMock(return_value=dialog_captions.REALM_NOT_ALLOWED_HERE)),
        patch_helper("get_text_by_path", new=AsyncMock(return_value="SomeRealm")),
        patches[0],
        patches[1],
    ):
        result = await cr._to_next_realm(machine, client, 3, 5, 0.0)

    assert result == "FAILURE"
    assert any("cannot be switched from inside a dungeon" in msg for msg, _lvl in machine.logs)
    # A one-button refusal is dismissed with the center button, and Go To Realm is not
    # re-clicked afterwards.
    assert clicked[-1] == MESSAGE_BOX_CENTER


async def test_dungeon_reset_warning_is_confirmed_and_the_hop_proceeds():
    """Verified live: outside the dungeon, the same action asks instead of refusing."""
    client = FakeClient()
    client.is_loading.side_effect = [False, True, False]
    patches, clicked = message_box_patches((MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT))

    with (
        patch.object(rl, "POLL_INTERVAL", 0),
        patch.object(rl, "navigate_to_page_1", new=AsyncMock(return_value=True)),
        patch.object(rl, "navigate_pages", new=AsyncMock(return_value=True)),
        patch_helper("poll_until", new=AsyncMock(return_value=True)),
        patch_helper("is_enabled_by_path", new=AsyncMock(return_value=True)),
        patch_helper("identify_modal", new=AsyncMock(return_value=dialog_captions.REALM_RESETS_DUNGEON)),
        patch_helper("get_text_by_path", new=AsyncMock(return_value="SomeRealm")),
        patches[0],
        patches[1],
    ):
        result = await cr._to_next_realm(FakeMachine(), client, 3, 5, 0.0)

    assert result == "SUCCESS"
    assert clicked[-1] == MESSAGE_BOX_CENTER


# ---------------------------------------------------------------------------
# The mode
# ---------------------------------------------------------------------------


async def test_favorite_mode_is_the_default():
    """A node with no use_favorite_realm param runs the favorite flow."""
    favorite = AsyncMock(return_value="SUCCESS")
    nxt = AsyncMock(return_value="SUCCESS")
    with patch.object(cr, "to_realm", new=favorite), patch.object(cr, "_to_next_realm", new=nxt):
        result = await cr.run(_machine("Wraith"), [FakeClient()], {})

    assert result == "SUCCESS"
    favorite.assert_awaited_once()
    nxt.assert_not_awaited()


async def test_next_realm_together_moves_the_leader_and_the_others_follow_it():
    """#131: each player taking the realm after its own kept a group together only by coincidence."""
    follow = AsyncMock(return_value="SUCCESS")
    nxt = AsyncMock(return_value="SUCCESS")
    clients = [FakeClient(), FakeClient()]
    with (
        patch.object(cr, "to_realm", new=follow),
        patch.object(cr, "_to_next_realm", new=nxt),
        patch.object(rl, "read_current_realm", new=AsyncMock(return_value="Wraith")),
    ):
        result = await cr.run(FakeMachine(), clients, {cr.PARAM_USE_FAVORITE_REALM: False})

    assert result == "SUCCESS"
    nxt.assert_awaited_once()
    assert nxt.await_args.args[1] is clients[0]
    assert nxt.await_args.args[2:4] == (cr.NEXT_REALM_FIRST_PAGE, cr.NEXT_REALM_LAST_PAGE)
    follow.assert_awaited_once()
    assert follow.await_args.args[1:3] == (clients[1], "Wraith")


async def test_next_realm_together_fails_when_the_leaders_realm_cannot_be_read():
    follow = AsyncMock(return_value="SUCCESS")
    with (
        patch.object(cr, "to_realm", new=follow),
        patch.object(cr, "_to_next_realm", new=AsyncMock(return_value="SUCCESS")),
        patch.object(rl, "read_current_realm", new=AsyncMock(return_value=None)),
    ):
        result = await cr.run(FakeMachine(), [FakeClient(), FakeClient()], {cr.PARAM_USE_FAVORITE_REALM: False})

    assert result == "FAILURE"
    follow.assert_not_awaited()


@pytest.mark.parametrize(
    ("positions", "targets"),
    [
        ([0, 1, 2, 3], [4, 5, 6, 7]),  # the reported bug: this gave 1, 3, 5, 7
        ([12, 12, 12, 12], [13, 14, 15, 16]),  # one realm fans out
        ([19, 20, 0, 1], [2, 3, 4, 5]),  # a block that wrapped
        ([None, None, None, None], [0, 1, 2, 3]),  # nobody in the range, e.g. after a favorite on page 1
        ([4, 5, 6, 3], [7, 8, 9, 10]),  # player 4's last hop failed
        ([17, 18, 19, 20], [0, 1, 2, 3]),  # the end of the range wraps to its start
        ([5, None], [6, 7]),  # a player outside the range holds no row
    ],
)
def test_separate_next_realms_are_one_block_after_everyone(positions, targets):
    assert cr.next_block(positions) == targets


def test_the_next_realm_ring_is_pages_3_to_5():
    assert cr.NEXT_REALM_RING == 21


async def test_next_realm_separate_finds_everyone_before_anyone_hops():
    """#131: each player hopping from its own realm doubled a spread start's spacing."""
    order: list[str] = []
    rows = iter([0, 1, 2, 3])

    async def find(_machine, client, *_args):
        order.append("find")
        return cr._Place(next(rows), 3, "")

    async def hop(_machine, client, _place, target, _start):
        order.append("hop")
        return "SUCCESS"

    hop_mock = AsyncMock(side_effect=hop)
    clients = [FakeClient(), FakeClient(), FakeClient(), FakeClient()]
    machine = FakeMachine(options={"Player Realms": "Separate"})
    with patch.object(cr, "_find_place", new=find), patch.object(cr, "_hop_in_range", new=hop_mock):
        result = await cr.run(machine, clients, {cr.PARAM_USE_FAVORITE_REALM: False})

    assert result == "SUCCESS"
    assert order == ["find"] * 4 + ["hop"] * 4
    assert [(call.args[1], call.args[3]) for call in hop_mock.await_args_list] == [
        (clients[0], 4),
        (clients[1], 5),
        (clients[2], 6),
        (clients[3], 7),
    ]


async def test_next_realm_separate_moves_nobody_when_a_player_cannot_be_found():
    """The block depends on every player's row; guessing one risks landing two players together."""
    places = iter([cr._Place(0, 3, ""), None])
    hop = AsyncMock(return_value="SUCCESS")
    close = AsyncMock()
    clients = [FakeClient(), FakeClient()]
    machine = FakeMachine(options={"Player Realms": "Separate"})
    with (
        patch.object(cr, "_find_place", new=AsyncMock(side_effect=lambda *_a: next(places))),
        patch.object(cr, "_hop_in_range", new=hop),
        patch.object(rl, "close_spellbook", new=close),
    ):
        result = await cr.run(machine, clients, {cr.PARAM_USE_FAVORITE_REALM: False})

    assert result == "FAILURE"
    hop.assert_not_awaited()
    close.assert_awaited_once_with(clients[0])  # the one left open; the other closed its own


async def test_favorite_separate_puts_player_1_on_the_favorite_and_the_rest_after_it():
    favorite = AsyncMock(return_value="SUCCESS")
    after = AsyncMock(return_value="SUCCESS")
    clients = [FakeClient(), FakeClient(), FakeClient()]
    for client in clients:
        see_each_other(client)
    machine = _machine("Balestrom", **{"Player Realms": "Separate"})
    with patch.object(cr, "to_realm", new=favorite), patch.object(cr, "_to_realm_after", new=after):
        result = await cr.run(machine, clients, {})

    assert result == "SUCCESS"
    assert favorite.await_args.args[1:3] == (clients[0], "Balestrom")
    assert [(call.args[1], call.args[3]) for call in after.await_args_list] == [(clients[1], 1), (clients[2], 2)]


async def test_separate_players_who_land_together_are_reported_not_failed():
    clients = [FakeClient(), FakeClient()]
    machine = FakeMachine(options={"Player Realms": "Separate"})
    with patch.object(cr, "_spread_to_next_realms", new=AsyncMock(return_value="SUCCESS")):
        result = await cr.run(machine, clients, {cr.PARAM_USE_FAVORITE_REALM: False})

    assert result == "SUCCESS"
    assert any("landed on one realm" in msg for msg, _ in machine.logs)


async def test_together_players_are_checked_and_gathered_after_the_change():
    ensure = AsyncMock(return_value=nh.TogetherOutcome.FAILED)
    with (
        patch.object(cr, "to_realm", new=AsyncMock(return_value="SUCCESS")),
        patch.object(nh, "ensure_together", ensure),
    ):
        result = await cr.run(_machine("Balestrom"), [FakeClient(), FakeClient()], {})

    assert result == "FAILURE"
    assert ensure.await_args.kwargs["where"] == "after the realm change"


_RANGE_PAGES = [["x"] * 7, ["y"] * 7] + [[f"r{p}{r}" for r in range(7)] for p in (3, 4, 5)]


@pytest.mark.parametrize(
    ("current", "page", "row"),
    [
        ("r36", 3, 0),  # the last row of page 3 carries onto page 4 (0-based page 3)
        ("r56", 2, 0),  # the last row of the range wraps to page 3
        ("x0", 2, 0),  # a realm outside the range starts it
    ],
)
async def test_the_next_realm_counts_across_the_ranges_pages_and_wraps(current, page, row):
    realm_list = _RealmList(_RANGE_PAGES, current=current)
    client = FakeClient()
    client.is_loading.side_effect = [True, False]

    with _faked(realm_list):
        result = await cr._to_next_realm(FakeMachine(), client, 3, 5, 0.0)

    assert result == "SUCCESS"
    assert realm_list.page == page
    assert realm_button_path(row) in realm_list.clicked


async def test_a_spread_hop_pages_from_where_the_scan_stopped():
    """Current on page 3 row 5; row 8 of the range is page 4 row 1."""
    realm_list = _RealmList(_RANGE_PAGES, current="r35")
    client = FakeClient()
    client.is_loading.side_effect = [True, False]

    with _faked(realm_list):
        place = await cr._find_place(FakeMachine(), client, 3, 5, 0.0)
        result = await cr._hop_in_range(FakeMachine(), client, place, 8, 3)

    assert place == cr._Place(5, 3, "r35")
    assert result == "SUCCESS"
    assert realm_list.page == 3  # 0-based: page 4
    assert realm_button_path(1) in realm_list.clicked


async def test_the_realm_n_rows_after_the_favorite_is_taken_across_a_page_break():
    realm_list = _RealmList(_PAGES, current="Wraith")
    client = FakeClient()
    client.is_loading.side_effect = [True, False]

    with _faked(realm_list):
        result = await cr._to_realm_after(FakeMachine(), client, "Centaur", 2, 0.0)

    assert result == "SUCCESS"
    assert realm_list.page == 1
    assert realm_button_path(1) in realm_list.clicked  # Diego, Drake: Drake


async def test_next_realm_mode_needs_no_favorite_realm():
    """An unset Favorite Realm only fails the mode that reads it."""
    with patch.object(cr, "_to_next_realm", new=AsyncMock(return_value="SUCCESS")):
        result = await cr.run(_machine(""), [FakeClient()], {cr.PARAM_USE_FAVORITE_REALM: False})

    assert result == "SUCCESS"


async def test_next_realm_mode_fails_if_any_client_fails():
    with patch.object(cr, "_to_next_realm", new=AsyncMock(side_effect=["SUCCESS", "FAILURE"])):
        result = await cr.run(FakeMachine(), [FakeClient(), FakeClient()], {cr.PARAM_USE_FAVORITE_REALM: False})

    assert result == "FAILURE"


async def test_a_client_raising_fails_the_node_rather_than_the_machine():
    with patch.object(cr, "_to_next_realm", new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = await cr.run(FakeMachine(), [FakeClient()], {cr.PARAM_USE_FAVORITE_REALM: False})

    assert result == "FAILURE"


def _one_state_bot(params: dict) -> BotConfig:
    return BotConfig(
        id="realm-bot",
        name="Realm Bot",
        initial="a",
        recovery_state="a",
        states=[StateModel(id="a", node_type="change_realm", params=params)],
        transitions=[],
    )


def test_setup_offers_favorite_realm_only_to_a_bot_using_favorite_mode():
    on = bot_options.required_option_specs(_one_state_bot({cr.PARAM_USE_FAVORITE_REALM: True}))
    off = bot_options.required_option_specs(_one_state_bot({cr.PARAM_USE_FAVORITE_REALM: False}))

    assert cr.OPTION_FAVORITE_REALM in on
    assert cr.OPTION_FAVORITE_REALM not in off
    assert "Safety Delay" in off


def test_the_page_range_is_no_longer_a_param():
    assert [f.key for f in cr.PARAM_SCHEMA] == [cr.PARAM_USE_FAVORITE_REALM]


# ---------------------------------------------------------------------------
# realm_list.read_current_realm -- which realm the leader is on (#133)
# ---------------------------------------------------------------------------


async def test_the_current_realm_is_read_from_txtyourrealmname_without_paging():
    realm_list = _RealmList(_PAGES, current="Drake", current_name_text="Drake")
    realm_list.page = 2

    with _faked(realm_list):
        key = await rl.read_current_realm(FakeMachine(), FakeClient(), 0.0, "test")

    assert key == "Drake"
    assert REALM_PREV_PAGE_PATH not in realm_list.clicked
    assert realm_list.clicked[-1] == BACKPACK_CLOSE_BUTTON_PATH


async def test_a_blank_txtyourrealmname_is_unknown():
    realm_list = _RealmList(_PAGES, current="Drake")

    with _faked(realm_list):
        assert await rl.read_current_realm(FakeMachine(), FakeClient(), 0.0, "test") is None

    assert realm_list.clicked[-1] == BACKPACK_CLOSE_BUTTON_PATH


async def test_a_realm_read_whose_spellbook_will_not_close_is_unknown():
    realm_list = _RealmList(_PAGES, current="Drake", current_name_text="Drake")

    with _faked(realm_list), patch.object(rl, "close_spellbook", new=AsyncMock(return_value=False)):
        assert await rl.read_current_realm(FakeMachine(), FakeClient(), 0.0, "test") is None


async def test_a_spellbook_that_never_opens_is_unknown():
    with (
        patch.object(rl, "open_realm_list", new=AsyncMock(return_value=False)),
        patch.object(rl, "close_spellbook", new=AsyncMock()) as close,
    ):
        assert await rl.read_current_realm(FakeMachine(), FakeClient(), 0.0, "test") is None
    close.assert_not_awaited()  # open_realm_list closes what it opened on its own failures
