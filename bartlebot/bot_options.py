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


# ---------------------------------------------------------------------------
# Declarations: derived, not persisted
# ---------------------------------------------------------------------------
#
# An option a node requires is not written to the bot file.  Its type, default
# and description come from the NodeSpec at load, which is what makes a
# description fix reach all 80 bots instead of leaving 80 stale copies -- and
# what stops Challenge Mode appearing in the 59 bots that never read it.
#
# The file keeps only what is authorial: an option the author hand-added, or a
# required one whose default they deliberately changed.


def _all_specs(specs=None):
    """The NodeSpec registry, imported late so this module stays importable alone."""
    if specs is not None:
        return specs
    from bartlebot.nodes.registry import NODE_SPECS  # noqa: PLC0415 - avoids an import cycle

    return NODE_SPECS


def required_option_specs(config, specs=None) -> dict:
    """``{name: BotOptionSpec}`` for every option the graph's nodes require.

    Ordered by the states that ask for them, so a composed list reads in the
    order the bot uses them.  Where two nodes declare the same option with
    different text -- Safety Delay has three variants -- the first state wins,
    which is the same rule ``BotMachine._option_spec`` follows.
    """
    out: dict = {}
    for state in config.states:
        spec = _all_specs(specs).get(state.node_type)
        if spec is None:
            continue
        for req in spec.required_bot_options:
            out.setdefault(req.name, req)
    return out


def system_option_names(specs=None) -> set[str]:
    """Every option name declared by any node in the registry."""
    return {req.name for spec in _all_specs(specs).values() for req in spec.required_bot_options}


def compose_options(config, specs=None) -> list:
    """The effective declaration list: the file's own options plus the derived ones.

    Nothing writes this back.  Callers that need to *display* the options a bot
    has use this; the file is the shorter thing underneath it.
    """
    from bartlebot.bot.schema import BotOptionModel  # noqa: PLC0415 - avoids an import cycle

    required = required_option_specs(config, specs)
    system = system_option_names(specs)

    composed: list = []
    seen: set[str] = set()
    for opt in config.options:
        spec = required.get(opt.name)
        if spec is None:
            # A system-managed name no node in this graph asks for is dead weight --
            # the same prune _inject_required_options did, minus the write-back.
            if opt.name in system:
                continue
            composed.append(opt)
        else:
            composed.append(
                BotOptionModel(
                    name=opt.name,
                    type=spec.type,
                    default=spec.default if opt.default is None else opt.default,
                    description=spec.description,
                )
            )
        seen.add(opt.name)

    for name, spec in required.items():
        if name not in seen:
            composed.append(
                BotOptionModel(
                    name=name,
                    type=spec.type,
                    default=spec.default,
                    description=spec.description,
                )
            )
    return composed


def _defaults_agree(stored, composed) -> bool:
    """Whether a stored default says anything the composed one does not.

    ``3`` and ``3.0`` agree -- three NodeSpecs shipped the int form, and JSON
    cannot tell them apart once written.  ``False`` and ``0`` do not: a checkbox
    answering "no" is not the number zero, and collapsing them would drop a real
    answer.
    """
    if isinstance(stored, bool) != isinstance(composed, bool):
        return False
    return stored == composed


def storable_options(config, specs=None) -> list:
    """The options that belong in the bot file, given what the graph derives.

    Keyed by **name**, never by byte-identity: only 66 of 97 stored declarations
    in the library match a NodeSpec exactly, and the other 31 differ only by
    carrying a description that was injected once and never refreshed.  Keeping
    those would file a stale copy as author intent.  So for a derived option the
    type and description are dropped unconditionally, and the default survives
    only when it differs from the composed one.
    """
    from bartlebot.bot.schema import BotOptionModel  # noqa: PLC0415 - avoids an import cycle

    required = required_option_specs(config, specs)
    system = system_option_names(specs)

    kept: list = []
    for opt in config.options:
        if opt.name not in system:
            kept.append(opt)
            continue
        spec = required.get(opt.name)
        if spec is None:
            continue
        if opt.default is None or _defaults_agree(opt.default, spec.default):
            continue
        kept.append(BotOptionModel(name=opt.name, type=spec.type, default=opt.default, description=None))
    return kept


def orphaned_flag_options(config, specs=None) -> list[tuple]:
    """``(transition, option name)`` for every flag guarding on an undeclared option.

    The cost of deriving declarations is that the file stops being
    self-describing: a bot opened on a build whose node no longer declares an
    option loses it silently, and ``BotMachine.__getattr__`` resolves
    ``_flag_<name>`` to ``False`` rather than erroring.  This is what makes that
    visible instead.

    Both flag forms are walked.  The grouped one takes precedence at run time,
    but a stale flat list is still what an older editor wrote and still what a
    hand-edited file may carry.
    """
    declared = {o.name for o in config.options} | set(required_option_specs(config, specs))
    orphans: list[tuple] = []
    for trans in config.transitions:
        names: list[str] = [fc.option for fc in trans.flags]
        names += [fc.option for group in trans.flag_groups for fc in group.flags]
        for name in names:
            if name not in declared and (trans, name) not in orphans:
                orphans.append((trans, name))
    return orphans
