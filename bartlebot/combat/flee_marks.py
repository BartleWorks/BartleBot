"""Fights fled on a default this session, and the words the Combat tab marks them with (Stage 7 of epic #201).

The moment a user is most ready to learn that combat strategies exist is right after a
fight goes badly. A fight fled while it ran a Solo or Team default (levels 3 and 4 of
:mod:`bartlebot.combat.combat_resolution`) is counted here, and the Combat tab marks it
where it is drawn: its own pill on the default card, or, for a fight the sidebar does not
list, the default card itself, or the banner when the bot has no Bot Combat Strategy.
Never a dialog: a dialog at the end of a run is an interruption by another name.

One :class:`FleeMarks` lives as long as the app, owned by ``AppController``, and nothing
is written to disk: the marks last until the app closes. Only a node's fight is counted.
An ambient fight is no fight of the bot's, and an idle-popup flee means nothing was
fighting, so neither has a fight on the tab to mark.

A mark is shown only while its fight would still run a default (:func:`fled_on_default`).
A fight given a strategy of its own since, or covered by a Bot Combat Strategy's default,
loses its mark, and gets it back with its count if that strategy goes. Qt-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from bartlebot.combat.combat_grouping import state_label
from bartlebot.combat.combat_resolution import DEFAULT_FIGHT, CombatResolver
from bartlebot.combat.fight_keys import exposed_combat_states

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from bartleby.shared.preferences import PreferenceStore

    from bartlebot.bot.schema import BotConfig
    from bartlebot.combat.combat_resolution import Level


class FleeMarks:
    """How many times each fight was fled on a default this session, per bot."""

    def __init__(self) -> None:
        self._counts: dict[str, dict[str, int]] = {}

    def record(self, bot_id: str, state_id: str | None, level: Level) -> bool:
        """Count one flee of *state_id* on *level*. True when it counted: a default, on a state.

        A flee on anything the user or an author chose counts for nothing here, and nor
        does one on the last resort, which runs when no file can be read.
        """
        if not level.is_default or not state_id:
            return False
        fights = self._counts.setdefault(bot_id, {})
        fights[state_id] = fights.get(state_id, 0) + 1
        return True

    def counts(self, bot_id: str | None) -> dict[str, int]:
        """Each of the bot's fights fled on a default this session, by state id, with how often."""
        return dict(self._counts.get(bot_id, {})) if bot_id else {}


@dataclass(frozen=True)
class FledFight:
    """A fight fled on a default this session, which would still run one now."""

    state_id: str
    label: str
    count: int


def fled_on_default(
    marks: FleeMarks,
    bot_config: BotConfig | None,
    clients: int,
    pick: str | None,
    *,
    prefs: PreferenceStore | None = None,
    root: str | Path | None = None,
    user_root: str | Path | None = None,
) -> list[FledFight]:
    """The bot's marked fights that would still run a default now, in the bot's state order.

    Each is resolved as a run would resolve it: an exposed fight as itself, any other as
    the bot's default fight. A state since deleted is left out. Nothing is written.
    """
    if bot_config is None:
        return []
    counts = marks.counts(bot_config.id)
    if not counts:
        return []
    exposed = {state.id for state in exposed_combat_states(bot_config)}
    resolver = CombatResolver(bot_config, max(clients, 1), pick, prefs=prefs, root=root, user_root=user_root)
    fled = []
    for state in bot_config.states:
        count = counts.get(state.id)
        if not count:
            continue
        fight = resolver.fight(state.id if state.id in exposed else DEFAULT_FIGHT)
        if fight.level.is_default:
            fled.append(FledFight(state.id, state_label(state), count))
    return fled


def fled_text(count: int) -> str:
    """The mark on a card or the banner, for every fight it covers: ``Fled 2× on the default``."""
    return f"Fled {count}× on the default"


def fled_pill_text(label: str, count: int) -> str:
    """A fight's own pill, marked: ``Lost Soul · fled 2×``."""
    return f"{label} · fled {count}×"


def fled_fight_tooltip(label: str, count: int) -> str:
    """The tooltip on a marked fight's pill, which is also where the fix is."""
    return (
        f"{label} was fled {count}× this session on the default duel strategy. Click to give it a strategy of its own."
    )


def fled_tooltip(fights: Iterable[FledFight], fix: str) -> str:
    """The tooltip on a card's or the banner's mark: which fights, how often, and *fix*, what to do."""
    named = ", ".join(f"{fight.label} {fight.count}×" for fight in fights)
    return f"Fled this session on the default duel strategy: {named}.\n{fix}"
