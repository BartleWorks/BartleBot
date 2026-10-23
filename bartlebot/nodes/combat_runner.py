"""Shared combat execution helpers used by handle_combat and the ambient combat watcher.

Extracted so that machine.py does not need to import from a specific node module.
"""

import asyncio
import contextlib
import enum
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, NamedTuple

from bartlebot.bot import run_trace
from bartlebot.game import drop_detection
from bartlebot.nodes.timing import (
    COMBAT_DROP_MESSAGE_POLL,
    COMBAT_DROP_MESSAGE_TIMEOUT,
    COMBAT_TP_RETRY_INTERVAL,
    FLEE_AFTER_ROUNDS,
    FLEE_CONFIRM_TIMEOUT,
    FLEE_TIMEOUT,
    FLEE_VOTE_TIMEOUT,
    POLL_INTERVAL,
    ROUND_REPORT_READS,
    ROUND_REPORT_RETRY_INTERVAL,
)
from bartlebot.shared.combat_defaults import LAST_RESORT_DSL

if TYPE_CHECKING:
    from bartlebot.combat.combat_resolution import Level

logger = logging.getLogger(__name__)

#: Spelled as ``combat_resolution.LAST_RESORT_SOURCE``, which this module cannot import at load.
_BUILTIN_SOURCE = "BartleBot built-in default"


class ResolvedCombat(NamedTuple):
    """What :func:`resolve_combat` hands a fight: a DSL per player slot, the order to enter, and where it came from."""

    dsls: list[str]
    order: list[int]
    source: str
    """The phrase the log names it by (``FightResolution.source_label``)."""
    level: "Level"
    """Which of the four levels answered, so a flee can say whether a default was running."""


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
    *,
    ambient: bool = False,
) -> ResolvedCombat:
    """Return the per-slot DSLs, combat order, source label and level for the fight *machine* is on.

    The duel comes from the four levels in :mod:`bartlebot.combat.combat_resolution`: the
    user's Bot Combat Strategy for the bot, the bot's Official one, the user's Solo or Team
    default, the shipped default. ``use_default_strategy`` is the node's param: True runs the
    bot's default fight, False the fight on ``machine.state``. ``ambient`` is a fight no node
    started, whose shipped Team default is Everyone Attacks.

    Each 1-based player slot gets its assignment's DSL, or the linked player strategy's.
    A slot with none, or whose player strategy cannot be read, gets ``LAST_RESORT_DSL``,
    and so does every slot when nothing can be read at all. Never raises: a fight always
    has something to run, and the label says what.
    """
    # Lazy imports to avoid circular imports and keep startup fast.
    from bartlebot.combat.combat_resolution import Level, resolve_fight  # noqa: PLC0415
    from bartlebot.settings_manager import SettingsManager  # noqa: PLC0415

    last_resort = ResolvedCombat([LAST_RESORT_DSL] * num_clients, [], _BUILTIN_SOURCE, Level.LAST_RESORT)
    bot_cfg = machine.bot_config
    if bot_cfg is None:
        return last_resort

    state_id = None if use_default_strategy else getattr(machine, "state", "")
    try:
        pick = SettingsManager.instance().get_active_strategy(bot_cfg.id)
        res = resolve_fight(bot_cfg, state_id, num_clients, pick, ambient=ambient)
    except Exception:
        logger.exception("resolve_combat: could not resolve the fight -- using the built-in default")
        return last_resort
    if res.duel is None:
        logger.error("resolve_combat: no strategy file could be read -- using the built-in default")
        return last_resort._replace(source=res.source_label)
    logger.debug("resolve_combat: %s", res.source_label)
    dcs = res.duel

    # Collect the DSL strings for assignments that link a player strategy.
    # Assignments with DSL of their own need no lookup.
    pcs_map: dict[str, str] = {}
    for pcs_id in dcs.linked_pcs_ids():
        if pcs_id not in pcs_map:
            pcs = res.lookup.player(pcs_id)
            if pcs is not None:
                pcs_map[pcs_id] = pcs.dsl
            else:
                logger.warning(
                    "resolve_combat: failed to load PlayerCombatStrategy %s — affected players use built-in default",
                    pcs_id,
                )
                pcs_map[pcs_id] = LAST_RESORT_DSL

    # Build per-client DSL list (1-based player numbers → 0-based client indices).
    per_client_dsl: list[str] = []
    for client_idx in range(num_clients):
        dsl = dcs.resolve_dsl_for_player(client_idx + 1, pcs_map)
        per_client_dsl.append(dsl if dsl else LAST_RESORT_DSL)

    combat_order: list[int] = dcs.combat_order if dcs.combat_order is not None else []

    return ResolvedCombat(per_client_dsl, combat_order, res.source_label, res.level)


# ---------------------------------------------------------------------------
# DSL parsing
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _lark_parser():
    """Build the wizsprinter combat grammar parser once and reuse it for checking DSLs.

    Building one compiles the grammar (~77 ms). ``validate_dsl`` parses with it so it
    keeps the tree, which the name warnings read too. A fight's provider
    (``CombatConfigProvider.from_string``) parses with wizsprinter's own parser, cached
    the same way since e82212b. Lark parsers are reusable across ``parse()`` calls, and
    one shared instance is safe across threads too: the GUI's background checks
    (``gui/combat_strategies/dsl_checks.py``) parse on a worker while the event loop may
    check another. lark's Earley parser sets its state when it is built and keeps each
    parse's in locals.
    """
    from lark import Lark  # noqa: PLC0415
    from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (  # noqa: PLC0415
        get_sprinty_grammar,
    )

    return Lark(get_sprinty_grammar())


