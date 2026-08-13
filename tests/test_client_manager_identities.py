"""Tests for character-switch detection in the client loop.

``ClientManager._refresh_wizard_identities`` is what keeps a cached wizard name from
outliving the character it belongs to. It never touches ``self``, so it is exercised
unbound rather than by constructing the whole QObject and its handler.
"""

from unittest.mock import AsyncMock, patch

import pytest

import bartlebot.wizard_names as wn
from bartlebot.client_manager import ClientManager
from tests.conftest import FakeClient


@pytest.fixture(autouse=True)
def _clear_cache():
    wn.clear_cache()
    yield
    wn.clear_cache()


async def _refresh(clients):
    """Call the method without building a ClientManager — it uses no instance state."""
    await ClientManager._refresh_wizard_identities(None, clients)


def _set_character_id(client: FakeClient, character_id: int) -> None:
    client.client_object.read_value_from_offset = AsyncMock(return_value=character_id)


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


async def test_never_raises_into_the_client_loop():
    """An exception here would kill hook lifecycle and crash recovery, not just naming."""
    with patch.object(wn, "refresh_identities", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await _refresh([FakeClient()])


async def test_empty_client_list_is_harmless():
    await _refresh([])
