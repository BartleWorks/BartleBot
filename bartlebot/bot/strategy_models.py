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

from pydantic import BaseModel, Field, field_validator, model_validator


def _schools_from_legacy(data: object) -> object:
    """Lift a pre-#125 single ``school`` into ``schools``, so old files keep loading.

    ``"school": "Fire"`` becomes ``["Fire"]`` and ``"school": null`` becomes ``[]``
    (any school). A file carrying both keeps ``schools`` and drops the stray ``school``.
    """
    if isinstance(data, dict) and "school" in data:
        data = dict(data)
        school = data.pop("school")
        if "schools" not in data:
            data["schools"] = [school] if isinstance(school, str) and school else []
    return data


def _dedupe_schools(value: list[str]) -> list[str]:
    """Drop blanks and repeats, keeping the author's order."""
    return list(dict.fromkeys(s for s in value if s))


def _normalize_tags(value: object) -> list[str]:
    # Imported here: strategy_tags needs StrategyKind from strategy_store, which
    # imports this module.
    from bartlebot.strategy_tags import normalize_strategy_tags  # noqa: PLC0415

    return normalize_strategy_tags(value)


class PlayerCombatStrategy(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    dsl: str = ""
    # Each one of deck_io.PIP_SCHOOLS; empty means any school. Authored, never
    # inferred from dsl.  See DuelConfig.school_requirements.
    schools: list[str] = Field(default_factory=list)
    description: str = ""
    # Stored tags — the Role group of bartlebot.strategy_tags. Source is derived, never here.
    tags: list[str] = Field(default_factory=list)

    _legacy_school = model_validator(mode="before")(_schools_from_legacy)
    _schools = field_validator("schools")(_dedupe_schools)
    _tags = field_validator("tags", mode="before")(_normalize_tags)

    @classmethod
    def load(cls, path: str | Path) -> PlayerCombatStrategy:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def save(self, path: str | Path) -> None:
        _atomic_write(self.model_dump(exclude_defaults=True), path)


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
    # The wizard's archmastery school ("Storm", "Ice", ...) at import time, or
    # None when it could not be read or was never set.  Saved and displayed, but
    # restored on apply only when ``apply_archmastery`` below resolves true —
    # see read_archmastery_school / set_archmastery_school in deck_io.
    archmastery_school: str | None = None
    # Whether applying this deck should also set the wizard's archmastery school
    # to the one above.  Three-state: True/False pin an answer for this deck,
    # None (the default) defers to the app setting.  Off by default because the
    # school is a persistent character setting that outlives the run, and most
    # decks do not care what it is -- see deck_io.resolve_apply_archmastery.
    apply_archmastery: bool | None = None

    def is_empty(self) -> bool:
        # Deliberately card-only: a DeckData carrying nothing but an archmastery
        # school is not a deck, and counting it would make an empty deck look
        # applicable — the same trap ``tc`` is excluded for.
        return not self.normal and not self.item

    def total_cards(self) -> int:
        return sum(self.normal.values()) + sum(self.item.values())


class DeckPreset(BaseModel):
    """Named, reusable deck configuration stored in the global deck library."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    deck: DeckData = Field(default_factory=DeckData)
    description: str = ""
    # Stored tags — the Role group of bartlebot.strategy_tags. No schools: most spells
    # can be trained on any wizard, so a deck's school is not a real constraint.
    tags: list[str] = Field(default_factory=list)

    _tags = field_validator("tags", mode="before")(_normalize_tags)

    @classmethod
    def load(cls, path: str | Path) -> DeckPreset:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def save(self, path: str | Path) -> None:
        _atomic_write(self.model_dump(exclude_defaults=True), path)


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
    # Each one of deck_io.PIP_SCHOOLS; empty means any. Authored, never inferred
    # from dsl — see DuelConfig.school_requirements. A non-empty set takes
    # precedence over a linked preset's own schools.
    schools: list[str] = Field(default_factory=list)
    note: str = ""  # e.g. "P2 passes to bank pips for round 3"

    _legacy_school = model_validator(mode="before")(_schools_from_legacy)
    _schools = field_validator("schools")(_dedupe_schools)

    @property
    def is_linked(self) -> bool:
        """True if this assignment points at a shared PlayerCombatStrategy preset."""
        return self.player_combat_strategy_id is not None


class DuelConfig(BaseModel):
    """The shape of a duel-level combat config — no identity of its own.

    :class:`DuelCombatStrategy` is this plus an id and a name, and it is the only
    thing that carries one: a fight always names a duel strategy by id, whether that
    strategy lives in the library or inside one combat profile (#118).
    """

    min_players: int = 1
    max_players: int = 4
    # None = all players enter combat simultaneously; list = sequential entry order (1-based)
    combat_order: list[int] | None = None
    player_assignments: list[PlayerAssignment] = Field(default_factory=list)
    # Author-written: what this fight needs and why.
    description: str = ""

    @model_validator(mode="after")
    def clamp_player_range(self) -> DuelConfig:
        """Keep max_players at or above min_players.

        An inverted range is not merely odd-looking in the "4-1 players" summary --
        it describes a duel that cannot be satisfied: combat_warnings demands four
        connected clients while the editor only lets P1 be assigned a strategy or
        placed in the combat order.  The editor now couples its two spin boxes, so
        this catches the other door in: a strategy file edited by hand.

        Repairs rather than rejects, because raising a ValidationError here would
        make such a file refuse to open at all.  max is the field that moves:
        min_players is the one with runtime meaning (it gates the connected-client
        warning), while max_players only bounds the editor, so lifting max preserves
        whatever the author was asking for.
        """
        if self.max_players < self.min_players:
            self.max_players = self.min_players
        return self

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

    @staticmethod
    def assignment_schools(assignment: PlayerAssignment, pcs_by_id: dict[str, PlayerCombatStrategy]) -> list[str]:
        """The schools ``assignment`` accepts: its own set, else its linked preset's, else [] (any)."""
        if assignment.schools:
            return list(assignment.schools)
        if assignment.player_combat_strategy_id is not None:
            pcs = pcs_by_id.get(assignment.player_combat_strategy_id)
            if pcs is not None:
                return list(pcs.schools)
        return []

    def slot_schools(self, pcs_by_id: dict[str, PlayerCombatStrategy]) -> list[frozenset[str]]:
        """One school set per player slot, 1..max_players; empty means any school.

        A slot no assignment covers is empty, since it falls back to the built-in
        default, which any wizard can play. This is what "works with school X" asks.
        """
        slots: list[frozenset[str]] = [frozenset() for _ in range(self.max_players)]
        for assignment in self.player_assignments:
            schools = frozenset(self.assignment_schools(assignment, pcs_by_id))
            for p in assignment.players:
                if 1 <= p <= self.max_players:
                    slots[p - 1] = schools
        return slots

    def school_requirements(self, pcs_by_id: dict[str, PlayerCombatStrategy]) -> dict[str, int]:
        """Count of players needed per school set, e.g. {"Storm": 2, "Life/Myth": 1}.

        Resolved per assignment by :meth:`assignment_schools`, and multiplied by how
        many players that assignment covers. A slot accepting several schools is
        keyed by them joined with "/", in the order they were authored. Descriptive
        only: never gates a run, since a school tag cannot be verified against the
        DSL it labels (see decision 01).

        ``pcs_by_id``: {pcs_uuid: PlayerCombatStrategy} — pre-loaded preset data.
        """
        counts: dict[str, int] = {}
        for assignment in self.player_assignments:
            schools = self.assignment_schools(assignment, pcs_by_id)
            if not schools:
                continue
            key = "/".join(schools)
            counts[key] = counts.get(key, 0) + len(assignment.players)
        return counts

    def is_empty(self) -> bool:
        """True if this config has no player assignments at all.

        Such a config runs the built-in default for everyone, which a summary says as
        "nothing configured yet" rather than as a problem.
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
        _atomic_write(self.model_dump(exclude_defaults=True), path)


class StateAssignment(BaseModel):
    """A group of FSM states and the DuelCombatStrategy they fight with.

    Unlinked means the states fall back to the profile's default. There is no inline
    config: a one-off strategy lives in the profile's own ``duel_strategies`` and is
    linked by id like any other (#118). A file from before that still loads, because
    the models do not forbid extra fields, and its stray ``duel_config`` is ignored.
    """

    state_ids: list[str]  # FSM state IDs this assignment covers
    duel_combat_strategy_id: str | None = None  # UUID of a DuelCombatStrategy, in the profile or the library

    @property
    def is_linked(self) -> bool:
        """True if this assignment points at a DuelCombatStrategy."""
        return self.duel_combat_strategy_id is not None


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

    @classmethod
    def load(cls, path: str | Path) -> BotCombatStrategy:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def save(self, path: str | Path) -> None:
        _atomic_write(self.model_dump(exclude_defaults=True), path)

    def find_assignment_for_state(self, state_id: str) -> StateAssignment | None:
        """Return the StateAssignment covering this FSM state, or None."""
        for assignment in self.state_assignments:
            if state_id in assignment.state_ids:
                return assignment
        return None

    def find_dcs_id_for_state(self, state_id: str) -> str | None:
        """Return the DCS UUID assigned to this FSM state, or None if no assignment links one."""
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