def _combat_provider(dsl: str, cast_time: float = 0.2):
    """wizsprinter's combat config provider over a DSL string, as a fight runs one.

    Imported here, not at the top, so importing this module does not pull in wizwalker.
    """
    from wizwalker.extensions.wizsprinter.combat_backends.config_backend import (  # noqa: PLC0415
        CombatConfigProvider,
    )

    return CombatConfigProvider.from_string(dsl, cast_time)


async def parse_strategies(dsls: list[str]) -> None:
    """Parse each distinct DSL in a worker thread, before a fight makes its providers from them.

    wizsprinter keeps each text's parse, so the provider each player's combat makes then
    costs nothing, in this fight and every later one. Left to those providers, a long
    strategy was parsed once per player on the event loop: Preceptor of Order's held it
    2.3 s at the start of every fight, and the players started 0.8 s apart. In a thread
    the loop keeps running, sharing the interpreter lock with the parse.
    Never raises: a DSL that does not parse fails in the fight's own provider, as before.
    """
    for dsl in dict.fromkeys(dsls):
        try:
            await asyncio.to_thread(_combat_provider, dsl)
        except Exception:
            logger.debug("A strategy did not parse ahead of its fight", exc_info=True)


class DslError(NamedTuple):
    """Why a DSL does not parse, and where.

    ``line`` and ``column`` are 1-based, and 0 when the failure has no position — an
    unexpected end of input, or a transform error in a rule that holds no token.
    """

    message: str
    line: int = 0
    column: int = 0


def validate_dsl(dsl: str) -> str | None:
    """Return a one-line parse error for ``dsl``, or None if it is valid. See :func:`dsl_error`."""
    error = dsl_error(dsl)
    return error.message if error else None


def dsl_error(dsl: str) -> DslError | None:
    """Return why ``dsl`` does not parse, and where, or None if it is valid.

    The position is what the DSL editor underlines; everything else wants only the
    message, through :func:`validate_dsl`.

    An empty or whitespace-only DSL is reported as valid: ``resolve_dsl_for_player``
    returns None for it and ``resolve_combat`` substitutes the built-in default, so
    it is never executed.  A newly created preset starts empty by design and must
    not be flagged.

    Pure — no client, no Qt.  Used to surface syntax errors in the pre-run warning
    dialog instead of mid-fight.  Answered by :func:`dsl_check`, which caches it.
    """
    return dsl_check(dsl).error


def _first_token(tree):  # noqa: ANN001, ANN202 - a Lark Tree, imported lazily
    from lark import Token, Tree  # noqa: PLC0415

    if not isinstance(tree, Tree):
        return None
    return next(tree.scan_values(lambda value: isinstance(value, Token)), None)


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


class DslCheck(NamedTuple):
    """Everything checking a DSL says: why it does not parse, or the names in it no card has."""

    error: DslError | None
    warnings: tuple[DslWarning, ...] = ()


_BLANK = DslCheck(None)


def dsl_warnings(dsl: str) -> tuple[DslWarning, ...]:
    """Every spell name in ``dsl`` that will never find a card, in the order written.

    A name that matches nothing is not a parse error: the move just never casts, and the
    fight moves on to the next one without saying why. ``spell_names.name_problem``
    decides what counts, against the snapshot of every card name.

    Empty for a DSL that does not parse — :func:`dsl_error` has the one thing to fix.
    Answered by :func:`dsl_check`, from the same parse as the error.
    """
    return dsl_check(dsl).warnings


@lru_cache(maxsize=512)
def dsl_check(dsl: str) -> DslCheck:
    """Check ``dsl`` once: the parse error, or the spell names no card has.

    Cached twice over, because a check is the most expensive thing the GUI does. A parse
    is an Earley parse and grows faster than the text does — 4 ms for one move, 300 ms
    for a typical shipped player strategy, 800 ms for the longest — and the strategy
    dialog asks about every entry it lists. In memory, for the life of the process;
    ``maxsize`` is generous on purpose, since typing in the DSL editor mints a new string
    per pause and evicting the saved strategies to hold those would defeat the point.
    And on disk (:mod:`bartlebot.combat.dsl_check_cache`), so the next session does not
    parse the same strategies again: that was most of the first open of the strategy
    dialog.
    """
    if not dsl or not dsl.strip():
        return _BLANK
    cache = _disk_cache()
    stored = cache.get(dsl) if cache is not None else None
    if stored is not None:
        try:
            return _decoded(stored)
        except (TypeError, ValueError, KeyError):
            pass  # written by a different shape of this code; check it again
    result = _checked(dsl)
    if cache is not None:
        cache.put(dsl, _encoded(result))
    return result


def known_dsl_check(dsl: str) -> DslCheck | None:
    """The check for ``dsl`` if it is already known, in memory or on disk; never a parse.

    None means checking it would parse it. Every answer :func:`dsl_check` gives is
    stored in the disk cache's table, which is read once and kept in memory, so this is
    a hash and a lookup -- what a surface asks while drawing, before deciding whether to
    check the rest somewhere the user is not waiting on.
    """
    if not dsl or not dsl.strip():
        return _BLANK
    stored = _disk_cache().get(dsl)
    if stored is None:
        return None
    try:
        return _decoded(stored)
    except (TypeError, ValueError, KeyError):
        return None


def store_dsl_check(dsl: str, check: DslCheck) -> None:
    """Record *check*, worked out elsewhere, as the answer for ``dsl``.

    For a check run in another process (``gui/combat_strategies/dsl_checks.py``): once
    stored, :func:`known_dsl_check` and :func:`dsl_check` answer it without parsing.
    """
    if dsl and dsl.strip():
        _disk_cache().put(dsl, _encoded(check))


