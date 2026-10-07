"""Pydantic models for the three-level combat strategy system.

PlayerCombatStrategy  — an optional *shared preset*: a named WizSprinter DSL
                        string reusable across duels.
DuelCombatStrategy    — team-level config for one type of fight
BotCombatStrategy     — one user's combat profile for a bot: FSM states to strategies,
                        deck data, and duel/player strategies of its own
DeckPreset            — named, reusable deck configuration in the global library

The first two and the deck models belong to the library shared with every product by
this vendor, so they live in :mod:`bartlebot.shared.strategy_models` and are imported
from there; they are re-exported here because every combat surface imports them from
this module. Only the Bot Combat Strategy is BartleBot's own, since only BartleBot has
bots.

Each top-level model is stored as an individual JSON file identified by UUID.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from bartlebot.shared.strategy_models import (
    DeckData,
    DeckPreset,
    DuelCombatStrategy,
    DuelConfig,
    PlayerAssignment,
    PlayerCombatStrategy,
    atomic_write,
)

__all__ = [
    "BotCombatStrategy",
    "DeckData",
    "DeckPreset",
    "DuelCombatStrategy",
    "DuelConfig",
    "FightRef",
    "PlayerAssignment",
    "PlayerCombatStrategy",
    "StateAssignment",
]


class FightRef(BaseModel):
    """One fight an assignment covers: the state it was on, and the enemy it named.

    ``enemy`` is the node's ``enemy`` param when that names the fight uniquely in its
    bot, and is what lets the strategy follow a fight whose node was replaced. None
    for a fight that takes the closest mob, or one written before fight keys. Placing
    a stored fight on a current state is :func:`bartlebot.fight_keys.bind`'s job.
    """

    state: str
    enemy: str | None = None


def _fights_from_legacy(data: object) -> object:
    """Lift a pre-fight-keys ``state_ids`` list into ``fights`` with no enemy.

    A file carrying both keeps ``fights`` and drops the stray ``state_ids``. Never
    written back on load: the next save writes the new shape.
    """
    if isinstance(data, dict) and "state_ids" in data:
        data = dict(data)
        state_ids = data.pop("state_ids")
        if "fights" not in data:
            data["fights"] = [{"state": sid} for sid in state_ids or []]
    return data


class StateAssignment(BaseModel):
    """A group of fights and the DuelCombatStrategy they fight with.

    Unlinked means the fights fall back to the profile's default. There is no inline
    config: a one-off strategy lives in the profile's own ``duel_strategies`` and is
    linked by id like any other (#118). A file from before that still loads, because
    the models do not forbid extra fields, and its stray ``duel_config`` is ignored.
    """

    fights: list[FightRef] = Field(default_factory=list)
    duel_combat_strategy_id: str | None = None  # UUID of a DuelCombatStrategy, in the profile or the library

    _legacy_state_ids = model_validator(mode="before")(_fights_from_legacy)

    @property
    def state_ids(self) -> list[str]:
        """The state each fight was stored on, in order. Read-only: edit ``fights``."""
        return [fight.state for fight in self.fights]

    @property
    def is_linked(self) -> bool:
        """True if this assignment points at a DuelCombatStrategy."""
        return self.duel_combat_strategy_id is not None

    def add_state(self, state_id: str) -> None:
        """Cover *state_id* too, unless a fight here is already on it."""
        if state_id not in self.state_ids:
            self.fights.append(FightRef(state=state_id))

    def remove_state(self, state_id: str) -> None:
        self.fights = [fight for fight in self.fights if fight.state != state_id]


class BotCombatStrategy(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    bot_id: str  # UUID of the BotConfig this strategy belongs to
    # 1-based player number → embedded deck data (not referenced by UUID)
    player_decks: dict[int, DeckData] = Field(default_factory=dict)
    # The duel strategy for every state no assignment covers; None is the built-in default.
    default_strategy_id: str | None = None
    state_assignments: list[StateAssignment] = Field(default_factory=list)
    description: str = ""
    # Strategies that belong to this profile alone, rather than to the library (#118).
    # Linked by id like any library entry, and found first — see bartlebot.combat_profile.
    duel_strategies: list[DuelCombatStrategy] = Field(default_factory=list)
    player_strategies: list[PlayerCombatStrategy] = Field(default_factory=list)
    # Content hashes of what earlier saves replaced, newest first (#169). None ship, so
    # there is never an Official base to record.
    history: list[str] = Field(default_factory=list)
    # The content hash it had as its sender sent it, when an import wrote it changed:
    # its links rewritten to a copy, or as a copy itself (#171; bartlebot.shared.content_hash).
    # Dropped by the next save that changes it, and never in a share file.
    sent_sha256: str | None = None

    @classmethod
    def load(cls, path: str | Path) -> BotCombatStrategy:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def file_dump(self) -> dict:
        """The JSON body of this file, as :meth:`save` writes it."""
        return self.model_dump(exclude_defaults=True)

    def save(self, path: str | Path) -> None:
        atomic_write(self.file_dump(), path)

    def find_assignment_for_state(self, state_id: str) -> StateAssignment | None:
        """Return the StateAssignment whose stored fights name this state id, or None.

        Raw state ids only, for when there is no bot to place the fights on. With one,
        ask :func:`bartlebot.fight_keys.bind`, which follows a fight by its enemy.
        """
        for assignment in self.state_assignments:
            if state_id in assignment.state_ids:
                return assignment
        return None

    def find_dcs_id_for_state(self, state_id: str) -> str | None:
        """Return the DCS UUID assigned to this FSM state, or None if no assignment links one."""
        assignment = self.find_assignment_for_state(state_id)
        return assignment.duel_combat_strategy_id if assignment else None
