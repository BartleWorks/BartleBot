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
level answered.  The source string is user-facing, and is the *same* string the
Setup surface prints under the row -- see ``SOURCE_OVERRIDE`` and friends below.
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


def is_set(value: Any) -> bool:
    """Whether *value* is a real value rather than the "nothing to say" sentinel."""
    return not isinstance(value, _Unset)


# One vocabulary for the four levels, used wherever a resolution is *named* -- the
# source caption under a Setup row, the Reset tooltip, the safety dialog's summary,
# and the once-per-run line ``BotMachine.get_option`` writes to the session log.
#
# There were two before, and they disagreed about what the levels were called: these
# constants read "your override" / "the bot file" while the surface the user was
# looking at read "Your value" / "Bot default". One value cannot have two names and
# stay one value, so the captions won -- they are what a user can actually see, and
# a log line saying "from App settings" names a place the user can go.
SOURCE_OVERRIDE = "Your value"
SOURCE_BOT_FILE = "Bot default"
SOURCE_APP_SETTING = "App settings"
SOURCE_NODE_DEFAULT = "Default"

#: The four, most local first. Anything laying out a caption slot has to size it to
#: the widest of them, and nothing else may enumerate the levels by hand.
SOURCE_LEVELS: tuple[str, ...] = (SOURCE_OVERRIDE, SOURCE_BOT_FILE, SOURCE_APP_SETTING, SOURCE_NODE_DEFAULT)

#: The Safety Delay option's description, in the one place both readers of it can
#: reach. The app setting on the Bot settings tab and the option itself are the same
#: value after #145, and one value described twice is how two descriptions come to
#: disagree -- ``test_the_safety_delay_setting_and_option_read_the_same`` is the guard.
SAFETY_DELAY_DESCRIPTION = (
    "Seconds to allow for game-server responses when waiting for prompts or menus. "
    "Increase on high-latency connections."
)

# Bot options that also exist as an app setting, and the ``general`` key holding it.
# An option absent from this map simply has no app-setting level -- resolution
# falls straight through the declaration to the node default.
APP_SETTING_KEYS: dict[str, str] = {
    "Safety Delay": "default_safety_delay",
    "Level Scaling Players": "default_level_scaling_players",
    # Quicksell: what to sell describes the user's tolerance for losing an item,
    # not the dungeon.  The keep list is the one part of it that is authorial.
    "Enable Quicksell": "default_quicksell_enabled",
    "Sell Pets": "default_quicksell_sell_pets",
    "Sell Socket Wrenches": "default_quicksell_sell_socket_wrenches",
    "Sell Skeleton Keys": "default_quicksell_sell_skeleton_keys",
    # A character name is never the bot's to carry (#102), so the app setting is
    # the shared level under a per-bot override.
    "Friend Name": "default_friend_name",
}

# Options an author has no business declaring at all, whatever their dungeon does.
# Having an app setting underneath is *not* the test -- that only says a shared
# level exists.  The test is whether a declaration could ever be right, and for
# these three it never is:
#
#   Safety Delay    describes the user's connection, not the bot.  A declaration
#                   would re-create the per-node pin #150 removed, one level up.
#   Friend Name     is a character name, so a declaration is a privacy leak and a
#                   bot only its author can run (#102).
#   Enable Quicksell  is whether the user is willing to lose items at all.  An
#                   author who did not want selling would leave the node out.
#
# Deliberately *not* here: ``Sell Pets`` and its two siblings, because a bot whose
# target drop is a pet has to be able to say so, and the keep list cannot spare an
# individual pet -- that is a fact about the dungeon, so the author gets a say.
#
# ``Level Scaling Players`` is the near miss, and worth recording.  Eleven shipped
# bots declared it, which read as authorial intent until the values were looked at:
# six were the same ``[2, 3, 4]`` propagated by copy, and the remaining five --
# ``[1, 2, 3, 4]`` twice, then ``[2]``, ``[1, 2, 3]``, ``[1, 3]`` -- are simply
# whichever of one author's own wizards were underlevelled the day they recorded
# the bot.  Which players need the elixir is a fact about the *user's team*, so all
# eleven were dropped and the app setting answers.  It stays out of this set even
# so: an author who really does know their dungeon scales only some players may
# still say so, and unlike the three above a wrong answer here costs an elixir
# rather than an item, a name, or a run.
USER_OWNED_OPTIONS: frozenset[str] = frozenset({"Safety Delay", "Friend Name", "Enable Quicksell"})


def is_user_owned(name: str) -> bool:
    """Whether *name* is a value only the user may set, never the bot's author.

    The Options surface omits these rows, so nothing the app writes can produce
    such a declaration.  A hand-edited file carrying one is still honoured -- the
    resolution stays a plain four levels, and silently deleting a value on save
    would be worse than the leak it prevents -- and
    ``test_no_bot_declares_a_user_owned_option`` keeps the shipped library clean
    of them.
    """
    return name in USER_OWNED_OPTIONS


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
    if declared_type in ("str", "friend_name"):
        return isinstance(value, str)
    if declared_type == "player_list":
        return isinstance(value, list) and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    if declared_type in ("str_list", "drop_keep_list"):
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    return True