def check_dsl_uncached(dsl: str) -> DslCheck:
    """Check ``dsl`` from scratch: a parse, and no cache read or written.

    What a separate process runs, leaving the caches to the process that asked. Anything
    else wants :func:`dsl_check`.
    """
    if not dsl or not dsl.strip():
        return _BLANK
    return _checked(dsl)


def _checked(dsl: str) -> DslCheck:
    """Parse ``dsl`` once and answer both questions from that parse."""
    from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (  # noqa: PLC0415
        TreeToConfig,
    )

    try:
        tree = _lark_parser().parse(dsl)
        # The transform is where a fight's provider refuses what parses but can never
        # hold: an unknown attribute, a % on a count, a negated filter.
        TreeToConfig().transform(tree)
    except Exception as exc:
        return DslCheck(_error_from(exc))
    return DslCheck(None, _warnings_in(tree))


def _error_from(exc: Exception) -> DslError:
    # An error raised while transforming arrives wrapped in Lark's VisitError, whose
    # own first line only names the rule. The wrapped error says what is wrong.
    message = str(getattr(exc, "orig_exc", exc)).strip()
    # Lark's UnexpectedInput carries both; an unexpected end of input sets them to -1.
    # A transform error carries the rule's tree instead, so it is placed at the rule's
    # first token: the attribute in ``?(boss.hp > 0)``, the kind in ``gambit(chrams)``.
    line, column = getattr(exc, "line", 0), getattr(exc, "column", 0)
    token = _first_token(getattr(exc, "obj", None))
    if token is not None:
        line, column = token.line, token.column
    positioned = isinstance(line, int) and isinstance(column, int) and line > 0 and column > 0
    return DslError(
        message.splitlines()[0] if message else exc.__class__.__name__,
        line if positioned else 0,
        column if positioned else 0,
    )


def _warnings_in(tree) -> tuple[DslWarning, ...]:  # noqa: ANN001 - a Lark Tree, imported lazily
    """The spell names in a parsed DSL that no card has.

    Names come from the parse tree rather than the text, because the two differ: the
    grammar reads a number in an unquoted name a digit at a time, so ``Detonate All 200``
    reaches the card lookup as ``Detonate All 2 0 0``.
    """
    from lark import Token, Tree  # noqa: PLC0415
    from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (  # noqa: PLC0415
        TreeToConfig,
    )

    from bartlebot.game import spell_names  # noqa: PLC0415

    transformer = TreeToConfig()
    warnings = []
    for node in tree.iter_subtrees():
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


def _encoded(check: DslCheck) -> dict:
    return {
        "error": list(check.error) if check.error is not None else None,
        "warnings": [list(warning) for warning in check.warnings],
    }


def _decoded(stored: dict) -> DslCheck:
    error = stored["error"]
    return DslCheck(
        DslError(*error) if error is not None else None,
        tuple(DslWarning(*warning) for warning in stored["warnings"]),
    )


def _disk_cache():  # noqa: ANN202 - imported lazily
    """The on-disk cache for this build of the checker."""
    from bartlebot.combat import dsl_check_cache  # noqa: PLC0415

    return dsl_check_cache.shared_cache(_check_fingerprint())


@lru_cache(maxsize=1)
def _check_fingerprint() -> str:
    """A hash of everything an answer depends on besides the DSL itself.

    The grammar and the transform (wizsprinter's parser and backend modules), the
    spell-name snapshot and the code that reads it, and this module. A change to any of
    them discards the whole on-disk cache, which is cheaper than being clever about
    which answers it could have changed.
    """
    import hashlib  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from wizwalker.extensions.wizsprinter.combat_backends import (  # noqa: PLC0415
        combat_config_parser,
        config_backend,
    )

    from bartlebot.game import spell_names  # noqa: PLC0415

    digest = hashlib.sha256(b"dsl-check-v1")
    for path in (
        combat_config_parser.__file__,
        config_backend.__file__,
        spell_names.__file__,
        spell_names.NAMES_PATH,
        __file__,
    ):
        try:
            digest.update(Path(path).read_bytes())
        except OSError:
            digest.update(b"missing:" + str(path).encode())
    return digest.hexdigest()


def dsl_warning_text(warnings: tuple[DslWarning, ...]) -> str:
    """The warnings as lines of text for a label, each message once."""
    return "\n".join(dict.fromkeys(warning.message for warning in warnings))


async def _run_combat(client, combat_dsl: str, watch: "FightWatch | None" = None, *, number: int | None = None) -> None:
    """Run SprintyCombat for a single client using an inline DSL string.

    With a *watch*, the client asks it at every planning phase whether to flee instead of
    casting. Without one it fights to the end, as the authoring tools' test fight wants.
    *number*, the client's 1-based player number, tags its lines in the turn trace.
    """
    provider = _combat_provider(combat_dsl)
    log_tag = f"[P{number}]" if number is not None else ""
    if watch is None:
        from wizwalker.extensions.wizsprinter import SprintyCombat  # noqa: PLC0415

        await SprintyCombat(client, provider, log_tag=log_tag).wait_for_combat()
    else:
        await _watched_combat_class()(client, provider, watch, log_tag=log_tag).wait_for_combat()


# ---------------------------------------------------------------------------
# Running a fight, and fleeing one that cannot be won (#111)
# ---------------------------------------------------------------------------


