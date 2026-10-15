"""Where the user plays, and how quick their connection is: setup's Realm and Safety Delay page (#231).

Favorite Realm and Safety Delay are the app-setting level of the ``Favorite Realm`` and
``Safety Delay`` bot options (``docs/BOT_OPTIONS.md``), in BartleBot's ``settings.json``.
Settings › Bot writes them through ``SettingsManager.favorite_realm`` and
``default_safety_delay``; :func:`record_realm_delay` writes the same two, then
:data:`ANSWERED_KEY`, so a first run asks once.

:data:`SETUP_REALM` is only what the page selects on a first run. The setting's own
default stays unset, so a user who never runs setup has no favorite realm, as before, and
Change Realm in favorite mode still tells them to choose one.

No Qt here: the page and the tab are the only Qt around it.
"""

from __future__ import annotations

import logging

from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

#: In ``settings.json``'s ``general`` section: the Realm and Safety Delay page was answered.
ANSWERED_KEY = "setup.realm_delay_answered"

#: The realm the page selects when the user has none, settled with the plan (epic #215).
SETUP_REALM = "Wraith"


def realm_delay_answered(settings: SettingsManager) -> bool:
    """Whether a first run need not ask: the page was answered, or a favorite realm is already set.

    A user who chose a realm in Settings › Bot has already decided where to play. A Safety
    Delay changed on its own does not answer the page, since the realm would still be unset.
    """
    return bool(settings.get_general(ANSWERED_KEY, default=False)) or bool(settings.favorite_realm)


def setup_realm(settings: SettingsManager) -> str:
    """The realm the page opens on: the one stored, or :data:`SETUP_REALM` while the page is unanswered.

    Once answered, an empty realm is the user's answer, "Not set", and a rerun shows it.
    """
    if settings.favorite_realm or realm_delay_answered(settings):
        return settings.favorite_realm
    return SETUP_REALM


def record_realm_delay(settings: SettingsManager, realm: str, delay: float) -> bool:
    """Store the page's realm and Safety Delay, mark it answered, and say whether it reached disk."""
    settings.favorite_realm = realm
    settings.default_safety_delay = float(delay)
    settings.set_general(ANSWERED_KEY, True)
    try:
        settings.save()
    except OSError:
        logger.warning("Could not save the realm and Safety Delay answer to %s", settings.settings_path, exc_info=True)
        return False
    return True
