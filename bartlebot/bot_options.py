"""Where a bot option's value comes from, and which level answered.

One field, ``BotOptionModel.default``, used to do two jobs: the author's
recommended value and the user's current choice.  Splitting them needs a rule
for which wins, and there are four places a value can come from:

    user override  ->  declaration (if non-null)  ->  app setting  ->  node default

A declaration of ``None`` means *the author has no opinion*, which is what makes
the middle two levels both reachable.  Without it whichever sits second is dead
weight, because a declaration is written for every required option.

Pure policy, like :mod:`bartlebot.relog` and ``client_refresh_interval()`` --
no Qt, no ``SettingsManager``, no client.  The caller gathers the four inputs and
this decides between them, so precedence is testable without a settings file.

Following ``resolve_combat``: never raise, always fall back, always say which
level answered.  The source string is user-facing -- it reads as the tail of
"Safety Delay = 3.0, from ...".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class _Unset:
    """Sentinel for "this level has nothing to say".

    Not ``None``: a declaration of ``None`` is itself meaningful, and a stored
    override of ``None`` would be indistinguishable from an absent one.
    """

    _instance: _Unset | None = None

    def __new__(cls) -> _Unset:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _Unset()

SOURCE_OVERRIDE = "your override"
SOURCE_BOT_FILE = "the bot file"
SOURCE_APP_SETTING = "the app setting"
SOURCE_NODE_DEFAULT = "the node default"

# Bot options that also exist as an app setting, and the ``general`` key holding it.
# An option absent from this map simply has no app-setting level -- resolution
# falls straight through the declaration to the node default.
APP_SETTING_KEYS: dict[str, str] = {
    "Safety Delay": "default_safety_delay",
    "Level Scaling Players": "default_level_scaling_players",
}


@dataclass(frozen=True)
class OptionResolution:
    """A resolved option value and the level that supplied it."""

    value: Any
    source: str


def value_matches_type(value: Any, declared_type: str | None) -> bool:
    """Whether *value* is usable as an option of *declared_type*.

    Unknown or absent types match everything -- this gates a discard, and
    discarding on a type nobody understands would throw away a good value.

    An ``int`` is accepted where a ``float`` is declared: three NodeSpecs ship
    exactly that (``enter_dungeon`` and friends declared Safety Delay as ``3``),
    and JSON has no way to tell ``3.0`` from ``3`` once written.
    """
    if declared_type is None:
        return True
    if declared_type == "bool":
        return isinstance(value, bool)
    # bool is a subclass of int; a checkbox value is not a number here.
    if declared_type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared_type == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared_type == "str":
        return isinstance(value, str)
    if declared_type == "player_list":
        return isinstance(value, list) and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    return True


def resolve_option(
    name: str,
    *,
    override: Any = UNSET,
    declaration: Any = None,
    declared_type: str | None = None,
    app_value: Any = UNSET,
    spec_default: Any = None,
) -> OptionResolution:
    """Resolve one option through the four levels, most local first.

    Args:
        name:          Option name, used only in the discard warning.
        override:      The user's stored value, or ``UNSET`` if they have none.
        declaration:   The author's value from the bot file; ``None`` = no opinion.
        declared_type: The option's declared type, used to discard a stale override.
        app_value:     The matching app setting, or ``UNSET`` if the option has none.
        spec_default:  The ``BotOptionSpec`` default -- the floor, always answers.
    """
    if not isinstance(override, _Unset):
        if value_matches_type(override, declared_type):
            return OptionResolution(override, SOURCE_OVERRIDE)
        # An author who changes an option's type strands every stored override of
        # it. Nothing here raises, so a kept value would reach the node as the
        # wrong type and fail somewhere with no mention of where it came from.
        logger.warning(
            "Discarding your saved value for %r: it is %s, but the option is now %s.",
            name,
            type(override).__name__,
            declared_type,
        )
    if declaration is not None:
        return OptionResolution(declaration, SOURCE_BOT_FILE)
    if not isinstance(app_value, _Unset) and app_value is not None:
        return OptionResolution(app_value, SOURCE_APP_SETTING)
    return OptionResolution(spec_default, SOURCE_NODE_DEFAULT)