class FightEnd(enum.Enum):
    """How :func:`run_fight` ended."""

    FINISHED = "finished"
    """The fight ended with nobody fleeing: won, or every one of our players defeated."""
    FLED = "fled"
    """At least one of our players fled."""
    STUCK = "stuck"
    """A flee was called for at the time limit, and the fight had still not ended
    FLEE_TIMEOUT later. The combat was cancelled; the players may still be in the fight."""


class FightResult(NamedTuple):
    end: FightEnd
    errors: list[tuple[int, BaseException]]
    """(player number, what its combat raised), for every player whose combat raised."""


class RoundReport(NamedTuple):
    """What one of our players saw at the start of a planning phase."""

    round: int
    ours_here: frozenset[int]
    """The global ids of our players in its duel, its own included."""
    has_damage: bool
    """It holds a castable damage card. False while it is defeated."""
    dot_on_enemies: bool
    """Some enemy carries damage over time, or damage deferred to a later round."""


@dataclass
class _Ballot:
    """One fight's reports for one planning phase."""

    expected: frozenset[int]
    votes: dict[int, RoundReport] = field(default_factory=dict)
    abstained: set[int] = field(default_factory=set)
    """Players whose report could not be read. Each counts as able to deal damage."""
    complete: asyncio.Event = field(default_factory=asyncio.Event)
    settled: bool = False

    def check(self) -> None:
        if self.expected <= self.votes.keys() | self.abstained:
            self.complete.set()


class FightWatch:
    """Decides, for each fight our players are in, when to flee it.

    One is shared by every player :func:`run_fight` runs, and each asks it at the start of
    every planning phase (:meth:`should_flee`). A fight is fled by every one of our players
    in it, never by one alone: a player out of damage is carried by the rest. It is fled

    - once, for FLEE_AFTER_ROUNDS planning phases in a row, none of our players in it has held
      a castable damage card and no enemy has carried damage over time; or
    - at once, for every fight, after :meth:`flee_all`.

    Players are grouped into fights by what each one's duel lists: two of ours share a fight
    when both are participants of it. Neither zone nor duel circle can tell, since two realms
    hold the same fight at the same coordinates. Grouping needs every player's global id, so
    a watch that cannot read one of them never decides a fight is unwinnable, and only
    :meth:`flee_all` can make its players flee.

    Every doubt counts against fleeing: a report that cannot be read, or that has not arrived
    within FLEE_VOTE_TIMEOUT, is read as "can still deal damage" and breaks the run of rounds.
    A player whose own report cannot be read abstains, which says so at once: staying silent
    left everyone else waiting out the timeout for it, 5 s of every round it happened in.
    """

    def __init__(self, numbers: dict, log: Callable[..., None]):
        self._numbers = numbers  # client -> 1-based player number, for log lines only
        self.log = log
        self._gids: dict = {}  # client -> global id; empty when any was unreadable
        self._ballots: dict[tuple[int, frozenset[int]], _Ballot] = {}
        self._abstentions: dict[int, set[int]] = {}  # round -> players who abstained in it
        self._streaks: dict[frozenset[int], int] = {}
        self._fleeing: list[frozenset[int]] = []
        self._flee_all_reason: str | None = None
        self.fled: set[int] = set()  # player numbers that carried out a flee

    async def identify(self) -> None:
        """Read every player's global id, which is its combat member's owner id."""

        async def _gid(client) -> int:
            return int(await client.client_object.global_id_full())

        clients = list(self._numbers)
        gids = await asyncio.gather(*(_gid(c) for c in clients), return_exceptions=True)
        if any(isinstance(g, BaseException) for g in gids):
            self.log("Fight watch: a player's id is unreadable; only the time limit can make them flee", logging.DEBUG)
            return
        self._gids = dict(zip(clients, gids))

    def flee_all(self, reason: str) -> None:
        """Make every player flee at its next planning phase."""
        if self._flee_all_reason is None:
            self._flee_all_reason = reason
            self.log(f"{reason}; fleeing", logging.WARNING)

    def note_fled(self, client) -> None:
        self.fled.add(self._numbers[client])

    def number(self, client) -> int:
        return self._numbers[client]

    async def should_flee(self, combat) -> bool:
        """Whether *combat*'s player should flee now rather than cast. Call once per planning phase."""
        if self._flee_all_reason is not None:
            return True
        gid = self._gids.get(combat.client)
        if gid is None:
            return False
        if self._flees(gid):
            return True
        report = await _round_report(combat, gid, frozenset(self._gids.values()))
        if report is None:
            self._abstain(gid, await _readable_round(combat))
            return False
        key = (report.round, report.ours_here)
        ballot = self._ballot(key)
        ballot.votes[gid] = report
        ballot.check()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ballot.complete.wait(), FLEE_VOTE_TIMEOUT)
        self._settle(key, ballot)
        return self._flees(gid)

    def _ballot(self, key: tuple[int, frozenset[int]]) -> _Ballot:
        """The ballot for *key*, made with any abstentions already cast in its round."""
        ballot = self._ballots.get(key)
        if ballot is None:
            ballot = self._ballots[key] = _Ballot(key[1])
            ballot.abstained |= self._abstentions.get(key[0], set()) & ballot.expected
        return ballot

    def _abstain(self, gid: int, round_num: int | None) -> None:
        """Count *gid* as able to deal damage in its fight this planning phase, so no one waits for it.

        Recorded against the round, for a ballot the others have not opened yet, and cast on
        every open ballot expecting it. With the round unreadable too, on every open one.
        """
        if round_num is not None:
            self._abstentions.setdefault(round_num, set()).add(gid)
        for (ballot_round, _ours), ballot in self._ballots.items():
            if gid in ballot.expected and not ballot.settled and round_num in (None, ballot_round):
                ballot.abstained.add(gid)
                ballot.check()

    def _flees(self, gid: int) -> bool:
        return any(gid in fight for fight in self._fleeing)

    def _settle(self, key: tuple[int, frozenset[int]], ballot: _Ballot) -> None:
        """Count one planning phase towards (or against) fleeing its fight. Once per ballot."""
        if ballot.settled:
            return
        ballot.settled = True
        fight = ballot.expected
        hopeless = (
            ballot.complete.is_set()
            and not ballot.abstained
            and not any(vote.has_damage or vote.dot_on_enemies for vote in ballot.votes.values())
        )
        if not hopeless:
            self._streaks[fight] = 0
            return
        streak = self._streaks.get(fight, 0) + 1
        self._streaks[fight] = streak
        what = f"{self._players(fight)}: no castable damage card and no damage over time on the enemies"
        if streak < FLEE_AFTER_ROUNDS:
            self.log(f"{what} (round {key[0]}, {streak} of {FLEE_AFTER_ROUNDS})", logging.DEBUG)
            return
        self._fleeing.append(fight)
        self.log(f"{what} for {streak} rounds; fleeing", logging.WARNING)

    def _players(self, gids: frozenset[int]) -> str:
        numbers = sorted(self._numbers[c] for c, gid in self._gids.items() if gid in gids)
        return ", ".join(f"P{n}" for n in numbers)


