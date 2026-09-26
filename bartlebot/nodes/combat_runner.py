"""Shared combat execution helpers used by handle_combat and the ambient combat watcher.

Extracted so that machine.py does not need to import from a specific node module.
"""

import asyncio
import logging
from functools import lru_cache
from typing import NamedTuple

from bartlebot.defaults.combat_config import BARTLEBOT_DEFAULT_COMBAT_CONFIG
from bartlebot.nodes.timing import COMBAT_DROP_MESSAGE_POLL, COMBAT_DROP_MESSAGE_TIMEOUT
from bartlebot.utils import drop_detection

logger = logging.getLogger(__name__)

_BUILTIN_SOURCE = "BartleBot built-in default"


def slots_in_battle(in_battle_flags: list) -> list[int]:
    """Return the 0-based client indices whose ``in_battle()`` came back True.

    ``in_battle_flags`` is the result of a ``gather(..., return_exceptions=True)``
    over every client, so entries may be True, False, or an Exception.  Callers
    need the *indices* rather than the clients themselves: a client's position in
    the full client list is its player slot, and combat DSL is resolved per slot.
    Filtering to a shorter list of clients and then indexing positionally into the
    resolved DSLs hands the wrong player's strategy to every client after the
    first gap.
    """
    return [i for i, flag in enumerate(in_battle_flags) if flag is True]


def resolve_combat(
    machine,
    use_default_strategy: bool,
    num_clients: int,
) -> tuple[list[str], list[int], str]:
    """Return (per_client_dsl, combat_order, source_label).

    Resolution path:
      1. Get bot_id from machine.bot_config.id.
      2. Look up active BotCombatStrategy UUID in SettingsManager.
      3. Load BotCombatStrategy; if use_default_strategy=True use the BCS default,
         otherwise find the StateAssignment whose state_ids contains machine.state,
         falling back to the BCS default when no assignment covers the state.
      4. Each of those (state assignment or BCS default) is either linked to a
         library DuelCombatStrategy (duel_combat_strategy_id set — load it) or
         inline (its own DuelConfig, used as-is; an empty one is equivalent to
         no config at all).
      5. For each 1-based player slot (1..num_clients) find the PlayerAssignment
         → its inline DSL, or the linked PlayerCombatStrategy preset's DSL if
         player_combat_strategy_id is set.
      6. Players with no assignment get BARTLEBOT_DEFAULT_COMBAT_CONFIG.
      7. combat_order from the resolved config's combat_order (None → []).

    Falls back to BARTLEBOT_DEFAULT_COMBAT_CONFIG at every level if any file is
    missing, no config (linked or inline) is assigned, or an error occurs.
    """
    # Lazy imports to avoid circular imports and keep startup fast.
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415
    from bartlebot.strategy_store import (  # noqa: PLC0415
        load_bot_strategy,
        load_duel_strategy,
        load_player_strategy,
    )

    bot_cfg = machine.bot_config
    if bot_cfg is None:
        return [BARTLEBOT_DEFAULT_COMBAT_CONFIG] * num_clients, [], _BUILTIN_SOURCE

    bot_id: str = bot_cfg.id
    sm = SettingsManager.instance()
    active_bcs_id = sm.get_active_strategy(bot_id)

    if not active_bcs_id:
        logger.debug("resolve_combat: no active BotCombatStrategy for bot %s — using built-in default", bot_id)
        return [BARTLEBOT_DEFAULT_COMBAT_CONFIG] * num_clients, [], _BUILTIN_SOURCE

    # Load the active BotCombatStrategy.
    try:
        bcs = load_bot_strategy(active_bcs_id)
    except Exception:
        logger.warning("resolve_combat: failed to load BotCombatStrategy %s — using built-in default", active_bcs_id)
        return [BARTLEBOT_DEFAULT_COMBAT_CONFIG] * num_clients, [], _BUILTIN_SOURCE

    # Determine which duel target to use: a state assignment (linked or inline),
    # falling back to the BCS default (also linked or inline) when unassigned.
    if use_default_strategy:
        dcs_id, inline_config = bcs.default_strategy_id, bcs.default_duel_config
        assignment_source = "default strategy"
    else:
        current_state: str = getattr(machine, "state", "")
        assignment = bcs.find_assignment_for_state(current_state)
        dcs_id, inline_config = (
            (assignment.duel_combat_strategy_id, assignment.duel_config) if assignment is not None else (None, None)
        )
        # An assignment that resolves to nothing (unlinked and empty) is
        # equivalent to no assignment at all — fall back to the BCS default.
        if dcs_id or (inline_config is not None and not inline_config.is_empty()):
            assignment_source = f"state assignment for '{current_state}'"
        else:
            dcs_id, inline_config = bcs.default_strategy_id, bcs.default_duel_config
            assignment_source = f"default strategy (state '{current_state}' has no assignment)"

    if dcs_id:
        try:
            dcs = load_duel_strategy(dcs_id)
        except Exception:
            logger.warning("resolve_combat: failed to load DuelCombatStrategy %s — using built-in default", dcs_id)
            return [BARTLEBOT_DEFAULT_COMBAT_CONFIG] * num_clients, [], _BUILTIN_SOURCE
        source_label = f"duel strategy '{dcs.name}' (via {assignment_source})"
    elif not inline_config.is_empty():
        dcs = inline_config
        source_label = f"inline duel config (via {assignment_source})"
    else:
        logger.debug("resolve_combat: no duel config assigned (%s) — using built-in default", assignment_source)
        return [BARTLEBOT_DEFAULT_COMBAT_CONFIG] * num_clients, [], _BUILTIN_SOURCE

    # Collect the DSL strings for assignments that link a shared PCS preset.
    # Inline assignments carry their own DSL and need no lookup.
    pcs_map: dict[str, str] = {}
    for pcs_id in dcs.linked_pcs_ids():
        if pcs_id not in pcs_map:
            try:
                pcs = load_player_strategy(pcs_id)
                pcs_map[pcs_id] = pcs.dsl
            except Exception:
                logger.warning(
                    "resolve_combat: failed to load PlayerCombatStrategy %s — affected players use built-in default",
                    pcs_id,
                )
                pcs_map[pcs_id] = BARTLEBOT_DEFAULT_COMBAT_CONFIG

    # Build per-client DSL list (1-based player numbers → 0-based client indices).
    per_client_dsl: list[str] = []
    for client_idx in range(num_clients):
        player_num = client_idx + 1  # 1-based
        dsl = dcs.resolve_dsl_for_player(player_num, pcs_map)
        per_client_dsl.append(dsl if dsl else BARTLEBOT_DEFAULT_COMBAT_CONFIG)

    combat_order: list[int] = dcs.combat_order if dcs.combat_order is not None else []

    return per_client_dsl, combat_order, source_label


