"""Tests for wizard-name upkeep in the client loop.

``ClientManager._refresh_wizard_names`` keeps a cached name from outliving the character it
belongs to, fills in names from memory for display, and announces changes. It touches only
two attributes of ``self``, so it is exercised against a stub rather than by constructing
the whole QObject and its handler.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bartlebot.wizard_names as wn
from bartlebot.client_manager import ClientManager
from tests.conftest import FakeClient

# "Luke SkullHunter" in the shipped tables -- synthetic, see CLAUDE.md "No real player data".
LUKE_KEYS = 56900648
LUKE = "Luke SkullHunter"


@pytest.fixture(autouse=True)
def _clear_cache():
    wn.clear_cache()
    yield
    wn.clear_cache()


class _Stub:
    """The two attributes _refresh_wizard_names uses on self."""

    def __init__(self):
        self._last_wizard_names: list = []
        self.wizard_names_changed = MagicMock()

    @property
    def emitted(self) -> list:
        """The names from the most recent emit, or None if it never fired."""
        calls = self.wizard_names_changed.emit.call_args_list
        return calls[-1].args[0] if calls else None


async def _refresh(clients, stub=None) -> _Stub:
    stub = stub or _Stub()
    await ClientManager._refresh_wizard_names(stub, clients)
    return stub


def _set_character_id(client: FakeClient, character_id: int) -> None:
    client.client_object.read_value_from_offset = AsyncMock(return_value=character_id)


def _with_name_behavior(client: FakeClient, name_keys: int = LUKE_KEYS, gender: int = 1) -> FakeClient:
    behavior = MagicMock()
    behavior.behavior_name = AsyncMock(return_value="WizPlayerNameBehavior")
    behavior.read_value_from_offset = AsyncMock(
        side_effect=lambda offset, _p: {wn.NAME_KEYS_OFFSET: name_keys, wn.GENDER_OFFSET: gender}[offset]
    )
    client.client_object.inactive_behaviors = AsyncMock(return_value=[behavior])
    return client


# ------------------------------------------------------------------ switch detection


async def test_switch_clears_the_cached_name():
    client = FakeClient()
    _set_character_id(client, 111)
    wn.remember(client, "Wolf Deathblade", 111)

    _set_character_id(client, 222)
    await _refresh([client])
    assert wn.cached_name(client) is None


async def test_no_switch_keeps_the_cached_name():
    client = FakeClient()
    _set_character_id(client, 111)
    wn.remember(client, "Wolf Deathblade", 111)

    await _refresh([client])
    assert wn.cached_name(client) == "Wolf Deathblade"


async def test_loading_screen_keeps_the_cached_name():
    """Runs once a second, so treating an unreadable id as a switch would thrash the cache."""
    client = FakeClient()
    _set_character_id(client, 111)
    wn.remember(client, "Wolf Deathblade", 111)

    _set_character_id(client, wn.NO_CHARACTER)
    await _refresh([client])
    assert wn.cached_name(client) == "Wolf Deathblade"


async def test_one_clients_switch_does_not_touch_another():
    a, b = FakeClient(), FakeClient()
    _set_character_id(a, 111)
    _set_character_id(b, 222)
    wn.remember(a, "Wolf Deathblade", 111)
    wn.remember(b, "Fire Stormblade", 222)

    _set_character_id(a, 999)
    await _refresh([a, b])
    assert wn.cached_name(a) is None
    assert wn.cached_name(b) == "Fire Stormblade"


# ------------------------------------------------------------------ filling from memory


async def test_fills_an_unknown_name_from_memory():
    client = _with_name_behavior(FakeClient())
    _set_character_id(client, 111)
    await _refresh([client])
    assert wn.cached_name(client) == LUKE


async def test_never_opens_the_character_page():
    """A sidebar label is not worth flashing the game UI on a live client."""
    client = FakeClient()  # no name behavior, so memory cannot answer
    _set_character_id(client, 111)
    await _refresh([client])
    assert client.send_key.await_count == 0
    assert wn.cached_name(client) is None


async def test_an_unresolvable_name_is_not_retried_every_tick():
    """The behavior walk is ~38 reads; repeating it once a second for nothing is not free."""
    client = _with_name_behavior(FakeClient(), name_keys=0x03FF01FF)  # past the end of the tables
    _set_character_id(client, 111)

    stub = _Stub()
    await _refresh([client], stub)
    first = client.client_object.inactive_behaviors.await_count
    await _refresh([client], stub)
    assert client.client_object.inactive_behaviors.await_count == first, "should not re-walk"


async def test_a_switch_retries_a_previously_unresolvable_client():
    """Swapping to a character the tables do know must resolve, not stay stuck on the miss."""
    client = _with_name_behavior(FakeClient(), name_keys=0x03FF01FF)
    _set_character_id(client, 111)
    await _refresh([client])
    assert wn.cached_name(client) is None

    _with_name_behavior(client)  # now a resolvable character
    _set_character_id(client, 222)
    await _refresh([client])
    assert wn.cached_name(client) == LUKE


# ------------------------------------------------------------------ the signal


async def test_emits_names_when_one_is_learned():
    client = _with_name_behavior(FakeClient())
    _set_character_id(client, 111)
    stub = await _refresh([client])
    assert stub.emitted == [LUKE]


async def test_does_not_emit_again_when_nothing_changed():
    client = _with_name_behavior(FakeClient())
    _set_character_id(client, 111)
    stub = await _refresh([client])
    before = stub.wizard_names_changed.emit.call_count
    await _refresh([client], stub)
    assert stub.wizard_names_changed.emit.call_count == before, "signal should fire on change only"


async def test_emits_none_after_a_switch_clears_a_name():
    """The tile must drop the old wizard's name rather than keep showing it."""
    client = FakeClient()
    _set_character_id(client, 111)
    wn.remember(client, "Wolf Deathblade", 111)
    stub = _Stub()
    stub._last_wizard_names = ["Wolf Deathblade"]

    _set_character_id(client, 222)
    await _refresh([client], stub)
    assert stub.emitted == [None]


# ------------------------------------------------------------------ robustness


async def test_never_raises_into_the_client_loop():
    """An exception here would kill hook lifecycle and crash recovery, not just naming."""
    with patch.object(wn, "fill_names_from_memory", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await _refresh([FakeClient()])


async def test_empty_client_list_is_harmless():
    await _refresh([])
