"""Import: a share file's items, matched by id against what the receiver has, and written (#171).

The other half of :mod:`bartlebot.share_export`. ``docs/SHARING.md`` is the reader's
version of this docstring.

REFUSED BEFORE ANYTHING IS WRITTEN
----------------------------------

:func:`read` and :func:`check` raise :class:`Refused`, whose message is for the user:

- a ``format`` newer than this build knows, an item kind it doesn't, or a library
  item in a newer format: "Made with a newer BartleBot (1.2.0). Update BartleBot, then
  import again." The models drop fields they don't know, so importing it anyway would
  lose data the first time it was saved;
- an Official item in ``requires`` that is missing here, or that is here but different
  while the sender's ``app_version`` is newer than this build's: the same. Decided by
  version, not ancestry, since a shipped item changes outside an in-app save (tools, hand
  edits, merges) and records no history (#179). A file from the other product keeps the
  hash check, against this item's hash and its history;
- an item that fails validation, uses a node type this build doesn't have, or names a
  restricted zone, with the reason a run would give (:mod:`bartlebot.restricted_zones`).

MATCHING
--------

Each packed item is matched by its id, by the vendor's rules
(:mod:`bartlebot.shared.share_match`): Add, Already have it, Update, Keep mine, a
Conflict defaulting to **Import as copy**, and an item that would shadow an Official one
always coming in as a copy. Items are matched in the order their links run -- player
strategies, duels, subgraphs (nested ones first), bots, Bot Combat Strategies, decks --
so when one comes in as a copy, every item after it that links it is rewritten to the
copy *before* it is matched: a bot's subgraph nodes, a definition's nested ones, a
duel's player links, and a Bot Combat Strategy's links and its ``bot_id``. The
receiver's own items are never rewritten, so their bots stay on their original.

An item written other than as it was sent, rewritten or a copy, records the hash it was
sent with (``sent_sha256``), so the sender's next version of it is an Update. A conflict
first looks for a copy an earlier version made (:func:`_earlier_copy`), which the next
version updates in place rather than copying again.

An item is written with the history it arrived with, never through ``record_save``: it
is the sender's item, and its past is theirs (#169). A copy starts with none.

WHAT AN IMPORT NEVER DOES
-------------------------

It never writes over an Official item, never makes anything Official, and never changes
how an existing bot fights: an imported Bot Combat Strategy becomes the active pick only
for a bot with none (:func:`assign_active_picks`). Settings are touched there alone, and
only through the object the caller passes.

Qt-free: the import summary is :mod:`bartlebot.gui.import_dialog`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from bartlebot import bot_roots, restricted_zones, strategy_store
from bartlebot.bot.schema import PORT_NODE_TYPES, BotConfig, SubgraphDefinition
from bartlebot.bot.strategy_models import BotCombatStrategy, DeckPreset, DuelCombatStrategy, PlayerCombatStrategy
from bartlebot.combat_profile import duplicate_profile, read_bot_names
from bartlebot.new_bot import file_stem, library_folders, unique_file_name
from bartlebot.nodes.registry import NODE_REGISTRY
from bartlebot.share_export import (
    BOT,
    BOT_COMBAT_STRATEGY,
    DECK,
    DUEL_STRATEGY,
    NOUNS,
    PLAYER_STRATEGY,
    SUBGRAPH,
    Sources,
    app_version,
)
from bartlebot.shared import strategy_library
from bartlebot.shared.content_hash import content_sha256, next_history
from bartlebot.shared.share_file import SHARE_FORMAT, ShareFile
from bartlebot.shared.share_match import (
    CHOICES,
    WRITES,
    Action,
    Held,
    Outcome,
    copy_id,
    copy_ids,
    copy_outcome,
    match,
    relink_duel,
    update_message,
    version_is_newer,
)
from bartlebot.shared.store_files import Origin, load_in
from bartlebot.shared.strategy_library import LibraryKind
from bartlebot.shared.strategy_models import LIBRARY_FORMAT
from bartlebot.shared.vendor import PRODUCT
from bartlebot.store_roots import resolve_roots, resolve_shared_roots
from bartlebot.strategy_store import StrategyKind, unique_name

logger = logging.getLogger(__name__)

__all__ = [
    "Action",
    "ImportPlan",
    "ImportResult",
    "Incoming",
    "Outcome",
    "Refused",
    "Row",
    "apply_import",
    "assign_active_picks",
    "check",
    "plan_import",
    "read",
]

#: The model each kind's content is validated through.
MODELS: dict[str, type[BaseModel]] = {
    BOT: BotConfig,
    SUBGRAPH: SubgraphDefinition,
    BOT_COMBAT_STRATEGY: BotCombatStrategy,
    DUEL_STRATEGY: DuelCombatStrategy,
    PLAYER_STRATEGY: PlayerCombatStrategy,
    DECK: DeckPreset,
}

#: The order items are matched and written in: each kind after every kind it links.
ORDER: tuple[str, ...] = (PLAYER_STRATEGY, DUEL_STRATEGY, SUBGRAPH, BOT, BOT_COMBAT_STRATEGY, DECK)

_LIBRARY_KINDS = {
    PLAYER_STRATEGY: LibraryKind.PLAYER,
    DUEL_STRATEGY: LibraryKind.DUEL,
    DECK: LibraryKind.DECK,
}
_STRATEGY_KINDS = {
    PLAYER_STRATEGY: StrategyKind.PLAYER,
    DUEL_STRATEGY: StrategyKind.DUEL,
    DECK: StrategyKind.DECK,
    BOT_COMBAT_STRATEGY: StrategyKind.BOT,
}

#: What a kind is called in the plural.
PLURALS: dict[str, str] = {
    BOT: "bots",
    BOT_COMBAT_STRATEGY: "Bot Combat Strategies",
    SUBGRAPH: "subgraphs",
    DUEL_STRATEGY: "duel strategies",
    PLAYER_STRATEGY: "player strategies",
    DECK: "decks",
}

NOT_A_SHARE_FILE = "This isn't a file BartleBot can import: it isn't a bot, subgraph or strategy someone exported."
TEMPLATE_TEXT = "Templates can't be shared yet, so this file can't be imported."


class Refused(Exception):
    """Nothing was written: the file can't be imported. The message is for the user."""


