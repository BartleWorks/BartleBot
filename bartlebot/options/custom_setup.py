"""Custom setup's Options page and the Card art page after it: what they record (#232).

The Options page shows three choices Quick setup leaves on: **Extract card art**,
**Automatically group players** and **Refill potions**. The last two are Settings › Bot's
``auto_group_players`` and ``default_refill_potions``, written as the Bot tab's OK writes
them. The first is setup's own, :data:`EXTRACT_ART_KEY`, and it is also the page's record:
the page is answered once the key is stored.

Quick writes none of the three. Grouping and refills stay as they are, which on a first run
is their default, and card art is extracted, which is what Quick means by its default.

The Card art page records :data:`ART_ANSWERED_KEY` when the user leaves it with Finish,
whether the extraction worked or not: art is optional everywhere, so a failure is not
asked about again on every launch.

No Qt here: the pages are the only Qt around it.
"""

from __future__ import annotations

import logging

from bartlebot.game import card_art
from bartlebot.settings_manager import SettingsManager

logger = logging.getLogger(__name__)

#: In ``settings.json``'s ``general`` section: whether Custom setup chose to extract card art.
#: Stored at all means the Options page was answered.
EXTRACT_ART_KEY = "setup.extract_art"
#: The Card art page was left with Finish.
ART_ANSWERED_KEY = "setup.art_answered"
REFILL_POTIONS_KEY = "default_refill_potions"


def options_answered(settings: SettingsManager) -> bool:
    """Whether the Options page was answered: its card art choice is stored."""
    return settings.get_general(EXTRACT_ART_KEY, default=None) is not None


def extract_art_chosen(settings: SettingsManager) -> bool:
    """Custom setup's card art choice, on until the page turns it off."""
    return bool(settings.get_general(EXTRACT_ART_KEY, default=True))


def refill_potions(settings: SettingsManager) -> bool:
    return bool(settings.get_general(REFILL_POTIONS_KEY, default=True))


def _save(settings: SettingsManager, what: str) -> bool:
    try:
        settings.save()
    except OSError:
        logger.warning("Could not save the %s answer to %s", what, settings.settings_path, exc_info=True)
        return False
    return True


def record_options(settings: SettingsManager, *, extract_art: bool, auto_group: bool, refill: bool) -> bool:
    """Store the Options page's three choices, and say whether they reached disk."""
    settings.set_general(EXTRACT_ART_KEY, bool(extract_art))
    settings.auto_group_players = auto_group
    settings.set_general(REFILL_POTIONS_KEY, bool(refill))
    return _save(settings, "options")


def art_answered(settings: SettingsManager) -> bool:
    """Whether a first run need not show the Card art page: it was left with Finish, or art is already extracted.

    Art extracted before setup asked, with ``tools/extract_card_art.py`` or Settings, is
    the page's work already done.
    """
    return bool(settings.get_general(ART_ANSWERED_KEY, default=False)) or card_art.is_available()


def record_art_answered(settings: SettingsManager) -> bool:
    settings.set_general(ART_ANSWERED_KEY, True)
    return _save(settings, "card art")