async def _round_report(combat, own_gid: int, our_gids: frozenset[int]) -> RoundReport | None:
    """What *combat*'s player sees at the start of this planning phase, or None if unreadable.

    Read from the duel's own participant list, never the combatant windows: the game rebuilds
    those at the start of every planning phase, and the first player there read 4 of a duel's
    8 (so its report named a different fight) or one mid-rebuild (MemoryInvalidated). Tried up
    to ROUND_REPORT_READS times.
    """
    for attempt in range(ROUND_REPORT_READS):
        if attempt:
            await asyncio.sleep(ROUND_REPORT_RETRY_INTERVAL)
        try:
            report = await _read_round_report(combat, own_gid, our_gids)
        except Exception as exc:
            logger.debug("Fight watch: round report unreadable (%r)", exc)
            continue
        if report is not None:
            return report
        logger.debug("Fight watch: the player is not among its duel's participants")
    return None


async def _read_round_report(combat, own_gid: int, our_gids: frozenset[int]) -> RoundReport | None:
    duel = combat.client.duel
    round_num = await duel.round_num()
    participants = [(p, int(await p.owner_id_full())) for p in await duel.participant_list()]
    me = next((p for p, owner in participants if owner == own_gid), None)
    if me is None:
        return None
    my_team = await me.team_id()
    enemies = [p for p, _owner in participants if await p.team_id() != my_team]
    defeated = await (await me.game_stats()).current_hitpoints() == 0
    has_damage = not defeated and await _holds_castable_damage(combat)
    dot_on_enemies = await _carries_damage_over_time(enemies)
    ours_here = frozenset(owner for _p, owner in participants if owner in our_gids)
    return RoundReport(round_num, ours_here, has_damage, dot_on_enemies)


async def _readable_round(combat) -> int | None:
    """The duel's round number, or None if it cannot be read."""
    try:
        return await combat.client.duel.round_num()
    except Exception:
        return None


async def _holds_castable_damage(combat) -> bool:
    """Whether the player holds a card it can cast now that damages an enemy."""
    from wizwalker.extensions.wizsprinter.combat_backends.combat_api import (  # noqa: PLC0415
        SpellType,
        TemplateSpell,
    )
    from wizwalker.extensions.wizsprinter.sprinty_combat import does_card_contain_reqs  # noqa: PLC0415

    damage = TemplateSpell([SpellType.type_damage])
    for card in await combat.get_castable_cards():
        if await does_card_contain_reqs(card, damage):
            return True
    return False


async def _carries_damage_over_time(participants: list) -> bool:
    """Whether any of *participants* has damage still to take from an effect already on it."""
    from wizwalker.memory import SpellEffects  # noqa: PLC0415

    pending = {SpellEffects.damage_over_time, SpellEffects.deferred_damage}
    for participant in participants:
        for effect in await participant.hanging_effects():
            if await effect.effect_type() in pending:
                return True
    return False


async def _flee(combat, watch: FightWatch) -> None:
    """Click Flee and confirm it. A miss is logged, and retried at the next planning phase."""
    from bartlebot.nodes import node_helpers  # noqa: PLC0415
    from bartlebot.nodes.paths import FLEE_BUTTON_PATH, FLEE_CONFIRM_PATH  # noqa: PLC0415

    client = combat.client
    number = watch.number(client)
    if not await node_helpers.click_by_path(client, FLEE_BUTTON_PATH):
        # A defeated player's Flee is on its defeat panel instead, which WizWalker knows.
        try:
            async with client.mouse_handler:
                await combat.flee_button()
        except Exception as exc:
            watch.log(f"P{number}: Flee could not be clicked ({exc!r}); trying again next round", logging.DEBUG)
            return

    async def _confirm_showing() -> bool:
        return await node_helpers.is_visible_by_path(client, FLEE_CONFIRM_PATH)

    if not await node_helpers.poll_until(_confirm_showing, FLEE_CONFIRM_TIMEOUT):
        watch.log(f"P{number}: clicked Flee, but no confirmation appeared; trying again next round", logging.DEBUG)
        return
    await node_helpers.click_by_path(client, FLEE_CONFIRM_PATH)
    watch.note_fled(client)
    watch.log(f"P{number}: fled", logging.INFO)