# ---------------------------------------------------------------------------
# Reading and checking
# ---------------------------------------------------------------------------


def _text(raw: Mapping, key: str, fallback: str) -> str:
    value = raw.get(key)
    return value if isinstance(value, str) and value else fallback


def read(path: str | Path) -> ShareFile:
    """The share file at ``path``. Raises :class:`Refused` for one this build can't read.

    The format is checked before the rest is validated, so a file from a newer product
    gets the message that says to update rather than one that says it is broken.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise Refused(f"The file couldn't be read:\n\n{e}") from e
    try:
        raw = json.loads(text)
    except ValueError as e:
        raise Refused(NOT_A_SHARE_FILE) from e
    if not isinstance(raw, dict):
        raise Refused(NOT_A_SHARE_FILE)
    file_format = raw.get("format")
    if isinstance(file_format, int) and file_format > SHARE_FORMAT:
        raise Refused(update_message(_text(raw, "app", "app"), _text(raw, "app_version", "unknown"), PRODUCT))
    try:
        return ShareFile.model_validate(raw)
    except ValidationError as e:
        raise Refused(NOT_A_SHARE_FILE) from e


@dataclass
class Incoming:
    """One packed item, validated: the item as the sender has it, and what came with it."""

    kind: str
    id: str
    item: Any
    #: Its content hash, as worked out here from its content.
    sha256: str
    history: list[str]
    overrides_official: bool
    folder: str | None

    @property
    def name(self) -> str:
        return getattr(self.item, "name", "") or self.id


def _node_types(item: Any) -> list[str]:
    return [s.node_type for s in getattr(item, "states", [])]


def _check_item(kind: str, item: Any, newer: str) -> None:
    """Raise :class:`Refused` for an item that may not come in: a template, an unknown node, a restricted zone."""
    name = getattr(item, "name", "") or item.id
    if kind == BOT:
        if item.is_template:
            raise Refused(TEMPLATE_TEXT)
        if item.is_definition:
            raise Refused(f"'{name}' in this file isn't a valid bot.")
    if kind in (BOT, SUBGRAPH):
        allowed = set(NODE_REGISTRY) | (PORT_NODE_TYPES if kind == SUBGRAPH else set())
        unknown = sorted({t for t in _node_types(item) if t not in allowed})
        if unknown:
            raise Refused(
                f"'{name}' uses a node this BartleBot doesn't have ({', '.join(unknown)}). "
                f"It may have been made with a newer one.\n\n{newer}"
            )
        found = restricted_zones.check_states(item.states)
        if found:
            raise Refused(
                f"'{name}' sends a client to a zone BartleBot does not automate:\n\n"
                f"{restricted_zones.describe_found(found)}\n\n{restricted_zones.PVE_ONLY_TEXT}"
            )


def _may_differ(share: ShareFile, have: Any, sha256: str) -> bool:
    """Whether the sender's Official item may differ from ``have``, the receiver's: it was not made by a newer build."""
    if share.app == PRODUCT:
        return not version_is_newer(share.app_version, app_version())
    return sha256 in getattr(have, "history", [])


def check(share: ShareFile, sources: Sources) -> list[Incoming]:
    """Every packed item of ``share``, validated. Raises :class:`Refused` for the first reason it can't come in.

    Reads, never writes: what makes a file importable is decided before anything is.
    """
    newer = update_message(share.app, share.app_version, PRODUCT)
    for ref in (share.main, *share.items, *share.requires):
        if ref.kind not in MODELS:
            raise Refused(newer)
    incoming: list[Incoming] = []
    seen: set[tuple[str, str]] = set()
    for packed in share.items:
        noun = NOUNS[packed.kind]
        key = (packed.kind, packed.id)
        if key in seen:
            raise Refused(f"This file is damaged: it holds the {noun} {packed.id} twice.")
        seen.add(key)
        file_format = packed.content.get("format", 1)
        if packed.kind in _LIBRARY_KINDS and isinstance(file_format, int) and file_format > LIBRARY_FORMAT:
            raise Refused(newer)
        try:
            item = MODELS[packed.kind].model_validate(packed.content)
        except ValidationError as e:
            name = packed.content.get("name") or packed.id
            reason = e.errors()[0].get("msg", "") if e.errors() else ""
            raise Refused(f"'{name}' in this file isn't a valid {noun}: {reason}") from e
        if item.id != packed.id:
            raise Refused(f"This file is damaged: its {noun} {packed.id} holds {item.id}.")
        _check_item(packed.kind, item, newer)
        sha = content_sha256(item)
        if sha != packed.sha256:
            # Hand-edited, or hashed by a build that dumps differently. Matched by what it says.
            logger.warning(
                "The %s %s in this file hashes to %s, not the %s it gives", noun, item.id, sha, packed.sha256
            )
        incoming.append(
            Incoming(packed.kind, packed.id, item, sha, list(packed.history), packed.overrides_official, packed.folder)
        )
    main = (share.main.kind, share.main.id)
    if main not in seen and main not in {(r.kind, r.id) for r in share.requires}:
        raise Refused("This file is damaged: it doesn't hold what it is for.")
    for req in share.requires:
        have = official_item(sources, req.kind, req.id)
        if have is None or (content_sha256(have) != req.sha256 and not _may_differ(share, have, req.sha256)):
            logger.info("Refusing an import: it needs the Official %s %s at %s", NOUNS[req.kind], req.id, req.sha256)
            raise Refused(newer)
    return incoming


# ---------------------------------------------------------------------------
# What the receiver has
# ---------------------------------------------------------------------------


@dataclass
class Holding:
    """The receiver's copy of an id: the item, where it is, and what writing over it needs."""

    item: Any
    origin: Origin
    #: A bot's file.
    path: Path | None = None
    #: A shared library item's fingerprint, which a write over it must pass.
    fingerprint: str | None = None
    #: In a format newer than this build knows, so never written over.
    read_only: bool = False

    @property
    def official(self) -> bool:
        """Whether this id is an Official item's, overridden or not."""
        return self.origin in (Origin.SHIPPED, Origin.SHIPPED_OVERRIDE)

    def held(self) -> Held:
        return Held(
            content_sha256(self.item),
            tuple(getattr(self.item, "history", ())),
            getattr(self.item, "sent_sha256", None),
        )


