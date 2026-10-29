"""An in-memory working copy of the strategy library, with a Save/Cancel boundary.

WHY THIS EXISTS
---------------

Every surface that edited a strategy used to write it on a 500ms debounce, which
made three things impossible at once: Cancel meant nothing, a half-typed DSL was
persisted, and a partial flush could commit some fields of an edit and drop
others. The fix is not a better debounce — it is a boundary. The combat strategy
dialog opens onto *copies*, every editor mutates a copy, and nothing reaches disk
until Save.

That also makes the copy-on-write in :mod:`bartlebot.combat.strategy_store` fall out for
free: writes only ever land in the user root, so an edit to a shipped strategy is
already a user override by the time it is written, and Cancel leaves the shipped
file as untouched as it was before the dialog opened.

WHAT IS AND IS NOT IN HERE
--------------------------

The three kinds the dialog owns — player, duel, deck. Bot Combat Strategies are
held too, but read-only: they are bot-scoped, they are edited on the Combat tab,
and the only thing this module wants from them is *who references what* for a
delete confirmation and for the Save confirmation below.

The one exception is the **active Bot Combat Strategy** (#118). It owns duel and
player strategies of its own, and those are edited here like any library entry, so
it is held as an editable copy inside the same boundary. Only the active one: every
other Bot Combat Strategy stays read-only, there only to be counted. Commit writes
the library before the active one, so moving an entry of its own into the library
leaves two copies if it is interrupted, never none — the rule ``save_by_id``
follows for a rename. Lookups go to its own first, then the library, as they do
everywhere else (:mod:`bartlebot.combat.bot_combat_strategy`).

Everything is Qt-free and needs no ``QApplication``, matching
:mod:`bartlebot.combat.strategy_summary` and :mod:`bartlebot.combat.combat_grouping` next door.

DIRTINESS IS DERIVED, NEVER REPORTED
------------------------------------

Nothing calls ``mark_dirty()``. An entry is modified when its current fields
differ from the snapshot taken at load, which means an editor that mutates its
model directly — as ``DuelConfigEditor`` has always done — needs no changes to
participate, and an edit typed and then typed back out again correctly leaves
the dialog clean. A dirty flag set by a ``changed`` signal cannot say that.

BUILT-IN, MODIFIED, CUSTOM — ALSO DERIVED
-----------------------------------------

Whether an entry is still the built-in one is asked of its *content*, against the
shipped version held from load, not of what is on disk (#124). Asking disk meant an
unsaved edit to a built-in still read as unedited, so the dialog offered Delete
where it meant Revert, and a revert took the entry out of the list until the dialog
was reopened, because disk was the only place the shipped version came back from.

So :meth:`StrategyWorkingSet.revert` puts the shipped version back *here*, and
:meth:`~StrategyWorkingSet.commit` removes the user copy of any built-in whose
content has come back to the shipped version, however it got there. A built-in is
never removed: there is nothing under the user root to delete, and the shipped file
would reappear on the next open anyway.

ANOTHER PROGRAM CAN CHANGE A FILE WHILE THE DIALOG IS OPEN
---------------------------------------------------------

The three kinds here are the shared library, which BartleBuddy reads and writes too
(:mod:`bartleby.shared.strategy_library`). Each entry is held with the fingerprint it
was loaded with, and :meth:`StrategyWorkingSet.commit` passes it to every write, so a
file another program changed since is never overwritten. Commit records it as a
:class:`Conflict` instead and leaves the entry dirty; the dialog then offers to
:meth:`~StrategyWorkingSet.reload` it, discarding this session's edit, or to
:meth:`~StrategyWorkingSet.keep_as_copy`, which keeps the edit under a new id. An entry
from a file in a newer format is read-only (:meth:`~StrategyWorkingSet.is_read_only`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from bartleby.shared.content_hash import content_sha256, official_updated
from bartleby.shared.strategy_library import is_newer_format
from bartleby.shared.strategy_models import DeckPreset, DuelCombatStrategy, PlayerCombatStrategy

from bartlebot.combat.bot_combat_strategy import StrategyLookup, own_players_of
from bartlebot.combat.bot_strategy_models import BotCombatStrategy
from bartlebot.combat.strategy_store import (
    ChangedOnDisk,
    NewerFormat,
    Origin,
    StrategyKind,
    delete_deck_preset,
    delete_duel_strategy,
    delete_player_strategy,
    fingerprint,
    library_entries,
    list_all_bot_strategies,
    list_shipped,
    load_library_entry,
    origin_of,
    save_bot_strategy,
    save_deck_preset,
    save_duel_strategy,
    save_player_strategy,
    unique_name,
)
from bartlebot.combat.strategy_summary import name_list

logger = logging.getLogger(__name__)

#: The three kinds this module can edit. ``StrategyKind.BOT`` is deliberately absent —
#: see the module docstring.
EDITABLE_KINDS = (StrategyKind.PLAYER, StrategyKind.DUEL, StrategyKind.DECK)

#: The kinds a Bot Combat Strategy can hold strategies of. Deck presets stay
#: library-only: its own decks are its ``player_decks``, one per slot, on the Combat tab.
OWN_KINDS = (StrategyKind.PLAYER, StrategyKind.DUEL)


def _own_list(bot_strategy: BotCombatStrategy, kind: StrategyKind) -> list:
    """*bot_strategy*'s own list of *kind* — the live list, so appending to it adds an entry."""
    return bot_strategy.duel_strategies if kind is StrategyKind.DUEL else bot_strategy.player_strategies


