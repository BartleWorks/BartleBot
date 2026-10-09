"""Unit tests for the post-login Remember Realm step.

The settings window is faked by the text the option shows and whether the window is
open; the step is judged by what it clicked and what it returned. It runs on a window
the cleanup's ESC pass has already opened, so opening is not its job and the fakes start
with the window up.
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

from bartlebot.launcher import auto_select, remember_realm
from bartlebot.nodes.paths import (
    BACKPACK_CLOSE_BUTTON_PATH,
    REMEMBER_REALM_TOGGLE_PATH,
    REMEMBER_REALM_VALUE_PATH,
    SETTINGS_OK_BUTTON_PATH,
)


class _OptionsPage:
    """The Remember Realm option as the page shows it, in some locale."""

    def __init__(self, value: str, *, toggles_to: str = "Yes"):
        self.value = value
        self.toggles_to = toggles_to
        self.spellbook_open = True
        self.clicked: list[list[str]] = []

    async def text(self, _client, path):
        return self.value if path == REMEMBER_REALM_VALUE_PATH else ""

    async def enabled(self, _client, path):
        if path == BACKPACK_CLOSE_BUTTON_PATH:
            return self.spellbook_open
        return True

    async def click(self, _client, path):
        self.clicked.append(path)
        if path == REMEMBER_REALM_TOGGLE_PATH:
            self.value = self.toggles_to
        elif path in (SETTINGS_OK_BUTTON_PATH, BACKPACK_CLOSE_BUTTON_PATH):
            self.spellbook_open = False
        return True


@contextlib.contextmanager
def _faked(page: _OptionsPage):
    """Stand *page* in for the game's UI reads, with every wait compressed."""
    with contextlib.ExitStack() as stack:
        for p in (
            patch.object(remember_realm, "POLL_INTERVAL", 0),
            patch.object(remember_realm, "TRIGGER_TIMEOUT", 0.05),
            patch.object(remember_realm, "is_visible_by_path", AsyncMock(return_value=True)),
            patch.object(remember_realm, "get_text_by_path", AsyncMock(side_effect=page.text)),
            patch.object(remember_realm, "click_by_path", AsyncMock(side_effect=page.click)),
        ):
            stack.enter_context(p)
        yield


async def test_an_option_reading_no_is_toggled_and_committed():
    page = _OptionsPage("No")
    with _faked(page):
        result = await remember_realm.turn_on_remember_realm(MagicMock())

    assert result is True
    assert REMEMBER_REALM_TOGGLE_PATH in page.clicked
    assert page.clicked.index(REMEMBER_REALM_TOGGLE_PATH) < page.clicked.index(SETTINGS_OK_BUTTON_PATH)


async def test_an_option_already_on_is_left_alone():
    page = _OptionsPage("Yes")
    with _faked(page):
        result = await remember_realm.turn_on_remember_realm(MagicMock())

    assert result is True
    assert REMEMBER_REALM_TOGGLE_PATH not in page.clicked
    assert SETTINGS_OK_BUTTON_PATH in page.clicked


async def test_reads_the_value_in_the_clients_own_locale():
    """A German client shows Nein; toggling it shows Ja."""
    page = _OptionsPage("Nein", toggles_to="Ja")
    with _faked(page):
        result = await remember_realm.turn_on_remember_realm(MagicMock())

    assert result is True
    assert REMEMBER_REALM_TOGGLE_PATH in page.clicked


async def test_an_unreadable_value_is_never_toggled():
    """A blind toggle from Yes would turn the option off; leave it, and the window, to the caller."""
    page = _OptionsPage("???")
    with _faked(page):
        result = await remember_realm.turn_on_remember_realm(MagicMock())

    assert result is None
    assert REMEMBER_REALM_TOGGLE_PATH not in page.clicked
    assert SETTINGS_OK_BUTTON_PATH not in page.clicked
    assert page.spellbook_open  # the cleanup's ESC loop closes it


async def test_a_toggle_that_does_not_take_reports_false():
    page = _OptionsPage("No", toggles_to="No")
    with _faked(page):
        result = await remember_realm.turn_on_remember_realm(MagicMock())

    assert result is False
    assert SETTINGS_OK_BUTTON_PATH not in page.clicked


async def test_never_raises():
    """Best-effort: it must not be able to fail an otherwise good launch."""
    with patch.object(remember_realm, "click_by_path", AsyncMock(side_effect=RuntimeError("boom"))):
        result = await remember_realm.turn_on_remember_realm(MagicMock())

    assert result is None


# ---------------------------------------------------------------------------
# The gate: the cleanup runs it on the window its first ESC opened, only when asked
# ---------------------------------------------------------------------------


async def _clean_up(remember: bool) -> tuple[AsyncMock, list[int]]:
    """Run the cleanup against a client whose first ESC opens settings; return (turn_on, key order).

    The key order is recorded as which read of the close button each ESC preceded, so the
    test can say the option was handled *between* the two ESC loops.
    """
    client = MagicMock(window_handle=999)
    events: list[str] = []
    turn_on = AsyncMock(side_effect=lambda _c: events.append("remember"))
    settings_open = False

    async def enabled(_client, _path):
        return settings_open

    async def press(_hwnd, _vk):
        nonlocal settings_open
        events.append("esc")
        settings_open = not settings_open

    with (
        patch.object(auto_select, "AUTO_SELECT_CLEANUP_INTERVAL", 0),
        patch.object(auto_select, "_press", press),
        patch.object(auto_select, "is_enabled_by_path", enabled),
        patch.object(auto_select, "poll_until", AsyncMock(return_value=True)),
        patch.object(auto_select, "click_by_path", AsyncMock()),
        patch.object(auto_select, "turn_on_remember_realm", turn_on),
    ):
        await auto_select.clean_up_after_auto_select(client, remember_realm=remember)
    return turn_on, events


async def test_the_cleanup_handles_the_option_while_settings_is_open():
    """Once settings is up from the first ESC, before the ESC that closes it."""
    turn_on, events = await _clean_up(True)

    turn_on.assert_awaited_once()
    assert events == ["esc", "remember", "esc"]


async def test_the_cleanup_leaves_the_option_alone_by_default():
    turn_on, events = await _clean_up(False)

    turn_on.assert_not_awaited()
    assert events == ["esc", "esc"]


async def test_drive_client_in_hands_the_flag_to_the_cleanup():
    cleanup = AsyncMock()
    with (
        patch.object(auto_select, "auto_select_character", AsyncMock(return_value=True)),
        patch.object(auto_select, "clean_up_after_auto_select", cleanup),
    ):
        await auto_select.drive_client_in("W101", lambda: True, lambda: MagicMock(), remember_realm=True)

    cleanup.assert_awaited_once()
    assert cleanup.await_args.kwargs == {"remember_realm": True}