def _shared_roots(sources: Sources) -> tuple[Path, Path]:
    return resolve_shared_roots(sources.strategy_root, sources.strategy_user_root)


def _bcs_dirs(sources: Sources) -> tuple[Path, Path]:
    shipped, user = resolve_roots(sources.strategy_root, sources.strategy_user_root)
    return shipped / "bot_strategies", user / "bot_strategies"


def holding(sources: Sources, kind: str, item_id: str) -> Holding | None:
    """The receiver's copy of ``item_id``, the user's over the Official one, or None when there is none."""
    if kind == SUBGRAPH:
        defn = sources.subgraphs.get(item_id)
        return None if defn is None else Holding(defn, sources.subgraphs.origin(item_id))
    if kind == BOT:
        found = sources.bot_file(item_id)
        if found is None:
            return None
        try:
            bot = BotConfig.load(found.path)
        except Exception:
            logger.warning("Could not read %s to match an import against it", found.path, exc_info=True)
            return None
        return Holding(bot, Origin.SHIPPED if found.shipped else Origin.USER, path=found.path)
    origin = sources.strategy_origin(_STRATEGY_KINDS[kind], item_id)
    if kind == BOT_COMBAT_STRATEGY:
        shipped, user = _bcs_dirs(sources)
        item = load_in(user, item_id, BotCombatStrategy.load) or load_in(shipped, item_id, BotCombatStrategy.load)
        return None if item is None else Holding(item, origin)
    shipped, user = _shared_roots(sources)
    try:
        entry = strategy_library.load(shipped, user, _LIBRARY_KINDS[kind], item_id)
    except FileNotFoundError:
        return None
    except Exception:
        logger.warning("Could not read the %s %s to match an import against it", NOUNS[kind], item_id, exc_info=True)
        return None
    return Holding(entry.item, origin, fingerprint=entry.fingerprint, read_only=entry.read_only)