_SAVERS = {
    StrategyKind.PLAYER: save_player_strategy,
    StrategyKind.DUEL: save_duel_strategy,
    StrategyKind.DECK: save_deck_preset,
}

_DELETERS = {
    StrategyKind.PLAYER: delete_player_strategy,
    StrategyKind.DUEL: delete_duel_strategy,
    StrategyKind.DECK: delete_deck_preset,
}

_FACTORIES = {
    StrategyKind.PLAYER: lambda name: PlayerCombatStrategy(name=name),
    StrategyKind.DUEL: lambda name: DuelCombatStrategy(name=name),
    StrategyKind.DECK: lambda name: DeckPreset(name=name),
}


def _fresh_copy(name: str) -> dict:
    """The fields a copy of an entry gets anew: an id, a name, and no history or Official base.

    A copy is a new item, as a bot's Make my own copy is: what it was copied from is not
    its own past, and it overrides nothing (#169).
    """
    return {"id": str(uuid4()), "name": name, "history": [], "official_sha256": None, "sent_sha256": None}


def _snapshot(item) -> str:
    """A stable string for "these fields, right now".

    ``model_dump`` rather than the ``exclude_defaults=True`` form ``save()``
    writes: a field explicitly set back to its default is not a change to the
    file, but it *is* a change the user made, and a dialog that called itself
    clean after one would lose it on Cancel without saying so.
    """
    return json.dumps(item.model_dump(), sort_keys=True, default=str)


@dataclass(frozen=True)
class Changes:
    """What Save would do, as three lists of ids. Also what a close prompt counts."""

    created: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.created) + len(self.modified) + len(self.deleted)

    def __bool__(self) -> bool:
        return self.total > 0


@dataclass(frozen=True)
class SharedUse:
    """A changed library entry, and the places outside the open Bot Combat Strategy that use it.

    What the Save confirmation lists (#118): an edit to a library entry reaches every
    Bot Combat Strategy and library duel that uses it, and the dialog only shows one.
    ``bot_strategies`` and ``duels`` are display names, already quoted.
    """

    name: str
    bot_strategies: tuple[str, ...] = ()
    duels: tuple[str, ...] = ()

    def sentence(self, *, other: bool = True) -> str:
        """ "'Boss Burn' is also used by 2 other Bot Combat Strategies: 'Solo' (Atlantea) and 'Team' (Darkmoor)."

        *other* is False in the library view, where none is open and "other" would have
        nothing to be other than.
        """
        parts = []
        if self.bot_strategies:
            plural = "Strategies" if len(self.bot_strategies) != 1 else "Strategy"
            noun = f"{'other ' if other else ''}Bot Combat {plural}"
            parts.append(f"{len(self.bot_strategies)} {noun}: {name_list(self.bot_strategies)}")
        if self.duels:
            noun = f"library duel{'s' if len(self.duels) != 1 else ''}"
            parts.append(f"{len(self.duels)} {noun}: {name_list(self.duels)}")
        return f"'{self.name}' is {'also ' if other else ''}used by {'; and '.join(parts)}."


@dataclass(frozen=True)
class Conflict:
    """A write :meth:`StrategyWorkingSet.commit` refused, so the entry is still unsaved."""

    kind: StrategyKind
    uuid: str
    name: str
    #: Its file is in a format newer than this build knows, rather than changed since load.
    newer_format: bool = False
    #: The refused write was a delete (or a revert), not a save.
    deleted: bool = False


@dataclass
class _Slot:
    """One kind's live items, plus what they looked like when they were loaded."""

    items: dict[str, object] = field(default_factory=dict)
    baselines: dict[str, str] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    #: Built-in entries as they ship, by id. Empty in developer mode, where nothing is shipped.
    shipped: dict[str, object] = field(default_factory=dict)
    #: Built-ins reverted this session. Counted as a change even when the content
    #: matches the baseline: a user copy identical to the shipped one still shadows
    #: every future update to it, so reverting one is a real write.
    reverted: set[str] = field(default_factory=set)
    #: The fingerprint each entry was loaded with: what a write expects to find. Absent
    #: (or None) for an entry with no user file.
    fingerprints: dict[str, str | None] = field(default_factory=dict)


