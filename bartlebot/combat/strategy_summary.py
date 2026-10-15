"""Qt-free summaries of a strategy — the shared vocabulary behind every card.

Pickers, the Strategy Library window, and the Combat tab panels each used to
format their own one-line description of the same models, and the three had
already drifted: ``1-4 players`` in one, ``Any players`` in another, for the
same config.  A :class:`StrategySummary` is the single place that formatting
happens, and every surface renders from one.

Everything here is pure and Qt-free, matching :mod:`bartlebot.combat.combat_warnings`
and :mod:`bartlebot.combat.combat_grouping` next door — no ``QApplication`` needed to
test it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from bartlebot.combat.slot_order import unfixed_slot_uses
from bartlebot.content.store_roots import Origin
from bartlebot.nodes.combat_runner import validate_dsl

#: Answers a DSL's parse error, or None: :func:`validate_dsl`, or a stand-in that answers
#: only from checks already known (``gui/combat_strategies/dsl_checks.py``).
Validate = Callable[[str], str | None]

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig
    from bartlebot.combat.bot_strategy_models import BotCombatStrategy
    from bartlebot.shared.strategy_models import DeckPreset, DuelConfig, PlayerCombatStrategy

# Truncation length for an inline DSL preview — the picker's own figure, now
# canonical everywhere.  Surfaces that elide visually (the Combat tab's
# ElidedLabel) still benefit from the extra context in the tooltip.
_DSL_PREVIEW_CHARS = 70


class HealthStatus(Enum):
    OK = "ok"
    EMPTY = "empty"
    INVALID_DSL = "invalid_dsl"
    #: Runs, but a strategy aims at a slot the duel leaves to chance (:mod:`~bartlebot.combat.slot_order`).
    SLOT_ORDER = "slot_order"


@dataclass(frozen=True)
class Health:
    """Whether a strategy's DSL is safe to run, and why not when it isn't.

    EMPTY is not a problem — resolve_combat substitutes the built-in default
    for an unset config, same as validate_dsl treats an empty string as valid.
    It exists as its own status because a picker still wants to say "nothing
    configured yet" rather than "OK", which reads as "checked and fine".

    SLOT_ORDER is not a problem either, in that the strategy runs: it is a warning, which
    the user may mean (``is_warning``).
    """

    status: HealthStatus
    reason: str = ""

    @property
    def is_ok(self) -> bool:
        return self.status is not HealthStatus.INVALID_DSL

    @property
    def is_warning(self) -> bool:
        return self.status is HealthStatus.SLOT_ORDER


_OK = Health(HealthStatus.OK)
_EMPTY = Health(HealthStatus.EMPTY)

#: A Bot Combat Strategy's facet with no fight overridden: its default covers every fight,
#: or it has no default either.
DEFAULT_ONLY = "Default strategy only"
NOTHING_ASSIGNED = "Nothing assigned"


@dataclass(frozen=True)
class AssignmentLine:
    """One player-assignment row within a duel config summary.

    ``players`` is pre-formatted ("P1", "P1–P3", "P1, P3") via
    :func:`format_players`.  A :class:`PlayerCombatStrategy` summary, which has
    no player assignments of its own, uses a single line with ``players=""``
    to carry its DSL preview instead.

    ``slots`` is the same players as numbers, sorted, for a surface that draws one
    account-colored pill per slot -- a range like ``P1–P3`` cannot wear three
    accounts' colors, so the Combat tab needs the members rather than the phrase.
    """

    players: str
    label: str  # linked preset name, inline DSL preview, or "(default)"
    is_linked: bool
    note: str = ""
    slots: tuple[int, ...] = ()


#: How many uses a usage sentence names before it counts the rest.
_NAMED_USES = 3


def name_list(names: list[str] | tuple[str, ...], limit: int = _NAMED_USES) -> str:
    """ "A", "A and B", "A, B and C", or "A, B, C and 2 more" — names already quoted."""
    names = list(names)
    if len(names) > limit:
        return f"{', '.join(names[:limit])} and {len(names) - limit} more"
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


@dataclass(frozen=True)
class UsageCount:
    """Where a strategy is already referenced — a delete confirmation or detail pane.

    ``bot_names`` and ``names`` say *what* uses it, one entry per use, already quoted: a
    Bot Combat Strategy as ``'Solo'`` and a duel as ``'Boss — P2'``, the duel's name and
    the slots that run this strategy in it. A count alone made a user go looking (#118).
    The two are kept apart so the phrase can name the level once, before its names,
    rather than once per name.
    """

    bots: int = 0
    duels: int = 0
    names: tuple[str, ...] = ()
    bot_names: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return self.bots + self.duels

    def phrase(self) -> str:
        """ "Used by the Bot Combat Strategy 'Solo' and 'Boss — P2'" — one sentence, or "Not used yet".

        Lives here rather than in the dialog because a delete confirmation and a
        detail pane both say it, and two surfaces counting the same references
        differently is the drift :class:`StrategySummary` exists to prevent. A count
        with no names behind it is said as counts.
        """
        if self.names or self.bot_names:
            parts = []
            if self.bot_names:
                noun = _bot_strategies_noun(len(self.bot_names))
                parts.append(f"the {noun} {name_list(self.bot_names)}")
            if self.names:
                parts.append(name_list(self.names))
            # A comma once either side is itself a list, so the "and" inside it is not misread.
            joiner = " and " if len(self.bot_names) <= 1 and len(self.names) <= 1 else ", and "
            return "Used by " + joiner.join(parts)
        parts = []
        if self.bots:
            parts.append(f"{self.bots} {_bot_strategies_noun(self.bots)}")
        if self.duels:
            parts.append(f"{self.duels} duel strateg{'ies' if self.duels != 1 else 'y'}")
        if not parts:
            return "Not used yet"
        return "Used by " + " and ".join(parts)


def _bot_strategies_noun(count: int) -> str:
    return "Bot Combat Strategy" if count == 1 else "Bot Combat Strategies"


def _duel_use(duel, uuid: str) -> str | None:
    """``'Boss — P2'`` if *duel* links *uuid*, naming the slots that run it; else None."""
    players = sorted({p for a in duel.player_assignments if a.player_combat_strategy_id == uuid for p in a.players})
    if not any(a.player_combat_strategy_id == uuid for a in duel.player_assignments):
        return None
    return f"'{duel.name} — {format_players(players)}'" if players else f"'{duel.name}'"


def find_references(uuid: str, bot_strategies=(), duel_strategies=()) -> UsageCount:
    """Count what would break if ``uuid`` were removed.

    Takes the lists rather than reading the store, for two reasons. The dialog
    holds a working set of *unsaved* copies, and a delete confirmation that
    counted what is on disk would miss a reference added a minute ago; and
    keeping it pure is what lets it be tested without a sandbox.

    Both kinds of reference are followed, whichever kind ``uuid`` names:

    - a ``BotCombatStrategy`` referencing a DuelCombatStrategy, as its default
      or through any state assignment
    - a ``DuelCombatStrategy`` referencing a PlayerCombatStrategy through an
      assignment — a library duel, or one of a Bot Combat Strategy's own (#118).
      Missing the second kind would call a library player strategy that only one
      bot's own duel uses "Not used yet", and delete it without a warning.

    A Bot Combat Strategy naming the same id twice — its default plus a state override
    — counts once. What the user is being told is how many *things* change, not
    how many fields do.
    """
    bots = 0
    duels = 0
    names: list[str] = []
    bot_names: list[str] = []
    for bcs in bot_strategies:
        if bcs.default_strategy_id == uuid or any(a.duel_combat_strategy_id == uuid for a in bcs.state_assignments):
            bots += 1
            bot_names.append(f"'{bcs.name}'")
    for duel in [*(d for bcs in bot_strategies for d in bcs.duel_strategies), *duel_strategies]:
        use = _duel_use(duel, uuid)
        if use is not None:
            duels += 1
            names.append(use)
    return UsageCount(bots=bots, duels=duels, names=tuple(names), bot_names=tuple(bot_names))


@dataclass(frozen=True)
class StrategySummary:
    """Everything a card needs to render, for any of the four strategy kinds."""

    name: str
    description: str
    facets: list[str] = field(default_factory=list)
    schools: dict[str, int] = field(default_factory=dict)
    lines: list[AssignmentLine] = field(default_factory=list)
    health: Health = field(default_factory=lambda: _OK)
    usage: UsageCount | None = None
    #: A duel's combat order as slot numbers, empty for simultaneous entry or for any
    #: other kind. The facet already says it in words; this is for a surface that draws
    #: the order as colored pills and has to leave that facet out when it does.
    combat_order: tuple[int, ...] = ()
    #: Official, modified or custom, for a surface that shows it; None where the question
    #: does not arise. Supplied by whoever knows the working set — the summarize_*
    #: functions see one entry and cannot tell (#124).
    source: Origin | None = None
    #: Whether the entry overrides an Official one that has changed since (#169): its
    #: pill then adds "Official updated". Supplied with ``source``, by the same caller.
    official_updated: bool = False
    #: The Bot Combat Strategy's name when the entry is one of its own rather than the
    #: library's (#118); None for a library entry. A card shows it in place of ``source``.
    bot_strategy_name: str | None = None
    #: Stored tags (the Role group of :mod:`bartlebot.combat.strategy_tags`), in vocabulary order.
    tags: tuple[str, ...] = ()
    #: The schools a deck is built for, in the order it lists them, for a card that draws
    #: each beside its icon. The same schools the first facet spells out in words.
    school_names: tuple[str, ...] = ()
    #: One school set per player slot, empty meaning any school — what the "works with"
    #: filter asks. One slot for a Player Strategy or a deck (#174), ``max_players`` for a
    #: duel (#125).
    slot_schools: tuple[frozenset[str], ...] = ()
    #: A duel's ``(min_players, max_players)``, for the team-size filter; None otherwise.
    player_range: tuple[int, int] | None = None

    def subtitle_line(self) -> str:
        """One line for a compact card: the facets, plus a bare DSL preview if
        that is all this summary has (a PlayerCombatStrategy has no facets of
        its own beyond an optional school).
        """
        parts = list(self.facets)
        if len(self.lines) == 1 and not self.lines[0].players:
            parts.append(self.lines[0].label)
        return " · ".join(parts)


def format_players(players: list[int]) -> str:
    """'P1', 'P1-P3', or 'P1, P3' depending on whether the run is contiguous."""
    ordered = sorted(players)
    if not ordered:
        return "no players"
    if len(ordered) == 1:
        return f"P{ordered[0]}"
    if ordered == list(range(ordered[0], ordered[-1] + 1)):
        return f"P{ordered[0]}–P{ordered[-1]}"
    return ", ".join(f"P{p}" for p in ordered)


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if not text:
        return ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _duel_facets(config: DuelConfig) -> list[str]:
    if config.min_players == 1 and config.max_players == 4:
        players = "Any players"
    elif config.min_players == config.max_players:
        players = f"{config.min_players} player{'s' if config.min_players != 1 else ''}"
    else:
        players = f"{config.min_players}–{config.max_players} players"
    return [players, format_combat_order(config.combat_order)]


def format_combat_order(order: list[int] | tuple[int, ...]) -> str:
    """'P3 ← P2 ← P1', or 'Simultaneous' for an empty order -- the combat-order facet.

    Right to left, because that is the order a duel lines a team up in: slot 1 is the
    rightmost wizard and turns run clockwise from there. Every surface that shows a combat
    order is mirrored the same way -- the editor, the summary pills, and this text, which
    is what the pills' tooltip says (#29). The arrow keeps meaning "then" and turns with
    the row, so its head is on whoever enters *after*.

    *order* is first-to-last, as it is stored and as every caller has it; the reversal
    happens here and only here.
    """
    return " ← ".join(f"P{p}" for p in reversed(order)) if order else "Simultaneous"


def slot_order_warning(config: DuelConfig, pcs_dsl: dict[str, str]) -> str | None:
    """Say which players aim at a slot this duel leaves to chance, or None.

    ``pcs_dsl`` maps a linked player strategy's id to its DSL.
    """
    uses = unfixed_slot_uses(config, pcs_dsl)
    if not uses:
        return None
    clauses = [
        f"{format_players(players)} {'targets' if len(players) == 1 else 'target'} {' and '.join(slots)}"
        for players, slots in uses
    ]
    return (
        f"{'; '.join(clauses)}, a slot on the team rather than a player. With no combat order, "
        "players fill the slots in the order they arrive, so that slot can be a different "
        "player each fight. Set a combat order to fix who is in which slot."
    )


def _duel_health(
    config: DuelConfig, pcs_by_id: dict[str, PlayerCombatStrategy], validate: Validate = validate_dsl
) -> Health:
    if config.is_empty():
        return _EMPTY
    for assignment in config.player_assignments:
        if assignment.player_combat_strategy_id is not None:
            pcs = pcs_by_id.get(assignment.player_combat_strategy_id)
            dsl = pcs.dsl if pcs is not None else ""
        else:
            dsl = assignment.dsl
        error = validate(dsl)
        if error:
            return Health(HealthStatus.INVALID_DSL, error)
    warning = slot_order_warning(config, {pcs_id: pcs.dsl for pcs_id, pcs in pcs_by_id.items()})
    if warning:
        return Health(HealthStatus.SLOT_ORDER, warning)
    return _OK


def summarize_duel(
    config: DuelConfig, all_pcs: list[PlayerCombatStrategy], name: str = "", *, validate: Validate = validate_dsl
) -> StrategySummary:
    """Summarize a duel-level config. Pass ``name`` for a named DuelCombatStrategy.

    *validate* decides the health; the default parses whatever it has not seen.
    """
    pcs_by_id = {p.id: p for p in all_pcs}
    lines: list[AssignmentLine] = []
    for assignment in config.player_assignments:
        if not assignment.players:
            continue
        players = format_players(assignment.players)
        if assignment.player_combat_strategy_id is not None:
            preset = pcs_by_id.get(assignment.player_combat_strategy_id)
            label = preset.name if preset is not None else "(unknown)"
        else:
            dsl = _truncate(assignment.dsl, _DSL_PREVIEW_CHARS)
            label = dsl or "(default)"
        lines.append(
            AssignmentLine(
                players=players,
                label=label,
                is_linked=assignment.is_linked,
                note=assignment.note,
                slots=tuple(sorted(assignment.players)),
            )
        )
    return StrategySummary(
        name=name,
        description=config.description,
        facets=_duel_facets(config),
        schools=config.school_requirements(pcs_by_id),
        lines=lines,
        health=_duel_health(config, pcs_by_id, validate),
        combat_order=tuple(config.combat_order or ()),
        slot_schools=tuple(config.slot_schools(pcs_by_id)),
        player_range=(config.min_players, config.max_players),
    )


def summarize_player(pcs: PlayerCombatStrategy, *, validate: Validate = validate_dsl) -> StrategySummary:
    """Summarize a shared Player Combat Strategy preset. *validate* as for :func:`summarize_duel`."""
    dsl = pcs.dsl.strip()
    preview = _truncate(dsl, _DSL_PREVIEW_CHARS) if dsl else "(empty DSL)"
    facets = ["/".join(pcs.schools)] if pcs.schools else []
    lines = [AssignmentLine(players="", label=preview, is_linked=False)]
    if not dsl:
        health = _EMPTY
    else:
        error = validate(dsl)
        health = Health(HealthStatus.INVALID_DSL, error) if error else _OK
    return StrategySummary(
        name=pcs.name,
        description=pcs.description,
        facets=facets,
        lines=lines,
        health=health,
        tags=tuple(pcs.tags),
        slot_schools=(frozenset(pcs.schools),),
    )


def summarize_bot(bcs: BotCombatStrategy, config: BotConfig | None = None) -> StrategySummary:
    """Summarize a Bot Combat Strategy: how many fights it overrides.

    With no override, the facet says whether its default covers every fight or it
    assigns nothing at all, so every fight falls to the Default Duel Strategies.

    ``config`` is accepted but not yet consulted — reserved for cross-checking
    against the bot's exposed combat states, the way ``check_combat``
    already does.
    """
    del config
    n = sum(len(a.fights) for a in bcs.state_assignments)
    if n:
        facets = [f"{n} state override{'s' if n != 1 else ''}"]
    else:
        facets = [DEFAULT_ONLY if bcs.default_strategy_id else NOTHING_ASSIGNED]
    health = _OK if (bcs.default_strategy_id or bcs.state_assignments) else _EMPTY
    return StrategySummary(name=bcs.name, description=bcs.description, facets=facets, health=health)


def summarize_deck(preset: DeckPreset) -> StrategySummary:
    """Summarize a Deck Preset: the schools it is built for, and how many cards it holds."""
    total = preset.deck.total_cards()
    facets = ["/".join(preset.schools)] if preset.schools else []
    facets.append(f"{total} cards" if total else "empty")
    health = _EMPTY if preset.deck.is_empty() else _OK
    return StrategySummary(
        name=preset.name,
        description=preset.description,
        facets=facets,
        health=health,
        tags=tuple(preset.tags),
        school_names=tuple(preset.schools),
        slot_schools=(frozenset(preset.schools),),
    )