def official_item(sources: Sources, kind: str, item_id: str) -> Any | None:
    """The Official item of ``item_id`` as it ships here, whatever override is over it; None when none ships.

    When the roots are one directory (developer mode) nothing is Official, and this is
    the one item there is.
    """
    if kind == SUBGRAPH:
        return sources.subgraphs.official(item_id)
    if kind == BOT:
        found = sources.bot_file(item_id)
        same = sources.shipped_bots == sources.user_bots
        if found is None or not (found.shipped or same):
            return None
        try:
            return BotConfig.load(found.path)
        except Exception:
            logger.warning("Could not read the Official bot %s", found.path, exc_info=True)
            return None
    if kind == BOT_COMBAT_STRATEGY:
        shipped, _user = _bcs_dirs(sources)
        return load_in(shipped, item_id, BotCombatStrategy.load)
    shipped, _user = _shared_roots(sources)
    return load_in(strategy_library.kind_dir(shipped, _LIBRARY_KINDS[kind]), item_id, MODELS[kind].load)  # type: ignore[attr-defined]


def _bot_dirs(sources: Sources) -> list[Path]:
    shipped, user = sources.shipped_bots, sources.user_bots
    return [shipped] if shipped == user else [shipped, user]


def _names_in_use(sources: Sources, kind: str) -> set[str]:
    """Every name the receiver's ``kind`` items have, for naming a copy apart from them."""
    if kind == SUBGRAPH:
        return {d.name for sid in sources.subgraphs.ids() if (d := sources.subgraphs.get(sid)) is not None}
    if kind == BOT:
        return set(read_bot_names(*_bot_dirs(sources)).values())
    if kind == BOT_COMBAT_STRATEGY:
        return {
            b.name for b in strategy_store.list_all_bot_strategies(sources.strategy_root, sources.strategy_user_root)
        }
    shipped, user = _shared_roots(sources)
    return {e.item.name for e in strategy_library.entries(shipped, user, _LIBRARY_KINDS[kind]).values()}  # type: ignore[attr-defined]


def _profile_links(bcs: BotCombatStrategy) -> set[str]:
    linked = {a.duel_combat_strategy_id for a in bcs.state_assignments if a.duel_combat_strategy_id}
    if bcs.default_strategy_id:
        linked.add(bcs.default_strategy_id)
    return linked


