"""Resolving a bot option against *this machine's* settings.

:mod:`bartlebot.bot_options` is the policy and stays pure -- it decides between
four values it is handed.  Two of those four live in ``SettingsManager``, which
that module must not import, so every caller has had to gather them itself.

Three surfaces now need the same gather and must not disagree about it: the run
loop (``BotMachine.get_option``), the Setup rows that show the user what the run
will do, and the safety dialog that is the last thing between the user and an
irreversible sale.  This is that gather, once.

Nothing here raises.  A settings file that cannot be read leaves the option
resolving through the levels below it, which is what the node defaults are for.
"""

from __future__ import annotations

import logging
from typing import Any

from bartlebot import bot_options
from bartlebot.bot_options import UNSET, OptionResolution

logger = logging.getLogger(__name__)


def _settings(settings=None):
    if settings is not None:
        return settings
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415 - avoids an import cycle

    return SettingsManager.instance()


def stored_override(config, name: str, *, settings=None) -> Any:
    """The user's stored value for *name* under *config*, or ``UNSET``."""
    if config is None:
        return UNSET
    try:
        return _settings(settings).get_option_override(config.id, name)
    except Exception:
        logger.exception("Failed to read the stored value for option %r", name)
        return UNSET


def app_value(name: str, *, settings=None) -> Any:
    """The app setting behind *name*, or ``UNSET`` when the option has none."""
    key = bot_options.APP_SETTING_KEYS.get(name)
    if key is None:
        return UNSET
    try:
        return _settings(settings).get("general", key, default=UNSET)
    except Exception:
        logger.exception("Failed to read app setting %r", key)
        return UNSET


def option_resolution(
    config,
    name: str,
    *,
    settings=None,
    option_specs: dict | None = None,
    default: Any = None,
) -> OptionResolution | None:
    """Resolve one of *config*'s options through all four levels.

    Returns ``None`` when *name* is not an option of this bot at all -- neither
    declared in its file nor required by any node the registry knows.

    *option_specs* is ``bot_options.option_specs_for(config)`` precomputed; the
    run loop keeps one for the whole run, because building it walks the registry.
    """
    if config is None:
        return None
    return bot_options.resolve_for_config(
        config,
        name,
        override=stored_override(config, name, settings=settings),
        app_value=app_value(name, settings=settings),
        option_specs=option_specs,
        default=default,
    )


def option_value(config, name: str, default: Any = None, *, settings=None) -> Any:
    """Just the value, for a caller with nothing to say about where it came from."""
    resolution = option_resolution(config, name, settings=settings, default=default)
    return default if resolution is None else resolution.value
