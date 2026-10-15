"""What bots may sell: setup's Quicksell page and Settings › Bot write it through here (#230).

Quicksell's four switches and the user's own keep list are the app-setting level of the
quicksell bot options (``docs/BOT_OPTIONS.md``), in BartleBot's ``settings.json``.
Settings › Bot writes the switches through :func:`write_sell_flags` and the list through
:func:`write_keep_list`. The setup page shows the same rows and records them through
:func:`record_quicksell`, which writes both and then two things more:

* ``show_quicksell_explanation = False``. The page has already explained quicksell, and
  an explanation before every run after that is the habit-forming dialog #145 removed.
  A sale a bot's own file declares beyond these settings is still flagged once per bot,
  since the switch never reached that case.
* :data:`ANSWERED_KEY`, so a first run asks once.

No Qt here: the page and the tab are the only Qt around it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from bartlebot.nodes.utility.quicksell import USER_KEEP_LIST_KEY
from bartlebot.settings_manager import SettingsManager, default_for

logger = logging.getLogger(__name__)

#: In ``settings.json``'s ``general`` section: the Quicksell page was answered.
ANSWERED_KEY = "setup.quicksell_answered"

ENABLED_KEY = "default_quicksell_enabled"
SELL_PETS_KEY = "default_quicksell_sell_pets"
SELL_SOCKET_WRENCHES_KEY = "default_quicksell_sell_socket_wrenches"
SELL_SKELETON_KEYS_KEY = "default_quicksell_sell_skeleton_keys"
EXPLANATION_KEY = "show_quicksell_explanation"


@dataclass(frozen=True)
class SellFlags:
    """Quicksell's four switches, as the app settings hold them."""

    enabled: bool
    sell_pets: bool
    sell_socket_wrenches: bool
    sell_skeleton_keys: bool


_KEYS = {
    "enabled": ENABLED_KEY,
    "sell_pets": SELL_PETS_KEY,
    "sell_socket_wrenches": SELL_SOCKET_WRENCHES_KEY,
    "sell_skeleton_keys": SELL_SKELETON_KEYS_KEY,
}

#: What a fresh install holds: sell drops and pets, keep wrenches and keys.
DEFAULT_FLAGS = SellFlags(**{field: bool(default_for("general", key)) for field, key in _KEYS.items()})


def current_flags(settings: SettingsManager) -> SellFlags:
    return SellFlags(
        **{
            field: bool(settings.get_general(key, default=getattr(DEFAULT_FLAGS, field)))
            for field, key in _KEYS.items()
        }
    )


def current_keep_list(settings: SettingsManager) -> list[str]:
    return [str(keyword) for keyword in settings.get_general(USER_KEEP_LIST_KEY, default=[]) or []]


def write_sell_flags(settings: SettingsManager, flags: SellFlags) -> None:
    """Set the four switches in memory; the caller saves."""
    for field, key in _KEYS.items():
        settings.set_general(key, getattr(flags, field))


def write_keep_list(settings: SettingsManager, keep: Sequence[str]) -> None:
    """Set the user's own keep list in memory; the caller saves."""
    settings.set_general(USER_KEEP_LIST_KEY, list(keep))


def quicksell_answered(settings: SettingsManager) -> bool:
    """Whether a first run need not ask: the page was answered, or a quicksell setting is already off its default.

    A user who changed one in Settings › Bot, or turned the explanation off under it, has
    already decided what bots sell.
    """
    if settings.get_general(ANSWERED_KEY, default=False):
        return True
    return (
        current_flags(settings) != DEFAULT_FLAGS
        or bool(current_keep_list(settings))
        or not settings.show_quicksell_explanation
    )


def record_quicksell(settings: SettingsManager, flags: SellFlags, keep: Sequence[str]) -> bool:
    """Store the page's switches and keep list, turn the explanation off, and say whether it reached disk."""
    write_sell_flags(settings, flags)
    write_keep_list(settings, keep)
    settings.show_quicksell_explanation = False
    settings.set_general(ANSWERED_KEY, True)
    try:
        settings.save()
    except OSError:
        logger.warning("Could not save the quicksell answer to %s", settings.settings_path, exc_info=True)
        return False
    return True