def bots_affected(sources: Sources, kind: str, item_id: str) -> list[str]:
    """The names of the receiver's bots that writing over their ``item_id`` changes, sorted.

    What an Update or a Replace names, so the user knows what they are agreeing to.
    """
    if kind == BOT or kind == DECK:
        return []
    dirs = _bot_dirs(sources)
    if kind == SUBGRAPH:
        from bartlebot.subgraph_store import bots_linking  # noqa: PLC0415 - one caller

        return bots_linking(item_id, *dirs)
    names = read_bot_names(*dirs)
    profiles = strategy_store.list_all_bot_strategies(sources.strategy_root, sources.strategy_user_root)
    if kind == BOT_COMBAT_STRATEGY:
        bot_ids = {b.bot_id for b in profiles if b.id == item_id}
    elif kind == DUEL_STRATEGY:
        bot_ids = {b.bot_id for b in profiles if item_id in _profile_links(b)}
    else:
        shipped, user = _shared_roots(sources)
        duels = strategy_library.entries(shipped, user, LibraryKind.DUEL).values()
        linking = {e.item.id for e in duels if item_id in e.item.linked_pcs_ids()}  # type: ignore[attr-defined]
        bot_ids = {
            b.bot_id
            for b in profiles
            if _profile_links(b) & linking or any(item_id in d.linked_pcs_ids() for d in b.duel_strategies)
        }
    return sorted({names[b] for b in bot_ids if b in names}, key=str.casefold)


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclass
class Row:
    """One packed item, matched: what it is to the receiver's, and what the import does with it."""

    kind: str
    id: str
    name: str
    description: str
    outcome: Outcome
    action: Action
    #: The id it is written under, or that the file's links to it now name: a copy's new one, else its own.
    target_id: str
    #: The item as it would be written: links followed to copies, and a copy re-identified.
    item: Any
    holding: Holding | None = None
    #: The receiver's bots that an Update or a Replace changes, by name.
    affected: list[str] = field(default_factory=list)
    #: A bot's library folder, from the file, when the receiver has one of that name.
    folder: str | None = None

    @property
    def choices(self) -> tuple[Action, ...]:
        return CHOICES[self.outcome]

    @property
    def asks(self) -> bool:
        """Whether the user has a choice to make about it."""
        return len(self.choices) > 1

    @property
    def writes(self) -> bool:
        return self.action in WRITES


@dataclass
class ImportPlan:
    """One import, matched and ready to write."""

    share: ShareFile
    rows: list[Row]
    #: The name of the item the file is for. An Official one is named by the receiver's own copy.
    main_name: str

    @property
    def main(self) -> Row | None:
        """The main item's row, or None when it is an Official one the file only names."""
        return self.row(self.share.main.kind, self.share.main.id)

    def row(self, kind: str, item_id: str) -> Row | None:
        return next((r for r in self.rows if (r.kind, r.id) == (kind, item_id)), None)

    @property
    def asks(self) -> bool:
        return any(r.asks for r in self.rows)

    @property
    def writes_anything(self) -> bool:
        return any(r.writes for r in self.rows)

    @property
    def choices(self) -> dict[tuple[str, str], Action]:
        """Each row's action, keyed as :func:`plan_import` takes them back."""
        return {(r.kind, r.id): r.action for r in self.rows}


def _links(kind: str, item: Any) -> set[str]:
    """The subgraph ids a bot or a definition links."""
    if kind not in (BOT, SUBGRAPH):
        return set()
    return {r.source.subgraph_id for r in item.regions if r.source is not None}


def _ordered(incoming: list[Incoming]) -> list[Incoming]:
    """``incoming`` in :data:`ORDER`, each subgraph after the subgraphs in the file it nests."""
    by_kind = {kind: [i for i in incoming if i.kind == kind] for kind in ORDER}
    subgraphs = {i.id: i for i in by_kind[SUBGRAPH]}
    placed: list[Incoming] = []
    # Marked on the way in, so a cycle (which no saved definition can have) ends rather than recursing.
    visiting: set[str] = set()

    def place(inc: Incoming) -> None:
        if inc.id in visiting:
            return
        visiting.add(inc.id)
        for linked in sorted(_links(SUBGRAPH, inc.item)):
            if linked in subgraphs:
                place(subgraphs[linked])
        placed.append(inc)

    for inc in by_kind[SUBGRAPH]:
        place(inc)
    by_kind[SUBGRAPH] = placed
    return [i for kind in ORDER for i in by_kind[kind]]