@lru_cache(maxsize=1)
def _watched_combat_class():
    """SprintyCombat that asks a FightWatch, each planning phase, whether to flee instead.

    Built lazily, so importing this module does not pull in wizwalker, and cached.
    """
    from wizwalker.extensions.wizsprinter import SprintyCombat  # noqa: PLC0415

    class _WatchedCombat(SprintyCombat):
        # The run_trace spans split each round into what the game took (the waits) and
        # what we took (the round itself: each priority tried, each condition, each cast).
        def __init__(self, client, provider, watch: FightWatch, *, log_tag: str = ""):
            super().__init__(client, provider, log_tag=log_tag)
            self.watch = watch
            self._tries = 0  # priorities tried this round, for the trace

        async def handle_combat(self):
            with run_trace.span("combat.fight", client=self.client):
                await super().handle_combat()

        async def wait_for_planning_phase(self, *args, **kwargs):
            with run_trace.span("combat.wait_planning"):
                await super().wait_for_planning_phase(*args, **kwargs)

        async def wait_until_next_round(self, current_round: int, *args, **kwargs):
            with run_trace.span("combat.wait_next_round", round=current_round):
                await super().wait_until_next_round(current_round, *args, **kwargs)

        async def handle_round(self):
            with run_trace.span("combat.round"):
                if run_trace.active():
                    run_trace.note(round=await self.round_number())
                self._tries = 0
                # Outside SprintyCombat's own mouse_handler block: the flee's clicks take it themselves.
                if await self.watch.should_flee(self):
                    run_trace.note(fled=True)
                    await _flee(self, self.watch)
                    return
                await super().handle_round()

        async def try_execute_config(self, move_config, willcasted: bool = False):
            self._tries += 1
            with run_trace.span("combat.priority", n=self._tries):
                result = await super().try_execute_config(move_config, willcasted)
                run_trace.note(result=repr(result))
                return result

        async def evaluate_condition(self, condition) -> bool:
            with run_trace.span("combat.condition"):
                return await super().evaluate_condition(condition)

    return _WatchedCombat


def _mark_retrieved(task: asyncio.Task) -> None:
    """Mark a finished combat task's exception as seen. :meth:`CombatRun.result` still reports it.

    One player's combat raising leaves the others fighting, and its error is read once they are
    done. A run cancelled first never gets there: crash recovery cancels the handle_combat node
    whose player died, and asyncio then reported that player's dead-client read as "Task exception
    was never retrieved", an ERROR with a traceback for a crash recovery had already handled.
    """
    if not task.cancelled():
        task.exception()


class CombatRun:
    """Every combat task of one fight session, sharing one FightWatch.

    :func:`run_fight` starts everyone at once. The ambient watcher starts each player as it
    is seen in battle, which is what lets a helper, or a bystander pulled into a fight of its
    own, have its combat run whenever it gets there.

    *numbers* maps every client that may fight to its 1-based player number, so the watch
    can group players by fight whichever of them turn up. The time limit runs from creation.
    """

    def __init__(self, numbers: dict, log: Callable[..., None], time_limit: float | None):
        self.watch = FightWatch(numbers, log)
        self._numbers = numbers
        self._deadline = None if time_limit is None else time.monotonic() + time_limit
        self._tasks: list[tuple[int, asyncio.Task]] = []
        self._live: dict = {}  # client -> its latest combat task
        self.stuck = False

    async def identify(self) -> None:
        await self.watch.identify()

    def start(self, client, dsl: str) -> bool:
        """Start *client*'s combat unless it is already running. True when it was started."""
        if self.fighting(client):
            return False
        task = asyncio.create_task(_run_combat(client, dsl, self.watch, number=self._numbers[client]))
        task.add_done_callback(_mark_retrieved)
        self._live[client] = task
        self._tasks.append((self._numbers[client], task))
        return True

    def fighting(self, client) -> bool:
        task = self._live.get(client)
        return task is not None and not task.done()

    @property
    def tasks(self) -> list[asyncio.Task]:
        return [task for _number, task in self._tasks]

    @property
    def busy(self) -> bool:
        return any(not task.done() for task in self.tasks)

    def check_time_limit(self) -> bool:
        """Enforce the time limit; call it as the fight goes on. True once combat is cancelled.

        Past the limit every fight is fled; FLEE_TIMEOUT after that, combat still running is
        cancelled and the run counts as stuck.
        """
        if self._deadline is None or self.stuck:
            return self.stuck
        now = time.monotonic()
        if now >= self._deadline:
            self.watch.flee_all("The fight ran past its time limit")
        if now >= self._deadline + FLEE_TIMEOUT and self.busy:
            self.cancel()
            self.stuck = True
        return self.stuck

    def cancel(self) -> None:
        for task in self.tasks:
            if not task.done():
                task.cancel()

    def result(self) -> FightResult:
        """How the run ended. Call once every task is done."""
        errors = [
            (number, task.exception())
            for number, task in self._tasks
            if task.done() and not task.cancelled() and task.exception() is not None
        ]
        if self.stuck:
            end = FightEnd.STUCK
        elif self.watch.fled:
            end = FightEnd.FLED
        else:
            end = FightEnd.FINISHED
        return FightResult(end, errors)


