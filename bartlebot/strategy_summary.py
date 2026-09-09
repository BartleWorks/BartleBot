"""Qt-free summaries of a strategy — the shared vocabulary behind every card.

Pickers, the Strategy Library window, and the Combat tab panels each used to
format their own one-line description of the same models, and the three had
already drifted: ``1-4 players`` in one, ``Any players`` in another, for the
same config.  A :class:`StrategySummary` is the single place that formatting
happens, and every surface renders from one.

Everything here is pure and Qt-free, matching :mod:`bartlebot.combat_warnings`
and :mod:`bartlebot.combat_grouping` next door — no ``QApplication`` needed to
test it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from bartlebot.nodes.combat_runner import validate_dsl

if TYPE_CHECKING:
    from bartlebot.bot.schema import BotConfig
    from bartlebot.bot.strategy_models import (
        BotCombatStrategy,
        DeckPreset,
        DuelConfig,
        PlayerCombatStrategy,
    )

# Truncation length for an inline DSL preview — the picker's own figure, now
# canonical everywhere.  Surfaces that elide visually (the Combat tab's
# ElidedLabel) still benefit from the extra context in the tooltip.
_DSL_PREVIEW_CHARS = 70


class HealthStatus(Enum):
    OK = "ok"
    EMPTY = "empty"
    INVALID_DSL = "invalid_dsl"


@dataclass(frozen=True)
class Health:
    """Whether a strategy's DSL is safe to run, and why not when it isn't.

    EMPTY is not a problem — resolve_combat substitutes the built-in default
    for an unset config, same as validate_dsl treats an empty string as valid.
    It exists as its own status because a picker still wants to say "nothing
    configured yet" rather than "OK", which reads as "checked and fine".
    """

    status: HealthStatus
    reason: str = ""

    @property
    def is_ok(self) -> bool:
        return self.status is not HealthStatus.INVALID_DSL


_OK = Health(HealthStatus.OK)
_EMPTY = Health(HealthStatus.EMPTY)


@dataclass(frozen=True)
class AssignmentLine:
    """One player-assignment row within a duel config summary.

    ``players`` is pre-formatted ("P1", "P1–P3", "P1, P3") via
    :func:`format_players`.  A :class:`PlayerCombatStrategy` summary, which has
    no player assignments of its own, uses a single line with ``players=""``
    to carry its DSL preview instead.
    """

    players: str
    label: str  # linked preset name, inline DSL preview, or "(default)"
    is_linked: bool
    note: str = ""


@dataclass(frozen=True)
class UsageCount:
    """Where a strategy is already referenced — a delete confirmation or detail pane."""

    bots: int = 0
    duels: int = 0

    @property
    def total(self) -> int:
        return self.bots + self.duels

    def phrase(self) -> str:
        """ "Used by 3 bot strategies and 2 duel strategies" — one sentence, or "Not used yet".

        Lives here rather than in the dialog because a delete confirmation and a
        detail pane both say it, and two surfaces counting the same references
        differently is the drift :class:`StrategySummary` exists to prevent.
        """
        parts = []
        if self.bots:
            parts.append(f"{self.bots} bot strateg{'ies' if self.bots != 1 else 'y'}")
        if self.duels:
            parts.append(f"{self.duels} duel strateg{'ies' if self.duels != 1 else 'y'}")
        if not parts:
            return "Not used yet"
        return "Used by " + " and ".join(parts)


def find_references(uuid: str, bot_strategies=(), duel_strategies=()) -> UsageCount:
    """Count what would break if ``uuid`` were removed.

    Takes the lists rather than reading the store, for two reasons. The dialog
    holds a working set of *unsaved* copies, and a delete confirmation that
    counted what is on disk would miss a reference added a minute ago; and
    keeping it pure is what lets it be tested without a sandbox.

    Both kinds of reference are followed, whichever kind ``uuid`` names:

    - a ``BotCombatStrategy`` referencing a DuelCombatStrategy, as its default
      or through any state assignment
    - a ``DuelConfig`` referencing a PlayerCombatStrategy through an assignment,
      counted for the named library entries *and* for the inline configs
      embedded on a bot strategy

    A bot strategy naming the same id twice — its default plus a state override
    — counts once. What the user is being told is how many *things* change, not
    how many fields do.
    """
    bots = 0
    for bcs in bot_strategies:
        if bcs.default_strategy_id == uuid or any(a.duel_combat_strategy_id == uuid for a in bcs.state_assignments):
            bots += 1
            continue
        inline = [bcs.default_duel_config, *(a.duel_config for a in bcs.state_assignments)]
        if any(uuid in config.linked_pcs_ids() for config in inline):
            bots += 1
    duels = sum(1 for dcs in duel_strategies if uuid in dcs.linked_pcs_ids())
    return UsageCount(bots=bots, duels=duels)


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
    order = " → ".join(f"P{p}" for p in config.combat_order) if config.combat_order else "Simultaneous"
    return [players, order]


def _duel_health(config: DuelConfig, pcs_by_id: dict[str, PlayerCombatStrategy]) -> Health:
    if config.is_empty():
        return _EMPTY
    for assignment in config.player_assignments:
        if assignment.player_combat_strategy_id is not None:
            pcs = pcs_by_id.get(assignment.player_combat_strategy_id)
            dsl = pcs.dsl if pcs is not None else ""
        else:
            dsl = assignment.dsl
        error = validate_dsl(dsl)
        if error:
            return Health(HealthStatus.INVALID_DSL, error)
    return _OK


def summarize_duel(config: DuelConfig, all_pcs: list[PlayerCombatStrategy], name: str = "") -> StrategySummary:
    """Summarize a duel-level config — a linked DuelCombatStrategy or an inline one.

    Takes a bare ``DuelConfig`` (rather than requiring a ``DuelCombatStrategy``)
    so the same function covers an inline config with no name of its own; pass
    ``name`` explicitly for a named library entry.
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
        lines.append(AssignmentLine(players=players, label=label, is_linked=assignment.is_linked, note=assignment.note))
    return StrategySummary(
        name=name,
        description=config.description,
        facets=_duel_facets(config),
        schools=config.school_requirements(pcs_by_id),
        lines=lines,
        health=_duel_health(config, pcs_by_id),
    )