def _relink(kind: str, item: Any, copies: Mapping[str, Mapping[str, str]]) -> None:
    """Point ``item``'s links at the copies ``copies`` names, by kind then old id. Changes ``item`` in place."""
    subgraphs, duels, players, bots = (copies.get(k, {}) for k in (SUBGRAPH, DUEL_STRATEGY, PLAYER_STRATEGY, BOT))
    if kind in (BOT, SUBGRAPH):
        for region in item.regions:
            if region.source is not None and region.source.subgraph_id in subgraphs:
                region.source.subgraph_id = subgraphs[region.source.subgraph_id]
    elif kind == DUEL_STRATEGY:
        relink_duel(item, players)
    elif kind == BOT_COMBAT_STRATEGY:
        item.bot_id = bots.get(item.bot_id, item.bot_id)
        for assignment in item.state_assignments:
            if assignment.duel_combat_strategy_id in duels:
                assignment.duel_combat_strategy_id = duels[assignment.duel_combat_strategy_id]
        if item.default_strategy_id in duels:
            item.default_strategy_id = duels[item.default_strategy_id]
        for duel in item.duel_strategies:
            relink_duel(duel, players)


def _as_copy(kind: str, item: Any, new_id: str, names: set[str]) -> Any:
    """``item`` as a copy under ``new_id``: named apart from the receiver's, with no history of its own."""
    name = unique_name(item.name, names)
    if kind == BOT_COMBAT_STRATEGY:
        # Its own strategies get new ids too, as Duplicate gives them (combat_profile).
        copy = duplicate_profile(item, name)
        copy.id = new_id
        return copy
    copy = item.model_copy(deep=True, update={"id": new_id, "name": name, "history": []})
    if "official_sha256" in type(copy).model_fields:
        copy.official_sha256 = None
    return copy


def _over_copy(kind: str, item: Any, copy: Any) -> Any:
    """``item``, a later version of what came in as ``copy``, as written over it: under its id and name.

    Its history is the copy's own, with the copy as it is now added, as any save over it
    would record.
    """
    history = next_history(copy.history, content_sha256(copy), content_sha256(item))
    if kind == BOT_COMBAT_STRATEGY:
        updated = duplicate_profile(item, copy.name)
        updated.id = copy.id
    else:
        updated = item.model_copy(deep=True, update={"id": copy.id, "name": copy.name})
    updated.history = history
    if "official_sha256" in type(updated).model_fields:
        updated.official_sha256 = None
    return updated


def _earlier_copy(sources: Sources, inc: Incoming) -> tuple[str, Outcome, Holding] | None:
    """The copy an earlier import made of ``inc``, unchanged since: its id, what ``inc`` is to it, and the copy.

    ``COPIED`` when it holds this version already, ``NEWER`` when an earlier one
    (:func:`~bartlebot.shared.share_match.copy_outcome`). None when there is no such copy.
    """
    for cid in copy_ids(inc.kind, inc.id, inc.sha256, inc.history):
        have = holding(sources, inc.kind, cid)
        if have is None or have.read_only:
            continue
        outcome = copy_outcome(inc.sha256, inc.history, have.held())
        if outcome is not None:
            return cid, outcome, have
    return None


def _folder(sources: Sources, folder: str | None) -> str | None:
    """``folder`` when the receiver's library has a folder of that name, else None, for the top."""
    if not folder:
        return None
    return folder if folder in library_folders(*_bot_dirs(sources)) else None