async def run_fight(
    clients: list,
    dsls: list[str],
    numbers: list[int],
    *,
    time_limit: float | None,
    log: Callable[..., None],
) -> FightResult:
    """Fight to the end with every one of *clients*, fleeing any fight that cannot be won.

    ``clients``, ``dsls`` and ``numbers`` are parallel; ``numbers`` are 1-based player numbers,
    used in log lines. *time_limit* is how many seconds from now every fight is fled, or None
    for no limit; zero or less flees at the first planning phase. *log* takes ``(message,
    level)``, so the caller decides whose logger the lines are filed under.

    One player's combat raising does not stop the others; it is reported in the result.
    """
    if not clients:
        return FightResult(FightEnd.FINISHED, [])
    await parse_strategies(dsls)
    run = CombatRun(dict(zip(clients, numbers)), log, time_limit)
    await run.identify()
    for client, dsl in zip(clients, dsls):
        run.start(client, dsl)
    try:
        _done, pending = await asyncio.wait(run.tasks, timeout=time_limit)
        if pending:
            run.watch.flee_all("The fight ran past its time limit")
            _done, pending = await asyncio.wait(pending, timeout=FLEE_TIMEOUT)
        if pending:
            run.stuck = True
            run.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        run.cancel()  # only anything left when this was cancelled from outside
    return run.result()


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


# ---------------------------------------------------------------------------
# Joining a fight already under way
# ---------------------------------------------------------------------------


@dataclass
class _Fight:
    publisher: object
    center: object
    members: list = field(default_factory=list)
    """Every client seen in battle in this fight, the publisher first."""
    center_blocked: bool = False
    """The center put a client back where it started instead of into the fight (#157)."""


class FightCenter:
    """The center of each fight our players are joining, published by the first of them seen in battle.

    Once a fight starts its mob walks to its seat, or is hidden or swapped, so a late client
    re-aiming at the mob aims at a moving or absent target (#110). The center of the duel
    circle stays put, and teleporting there joins the fight just as teleporting onto an
    engaged mob does.

    The center is read from the duel the client is in, and only once in_battle() has read
    true: outside a fight the duel struct still holds the last fight's data.

    *share* says whether one client's center is any use to another. None shares it with
    everybody, which is right when every player is on one realm. Otherwise pass the machine's
    record of who shares a realm: a center on another realm is the coordinates of a fight the
    client is not in, and teleporting there starts a second one. Each group then publishes a
    center of its own.

    Some centers cannot be stood on (#157). A zone can have an obstacle in its duel area that
    the fight clears, and the spot it took stays invalid afterwards: a player teleported there
    is put straight back at its last valid position, outside the circle. Once a client sees
    that, the center is marked blocked for the whole fight, and every later attempt aims at a
    player already in it instead. A seat is a spot inside the circle a player stands on.
    """

    def __init__(self, share: Callable[[object, object], bool] | None = None):
        self._share = share
        self._fights: list[_Fight] = []

    def _shares(self, fight: _Fight, client) -> bool:
        return self._share is None or fight.publisher is client or self._share(fight.publisher, client)

    def _fight_for(self, client) -> _Fight | None:
        """The fight *client* is in, else the first on its realm: the one it would join."""
        for fight in self._fights:
            if client in fight.members:
                return fight
        for fight in self._fights:
            if self._shares(fight, client):
                return fight
        return None

    def is_center_blocked(self, client) -> bool:
        """Whether the center *client* would aim at is known to put players back where they started."""
        fight = self._fight_for(client)
        return fight is not None and fight.center_blocked

    def mark_center_blocked(self, client) -> None:
        """Record that the center *client* aims at cannot be stood on, for every client joining that fight."""
        if (fight := self._fight_for(client)) is not None:
            fight.center_blocked = True

    async def member_position(self, client, skip: int = 0):
        """Where a player in *client*'s fight stands, or None when no member's position can be read.

        *skip* rotates which member comes first, so successive attempts try each in turn; a
        member whose position cannot be read is passed over for the next.
        """
        fight = self._fight_for(client)
        members = [m for m in fight.members if m is not client] if fight is not None else []
        for i in range(len(members)):
            if (where := await _position(members[(skip + i) % len(members)])) is not None:
                return where
        return None

    def groups(self) -> list[list]:
        """Every fight's members, each list in the order they were seen in battle."""
        return [list(fight.members) for fight in self._fights]

    def center_for(self, client):
        """The center *client* should aim at, or None while nobody it shares a realm with has one."""
        fight = self._fight_for(client)
        return fight.center if fight is not None else None

    async def publish(self, machine, client, index: int) -> None:
        """Record *client* as in its group's fight, publishing the center if there is none yet.

        Call only while *client* is in battle.
        """
        if any(client in fight.members for fight in self._fights):
            return
        try:
            center = await client.duel.position()
        except Exception as exc:
            if (fight := self._fight_for(client)) is not None:
                _join(fight, client)  # nothing better to go on than the realm
                return
            machine.log(
                f"Client {index}: fight center unreadable ({exc!r}); late clients keep aiming at the mob",
                logging.DEBUG,
            )
            return
        # A second fight on one realm has a circle of its own; one within a hair is this one.
        for fight in self._fights:
            if self._shares(fight, client) and _distance(fight.center, center) <= _SAME_CIRCLE:
                _join(fight, client)
                return
        self._fights.append(_Fight(client, center, [client]))
        where = await _position(client)
        machine.log(
            f"Client {index} is in battle; publishing its fight center"
            + (f" ({where.distance(center):.1f} units from it)" if where is not None else ""),
            logging.DEBUG,
        )

    async def is_over(self, client) -> bool:
        """Whether the fight *client* would join has ended: nobody seen in it is still in battle.

        False while *client* has no center to aim at, and False when a read fails: giving up on
        a fight still going leaves a player out of it, which is worse than one wasted teleport.
        """
        fight = self._fight_for(client)
        if fight is None:
            return False
        for member in fight.members:
            try:
                if await member.in_battle():
                    return False
            except Exception:
                return False
        return True


