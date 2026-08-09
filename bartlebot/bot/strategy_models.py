"""Pydantic models for the three-level combat strategy system.

PlayerCombatStrategy  — an optional *shared preset*: a named WizSprinter DSL
                        string reusable across duels.  Most player DSL lives
                        inline on PlayerAssignment instead; only strategies
                        used by several duels are worth promoting here.
DuelCombatStrategy    — team-level config for one type of fight
BotCombatStrategy     — bot-specific mapping of FSM states to strategies + deck data
DeckPreset            — named, reusable deck configuration in the global library

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
    """Embedded deck config — no id/name, always inline within BotCombatStrategy.

    ``normal`` and ``item`` are both live — see :mod:`bartlebot.deck_io`.

    ``tc`` is retained only so decks saved by earlier versions still parse.
    Treasure cards cannot be read or restored: reaching the treasure card view
    leaves the spell list in a state that breaks normal card restore.  It is
    excluded from :meth:`is_empty` and :meth:`total_cards` so stale values in
    old files can't make an unusable deck look populated.
    """

    normal: dict[str, int] = Field(default_factory=dict)
    tc: dict[str, int] = Field(default_factory=dict)  # legacy — never read or applied
    item: dict[str, int] = Field(default_factory=dict)
    # Card name → "blade"/"charm"/"ward"/"trap"/"aura", recorded at import time
    # from the authoring wizard's spell templates.  Used only to explain a
    # failed apply ("Inferno Blade — a blade; you have Storm Blade"), since the
    # applying wizard does not own the card and so cannot classify it.  Empty
    # for decks saved before this existed, and for cards in none of the
    # categories — a missing entry costs a label, nothing more.
    card_kinds: dict[str, str] = Field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.normal and not self.item

    def total_cards(self) -> int:
        return sum(self.normal.values()) + sum(self.item.values())


class DeckPreset(BaseModel):
    """Named, reusable deck configuration stored in the global deck library."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    deck: DeckData = Field(default_factory=DeckData)

    @classmethod
    def load(cls, path: str | Path) -> DeckPreset:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def save(self, path: str | Path) -> None:
        _atomic_write(self.model_dump(exclude_none=True), path)


class PlayerAssignment(BaseModel):
    """Maps a set of player slots to a combat DSL.

    The DSL is stored inline by default — most assignments are a one-line
    strategy used by exactly one duel, and promoting each to its own library
    file produced far more clutter than reuse.  Set
    ``player_combat_strategy_id`` to link a shared PlayerCombatStrategy
    preset instead; when it is set, ``dsl`` is ignored.
    """

    players: list[int]  # 1-based player numbers, e.g. [1, 2]
    dsl: str = ""  # inline DSL — used when player_combat_strategy_id is None
    player_combat_strategy_id: str | None = None  # UUID of a PlayerCombatStrategy preset

    @property
    def is_linked(self) -> bool:
        """True if this assignment points at a shared PlayerCombatStrategy preset."""
        return self.player_combat_strategy_id is not None


class DuelConfig(BaseModel):
    """The reusable shape of a duel-level combat config — no identity of its own.

    Split out of :class:`DuelCombatStrategy` so the same shape can live either
    as a named library entry, or inline on a single :class:`StateAssignment` /
    :class:`BotCombatStrategy` default — a one-off tweak for one bot that isn't
    worth promoting to a shared file.  Mirrors the inline-vs-linked split
    :class:`PlayerAssignment` already uses one level down.
    """

    min_players: int = 1
    max_players: int = 4
    # None = all players enter combat simultaneously; list = sequential entry order (1-based)
    combat_order: list[int] | None = None
    player_assignments: list[PlayerAssignment] = Field(default_factory=list)

    def resolve_dsl_for_player(self, player_num: int, pcs_map: dict[str, str]) -> str | None:
        """Return DSL for a 1-based player number, or None if unassigned/empty.

        Linked assignments resolve through pcs_map; inline assignments use their
        own ``dsl``.  An empty inline DSL resolves to None so the caller falls
        back to the built-in default — which also means an entirely empty
        ``DuelConfig`` (no player_assignments at all) degrades to "built-in
        default for everyone" with no special-casing needed.

        pcs_map: dict of {pcs_uuid: dsl_string} — pre-loaded PCS preset data.
        """
        for assignment in self.player_assignments:
            if player_num in assignment.players:
                if assignment.player_combat_strategy_id is not None:
                    return pcs_map.get(assignment.player_combat_strategy_id)
                return assignment.dsl or None
        return None

    def linked_pcs_ids(self) -> list[str]:
        """UUIDs of every PlayerCombatStrategy preset this duel links to."""
        return [a.player_combat_strategy_id for a in self.player_assignments if a.player_combat_strategy_id is not None]

    def is_empty(self) -> bool:
        """True if this config has no player assignments at all.

        Used to tell "a deliberately inline config with real content" apart
        from "no real config was ever entered" — the latter is worth flagging
        even though both resolve to the same built-in-default runtime behavior.
        """
        return not self.player_assignments


class DuelCombatStrategy(DuelConfig):
    """A named, shared DuelConfig stored in the library."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str

    @classmethod
    def load(cls, path: str | Path) -> DuelCombatStrategy:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def save(self, path: str | Path) -> None:
        _atomic_write(self.model_dump(exclude_none=True), path)


class StateAssignment(BaseModel):
    """One FSM state's combat override: inline, or linked to a library DuelCombatStrategy.

    Mirrors :class:`PlayerAssignment`'s pattern one level up: ``duel_config`` is
    always present and used when unlinked; set ``duel_combat_strategy_id`` to
    point at a shared library entry instead, which then takes precedence.
    """

    state_ids: list[str]  # FSM state IDs this assignment covers
    duel_config: DuelConfig = Field(default_factory=DuelConfig)  # inline; used when unlinked
    duel_combat_strategy_id: str | None = None  # UUID of a DuelCombatStrategy; when set, duel_config is ignored

    @property
    def is_linked(self) -> bool:
        """True if this assignment points at a shared DuelCombatStrategy."""
        return self.duel_combat_strategy_id is not None


class BotCombatStrategy(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    bot_id: str  # UUID of the BotConfig this strategy belongs to
    # 1-based player number → embedded deck data (not referenced by UUID)
    player_decks: dict[int, DeckData] = Field(default_factory=dict)
    # Default duel config for unassigned states: linked library entry takes
    # precedence over default_duel_config; an empty default_duel_config (the
    # common case) degrades to the built-in default, same as DuelConfig.is_empty.
    default_strategy_id: str | None = None
    default_duel_config: DuelConfig = Field(default_factory=DuelConfig)
    state_assignments: list[StateAssignment] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> BotCombatStrategy:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def save(self, path: str | Path) -> None:
        _atomic_write(self.model_dump(exclude_none=True), path)

    def find_assignment_for_state(self, state_id: str) -> StateAssignment | None:
        """Return the StateAssignment covering this FSM state, or None."""
        for assignment in self.state_assignments:
            if state_id in assignment.state_ids:
                return assignment
        return None

    def find_dcs_id_for_state(self, state_id: str) -> str | None:
        """Return the linked DCS UUID assigned to this FSM state, or None.

        None covers both "no assignment exists" and "the assignment is inline"
        — callers that only understand library ids can't do anything with an
        inline config anyway.  Use :meth:`find_assignment_for_state` for the
        full picture.
        """
        assignment = self.find_assignment_for_state(state_id)
        return assignment.duel_combat_strategy_id if assignment else None


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