def plan_import(
    share: ShareFile,
    sources: Sources,
    choices: Mapping[tuple[str, str], Action] | None = None,
    *,
    incoming: list[Incoming] | None = None,
) -> ImportPlan:
    """Match every item of ``share`` against what ``sources`` holds, with the user's ``choices``.

    ``choices`` maps ``(kind, id)`` to an action, as :attr:`ImportPlan.choices` gives
    them; one the row doesn't allow, or none, takes the row's default. ``incoming`` is
    :func:`check`'s result, when the caller already has it; otherwise it is checked
    here, and may raise :class:`Refused`. Reads, never writes.
    """
    if incoming is None:
        incoming = check(share, sources)
    choices = choices or {}
    copies: dict[str, dict[str, str]] = {}
    names: dict[str, set[str]] = {}
    rows: list[Row] = []
    ordered = _ordered(incoming)
    # An item the receiver already has as it came, before any relinking, keeps its links, so a second
    # import of the same file finds it again (#185). What it links defaults to Keep mine, not a copy.
    pinned: set[tuple[str, str]] = set()
    kept_links: set[tuple[str, str]] = set()
    for inc in ordered:
        if inc.kind not in (BOT, SUBGRAPH):
            continue
        have = holding(sources, inc.kind, inc.id)
        if have is not None and not have.read_only and content_sha256(inc.item) == content_sha256(have.item):
            pinned.add((inc.kind, inc.id))
            kept_links |= {(SUBGRAPH, linked) for linked in _links(inc.kind, inc.item)}
    for inc in ordered:
        # Content never carries the history: it travels beside it, and is written as it arrived.
        item = inc.item.model_copy(deep=True, update={"history": list(inc.history)})
        if (inc.kind, inc.id) not in pinned:
            _relink(inc.kind, item, copies)
        sha = content_sha256(item)
        held = holding(sources, inc.kind, inc.id)
        new_id = copy_id(inc.kind, inc.id, inc.sha256)
        official = inc.overrides_official or (held is not None and held.official)
        if held is not None and held.read_only:
            outcome = Outcome.OLDER
        else:
            outcome = match(
                sha,
                inc.history,
                held.held() if held is not None else None,
                sent=inc.sha256,
                official=official,
                copied=holding(sources, inc.kind, new_id) is not None,
            )
        # The copy an earlier import made of this item, which its next version updates.
        earlier = _earlier_copy(sources, inc) if outcome in (Outcome.CONFLICT, Outcome.OFFICIAL) else None
        if earlier is not None:
            outcome, held = earlier[1], earlier[2]
        allowed = CHOICES[outcome]
        wanted = choices.get((inc.kind, inc.id))
        action = wanted if wanted in allowed else allowed[0]
        if wanted is None and outcome is Outcome.CONFLICT and (inc.kind, inc.id) in kept_links:
            action = Action.KEEP
        target = inc.id
        if earlier is not None and action is not Action.COPY:
            # Update, Keep, or already there: the copy stands for the item, and links follow it.
            target = earlier[0]
            copies.setdefault(inc.kind, {})[inc.id] = target
        elif action is Action.COPY or outcome is Outcome.COPIED:
            target = new_id
            copies.setdefault(inc.kind, {})[inc.id] = new_id
        if action is Action.COPY:
            if inc.kind not in names:
                names[inc.kind] = _names_in_use(sources, inc.kind)
            item = _as_copy(inc.kind, item, new_id, names[inc.kind])
            names[inc.kind].add(item.name)
        elif earlier is not None and action is Action.UPDATE:
            item = _over_copy(inc.kind, item, held.item)
        # Written other than as sent, it remembers how it was sent, so the next version updates it.
        item.sent_sha256 = inc.sha256 if content_sha256(item) != inc.sha256 else None
        rows.append(
            Row(
                kind=inc.kind,
                id=inc.id,
                name=inc.name,
                description=getattr(inc.item, "description", None) or "",
                outcome=outcome,
                action=action,
                target_id=target,
                item=item,
                holding=held,
                affected=bots_affected(sources, inc.kind, earlier[0] if earlier else inc.id)
                if len(allowed) > 1
                else [],
                folder=_folder(sources, inc.folder) if inc.kind == BOT else None,
            )
        )
    main = next((r for r in rows if (r.kind, r.id) == (share.main.kind, share.main.id)), None)
    if main is not None:
        main_name = main.name
    else:
        official = official_item(sources, share.main.kind, share.main.id)
        main_name = getattr(official, "name", None) or share.main.id
    return ImportPlan(share, rows, main_name)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


@dataclass
class ImportResult:
    """What an import wrote."""

    rows: list[Row] = field(default_factory=list)
    #: Each bot written, by the id it was written under, and where.
    bot_paths: dict[str, Path] = field(default_factory=dict)
    #: ``(Bot Combat Strategy id, bot id)`` for each one new here, which may become its bot's pick.
    strategies_added: list[tuple[str, str]] = field(default_factory=list)


def _write_bot(sources: Sources, row: Row) -> Path:
    if row.action in (Action.UPDATE, Action.REPLACE) and row.holding is not None and row.holding.path is not None:
        path = row.holding.path
    else:
        folder = sources.user_bots / row.folder if row.folder else sources.user_bots
        path = folder / unique_file_name(file_stem(row.item.name), folder)
    if not bot_roots.may_write(path, sources.user_bots):
        # Never reached by the matching above, which only writes over the user's own bots.
        raise PermissionError(f"Not writing {path}: it is an Official bot")
    path.parent.mkdir(parents=True, exist_ok=True)
    row.item.save(path)
    return path