def value_within_bounds(value: Any, minimum: float | None, maximum: float | None) -> bool:
    """Whether *value* sits inside the option's declared range.

    Only numbers are bounded, and a bool is never one of them: ``False`` is not the
    number zero here, the same distinction :func:`values_agree` draws.  A value of
    any other shape passes -- this gates a discard, and discarding what the bounds
    do not describe would throw away a good value.
    """
    if minimum is None and maximum is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return True
    if minimum is not None and value < minimum:
        return False
    return not (maximum is not None and value > maximum)


def resolve_option(
    name: str,
    *,
    override: Any = UNSET,
    declaration: Any = None,
    declared_type: str | None = None,
    app_value: Any = UNSET,
    spec_default: Any = None,
    minimum: float | None = None,
    maximum: float | None = None,
) -> OptionResolution:
    """Resolve one option through the four levels, most local first.

    A level whose value is the wrong type, or outside the option's declared range,
    is **discarded with a warning** and the level below it answers instead.  Never
    coerced: clamping a hand-edited ``-1`` to ``0`` would hide a wrong file, and
    this is the one funnel every consumer of an option goes through, so it is the
    only place a value arriving from outside the UI can be caught at all.

    Args:
        name:          Option name, used only in the discard warning.
        override:      The user's stored value, or ``UNSET`` if they have none.
        declaration:   The author's value from the bot file; ``None`` = no opinion.
        declared_type: The option's declared type, used to discard a stale override.
        app_value:     The matching app setting, or ``UNSET`` if the option has none.
        spec_default:  The ``BotOptionSpec`` default -- the floor, always answers.
        minimum:       Lowest value any level may supply, or ``None`` for no floor.
        maximum:       Highest value any level may supply, or ``None`` for no ceiling.
    """

    def usable(value: Any, level: str, *, check_type: bool = False) -> bool:
        # The type check is the *override's* alone: an author who changes an option's
        # type strands every stored override of it, and nothing here raises, so a kept
        # value would reach the node as the wrong type and fail somewhere with no
        # mention of where it came from. A declaration's type is the one the file
        # itself states, so there is nothing to disagree with.
        if check_type and not value_matches_type(value, declared_type):
            logger.warning(
                "%s for option %r discarded: it is %s, but the option is now %s.",
                level,
                name,
                type(value).__name__,
                declared_type,
            )
            return False
        # Bounds apply at every level, because a value can arrive from a hand-edited
        # settings.json *or* bot file and this is the one funnel both go through.
        if not value_within_bounds(value, minimum, maximum):
            logger.warning(
                "%s for option %r discarded: %r is outside the allowed range (%s to %s).",
                level,
                name,
                value,
                "any" if minimum is None else minimum,
                "any" if maximum is None else maximum,
            )
            return False
        return True

    if not isinstance(override, _Unset) and usable(override, SOURCE_OVERRIDE, check_type=True):
        return OptionResolution(override, SOURCE_OVERRIDE)
    if declaration is not None and usable(declaration, SOURCE_BOT_FILE):
        return OptionResolution(declaration, SOURCE_BOT_FILE)
    if not isinstance(app_value, _Unset) and app_value is not None and usable(app_value, SOURCE_APP_SETTING):
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

    A spec carrying ``required_when`` is required only by a state whose param of
    that name is on: an option the author has said their dungeon does not offer is
    not an option, and one state offering it is enough for the whole bot.
    """
    out: dict = {}
    for state in config.states:
        spec = _all_specs(specs).get(state.node_type)
        if spec is None:
            continue
        for req in spec.required_bot_options:
            if req.required_when and not state.params.get(req.required_when):
                continue
            out.setdefault(req.name, req)
    return out


def system_option_names(specs=None) -> set[str]:
    """Every option name declared by any node in the registry."""
    return {req.name for spec in _all_specs(specs).values() for req in spec.required_bot_options}


def compose_options(config, specs=None) -> list:
    """The effective declaration list: the file's own options plus the derived ones.

    Nothing writes this back.  Callers that need to *display* the options a bot
    has use this; the file is the shorter thing underneath it.

    Ordered by **where an option comes from, not by whether it happens to be in
    the file**: the author's own options first, in file order, then the derived
    ones grouped by the node that requires them.  A derived option keeps its place
    whether or not a declaration has been materialised for it, which is what stops
    a row jumping up the list the moment its value is edited -- and it keeps a
    node's options together, so quicksell's five are not left with another node's
    threaded through them.
    """
    from bartlebot.bot.schema import BotOptionModel  # noqa: PLC0415 - avoids an import cycle

    required = required_option_specs(config, specs)
    system = system_option_names(specs)
    stored = {opt.name: opt for opt in config.options}

    # A system-managed name no node in this graph asks for is dead weight -- the same
    # prune _inject_required_options did, minus the write-back.
    composed: list = [opt for opt in config.options if opt.name not in required and opt.name not in system]

    for name, spec in required.items():
        opt = stored.get(name)
        composed.append(
            BotOptionModel(
                name=name,
                type=spec.type,
                default=spec.default if opt is None or opt.default is None else opt.default,
                description=spec.description,
                minimum=spec.minimum,
                maximum=spec.maximum,
                suffix=spec.suffix,
                # Dropped when the parent is not in this graph: a child indented under
                # a row that is not there reads as a child of whatever precedes it.
                depends_on=spec.depends_on if spec.depends_on in required else None,
            )
        )
    return composed


def options_signature(config, specs=None) -> tuple:
    """Everything the two option surfaces build their rows from, as a comparable value.

    ``compose_options`` is a pure function of the graph and the stored
    declarations, and building the rows from it costs ~55ms on a 67-state bot --
    paid on every graph edit, while most edits touch no node carrying a
    ``BotOptionSpec`` at all.  Composing costs 0.05ms, so asking whether the answer
    moved is three orders of magnitude cheaper than rebuilding regardless (#184).

    Carries more than the composed list: the id, because Setup resolves the user's
    overrides through it and Save As moves it under a loaded bot; the drop table,
    because the quicksell keep list is picked from it; and which names are
    *derived*, because that is what decides whether a row on the Options surface
    gets an Edit button, and it can move while the composed list does not.

    Compared with ``==`` and never hashed -- an option's default can be a list.
    """
    if config is None:
        return ()
    required = set(required_option_specs(config, specs))
    return (
        config.id,
        tuple((drop.name, drop.category, drop.match_name) for drop in config.drops),
        tuple(
            (
                opt.name,
                opt.type,
                opt.default,
                opt.description,
                opt.minimum,
                opt.maximum,
                opt.suffix,
                opt.depends_on,
                opt.name in required,
            )
            for opt in compose_options(config, specs)
        ),
    )


def values_agree(a, b) -> bool:
    """Whether two option values say the same thing.

    ``3`` and ``3.0`` agree -- three NodeSpecs shipped the int form, and JSON
    cannot tell them apart once written.  ``False`` and ``0`` do not: a checkbox
    answering "no" is not the number zero, and collapsing them would drop a real
    answer.

    Used at both ends of the same question: the save-time prune asking whether a
    stored declaration says anything the composed one does not, and the Setup view
    asking whether the value just typed is already what the option resolves to.
    """
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return a == b


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
        if opt.default is None or values_agree(opt.default, spec.default):
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


def option_specs_for(config, specs=None) -> dict:
    """``{name: BotOptionSpec}`` reachable from *config*, its own nodes first.

    Several nodes declare Safety Delay with slightly different text, so which one
    answers matters: the bot's own graph is the honest source, and the rest of the
    registry is only a fallback for an option whose node has since been cut.
    """
    out: dict = dict(required_option_specs(config, specs))
    for spec in _all_specs(specs).values():
        for req in spec.required_bot_options:
            out.setdefault(req.name, req)
    return out


def resolve_for_config(
    config,
    name: str,
    *,
    override: Any = UNSET,
    app_value: Any = UNSET,
    specs=None,
    option_specs: dict | None = None,
    default: Any = None,
) -> OptionResolution | None:
    """Resolve one of *config*'s options, reading the declaration off the file itself.

    The two halves of the four-level lookup live in different places: the
    declaration and the node default come from the bot and the registry, which
    this gathers, while the override and the app setting come from
    ``SettingsManager``, which this module must not import.  So the caller passes
    those two in -- ``BotMachine`` at run time, the Setup view for display.

    Returns ``None`` when *name* is not an option of this bot at all: neither
    declared in its file nor required by any node the registry knows.

    *option_specs* is ``option_specs_for(config)`` precomputed.  ``get_option``
    fires on nearly every node, and rebuilding that map over the whole registry
    per call is the one part of this lookup worth remembering.
    """
    declaration = None
    declared_type = None
    declared = False
    for opt in config.options:
        if opt.name == name:
            declaration = opt.default
            declared_type = opt.type
            declared = True
            break

    if option_specs is None:
        option_specs = option_specs_for(config, specs)
    spec = option_specs.get(name)
    if not declared and spec is None:
        return None
    if declared_type is None and spec is not None:
        declared_type = spec.type

    return resolve_option(
        name,
        override=override,
        declaration=declaration,
        declared_type=declared_type,
        app_value=app_value,
        spec_default=spec.default if spec is not None else default,
        minimum=getattr(spec, "minimum", None),
        maximum=getattr(spec, "maximum", None),
    )