def _join(fight: _Fight, client) -> None:
    if client not in fight.members:
        fight.members.append(client)


#: Two duel centers this close are one circle. A center is read from the duel itself, so every
#: client in one fight reads the same point; this is only room for float noise.
_SAME_CIRCLE = 10.0


def _distance(a, b) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5


async def join_fight(
    machine,
    client,
    index: int,
    fight: FightCenter,
    timeout: float,
    *,
    find_target: Callable[[object], Awaitable[object]] | None = None,
) -> bool:
    """Teleport *client* into a fight, retrying every COMBAT_TP_RETRY_INTERVAL until it is in battle.

    Once *fight* has a center for the client, each attempt teleports there, and a client that
    gets in first publishes its own center for the rest. A center that puts the client back
    exactly where it started is marked blocked (see FightCenter), and from then on attempts aim
    at a player in the fight instead, a different one each time, or at the center again when
    no member's position can be read. Before that, *find_target* says what
    to aim at: handle_combat passes its find-the-mob, which is how a fight starts at all. The
    ambient watcher passes nothing, so a client only ever teleports to a published center: a
    teleport to "the closest mob" could start a second fight.

    Gives up, returning False, at *timeout* or once the fight it would join is over (see
    FightCenter.is_over): a late client teleporting onto an empty circle joins nothing.

    A target that disappears is waited out rather than given up on: an encounter that swaps
    or hides its mob as its fight starts may still pull the player in before the timeout.
    Giving up at once also said nothing, which left a player standing still for the rest
    of the wait with no line in the log to say why.

    Each re-teleport logs how far it moved the player. Zero means the player was already
    on the mob or the center, so the retry could not have started the fight.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    attempt = 0
    target_lost = False
    aimed_at = None
    member_attempts = 0
    waiting_noted = False
    while True:
        attempt += 1
        start = None  # where the client stood before a teleport to the center, to see it put back
        if (center := fight.center_for(client)) is not None:
            if await fight.is_over(client):
                machine.log(f"Client {index}: the fight ended before it could join", logging.DEBUG)
                return False
            target, aim = center, "the fight center"
            if fight.is_center_blocked(client):
                if (seat := await fight.member_position(client, member_attempts)) is not None:
                    target, aim = seat, "a player in the fight"
                    member_attempts += 1
            if aim != aimed_at:
                machine.log(f"Client {index}: teleporting to {aim} on attempt {attempt}", logging.DEBUG)
                aimed_at = aim
            before = await _position(client)
            try:
                await client.teleport(target)
            except Exception as exc:
                machine.log(f"Client {index}: teleport to {aim} failed ({exc!r})", logging.DEBUG)
            else:
                await _log_re_teleport(machine, client, index, attempt, before if attempt > 1 else None)
                if target is center:
                    start = before
        elif find_target is None:
            if not waiting_noted:
                machine.log(f"Client {index}: no fight center yet; waiting without teleporting", logging.DEBUG)
                waiting_noted = True
        elif (target := await find_target(client)) is None:
            if not target_lost:
                machine.log(
                    f"Client {index}: target not found on attempt {attempt}; waiting for the fight without teleporting",
                    logging.DEBUG,
                )
                target_lost = True
        else:
            if target_lost:
                machine.log(f"Client {index}: target found again on attempt {attempt}", logging.DEBUG)
                target_lost = False
            before = await _position(client) if attempt > 1 else None
            await client.tp_to(target)
            await _log_re_teleport(machine, client, index, attempt, before)

        # Poll in_battle() for up to COMBAT_TP_RETRY_INTERVAL (or remaining time). After a
        # teleport to the center, also watch for the game putting the client back where it
        # started: seen away from the start, then on it exactly. No distance threshold: the game
        # restores the stored last valid position, so the coordinates come back identical.
        retry_at = min(loop.time() + COMBAT_TP_RETRY_INTERVAL, deadline)
        moved = False
        while loop.time() < retry_at:
            if await client.in_battle():
                await fight.publish(machine, client, index)
                return True
            if start is not None and (now := await _position(client)) is not None:
                if not _same_place(now, start):
                    moved = True
                elif moved:
                    if not fight.is_center_blocked(client):  # known already if no seat could be read
                        fight.mark_center_blocked(client)
                        machine.log(
                            f"Client {index}: the fight center put it back where it started;"
                            " joining by a player in the fight instead",
                            logging.INFO,
                        )
                    break
            await asyncio.sleep(POLL_INTERVAL)

        if loop.time() >= deadline:
            break

    return False


async def duel_center(client):
    """The center of *client*'s duel, or None when it cannot be read."""
    try:
        return await client.duel.position()
    except Exception:
        return None


async def _log_re_teleport(machine, client, index: int, attempt: int, before) -> None:
    """Log how far a re-teleport moved the player; *before* is None on the first attempt."""
    if before is not None and (after := await _position(client)) is not None:
        machine.log(
            f"Client {index}: not in battle; re-teleport {attempt} moved {before.distance(after):.1f} units",
            logging.DEBUG,
        )


def _same_place(a, b) -> bool:
    """Whether two positions are identical. XYZ defines no equality of its own."""
    return tuple(a) == tuple(b)


async def _position(client):
    """The client's position, or None when it cannot be read; only a log line depends on it."""
    try:
        return await client.body.position()
    except Exception:
        return None
