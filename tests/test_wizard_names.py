"""Unit tests for wizard name detection and caching."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bartlebot.wizard_names as wn
from tests.conftest import FakeClient


@pytest.fixture(autouse=True)
def _clear_cache():
    """The name cache is module-level state, so it must not leak between tests."""
    wn.clear_cache()
    yield
    wn.clear_cache()


def _title_window(text: str | None) -> MagicMock:
    """Build a TitleScroll window whose single child carries `text`."""
    child = MagicMock()
    child.maybe_text = AsyncMock(return_value=text)
    window = MagicMock()
    window.children = AsyncMock(return_value=[child])
    return window


def _client_with_title(text: str | None) -> FakeClient:
    c = FakeClient()
    c.root_window.get_windows_with_name = AsyncMock(return_value=[_title_window(text)])
    return c


def _client_without_page() -> FakeClient:
    c = FakeClient()
    c.root_window.get_windows_with_name = AsyncMock(return_value=[])
    return c


# ---------------------------------------------------------------------------
# Markup stripping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<Color;255,255,255>Wolf Deathblade</Color>", "Wolf Deathblade"),
        ("<center>Iridian Spellthief</center>", "Iridian Spellthief"),
        ("Plain Name", "Plain Name"),
        ("<Color;FFFFFFFF>  Spaced   Out  </Color>", "Spaced Out"),
        ("", ""),
        (None, ""),
    ],
)
def test_strip_markup(raw, expected):
    assert wn.strip_markup(raw) == expected


def test_strip_markup_handles_attribute_changes():
    """A fixed [8:-9] slice (as Deimos uses) breaks when the tag length changes."""
    assert wn.strip_markup("<Color;12,34,56;extra>Name Here</Color>") == "Name Here"


# ---------------------------------------------------------------------------
# detect_wizard_name
# ---------------------------------------------------------------------------


async def test_detect_reads_and_cleans_the_title():
    client = _client_with_title("<Color;FFFFFFFF>Wolf Deathblade</Color>")

    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0):
        assert await wn.detect_wizard_name(client) == "Wolf Deathblade"


async def test_detect_closes_the_character_page():
    """Two C presses: one to open, one to close."""
    client = _client_with_title("Wolf Deathblade")

    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0):
        await wn.detect_wizard_name(client)

    assert client.send_key.await_count == 2


async def test_detect_closes_the_page_even_when_reading_fails():
    """Leaving the character page open would obscure the HUD for every later node."""
    client = _client_without_page()

    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0), patch.object(wn, "TRIGGER_TIMEOUT", 0):
        assert await wn.detect_wizard_name(client) is None

    assert client.send_key.await_count == 2


async def test_detect_returns_none_when_the_page_never_appears():
    client = _client_without_page()

    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0), patch.object(wn, "TRIGGER_TIMEOUT", 0):
        assert await wn.detect_wizard_name(client) is None


async def test_detect_returns_none_on_empty_title():
    client = _client_with_title("")

    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0), patch.object(wn, "TRIGGER_TIMEOUT", 0):
        assert await wn.detect_wizard_name(client) is None


async def test_detect_never_raises_on_a_broken_client():
    client = FakeClient()
    client.send_key = AsyncMock(side_effect=RuntimeError("client is gone"))

    assert await wn.detect_wizard_name(client) is None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


async def test_resolve_names_detects_once_and_caches():
    client = _client_with_title("Wolf Deathblade")

    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0):
        first = await wn.resolve_names([client])
        second = await wn.resolve_names([client])

    assert first == ["Wolf Deathblade"]
    assert second == ["Wolf Deathblade"]
    # Detection ran only for the first call (2 keypresses, not 4).
    assert client.send_key.await_count == 2


async def test_resolve_names_keeps_positional_alignment_with_unknowns():
    known = _client_with_title("Wolf Deathblade")
    unknown = _client_without_page()

    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0), patch.object(wn, "TRIGGER_TIMEOUT", 0):
        names = await wn.resolve_names([known, unknown])

    assert names == ["Wolf Deathblade", None]


async def test_resolve_names_detects_concurrently():
    """Every client must reach the page read before any of them finishes.

    The barrier only releases once all three arrive, so a sequential implementation
    deadlocks here and trips the timeout rather than quietly costing three settles.
    """
    barrier = asyncio.Barrier(3)

    def _barriered_client() -> FakeClient:
        c = FakeClient()

        async def _get_windows(_name):
            await barrier.wait()
            return [_title_window("Wolf Deathblade")]

        c.root_window.get_windows_with_name = AsyncMock(side_effect=_get_windows)
        return c

    clients = [_barriered_client() for _ in range(3)]

    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0):
        names = await asyncio.wait_for(wn.resolve_names(clients), timeout=5)

    assert names == ["Wolf Deathblade"] * 3


async def test_resolve_names_only_detects_the_uncached_clients():
    cached = _client_with_title("Wolf Deathblade")
    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0):
        await wn.resolve_names([cached])

    fresh = _client_with_title("Iridian Spellthief")
    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0):
        names = await wn.resolve_names([cached, fresh])

    assert names == ["Wolf Deathblade", "Iridian Spellthief"]
    # The cached client was not re-opened: still just its original two keypresses.
    assert cached.send_key.await_count == 2
    assert fresh.send_key.await_count == 2


async def test_a_relaunched_client_is_redetected():
    """A recovered client has a new window handle, so its name is confirmed afresh."""
    original = _client_with_title("Wolf Deathblade")
    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0):
        await wn.resolve_names([original])

    relaunched = _client_with_title("Wolf Deathblade")
    assert wn.cached_name(relaunched) is None

    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0):
        assert await wn.resolve_names([relaunched]) == ["Wolf Deathblade"]
    assert relaunched.send_key.await_count == 2


def test_remember_ignores_empty_names():
    client = FakeClient()
    wn.remember(client, "")
    assert wn.cached_name(client) is None


# ---------------------------------------------------------------------------
# character switch detection
# ---------------------------------------------------------------------------


def _set_character_id(client: FakeClient, character_id: int) -> None:
    client.client_object.read_value_from_offset = AsyncMock(return_value=character_id)


async def test_read_character_id_returns_the_value():
    client = FakeClient()
    _set_character_id(client, 12345)
    assert await wn.read_character_id(client) == 12345


async def test_read_character_id_survives_a_failed_read():
    client = FakeClient()
    client.client_object.read_value_from_offset = AsyncMock(side_effect=RuntimeError("boom"))
    assert await wn.read_character_id(client) == wn.NO_CHARACTER


async def test_switching_character_drops_the_cached_name():
    client = FakeClient()
    _set_character_id(client, 111)
    wn.remember(client, "Wolf Deathblade", 111)

    _set_character_id(client, 222)
    assert await wn.refresh_identity(client) == 222
    assert wn.cached_name(client) is None, "the name belonged to the previous character"


async def test_staying_on_the_same_character_keeps_the_cached_name():
    client = FakeClient()
    _set_character_id(client, 111)
    wn.remember(client, "Wolf Deathblade", 111)

    await wn.refresh_identity(client)
    assert wn.cached_name(client) == "Wolf Deathblade"


async def test_character_select_does_not_drop_the_cached_name():
    """A zero id means character select or mid-login, not a switch to a different wizard."""
    client = FakeClient()
    _set_character_id(client, 111)
    wn.remember(client, "Wolf Deathblade", 111)

    _set_character_id(client, wn.NO_CHARACTER)
    assert await wn.refresh_identity(client) == wn.NO_CHARACTER
    assert wn.cached_name(client) == "Wolf Deathblade"


async def test_an_unreadable_id_does_not_drop_the_cached_name():
    """A loading screen must not throw away a good name once per client-loop cycle."""
    client = FakeClient()
    _set_character_id(client, 111)
    wn.remember(client, "Wolf Deathblade", 111)

    client.client_object.read_value_from_offset = AsyncMock(side_effect=RuntimeError("boom"))
    await wn.refresh_identity(client)
    assert wn.cached_name(client) == "Wolf Deathblade"


async def test_a_name_cached_without_an_id_adopts_the_live_one():
    """capture_from_combat has no id to give; that must not read as a switch."""
    client = FakeClient()
    _set_character_id(client, 111)
    wn.remember(client, "Wolf Deathblade")  # no character_id
    assert wn.cached_character_id(client) == wn.NO_CHARACTER

    await wn.refresh_identity(client)
    assert wn.cached_name(client) == "Wolf Deathblade"
    assert wn.cached_character_id(client) == 111, "adopting the id makes the next switch detectable"


async def test_adopted_id_makes_the_following_switch_detectable():
    client = FakeClient()
    _set_character_id(client, 111)
    wn.remember(client, "Wolf Deathblade")
    await wn.refresh_identity(client)

    _set_character_id(client, 222)
    await wn.refresh_identity(client)
    assert wn.cached_name(client) is None


async def test_refresh_identity_on_an_uncached_client_is_harmless():
    client = FakeClient()
    _set_character_id(client, 111)
    assert await wn.refresh_identity(client) == 111
    assert wn.cached_name(client) is None


async def test_refresh_identities_is_positionally_aligned():
    a, b = FakeClient(), FakeClient()
    _set_character_id(a, 111)
    _set_character_id(b, 222)
    assert await wn.refresh_identities([a, b]) == [111, 222]


async def test_resolve_names_redetects_after_a_character_switch():
    """The whole point: a switch must not be answered from a cache naming the old wizard."""
    client = _client_with_title("Wolf Deathblade")
    _set_character_id(client, 111)
    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0):
        assert await wn.resolve_names([client]) == ["Wolf Deathblade"]
    assert client.send_key.await_count == 2

    client.root_window.get_windows_with_name = AsyncMock(return_value=[_title_window("Fire Stormblade")])
    _set_character_id(client, 222)
    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0):
        assert await wn.resolve_names([client]) == ["Fire Stormblade"]
    assert client.send_key.await_count == 4, "the switch forced a fresh character-page read"


async def test_resolve_names_records_the_character_id_it_detected():
    client = _client_with_title("Wolf Deathblade")
    _set_character_id(client, 111)
    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0):
        await wn.resolve_names([client])
    assert wn.cached_character_id(client) == 111


async def test_resolve_names_still_caches_across_calls_on_one_character():
    client = _client_with_title("Wolf Deathblade")
    _set_character_id(client, 111)
    with patch.object(wn, "CHARACTER_PAGE_SETTLE", 0):
        await wn.resolve_names([client])
        await wn.resolve_names([client])
    assert client.send_key.await_count == 2, "no re-detection while the character is unchanged"


# ---------------------------------------------------------------------------
# capture_from_combat
# ---------------------------------------------------------------------------


def _member(*, is_client: bool, name: str) -> MagicMock:
    m = MagicMock()
    m.is_client = AsyncMock(return_value=is_client)
    m.name = AsyncMock(return_value=name)
    return m


async def test_capture_from_combat_picks_the_local_player():
    client = FakeClient()
    client.combat_handler.get_members = AsyncMock(
        return_value=[
            _member(is_client=False, name="Some Enemy"),
            _member(is_client=True, name="<Color;FFFFFFFF>Wolf Deathblade</Color>"),
        ]
    )

    assert await wn.capture_from_combat(client) == "Wolf Deathblade"


async def test_capture_from_combat_returns_none_outside_combat():
    assert await wn.capture_from_combat(FakeClient()) is None


async def test_capture_from_combat_never_raises():
    client = FakeClient()
    client.combat_handler.get_members = AsyncMock(side_effect=RuntimeError("not in combat"))

    assert await wn.capture_from_combat(client) is None