# ---------------------------------------------------------------------------
# DSL parsing
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _lark_parser():
    """Build the wizsprinter combat grammar parser once and reuse it.

    ``CombatConfigProvider.parse_config()`` rebuilds a ``Lark`` instance on every
    call (~77 ms), and ``_run_combat`` constructs a provider per client per fight,
    so that cost lands squarely in the hot path.  Lark parsers are reusable across
    ``parse()`` calls and this app is single-threaded under qasync, so one shared
    instance is safe.
    """
    from lark import Lark  # noqa: PLC0415
    from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (  # noqa: PLC0415
        get_sprinty_grammar,
    )

    return Lark(get_sprinty_grammar())


@lru_cache(maxsize=1)
def _str_provider_class():
    """Return a CombatConfigProvider subclass that accepts a DSL string.

    Built lazily and cached so importing this module does not pull in wizwalker,
    and so both ``_run_combat`` and ``validate_dsl`` share one definition.
    """
    from wizwalker.extensions.wizsprinter.combat_backends.backend_base import (  # noqa: PLC0415
        BaseCombatBackend,
    )
    from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (  # noqa: PLC0415
        TreeToConfig,
    )
    from wizwalker.extensions.wizsprinter.combat_backends.config_backend import (  # noqa: PLC0415
        CombatConfigProvider,
    )

    class _StrCombatConfigProvider(CombatConfigProvider):
        """CombatConfigProvider variant that accepts a DSL string directly."""

        def __init__(self, content: str, cast_time: float = 0.2):
            # Bypass the parent __init__ (which opens a file path).
            BaseCombatBackend.__init__(self, cast_time=cast_time)
            self.filename = "<inline>"
            self.config = self.parse_config(content)

        def parse_config(self, file_contents):
            # Same as the parent, but against the shared parser instead of a new one.
            tree = _lark_parser().parse(file_contents)
            return self._expand_config(TreeToConfig().transform(tree))

    return _StrCombatConfigProvider


class DslError(NamedTuple):
    """Why a DSL does not parse, and where.

    ``line`` and ``column`` are 1-based, and 0 when the failure has no position — an
    unexpected end of input, or an error raised while transforming a tree that parsed.
    """

    message: str
    line: int = 0
    column: int = 0