def _write(sources: Sources, row: Row, result: ImportResult) -> None:
    item = row.item
    if row.kind == BOT:
        result.bot_paths[item.id] = _write_bot(sources, row)
    elif row.kind == SUBGRAPH:
        sources.subgraphs.save(item, record=False)
    elif row.kind == BOT_COMBAT_STRATEGY:
        strategy_store.save_bot_strategy(item, sources.strategy_root, sources.strategy_user_root, record=False)
        if row.action in (Action.ADD, Action.COPY):
            result.strategies_added.append((item.id, item.bot_id))
    else:
        shipped, user = _shared_roots(sources)
        over = row.action in (Action.UPDATE, Action.REPLACE) and row.holding is not None
        expected = row.holding.fingerprint if over else None
        strategy_library.save(user, _LIBRARY_KINDS[row.kind], item, expected=expected, shipped=shipped, record=False)


def apply_import(plan: ImportPlan, sources: Sources) -> ImportResult:
    """Write every row of ``plan`` that writes, in the order its links run. What was written.

    A write that fails -- the disk, or the shared library changed by another product
    since the plan was made -- raises, having written the rows before it and none after.
    """
    result = ImportResult()
    for row in plan.rows:
        if not row.writes:
            continue
        _write(sources, row, result)
        result.rows.append(row)
        logger.info("Imported the %s '%s' (%s)", NOUNS[row.kind], row.item.name, row.action.value)
    return result


def assign_active_picks(result: ImportResult, settings: Any) -> list[str]:
    """Make each new Bot Combat Strategy its bot's active pick, for a bot that has none. The bot ids changed.

    ``settings`` is the settings manager, passed so this module never imports it. A bot
    that already has a pick keeps it: an import never changes how an existing bot fights.
    """
    changed: list[str] = []
    for bcs_id, bot_id in result.strategies_added:
        if bot_id in changed or settings.get_active_strategy(bot_id) is not None:
            continue
        settings.set_active_strategy(bot_id, bcs_id)
        changed.append(bot_id)
    return changed


# ---------------------------------------------------------------------------
# What the summary says
# ---------------------------------------------------------------------------


def _counted(kind: str, n: int) -> str:
    noun = NOUNS[kind]
    if n == 1:
        return f"{'an' if noun[0].lower() in 'aeiou' else 'a'} {noun}"
    return f"{n} {PLURALS[kind]}"


def _listed(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _riders(rows: Iterable[Row]) -> list[str]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.kind] = counts.get(row.kind, 0) + 1
    return [_counted(kind, counts[kind]) for kind in ORDER if kind in counts]


def headline(plan: ImportPlan) -> str:
    """The summary's one line: "Import 'Boss X', with 2 subgraphs and a Bot Combat Strategy?"."""
    main = plan.main
    written = [r for r in plan.rows if r.writes]
    if not written:
        return f"You already have '{plan.main_name}' and everything that comes with it."
    others = [r for r in written if r is not main]
    if main is not None and main.writes:
        riders = _riders(others)
        return f"Import '{plan.main_name}'" + (f", with {_listed(riders)}?" if riders else "?")
    return f"You already have '{plan.main_name}'. Import {_listed(_riders(others))} for it?"


#: What a row says about its item, by outcome.
OUTCOME_TEXT: dict[Outcome, str] = {
    Outcome.NEW: "New to you",
    Outcome.SAME: "Already in your library",
    Outcome.COPIED: "Already in your library, as a copy",
    Outcome.OFFICIAL: "A change to an Official one, so it comes in as a copy",
    Outcome.NEWER: "A newer version of yours",
    Outcome.OLDER: "Yours is newer, so yours is kept",
    Outcome.CONFLICT: "Different from yours",
}

#: What each action is called where the user picks one.
ACTION_TEXT: dict[Action, str] = {
    Action.ADD: "Add",
    Action.UPDATE: "Update mine",
    Action.REPLACE: "Replace mine",
    Action.COPY: "Import as copy",
    Action.KEEP: "Keep mine",
}


def affected_text(row: Row) -> str:
    """ "Used by Boss X and Boss Y": the bots an Update or a Replace changes, or "" for none."""
    return f"Used by {_listed(row.affected)}" if row.affected else ""
