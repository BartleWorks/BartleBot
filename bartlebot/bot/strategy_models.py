"""Pydantic models for the three-level combat strategy system.

PlayerCombatStrategy  — a named WizSprinter DSL string for one player
DuelCombatStrategy    — team-level config for one type of fight
BotCombatStrategy     — bot-specific mapping of FSM states to strategies + deck data

Each top-level model is stored as an individual JSON file identified by UUID.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field


class PlayerCombatStrategy(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    dsl: str = ""

    @classmethod
    def load(cls, path: str | Path) -> PlayerCombatStrategy:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def save(self, path: str | Path) -> None:
        _atomic_write(self.model_dump(exclude_none=True), path)


class DeckData(BaseModel):
    """Embedded deck config — no id/name, always inline within BotCombatStrategy."""

    normal: dict[str, int] = Field(default_factory=dict)
    tc: dict[str, int] = Field(default_factory=dict)
    item: dict[str, int] = Field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.normal and not self.tc and not self.item

    def total_cards(self) -> int:
        return sum(self.normal.values()) + sum(self.tc.values()) + sum(self.item.values())


class PlayerAssignment(BaseModel):
    """Maps a set of player slots to a single PlayerCombatStrategy."""

    players: list[int]  # 1-based player numbers, e.g. [1, 2]
    player_combat_strategy_id: str  # UUID of a PlayerCombatStrategy


class DuelCombatStrategy(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    min_players: int = 1
    max_players: int = 4
    # None = all players enter combat simultaneously; list = sequential entry order (1-based)
    combat_order: list[int] | None = None
    player_assignments: list[PlayerAssignment] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> DuelCombatStrategy:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def save(self, path: str | Path) -> None:
        _atomic_write(self.model_dump(exclude_none=True), path)

    def resolve_dsl_for_player(self, player_num: int, pcs_map: dict[str, str]) -> str | None:
        """Return DSL for a 1-based player number, or None if unassigned.

        pcs_map: dict of {pcs_uuid: dsl_string} — pre-loaded PCS data.
        """
        for assignment in self.player_assignments:
            if player_num in assignment.players:
                return pcs_map.get(assignment.player_combat_strategy_id)
        return None


class StateAssignment(BaseModel):
    """Maps a DuelCombatStrategy to one or more FSM state IDs."""

    duel_combat_strategy_id: str  # UUID of a DuelCombatStrategy
    state_ids: list[str]  # FSM state IDs this assignment covers


class BotCombatStrategy(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    bot_id: str  # UUID of the BotConfig this strategy belongs to
    # 1-based player number → embedded deck data (not referenced by UUID)
    player_decks: dict[int, DeckData] = Field(default_factory=dict)
    # DuelCombatStrategy to use for all unassigned states; None = built-in default
    default_strategy_id: str | None = None
    state_assignments: list[StateAssignment] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> BotCombatStrategy:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def save(self, path: str | Path) -> None:
        _atomic_write(self.model_dump(exclude_none=True), path)

    def find_dcs_id_for_state(self, state_id: str) -> str | None:
        """Return the DCS UUID assigned to this FSM state, or None."""
        for assignment in self.state_assignments:
            if state_id in assignment.state_ids:
                return assignment.duel_combat_strategy_id
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _atomic_write(data: dict, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=path.stem + "_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