def validate_dsl(dsl: str) -> str | None:
    """Return a one-line parse error for ``dsl``, or None if it is valid. See :func:`dsl_error`."""
    error = dsl_error(dsl)
    return error.message if error else None


@lru_cache(maxsize=512)
def dsl_error(dsl: str) -> DslError | None:
    """Return why ``dsl`` does not parse, and where, or None if it is valid.

    The position is what the DSL editor underlines; everything else wants only the
    message, through :func:`validate_dsl`.

    An empty or whitespace-only DSL is reported as valid: ``resolve_dsl_for_player``
    returns None for it and ``resolve_combat`` substitutes the built-in default, so
    it is never executed.  A newly created preset starts empty by design and must
    not be flagged.

    Pure — no client, no Qt, no disk.  Used to surface syntax errors in the pre-run
    warning dialog instead of mid-fight.

    Cached because it is not cheap: a parse plus a transform is ~60-90 ms for a
    typical preset, and the strategy dialog re-summarizes a whole list whenever a
    card is redrawn.  The grammar is fixed for the life of the process, so the
    answer for a given string never changes.  ``maxsize`` is generous on purpose —
    typing in the DSL editor mints a new string per keystroke, and evicting the
    saved strategies to hold those would defeat the point.
    """
    if not dsl or not dsl.strip():
        return None
    try:
        _str_provider_class()(dsl)
    except Exception as exc:
        # An error raised while transforming arrives wrapped in Lark's VisitError, whose
        # own first line only names the rule. The wrapped error says what is wrong.
        message = str(getattr(exc, "orig_exc", exc)).strip()
        # Lark's UnexpectedInput carries both; an unexpected end of input sets them to -1.
        line, column = getattr(exc, "line", 0), getattr(exc, "column", 0)
        positioned = isinstance(line, int) and isinstance(column, int) and line > 0 and column > 0
        return DslError(
            message.splitlines()[0] if message else exc.__class__.__name__,
            line if positioned else 0,
            column if positioned else 0,
        )
    return None


class DslWarning(NamedTuple):
    """A spell name in a DSL that parses, but that no card is named.

    ``line`` and ``column`` are 1-based, as in :class:`DslError`; ``length`` is how many
    characters of that line the name covers.
    """

    message: str
    line: int
    column: int
    length: int


#: Names ``SprintyCombat.try_get_spell`` answers itself, before it looks for a card.
_RESERVED_SPELL_NAMES = frozenset({"pass", "none", "willcast", "discard"})
#: Grammar nodes whose name is a card's: the move, its enchants, ``spell(…)``'s list, and
#: a name excluded inside ``any<…>`` (``!feint``). Not ``target_named`` — that name is a
#: combatant's.
_CARD_NAME_NODES = frozenset({"spell", "enchant", "second_enchant", "target_spell", "neg_name"})


@lru_cache(maxsize=512)
def dsl_warnings(dsl: str) -> tuple[DslWarning, ...]:
    """Every spell name in ``dsl`` that will never find a card, in the order written.

    A name that matches nothing is not a parse error: the move just never casts, and the
    fight moves on to the next one without saying why. ``spell_names.name_problem``
    decides what counts, against the snapshot of every card name.

    Empty for a DSL that does not parse — :func:`dsl_error` has the one thing to fix.

    Names come from the parse tree rather than the text, because the two differ: the
    grammar reads a number in an unquoted name a digit at a time, so ``Detonate All 200``
    reaches the card lookup as ``Detonate All 2 0 0``. Cached for the same reason as
    :func:`dsl_error`: this is a second parse of the same string.
    """
    if not dsl or not dsl.strip() or dsl_error(dsl) is not None:
        return ()
    from lark import Token, Tree  # noqa: PLC0415
    from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (  # noqa: PLC0415
        TreeToConfig,
    )

    from bartlebot import spell_names  # noqa: PLC0415

    transformer = TreeToConfig()
    warnings = []
    for node in _lark_parser().parse(dsl).iter_subtrees():
        if node.data not in _CARD_NAME_NODES:
            continue
        for child in node.children:
            if not isinstance(child, Tree) or child.data not in ("words", "string"):
                continue
            tokens = list(child.scan_values(lambda value: isinstance(value, Token)))
            if not tokens:
                continue
            literal = child.data == "string"
            name = transformer.transform(child)
            name = name[1:-1] if literal else name
            if name in _RESERVED_SPELL_NAMES:
                continue
            problem = spell_names.name_problem(name, literal=literal)
            if problem is None:
                continue
            first, last = tokens[0], tokens[-1]
            end = last.end_column if last.line == first.line else first.end_column
            warnings.append(DslWarning(problem, first.line, first.column, end - first.column))
    return tuple(sorted(warnings, key=lambda warning: (warning.line, warning.column)))