class StrategyWorkingSet:
    """Deep copies of the library, edited freely, written only by :meth:`commit`.

    ``root`` / ``user_root`` are held from construction and reused by
    :meth:`commit`, so a test that loads from a sandbox writes back to the same
    sandbox without having to thread the paths through every call site.
    """

    def __init__(
        self,
        players: list[PlayerCombatStrategy],
        duels: list[DuelCombatStrategy],
        decks: list[DeckPreset],
        bot_strategies: list[BotCombatStrategy] | None = None,
        root: str | Path | None = None,
        user_root: str | Path | None = None,
        shipped: dict[StrategyKind, list] | None = None,
        bot_strategy: BotCombatStrategy | None = None,
        bot_id: str | None = None,
        fingerprints: dict[StrategyKind, dict[str, str | None]] | None = None,
    ) -> None:
        """*bot_strategy* is the active Bot Combat Strategy, held editable; see the module docstring.

        *bot_id* names the loaded bot when it has none yet, so the dialog can offer to make
        one. It is taken from *bot_strategy* when one is given.

        *fingerprints* are what each entry was loaded with (:meth:`from_store` passes the
        ones its load read). Without them, each is read from disk now.
        """
        self._root = root
        self._user_root = user_root
        # The active one, as a copy: the Combat tab is still drawing from the original.
        self._bot_strategy = bot_strategy.model_copy(deep=True) if bot_strategy is not None else None
        # None for one made in this session, which Save writes for the first time.
        self._bot_strategy_baseline = _snapshot(bot_strategy) if bot_strategy is not None else None
        self._own_entry_baselines: dict[str, str] = {}
        if bot_strategy is not None:
            for kind in OWN_KINDS:
                for item in _own_list(bot_strategy, kind):
                    self._own_entry_baselines[item.id] = _snapshot(item)
        self._bot_id = bot_strategy.bot_id if bot_strategy is not None else bot_id
        self._slots: dict[StrategyKind, _Slot] = {kind: _Slot() for kind in EDITABLE_KINDS}
        for kind, loaded in (
            (StrategyKind.PLAYER, players),
            (StrategyKind.DUEL, duels),
            (StrategyKind.DECK, decks),
        ):
            slot = self._slots[kind]
            given = (fingerprints or {}).get(kind)
            for item in loaded:
                # Deep copies: the caller's lists are what the Combat tab is still
                # drawing from, and an editor mutating those would change the cards
                # behind the dialog before anything was saved.
                slot.items[item.id] = item.model_copy(deep=True)
                slot.baselines[item.id] = _snapshot(item)
                slot.fingerprints[item.id] = (
                    given.get(item.id) if given is not None else fingerprint(kind, item.id, root, user_root)
                )
            for item in (shipped or {}).get(kind, []):
                slot.shipped[item.id] = item
        # Read-only, and not copied: nothing here edits one.
        self._bot_strategies = list(bot_strategies or [])
        self._conflicts: list[Conflict] = []

    @classmethod
    def from_store(
        cls,
        root: str | Path | None = None,
        user_root: str | Path | None = None,
        *,
        bot_strategy_id: str | None = None,
        bot_id: str | None = None,
    ) -> StrategyWorkingSet:
        """Load every kind from the ordered roots — the dialog's own entry point.

        *bot_strategy_id* is the active Bot Combat Strategy to hold editable, read from the
        same load as every other so the two can never disagree. *bot_id* is the loaded bot,
        for when it has none yet. Leave both unset for the library view.
        """
        bot_strategies = list_all_bot_strategies(root, user_root)
        active = next((b for b in bot_strategies if b.id == bot_strategy_id), None) if bot_strategy_id else None
        entries = {kind: library_entries(kind, root, user_root) for kind in EDITABLE_KINDS}
        return cls(
            players=[e.item for e in entries[StrategyKind.PLAYER].values()],
            duels=[e.item for e in entries[StrategyKind.DUEL].values()],
            decks=[e.item for e in entries[StrategyKind.DECK].values()],
            fingerprints={kind: {uuid: e.fingerprint for uuid, e in found.items()} for kind, found in entries.items()},
            bot_strategies=bot_strategies,
            root=root,
            user_root=user_root,
            shipped={kind: list_shipped(kind, root, user_root) for kind in EDITABLE_KINDS},
            bot_strategy=active,
            bot_id=bot_id,
        )

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def items(self, kind: StrategyKind) -> list:
        """Every live entry of one kind, name-sorted — what the card list draws.

        Sorted here rather than at each call site so the list does not reorder
        under the user when a rename lands, and so ``+ New`` puts its entry where
        the alphabet says it goes rather than at the end.
        """
        return sorted(self._slots[kind].items.values(), key=lambda i: i.name.lower())

    def get(self, kind: StrategyKind, uuid: str | None):
        """The live entry for ``uuid``, or None. This is the object editors mutate.

        The active Bot Combat Strategy's own entries first, then the library's — the lookup order
        everything else uses (:mod:`bartlebot.combat.bot_combat_strategy`).
        """
        if uuid is None:
            return None
        own = self._own_entry(kind, uuid)
        return own if own is not None else self._slots[kind].items.get(uuid)

    @property
    def players(self) -> list[PlayerCombatStrategy]:
        return self.items(StrategyKind.PLAYER)

    @property
    def duels(self) -> list[DuelCombatStrategy]:
        return self.items(StrategyKind.DUEL)

    @property
    def decks(self) -> list[DeckPreset]:
        return self.items(StrategyKind.DECK)

    @property
    def bot_strategies(self) -> list[BotCombatStrategy]:
        """Every Bot Combat Strategy, for reference counting — the active one as its live copy.

        The others are read-only; see the module docstring.
        """
        if self._bot_strategy is None:
            return list(self._bot_strategies)
        return [self._bot_strategy, *(b for b in self._bot_strategies if b.id != self._bot_strategy.id)]

    @property
    def roots(self) -> tuple[str | Path | None, str | Path | None]:
        """``(root, user_root)``, in the strategy store's argument convention: where this set was loaded from."""
        return self._root, self._user_root

    def has_unsaved(self, kind: StrategyKind, uuid: str | None) -> bool:
        """Whether *uuid* is new or changed since the set was loaded or last saved.

        What Export asks before it sends an entry: a share file carries what is saved.
        """
        changes = self.changes(kind)
        return uuid in changes.created or uuid in changes.modified

    # ------------------------------------------------------------------
    # The active Bot Combat Strategy (#118)
    # ------------------------------------------------------------------

    @property
    def bot_strategy(self) -> BotCombatStrategy | None:
        """The active Bot Combat Strategy's live copy, or None — the library view, or a bot with none yet."""
        return self._bot_strategy

    @property
    def bot_id(self) -> str | None:
        """The loaded bot, if any. Set without a Bot Combat Strategy when that bot has none yet."""
        return self._bot_id

    @property
    def bot_strategy_created(self) -> bool:
        """Whether the Bot Combat Strategy was made in this session, so Save writes it for the first time."""
        return self._bot_strategy is not None and self._bot_strategy_baseline is None

    def create_bot_strategy(self, name: str) -> BotCombatStrategy:
        """A new, empty Bot Combat Strategy for the loaded bot, held here until Save. Cancel discards it."""
        if self._bot_id is None:
            raise ValueError("No bot is loaded to make a Bot Combat Strategy for")
        self._bot_strategy = BotCombatStrategy(name=name, bot_id=self._bot_id)
        self._bot_strategy_baseline = None
        self._own_entry_baselines = {}
        return self._bot_strategy

    def own_items(self, kind: StrategyKind) -> list:
        """The active Bot Combat Strategy's own entries of *kind*, name-sorted; empty without one."""
        if self._bot_strategy is None or kind not in OWN_KINDS:
            return []
        return sorted(_own_list(self._bot_strategy, kind), key=lambda i: i.name.lower())

    def is_own_entry(self, kind: StrategyKind, uuid: str | None) -> bool:
        """Whether *uuid* is one of the active Bot Combat Strategy's own entries rather than the library's."""
        return uuid is not None and self._own_entry(kind, uuid) is not None

    def _own_entry(self, kind: StrategyKind, uuid: str):
        if self._bot_strategy is None or kind not in OWN_KINDS:
            return None
        return next((i for i in _own_list(self._bot_strategy, kind) if i.id == uuid), None)

    def lookup(self) -> StrategyLookup:
        """Every duel and player strategy in reach, the bot's own first — for summaries and the tab."""
        return StrategyLookup(self._bot_strategy, self.duels, self.players)

    def players_for_duel(self, duel_id: str | None) -> list[PlayerCombatStrategy]:
        """The player strategies an assignment in duel *duel_id* may link.

        One of the bot's own duels may use its own and the library's, its own first. A
        library duel may use the library's only: every bot shares it, so it must not
        depend on one bot's own.
        """
        if self.is_own_entry(StrategyKind.DUEL, duel_id):
            return self.own_items(StrategyKind.PLAYER) + self.players
        return self.players

    def was_created(self, kind: StrategyKind, uuid: str | None) -> bool:
        """Whether *uuid* was made in this session — by New, Duplicate or a promotion.

        What lets the Combat tab link the card that opened the dialog to a strategy made
        on this visit, and to nothing else. An entry of the bot's own moved into the
        library still counts only if it was new to begin with.
        """
        if uuid is None or self.get(kind, uuid) is None:
            return False
        return uuid not in self._slots[kind].baselines and uuid not in self._own_entry_baselines

    def move_to_library(self, kind: StrategyKind, uuid: str) -> list:
        """Move an entry of the bot's own into the library, keeping its id; return every entry moved.

        A duel takes the bot's own player strategies it uses along with it, since a
        library duel may not link a bot's own content — the caller confirms that first, with
        :func:`bartlebot.combat.bot_combat_strategy.own_players_of`. Like every edit it reaches disk
        at :meth:`commit` and not before, and Cancel undoes it.
        """
        entry = self._own_entry(kind, uuid)
        if entry is None:
            return []
        moving = [(kind, entry)]
        if kind is StrategyKind.DUEL:
            moving += [(StrategyKind.PLAYER, pcs) for pcs in own_players_of(entry, self._bot_strategy)]
        for moved_kind, item in moving:
            own = _own_list(self._bot_strategy, moved_kind)
            own[:] = [i for i in own if i.id != item.id]
            self._slots[moved_kind].items[item.id] = item
            # A tombstone for this id, left by deleting a library entry it once shadowed,
            # would otherwise delete the file this move is about to write.
            slot = self._slots[moved_kind]
            slot.deleted = [d for d in slot.deleted if d != item.id]
        return [item for _kind, item in moving]

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def create(self, kind: StrategyKind, base_name: str, *, in_bot_strategy: bool = False):
        """A new, empty entry of ``kind``, named uniquely against what is here now.

        *in_bot_strategy* puts it in the active Bot Combat Strategy rather than the library (#118).
        """
        item = _FACTORIES[kind](self.unique_name(kind, base_name))
        self._add(kind, item, in_bot_strategy)
        return item

    def duplicate(self, kind: StrategyKind, uuid: str, *, in_bot_strategy: bool = False):
        """A copy of ``uuid`` under a fresh id and the next free name, or None if it is gone.

        A fresh id, not the source's: an override shares its id with the shipped
        file it shadows, and duplicating one under that id would silently *become*
        the override rather than making a second entry. *in_bot_strategy* puts the copy in
        the active Bot Combat Strategy, whichever side the source was on — which is how "this
        fight needs something slightly different" is done.
        """
        source = self.get(kind, uuid)
        if source is None:
            return None
        copy = source.model_copy(deep=True, update=_fresh_copy(self.unique_name(kind, source.name)))
        self._add(kind, copy, in_bot_strategy)
        return copy

    def _add(self, kind: StrategyKind, item, in_bot_strategy: bool) -> None:
        if in_bot_strategy:
            if self._bot_strategy is None or kind not in OWN_KINDS:
                raise ValueError(f"No Bot Combat Strategy to hold a {kind.value} strategy")
            _own_list(self._bot_strategy, kind).append(item)
        else:
            self._slots[kind].items[item.id] = item

    def remove(self, kind: StrategyKind, uuid: str) -> None:
        """Drop an entry. Written through as a delete by :meth:`commit`.

        An entry created in this same session is simply forgotten — there is no
        file to delete, and recording a tombstone for an id that was never
        written would make ``changes()`` overstate what Save is about to do. An
        entry of the bot's own is a plain delete: it leaves the file it is in when that is saved.
        """
        if self.is_own_entry(kind, uuid):
            own = _own_list(self._bot_strategy, kind)
            own[:] = [i for i in own if i.id != uuid]
            return
        slot = self._slots[kind]
        if uuid in slot.shipped:
            # Removing only ever touches the user root, so a built-in would reappear on
            # the next open. The dialog does not offer it; this is the backstop.
            logger.warning("Refusing to remove built-in %s strategy %s; revert it instead", kind.value, uuid)
            return
        if slot.items.pop(uuid, None) is None:
            return
        if uuid in slot.baselines:
            slot.deleted.append(uuid)

    def origin(self, kind: StrategyKind, uuid: str) -> Origin:
        """Where ``uuid``'s file is, under this set's own roots — see :class:`Origin`.

        Offered here so a surface can ask whether removing an entry deletes it or
        merely reverts it without reaching for the roots the set was built with.
        An entry created in this session has no file under either root and reads
        as :attr:`Origin.MISSING`, which is the truth: there is nothing to revert
        to, and Save would be its first write.
        """
        return origin_of(kind, uuid, root=self._root, user_root=self._user_root)

    def status(self, kind: StrategyKind, uuid: str) -> Origin:
        """What this entry *is* right now, unsaved edits included — the source a surface shows.

        :meth:`origin` answers where the file is; this answers what Save would leave
        behind. A built-in edited a moment ago reads :attr:`Origin.SHIPPED_OVERRIDE`
        before anything is written, and a reverted one reads :attr:`Origin.SHIPPED`
        while its user copy is still on disk. Anything that is not a built-in, including
        an entry created this session, reads :attr:`Origin.USER`.
        """
        if self.is_own_entry(kind, uuid):
            # Never built-in: an entry of the bot's own is always the user's, and never reverts.
            return Origin.USER
        slot = self._slots[kind]
        item = slot.items.get(uuid)
        if item is None:
            return Origin.MISSING
        shipped = slot.shipped.get(uuid)
        if shipped is None:
            return Origin.USER
        if content_sha256(item) != content_sha256(shipped):
            return Origin.SHIPPED_OVERRIDE
        if uuid in slot.reverted or _snapshot(item) != slot.baselines.get(uuid):
            # Back to the shipped content this session; Save removes the user copy.
            return Origin.SHIPPED
        # Unchanged since load and identical to the shipped version: only disk can say
        # whether a user copy is shadowing it.
        return self.origin(kind, uuid)

    def revert(self, kind: StrategyKind, uuid: str):
        """Put the shipped version of ``uuid`` back in place and return it; None if not a built-in.

        The returned object replaces the live one, so a surface holding the old one
        must reload. The user copy, if there is one, goes on :meth:`commit`.
        """
        slot = self._slots[kind]
        shipped = slot.shipped.get(uuid)
        if shipped is None or uuid not in slot.items:
            return None
        restored = shipped.model_copy(deep=True)
        slot.items[uuid] = restored
        # Only a user copy on disk makes this a change. Reverting unsaved edits to a
        # built-in that was never saved over just undoes them, and leaves the set clean.
        if self.origin(kind, uuid) is Origin.SHIPPED_OVERRIDE:
            slot.reverted.add(uuid)
        return restored

    def _matches_shipped(self, kind: StrategyKind, uuid: str) -> bool:
        slot = self._slots[kind]
        shipped = slot.shipped.get(uuid)
        item = slot.items.get(uuid)
        # Content, not every field: an override's history and Official base are its record
        # of where it came from, and say nothing about whether it still differs (#169).
        return shipped is not None and item is not None and content_sha256(item) == content_sha256(shipped)

    def official_updated(self, kind: StrategyKind, uuid: str) -> bool:
        """Whether ``uuid`` overrides an Official strategy that has changed since the override was made (#169).

        Asked of the override as loaded: its ``official_sha256`` against the shipped one
        held from load. True only while the entry is still an override; Revert takes the
        new Official one, and the user's changes go with the override.
        """
        if self.is_own_entry(kind, uuid) or self.status(kind, uuid) is not Origin.SHIPPED_OVERRIDE:
            return False
        slot = self._slots[kind]
        return official_updated(slot.items.get(uuid), slot.shipped.get(uuid))

    def unique_name(self, kind: StrategyKind, base: str) -> str:
        """``base`` made unique against every live name of that kind, the bot's own
        included — see :func:`bartlebot.combat.strategy_store.unique_name`."""
        names = [i.name for i in self._slots[kind].items.values()]
        names += [i.name for i in self.own_items(kind)]
        return unique_name(base, names)

    # ------------------------------------------------------------------
    # Dirtiness
    # ------------------------------------------------------------------

    def changes(self, kind: StrategyKind | None = None) -> Changes:
        """What Save would write. Derived by comparison — see the module docstring.

        Counted per entry, the active Bot Combat Strategy's own included. An entry moved
        into the library counts once, as created there, not also as deleted from its old home.
        """
        kinds = EDITABLE_KINDS if kind is None else (kind,)
        created: list[str] = []
        modified: list[str] = []
        deleted: list[str] = []
        for k in kinds:
            slot = self._slots[k]
            for uuid, item in slot.items.items():
                baseline = slot.baselines.get(uuid)
                if baseline is None:
                    created.append(uuid)
                elif _snapshot(item) != baseline or uuid in slot.reverted:
                    modified.append(uuid)
            deleted.extend(slot.deleted)
            for item in self.own_items(k):
                baseline = self._own_entry_baselines.get(item.id)
                if baseline is None:
                    created.append(item.id)
                elif _snapshot(item) != baseline:
                    modified.append(item.id)
        if kind is None:
            live = {i.id for k in OWN_KINDS for i in self.own_items(k)}
            moved = {uuid for k in OWN_KINDS for uuid in self._slots[k].items}
            deleted.extend(uuid for uuid in self._own_entry_baselines if uuid not in live | moved)
        return Changes(created=created, modified=modified, deleted=deleted)

    @property
    def bot_strategy_dirty(self) -> bool:
        """Whether Save has to write the Bot Combat Strategy's file: new, or changed in any way."""
        if self._bot_strategy is None:
            return False
        return self._bot_strategy_baseline is None or _snapshot(self._bot_strategy) != self._bot_strategy_baseline

    @property
    def is_dirty(self) -> bool:
        return bool(self.changes()) or self.bot_strategy_dirty

    # ------------------------------------------------------------------
    # Committing
    # ------------------------------------------------------------------

    def commit(self) -> Changes:
        """Write every change to the user root and re-baseline. Returns what was written.

        Deletes run last. An entry removed and another created under the same
        name in one session is a rename by another route, and doing the delete
        first would be the only ordering where a failure mid-commit loses both.

        A built-in whose content is back to the shipped version — reverted, or edited
        back by hand — is not written: its user copy is deleted instead, so the
        shipped file shows through again and keeps receiving updates.

        A single failed write is logged and skipped rather than raised: the rest
        of the session's work is not forfeit because one file could not be
        written, and the entry keeps its old baseline, so it still reads as
        modified and Save can be tried again. A write refused because another
        program changed the file, or because it is in a newer format, is also
        listed in :attr:`conflicts`, for the dialog to ask about.

        The active Bot Combat Strategy is written after the library and before any
        library delete. If a library write failed, it is not written at all: the library
        may be the new home of an entry that just left it, and rewriting it without that
        entry would leave no copy anywhere.
        """
        written = self.changes()
        self._conflicts = []
        library_failed = False
        for kind in EDITABLE_KINDS:
            slot = self._slots[kind]
            save = _SAVERS[kind]
            delete = _DELETERS[kind]
            for uuid in [*written.created, *written.modified]:
                item = slot.items.get(uuid)
                if item is None:
                    continue
                expected = slot.fingerprints.get(uuid)
                reverting = self._matches_shipped(kind, uuid)
                try:
                    if reverting:
                        delete(uuid, root=self._root, user_root=self._user_root, expected=expected)
                        slot.fingerprints.pop(uuid, None)
                    else:
                        slot.fingerprints[uuid] = save(
                            item, root=self._root, user_root=self._user_root, expected=expected
                        )
                except (ChangedOnDisk, NewerFormat) as refused:
                    self._refused(kind, uuid, item.name, refused, deleted=reverting)
                    library_failed = True
                    continue
                except Exception:
                    logger.exception("Failed to save %s strategy %s", kind.value, uuid)
                    library_failed = True
                    continue
                slot.baselines[uuid] = _snapshot(item)
                slot.reverted.discard(uuid)
        if self.bot_strategy_dirty:
            if library_failed:
                logger.warning(
                    "Not saving Bot Combat Strategy %s: a library strategy failed to save", self._bot_strategy.id
                )
            else:
                self._commit_bot_strategy()
        for kind in EDITABLE_KINDS:
            slot = self._slots[kind]
            delete = _DELETERS[kind]
            kept: list[str] = []
            for uuid in slot.deleted:
                try:
                    delete(uuid, root=self._root, user_root=self._user_root, expected=slot.fingerprints.get(uuid))
                except (ChangedOnDisk, NewerFormat) as refused:
                    # The entry itself is gone from the set; its baseline still has its name.
                    name = json.loads(slot.baselines.get(uuid, "{}")).get("name") or uuid
                    self._refused(kind, uuid, name, refused, deleted=True)
                    kept.append(uuid)
                    continue
                except Exception:
                    logger.exception("Failed to delete %s strategy %s", kind.value, uuid)
                    continue
                slot.baselines.pop(uuid, None)
                slot.fingerprints.pop(uuid, None)
            # A refused delete stays pending, so the set still reads dirty until it is resolved.
            slot.deleted = kept
        return written

    def _refused(self, kind: StrategyKind, uuid: str, name: str, refused: Exception, *, deleted: bool) -> None:
        logger.warning("Not saving %s strategy %s: %s", kind.value, uuid, refused)
        self._conflicts.append(
            Conflict(kind, uuid, name, newer_format=isinstance(refused, NewerFormat), deleted=deleted)
        )

    @property
    def conflicts(self) -> list[Conflict]:
        """The writes the last :meth:`commit` refused; see the module docstring."""
        return list(self._conflicts)

    def is_read_only(self, kind: StrategyKind, uuid: str | None) -> bool:
        """Whether ``uuid`` came from a file in a newer format than this build knows, so it may not be edited."""
        if uuid is None or self.is_own_entry(kind, uuid):
            return False
        item = self._slots[kind].items.get(uuid)
        return item is not None and is_newer_format(item)

    def reload(self, kind: StrategyKind, uuid: str):
        """Replace ``uuid`` with what is on disk now, discarding this session's edit to it. Returns it.

        None when no root has it any more: another program deleted it, so it leaves the
        list too. A surface holding the old object must reload, as after :meth:`revert`.
        """
        slot = self._slots[kind]
        slot.deleted = [d for d in slot.deleted if d != uuid]
        slot.reverted.discard(uuid)
        self._conflicts = [c for c in self._conflicts if (c.kind, c.uuid) != (kind, uuid)]
        try:
            entry = load_library_entry(kind, uuid, self._root, self._user_root)
        except FileNotFoundError:
            for held in (slot.items, slot.baselines, slot.fingerprints):
                held.pop(uuid, None)
            return None
        slot.items[uuid] = entry.item
        slot.baselines[uuid] = _snapshot(entry.item)
        slot.fingerprints[uuid] = entry.fingerprint
        return entry.item

    def keep_as_copy(self, kind: StrategyKind, uuid: str):
        """Keep this session's edit to ``uuid`` as a new entry, and reload ``uuid`` from disk. Returns the copy.

        The copy gets a fresh id and the next free name, as :meth:`duplicate` gives, so
        Save writes it as a new file and nothing the other program wrote is touched.
        """
        source = self._slots[kind].items.get(uuid)
        if source is None:
            return None
        copy = source.model_copy(deep=True, update=_fresh_copy(self.unique_name(kind, source.name)))
        self.reload(kind, uuid)
        self._add(kind, copy, in_bot_strategy=False)
        return copy

    def _commit_bot_strategy(self) -> None:
        try:
            save_bot_strategy(self._bot_strategy, root=self._root, user_root=self._user_root)
        except Exception:
            logger.exception("Failed to save Bot Combat Strategy %s", self._bot_strategy.id)
            return
        self._bot_strategy_baseline = _snapshot(self._bot_strategy)
        self._own_entry_baselines = {
            item.id: _snapshot(item) for kind in OWN_KINDS for item in _own_list(self._bot_strategy, kind)
        }

    # ------------------------------------------------------------------
    # References
    # ------------------------------------------------------------------

    def usage(self, uuid: str):
        """How many things reference ``uuid``, counting unsaved edits.

        Counted against the working set rather than disk, which is the whole
        reason :func:`bartlebot.combat.strategy_summary.find_references` takes lists:
        a player strategy linked into a duel a minute ago has no file saying so
        yet, and a delete confirmation that missed it would be wrong in exactly
        the case the user most needs it to be right. The active Bot Combat Strategy is
        counted as its live copy, for the same reason.
        """
        from bartlebot.combat.strategy_summary import find_references  # noqa: PLC0415 — avoids a cycle

        return find_references(uuid, self.bot_strategies, self.duels)

    def shared_uses(self, bot_name: Callable[[str], str | None] | None = None) -> list[SharedUse]:
        """Every changed library entry used somewhere the user does not have open.

        What the Save confirmation asks about (#118): an edit to a library entry
        reaches every Bot Combat Strategy and library duel using it, and the dialog shows
        at most one. "Not open" means any Bot Combat Strategy but the active one, of any
        bot, plus any library duel that links a changed player strategy. With none open —
        the library view — every one counts.

        *bot_name* turns a bot id into its name, so one reads as ``'Solo' (Atlantea)``;
        without it, or when it has no answer, its name stands alone. Only edits count: a new entry has no uses yet, and a
        delete has a confirmation of its own.
        """
        active_id = self._bot_strategy.id if self._bot_strategy is not None else None
        others = [b for b in self._bot_strategies if b.id != active_id]
        modified = set(self.changes().modified)
        uses: list[SharedUse] = []
        for kind in OWN_KINDS:
            for item in self.items(kind):
                if item.id not in modified:
                    continue
                users = [self._bot_strategy_label(b, bot_name) for b in others if _bot_strategy_uses(b, kind, item.id)]
                duels = []
                if kind is StrategyKind.PLAYER:
                    duels = [f"'{d.name}'" for d in self.duels if item.id in d.linked_pcs_ids()]
                if users or duels:
                    uses.append(SharedUse(item.name, tuple(users), tuple(duels)))
        return uses

    @staticmethod
    def _bot_strategy_label(bcs: BotCombatStrategy, bot_name: Callable[[str], str | None] | None) -> str:
        name = bot_name(bcs.bot_id) if bot_name is not None else None
        return f"'{bcs.name}' ({name})" if name else f"'{bcs.name}'"


def _bot_strategy_uses(bcs: BotCombatStrategy, kind: StrategyKind, uuid: str) -> bool:
    """Whether Bot Combat Strategy *bcs* uses *uuid*: a duel on a card or its default, or a player
    strategy in one of its own duels."""
    if kind is StrategyKind.DUEL:
        return bcs.default_strategy_id == uuid or any(a.duel_combat_strategy_id == uuid for a in bcs.state_assignments)
    return any(uuid in d.linked_pcs_ids() for d in bcs.duel_strategies)
