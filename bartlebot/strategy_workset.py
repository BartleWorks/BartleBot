"""An in-memory working copy of the strategy library, with a Save/Cancel boundary.

WHY THIS EXISTS
---------------

Every surface that edited a strategy used to write it on a 500ms debounce, which
made three things impossible at once: Cancel meant nothing, a half-typed DSL was
persisted, and a partial flush could commit some fields of an edit and drop
others. The fix is not a better debounce — it is a boundary. The combat strategy
dialog opens onto *copies*, every editor mutates a copy, and nothing reaches disk
until Save.

That also makes the copy-on-write in :mod:`bartlebot.strategy_store` fall out for
free: writes only ever land in the user root, so an edit to a shipped strategy is
already a user override by the time it is written, and Cancel leaves the shipped
file as untouched as it was before the dialog opened.

WHAT IS AND IS NOT IN HERE
--------------------------

The three kinds the dialog owns — player, duel, deck. Bot Combat Strategies are
held too, but read-only: they are bot-scoped, they are edited on the Combat tab,
and the only thing this module wants from them is *who references what* for a
delete confirmation.

Everything is Qt-free and needs no ``QApplication``, matching
:mod:`bartlebot.strategy_summary` and :mod:`bartlebot.combat_grouping` next door.

DIRTINESS IS DERIVED, NEVER REPORTED
------------------------------------

Nothing calls ``mark_dirty()``. An entry is modified when its current fields
differ from the snapshot taken at load, which means an editor that mutates its
model directly — as ``DuelConfigEditor`` has always done — needs no changes to
participate, and an edit typed and then typed back out again correctly leaves
the dialog clean. A dirty flag set by a ``changed`` signal cannot say that.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DeckPreset,
    DuelCombatStrategy,
    PlayerCombatStrategy,
)
from bartlebot.strategy_store import (
    Origin,
    StrategyKind,
    delete_deck_preset,
    delete_duel_strategy,
    delete_player_strategy,
    list_all_bot_strategies,
    list_deck_presets,
    list_duel_strategies,
    list_player_strategies,
    origin_of,
    save_deck_preset,
    save_duel_strategy,
    save_player_strategy,
    unique_name,
)

logger = logging.getLogger(__name__)

#: The three kinds this module can edit. ``StrategyKind.BOT`` is deliberately absent —
#: see the module docstring.
EDITABLE_KINDS = (StrategyKind.PLAYER, StrategyKind.DUEL, StrategyKind.DECK)

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


@dataclass
class _Slot:
    """One kind's live items, plus what they looked like when they were loaded."""

    items: dict[str, object] = field(default_factory=dict)
    baselines: dict[str, str] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)


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
    ) -> None:
        self._root = root
        self._user_root = user_root
        self._slots: dict[StrategyKind, _Slot] = {kind: _Slot() for kind in EDITABLE_KINDS}
        for kind, loaded in (
            (StrategyKind.PLAYER, players),
            (StrategyKind.DUEL, duels),
            (StrategyKind.DECK, decks),
        ):
            slot = self._slots[kind]
            for item in loaded:
                # Deep copies: the caller's lists are what the Combat tab is still
                # drawing from, and an editor mutating those would change the cards
                # behind the dialog before anything was saved.
                slot.items[item.id] = item.model_copy(deep=True)
                slot.baselines[item.id] = _snapshot(item)
        # Read-only, and not copied: nothing here edits one.
        self._bot_strategies = list(bot_strategies or [])

    @classmethod
    def from_store(cls, root: str | Path | None = None, user_root: str | Path | None = None) -> StrategyWorkingSet:
        """Load every kind from the ordered roots — the dialog's own entry point."""
        return cls(
            players=list_player_strategies(root, user_root),
            duels=list_duel_strategies(root, user_root),
            decks=list_deck_presets(root, user_root),
            bot_strategies=list_all_bot_strategies(root, user_root),
            root=root,
            user_root=user_root,
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
        """The live entry for ``uuid``, or None. This is the object editors mutate."""
        if uuid is None:
            return None
        return self._slots[kind].items.get(uuid)

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
        """Read-only, for reference counting only — see the module docstring."""
        return list(self._bot_strategies)

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def create(self, kind: StrategyKind, base_name: str):
        """A new, empty entry of ``kind``, named uniquely against what is here now."""
        item = _FACTORIES[kind](self.unique_name(kind, base_name))
        self._slots[kind].items[item.id] = item
        return item

    def duplicate(self, kind: StrategyKind, uuid: str):
        """A copy of ``uuid`` under a fresh id and the next free name, or None if it is gone.

        A fresh id, not the source's: an override shares its id with the shipped
        file it shadows, and duplicating one under that id would silently *become*
        the override rather than making a second entry.
        """
        from uuid import uuid4  # noqa: PLC0415 — only needed on this path

        source = self.get(kind, uuid)
        if source is None:
            return None
        copy = source.model_copy(deep=True, update={"id": str(uuid4()), "name": self.unique_name(kind, source.name)})
        self._slots[kind].items[copy.id] = copy
        return copy

    def remove(self, kind: StrategyKind, uuid: str) -> None:
        """Drop an entry. Written through as a delete by :meth:`commit`.

        An entry created in this same session is simply forgotten — there is no
        file to delete, and recording a tombstone for an id that was never
        written would make ``changes()`` overstate what Save is about to do.
        """
        slot = self._slots[kind]
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

    def unique_name(self, kind: StrategyKind, base: str) -> str:
        """``base`` made unique against every live name of that kind — see
        :func:`bartlebot.strategy_store.unique_name`."""
        return unique_name(base, (i.name for i in self._slots[kind].items.values()))

    # ------------------------------------------------------------------
    # Dirtiness
    # ------------------------------------------------------------------

    def changes(self, kind: StrategyKind | None = None) -> Changes:
        """What Save would write. Derived by comparison — see the module docstring."""
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
                elif _snapshot(item) != baseline:
                    modified.append(uuid)
            deleted.extend(slot.deleted)
        return Changes(created=created, modified=modified, deleted=deleted)

    @property
    def is_dirty(self) -> bool:
        return bool(self.changes())

    # ------------------------------------------------------------------
    # Committing
    # ------------------------------------------------------------------

    def commit(self) -> Changes:
        """Write every change to the user root and re-baseline. Returns what was written.

        Deletes run last. An entry removed and another created under the same
        name in one session is a rename by another route, and doing the delete
        first would be the only ordering where a failure mid-commit loses both.

        A single failed write is logged and skipped rather than raised: the rest
        of the session's work is not forfeit because one file could not be
        written, and the entry keeps its old baseline, so it still reads as
        modified and Save can be tried again.
        """
        written = self.changes()
        for kind in EDITABLE_KINDS:
            slot = self._slots[kind]
            save = _SAVERS[kind]
            for uuid in [*written.created, *written.modified]:
                item = slot.items.get(uuid)
                if item is None:
                    continue
                try:
                    save(item, root=self._root, user_root=self._user_root)
                except Exception:
                    logger.exception("Failed to save %s strategy %s", kind.value, uuid)
                    continue
                slot.baselines[uuid] = _snapshot(item)
        for kind in EDITABLE_KINDS:
            slot = self._slots[kind]
            delete = _DELETERS[kind]
            for uuid in slot.deleted:
                try:
                    delete(uuid, root=self._root, user_root=self._user_root)
                except Exception:
                    logger.exception("Failed to delete %s strategy %s", kind.value, uuid)
                    continue
                slot.baselines.pop(uuid, None)
            slot.deleted.clear()
        return written

    # ------------------------------------------------------------------
    # References
    # ------------------------------------------------------------------

    def usage(self, uuid: str):
        """How many things reference ``uuid``, counting unsaved edits.

        Counted against the working set rather than disk, which is the whole
        reason :func:`bartlebot.strategy_summary.find_references` takes lists:
        a player strategy linked into a duel a minute ago has no file saying so
        yet, and a delete confirmation that missed it would be wrong in exactly
        the case the user most needs it to be right.
        """
        from bartlebot.strategy_summary import find_references  # noqa: PLC0415 — avoids a cycle

        return find_references(uuid, self._bot_strategies, self.duels)