def dsl_warning_text(warnings: tuple[DslWarning, ...]) -> str:
    """The warnings as lines of text for a label, each message once."""
    return "\n".join(dict.fromkeys(warning.message for warning in warnings))


async def _run_combat(client, combat_dsl: str) -> None:
    """Run SprintyCombat for a single client using an inline DSL string."""
    from wizwalker.extensions.wizsprinter import SprintyCombat  # noqa: PLC0415

    provider = _str_provider_class()(combat_dsl)
    await SprintyCombat(client, provider).wait_for_combat()


async def seed_chat_baselines(machine, clients: list) -> None:
    """Give every client in *clients* a chat-log baseline if it does not have one.

    Called before a fight starts. A client that already has a baseline keeps it: that
    is what lets an announcement which missed the previous fight's poll window still be
    detected by this fight's. Re-snapshotting here would absorb it instead, which is
    the bug this whole path exists to fix.
    """
    unseeded = [c for c in clients if machine.chat_baseline(c) is None]
    if not unseeded:
        return
    snapshots = await asyncio.gather(*[drop_detection.snapshot_chat(c) for c in unseeded], return_exceptions=True)
    for client, snap in zip(unseeded, snapshots):
        # An unreadable chat log seeds "" rather than staying unseeded: leaving it None
        # would make the next fight seed from a log that already holds this fight's
        # drops, silently losing them. "" over-reports at worst, and only once.
        machine.set_chat_baseline(client, snap if isinstance(snap, str) else "")


async def poll_combat_drops(machine, clients: list, slots: list[int]) -> list[str]:
    """Watch each client's chat log for drops from the fight that just ended.

    ``clients`` and ``slots`` are parallel: ``slots[i]`` is the player slot of
    ``clients[i]``, which is what the drop is attributed to. They differ whenever the
    caller is handling a subset of the client list, as the ambient watcher does.

    Polls rather than reading once, because the announcement is a server round-trip
    that lands after the duel has locally ended — and end-of-fight is exactly when a
    long session lags hardest. Every client is polled concurrently: serially, a group
    of four would pay four full timeouts on a fight that dropped nothing.

    Returns the item names found, for logging. Each is already reported via
    ``machine.on_drop``.
    """

    async def _one(client, slot: int) -> list[str]:
        before = machine.chat_baseline(client)
        if before is None:
            # Never seeded — there is nothing to diff against, and treating this as ""
            # would report the entire existing chat log as drops. Seed and report none.
            machine.set_chat_baseline(client, await drop_detection.snapshot_chat(client))
            return []
        drops, baseline = await drop_detection.poll_new_drops(
            client, before, COMBAT_DROP_MESSAGE_TIMEOUT, COMBAT_DROP_MESSAGE_POLL
        )
        # Stored on a miss too: poll_new_drops hands back the caller's own baseline
        # unchanged, so a late announcement is still ahead of it next fight.
        machine.set_chat_baseline(client, baseline)
        for name in drops:
            machine.on_drop(name, 1, slot)
        return drops

    results = await asyncio.gather(*[_one(c, s) for c, s in zip(clients, slots)], return_exceptions=True)

    found: list[str] = []
    for slot, result in zip(slots, results):
        if isinstance(result, Exception):
            # A failed read must not fail the fight that already succeeded. Logged here
            # rather than through machine.log: this usually runs in the background, after
            # the node that started it has finished.
            logger.debug("Drop detection error on client %d: %s", slot, result)
            continue
        found.extend(result)
    return found


def start_combat_drop_poll(machine, clients: list, slots: list[int]) -> None:
    """Run ``poll_combat_drops`` for a finished fight in the background.

    The fight's caller returns at once instead of waiting out the poll window, which a
    fight that drops nothing pays in full. ``machine.start_drop_poll`` owns the task and
    cancels it when the next fight's poll starts; its docstring says why that is safe.
    """

    async def _poll() -> None:
        try:
            found = await poll_combat_drops(machine, clients, slots)
        except Exception:
            logger.exception("Drop poll for client(s) %s raised", slots)
            return
        if found:
            logger.info("Detected %d drop(s) on client(s) %s: %s", len(found), slots, ", ".join(found))
        else:
            logger.debug("No drops detected on client(s) %s", slots)

    machine.start_drop_poll(asyncio.create_task(_poll()))
