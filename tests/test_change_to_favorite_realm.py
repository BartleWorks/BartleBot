"""Unit tests for the change_to_favorite_realm node.

The realm list is faked as pages of names: ``get_text_by_path`` answers by row from the
page currently showing, the right arrow's enabled state says whether there is another
page, and the current realm is the one whose button reads disabled -- exactly the three
reads the node relies on.
"""

import contextlib
from unittest.mock import AsyncMock, patch

import bartlebot.nodes.node_helpers as nh
import bartlebot.nodes.utility.change_to_favorite_realm as cf
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
from tests.conftest import FakeClient, FakeMachine


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


@contextlib.contextmanager
def _faked(realm_list: _RealmList):
    """Stand *realm_list* in for the game's UI reads, with every wait compressed."""
    with contextlib.ExitStack() as stack:
        for p in (
            patch.object(rl, "POLL_INTERVAL", 0),
            patch.object(nh, "poll_until", new=AsyncMock(return_value=True)),
            patch.object(nh, "is_visible_by_path", new=AsyncMock(return_value=True)),
            patch.object(nh, "get_text_by_path", new=AsyncMock(side_effect=realm_list.text)),
            patch.object(nh, "is_enabled_by_path", new=AsyncMock(side_effect=realm_list.enabled)),
            patch.object(nh, "click_by_path", new=AsyncMock(side_effect=realm_list.click)),
        ):
            stack.enter_context(p)
        yield


def _machine(favorite: str, **options) -> FakeMachine:
    return FakeMachine(options={cf.OPTION_FAVORITE_REALM: favorite, **options})


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

    result = await cf.run(machine, [FakeClient()], {})

    assert result == "FAILURE"
    assert any("Settings" in msg for msg, _lvl in machine.logs)


async def test_an_unknown_realm_fails_before_touching_the_client():
    machine = _machine("Narnia")
    client = FakeClient()

    with patch.object(nh, "click_by_path", new=AsyncMock()) as click:
        result = await cf.run(machine, [client], {})

    assert result == "FAILURE"
    click.assert_not_awaited()
    assert any("Narnia" in msg for msg, _lvl in machine.logs)


async def test_run_fails_if_any_client_fails():
    with patch.object(cf, "_change_realm_for_client", new=AsyncMock(side_effect=["SUCCESS", "FAILURE"])):
        result = await cf.run(_machine("Wraith"), [FakeClient(), FakeClient()], {})

    assert result == "FAILURE"


# ---------------------------------------------------------------------------
# _change_realm_for_client
# ---------------------------------------------------------------------------


async def test_hops_to_the_favorite_on_a_later_page():
    """Wraith is on page 3: page right twice, select row 0, Go To Realm, ride out the load."""
    client = FakeClient()
    client.is_loading.side_effect = [True, False]
    realm_list = _RealmList(_PAGES, current="Ambrose")
    machine = _machine("Wraith")

    with _faked(realm_list):
        result = await cf._change_realm_for_client(machine, client, "Wraith", 0.0)

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
        result = await cf._change_realm_for_client(_machine("Balestrom"), client, "Balestrom", 0.0)

    assert result == "SUCCESS"
    assert realm_button_path(1) in realm_list.clicked


async def test_already_on_the_favorite_closes_the_spellbook_without_hopping():
    client = FakeClient()
    realm_list = _RealmList(_PAGES, current="Banshee")
    machine = _machine("Banshee")

    with _faked(realm_list):
        result = await cf._change_realm_for_client(machine, client, "Banshee", 0.0)

    assert result == "SUCCESS"
    assert REALM_GO_TO_BUTTON_PATH not in realm_list.clicked
    assert realm_list.clicked[-1] == BACKPACK_CLOSE_BUTTON_PATH
    assert any("already on" in msg for msg, _lvl in machine.logs)


async def test_already_on_the_favorite_is_detected_from_txtyourrealmname_without_paging():
    """The current realm is read straight from the settings text, skipping the scan entirely."""
    client = FakeClient()
    realm_list = _RealmList(_PAGES, current="Banshee", current_name_text="Banshee")
    machine = _machine("Banshee")

    with _faked(realm_list):
        result = await cf._change_realm_for_client(machine, client, "Banshee", 0.0)

    assert result == "SUCCESS"
    assert REALM_PREV_PAGE_PATH not in realm_list.clicked  # no page-1 anchor
    assert REALM_NEXT_PAGE_PATH not in realm_list.clicked  # no scanning
    assert not any(path == realm_button_path(row) for row in range(rl.REALMS_PER_PAGE) for path in realm_list.clicked)
    assert realm_list.clicked[-1] == BACKPACK_CLOSE_BUTTON_PATH
    assert any("already on" in msg for msg, _lvl in machine.logs)


async def test_anchors_to_page_1_before_scanning():
    """The list opens on whatever page was last visited; a favourite on page 1 must still be found."""
    client = FakeClient()
    client.is_loading.side_effect = [True, False]
    realm_list = _RealmList(_PAGES, current="Wraith")
    realm_list.page = 2

    with _faked(realm_list):
        result = await cf._change_realm_for_client(_machine("Ambrose"), client, "Ambrose", 0.0)

    assert result == "SUCCESS"
    assert realm_list.clicked.count(REALM_PREV_PAGE_PATH) == 2
    assert realm_button_path(0) in realm_list.clicked


async def test_a_realm_missing_from_every_page_fails_and_closes_the_spellbook():
    client = FakeClient()
    realm_list = _RealmList(_PAGES, current="Ambrose")
    machine = _machine("Unicorn")

    with _faked(realm_list):
        result = await cf._change_realm_for_client(machine, client, "Unicorn", 0.0)

    assert result == "FAILURE"
    assert realm_list.clicked.count(REALM_NEXT_PAGE_PATH) == 2  # every page was looked at
    assert REALM_GO_TO_BUTTON_PATH not in realm_list.clicked
    assert realm_list.clicked[-1] == BACKPACK_CLOSE_BUTTON_PATH
    assert any("not found" in msg for msg, _lvl in machine.logs)


async def test_spellbook_never_opens():
    client = FakeClient()

    with (
        patch.object(rl, "POLL_INTERVAL", 0),
        patch.object(nh, "click_by_path", new=AsyncMock()),
        patch.object(nh, "poll_until", new=AsyncMock(return_value=False)),
    ):
        result = await cf._change_realm_for_client(_machine("Wraith"), client, "Wraith", 0.0)

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
        result = await cf._change_realm_for_client(_machine("Wraith"), client, "Wraith", 0.0)

    assert result == "FAILURE"
    assert realm_list.clicked[-1] == BACKPACK_CLOSE_BUTTON_PATH


# ---------------------------------------------------------------------------
# The option
# ---------------------------------------------------------------------------


def test_the_node_declares_the_realm_with_no_opinion_of_its_own():
    """Null, always: an author cannot have a view on which realm you farm in."""
    from bartlebot import bot_options

    spec = next(o for o in cf.NODE_SPEC.required_bot_options if o.name == cf.OPTION_FAVORITE_REALM)

    assert spec.default is None
    assert spec.type == "realm"
    assert bot_options.is_user_owned(cf.OPTION_FAVORITE_REALM)
    assert bot_options.APP_SETTING_KEYS[cf.OPTION_FAVORITE_REALM] == "favorite_realm"