def summarize_player(pcs: PlayerCombatStrategy) -> StrategySummary:
    """Summarize a shared Player Combat Strategy preset."""
    dsl = pcs.dsl.strip()
    preview = _truncate(dsl, _DSL_PREVIEW_CHARS) if dsl else "(empty DSL)"
    facets = [pcs.school] if pcs.school else []
    lines = [AssignmentLine(players="", label=preview, is_linked=False)]
    if not dsl:
        health = _EMPTY
    else:
        error = validate_dsl(dsl)
        health = Health(HealthStatus.INVALID_DSL, error) if error else _OK
    return StrategySummary(name=pcs.name, description=pcs.description, facets=facets, lines=lines, health=health)


def summarize_bot(bcs: BotCombatStrategy, config: BotConfig | None = None) -> StrategySummary:
    """Summarize a Bot Combat Strategy: how many fights it overrides.

    ``config`` is accepted but not yet consulted — reserved for cross-checking
    against the bot's exposed combat states, the way ``collect_combat_warnings``
    already does.
    """
    del config
    n = sum(len(a.state_ids) for a in bcs.state_assignments)
    facets = [f"{n} state override{'s' if n != 1 else ''}"] if n else ["Default strategy only"]
    has_default = bool(bcs.default_strategy_id) or not bcs.default_duel_config.is_empty()
    health = _OK if (has_default or bcs.state_assignments) else _EMPTY
    return StrategySummary(name=bcs.name, description=bcs.description, facets=facets, health=health)


def summarize_deck(preset: DeckPreset) -> StrategySummary:
    """Summarize a Deck Preset: how many cards it holds."""
    total = preset.deck.total_cards()
    facets = [f"{total} cards"] if total else ["empty"]
    health = _EMPTY if preset.deck.is_empty() else _OK
    return StrategySummary(name=preset.name, description=preset.description, facets=facets, health=health)
