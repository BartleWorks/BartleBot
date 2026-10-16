"""The models of the shared strategy library: player strategies, duel strategies and decks.

PlayerCombatStrategy  — an optional *shared preset*: a named WizSprinter DSL
                        string reusable across duels.  Most player DSL lives
                        inline on PlayerAssignment instead; only strategies
                        used by several duels are worth promoting here.
DuelCombatStrategy    — team-level config for one type of fight
DeckPreset            — named, reusable deck configuration in the library

Every product by this vendor reads and writes these (``docs/EPICS.md``, *Official and shared content*), so
they live in :mod:`bartlebot.shared` and import nothing product-specific, and every
surface imports them from here. BartleBot's Bot Combat Strategy, which embeds them, is
BartleBot's own and lives in :mod:`bartlebot.combat.bot_strategy_models`.

Each top-level model is stored as an individual JSON file identified by UUID, keeps a
``history`` of content hashes and, as an override of an Official one, the hash of what it
was made from (:mod:`bartlebot.shared.content_hash`), and carries a ``format``: the version of its file format. It is left out of the file while
it is 1, so no file written before it existed changes. A product refuses to save over a
file whose format is newer than :data:`LIBRARY_FORMAT`, since the models drop fields
they don't know and the save would lose them.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

#: The newest library file format this build reads and writes.
LIBRARY_FORMAT = 1

#: The Role tags a player strategy or a deck stores, in the order they are stored and offered.
ROLE_TAGS: tuple[str, ...] = (
    "Hitter",  # deals the damage the fight is won with
    "Support",  # blades, traps and feints for someone else's hit
    "Healer",  # keeps the team up
    "Solo",  # carries a fight alone, with no one to hand a hit or a heal to
)

_ROLE_ORDER: dict[str, int] = {tag: i for i, tag in enumerate(ROLE_TAGS)}


def normalize_role_tags(raw: object) -> list[str]:
    """Coerce a file's raw ``tags`` into valid, ordered, de-duplicated Role tags.

    Tolerant, because this runs on every load and raising would make a hand-edited
    file refuse to open. A tag outside :data:`ROLE_TAGS`, a derived Source tag written
    in by hand included, is dropped with a warning.
    """
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            continue
        if entry not in _ROLE_ORDER:
            logger.warning("ignoring unknown strategy tag %r (not in ROLE_TAGS)", entry)
            continue
        seen.add(entry)
    return sorted(seen, key=_ROLE_ORDER.__getitem__)


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


class PlayerCombatStrategy(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    dsl: str = ""
    # Each one of deck_io.PIP_SCHOOLS; empty means any school. Authored, never
    # inferred from dsl.  See DuelConfig.school_requirements.
    schools: list[str] = Field(default_factory=list)
    description: str = ""
    # Stored tags — the Role group of bartlebot.combat.strategy_tags. Source is derived, never here.
    tags: list[str] = Field(default_factory=list)
    #: The file format; see the module docstring.
    format: int = 1
    #: Content hashes of what earlier saves replaced, and an override's Official base (#169).
    history: list[str] = Field(default_factory=list)
    official_sha256: str | None = None
    #: The content hash it had as its sender sent it, when an import wrote it changed: its
    #: links rewritten to a copy, or as a copy itself (#171). Dropped by the next save that
    #: changes it, and never in a share file.
    sent_sha256: str | None = None

    _legacy_school = model_validator(mode="before")(_schools_from_legacy)
    _schools = field_validator("schools")(_dedupe_schools)
    _tags = field_validator("tags", mode="before")(normalize_role_tags)

    @classmethod
    def load(cls, path: str | Path) -> PlayerCombatStrategy:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def file_dump(self) -> dict:
        """The JSON body of this file, as :meth:`save` writes it."""
        return self.model_dump(exclude_defaults=True)

    def save(self, path: str | Path) -> None:
        atomic_write(self.file_dump(), path)


class DeckData(BaseModel):
    """Embedded deck config — no id/name, always inline within a deck preset or a Bot Combat Strategy.

    ``normal`` and ``item`` are both live — see :mod:`bartlebot.combat.deck_io`.

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
    """Named, reusable deck configuration stored in the deck library."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    deck: DeckData = Field(default_factory=DeckData)
    description: str = ""
    # Stored tags — the Role group of bartlebot.combat.strategy_tags.
    tags: list[str] = Field(default_factory=list)
    # The schools the deck is built for (#174), each one of deck_io.PIP_SCHOOLS; empty
    # means any. Authored, never inferred from the cards: most spells can be trained on
    # any wizard, so a card list cannot say whose deck it is. Not the archmastery school
    # the deck captured (DeckData.archmastery_school), which is the wizard's, not the deck's.
    schools: list[str] = Field(default_factory=list)
    #: The file format; see the module docstring.
    format: int = 1
    #: Content hashes of what earlier saves replaced, and an override's Official base (#169).
    history: list[str] = Field(default_factory=list)
    official_sha256: str | None = None
    #: The content hash it had as its sender sent it, when an import wrote it changed: its
    #: links rewritten to a copy, or as a copy itself (#171). Dropped by the next save that
    #: changes it, and never in a share file.
    sent_sha256: str | None = None

    _tags = field_validator("tags", mode="before")(normalize_role_tags)
    _schools = field_validator("schools")(_dedupe_schools)

    @classmethod
    def load(cls, path: str | Path) -> DeckPreset:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def file_dump(self) -> dict:
        """The JSON body of this file, as :meth:`save` writes it."""
        return self.model_dump(exclude_defaults=True)

    def save(self, path: str | Path) -> None:
        atomic_write(self.file_dump(), path)


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
    strategy lives in the library or inside one Bot Combat Strategy (#118).
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
        min_players is the one the pre-run check holds a picked duel to, so lifting
        max preserves whatever the author was asking for.  Both ends decide whether
        a duel :meth:`accepts` a team.
        """
        if self.max_players < self.min_players:
            self.max_players = self.min_players
        return self

    def accepts(self, players: int) -> bool:
        """Whether *players* connected clients is a team this duel was written for.

        ``min_players`` and ``max_players`` bound it, both ends included. A duel that does not
        accept the team is what the run-time resolver passes over, except one the user picked
        for the bot (``docs/COMBAT_CONFIG.md``, "Resolution at run time").
        """
        return self.min_players <= players <= self.max_players

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
    #: The file format; see the module docstring.
    format: int = 1
    #: Content hashes of what earlier saves replaced, and an override's Official base (#169).
    history: list[str] = Field(default_factory=list)
    official_sha256: str | None = None
    #: The content hash it had as its sender sent it, when an import wrote it changed: its
    #: links rewritten to a copy, or as a copy itself (#171). Dropped by the next save that
    #: changes it, and never in a share file.
    sent_sha256: str | None = None

    @classmethod
    def load(cls, path: str | Path) -> DuelCombatStrategy:
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(json.load(f))

    def file_dump(self) -> dict:
        """The JSON body of this file, as :meth:`save` writes it."""
        return self.model_dump(exclude_defaults=True)

    def save(self, path: str | Path) -> None:
        atomic_write(self.file_dump(), path)


def atomic_write(data: dict, path: str | Path) -> None:
    """Write ``data`` as indented JSON to ``path`` through a temporary file, so a crash never half-writes it."""
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
