"""Turn the game's "Remember Realm" option on in a client that has just logged in.

With it on, the game logs a character into the realm it last left, so a player that
relogs mid-run comes back to the favourite realm instead of wherever the login server
puts it. There is no tag for it in config.xml or preferences.xml -- both were searched
-- so unlike quick friend teleport it cannot be patched before launch and has to be
toggled through the settings page once the client is in-game.

That page is already open once per launch: the post-login cleanup in ``auto_select``
presses ESC until the settings window appears, then ESC until it goes away. This runs
in between, on the window that first ESC opened, rather than opening the spellbook a
second time afterwards -- which is why it takes the page as given and never opens it.
``clean_up_after_auto_select`` is the only caller.

Only worth doing when a favourite realm is set, which is how the caller gates it:
without one the option changes nothing a bot relies on.

Best-effort throughout, like the rest of the cleanup: the option is a convenience, and
nothing here may fail a launch that otherwise succeeded. The value the page shows is
localized text, read through :func:`bartlebot.realms.yes_no`; an unreadable value is
left alone rather than blindly toggled, because a blind toggle from "Yes" turns it off.
"""

from __future__ import annotations

import logging

from bartlebot import realms
from bartlebot.nodes.node_helpers import (
    click_by_path,
    get_text_by_path,
    is_visible_by_path,
    poll_until,
    strip_html,
)
from bartlebot.nodes.paths import (
    REMEMBER_REALM_TOGGLE_PATH,
    REMEMBER_REALM_VALUE_PATH,
    SETTINGS_MORE_GAMEPLAY_TAB_PATH,
    SETTINGS_OK_BUTTON_PATH,
)
from bartlebot.nodes.timing import POLL_INTERVAL, TRIGGER_TIMEOUT

logger = logging.getLogger(__name__)


async def _read_value(client) -> bool | None:
    return realms.yes_no(strip_html(await get_text_by_path(client, REMEMBER_REALM_VALUE_PATH)))


async def _read_value_is(client, expected: bool) -> bool:
    return await _read_value(client) is expected


async def turn_on_remember_realm(client) -> bool | None:
    """On an open settings window, make sure Remember Realm is on.

    Third gameplay tab -> read the value -> flip it with the right arrow if it reads No
    -> OK. Returns True if the option is on (already, or now), False if the toggle did
    not take, None if the page could not be read. Every wait is TRIGGER_TIMEOUT: this
    runs outside any bot, so there is no Safety Delay to borrow.

    Whether OK closes the window is left to the caller, whose ESC loop closes it if
    not; on every early return the window is likewise left for the caller.
    """
    try:
        if not await poll_until(
            lambda: is_visible_by_path(client, SETTINGS_MORE_GAMEPLAY_TAB_PATH), TRIGGER_TIMEOUT, POLL_INTERVAL
        ):
            logger.warning(
                "Remember Realm: the gameplay options tab is not on this window; leaving the option as it is"
            )
            return None

        await click_by_path(client, SETTINGS_MORE_GAMEPLAY_TAB_PATH)
        if not await poll_until(
            lambda: is_visible_by_path(client, REMEMBER_REALM_VALUE_PATH), TRIGGER_TIMEOUT, POLL_INTERVAL
        ):
            logger.warning("Remember Realm: the option was not found on the page; leaving it as it is")
            return None

        value = await _read_value(client)
        if value is None:
            logger.warning("Remember Realm: could not read the option's value; leaving it as it is")
            return None

        if not value:
            await click_by_path(client, REMEMBER_REALM_TOGGLE_PATH)
            if not await poll_until(lambda: _read_value_is(client, True), TRIGGER_TIMEOUT, POLL_INTERVAL):
                logger.warning("Remember Realm: the option did not change to Yes")
                return False
            logger.info("Remember Realm: turned on")
        else:
            logger.debug("Remember Realm: already on")

        await click_by_path(client, SETTINGS_OK_BUTTON_PATH)
        return True
    except Exception:
        logger.warning("Remember Realm: could not check the option", exc_info=True)
        return None
