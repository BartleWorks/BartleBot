"""Export: one item, and everything Custom it needs, as one share file (#170).

The format, and the three kinds BartleBot shares with BartleBuddy (duel strategies,
player strategies and decks), are the vendor's: :mod:`bartlebot.shared.share_file`. This
module adds BartleBot's own kinds and follows each kind's links:

==========================  ===============  ==================================================
main item                   extension        what rides inside
==========================  ===============  ==================================================
bot                         ``.bbot``        its Custom subgraphs, nested ones included; the
                                             sender's Bot Combat Strategy for it, on request
Bot Combat Strategy         ``.bbcombat``    its duel and player strategies, from the library
                                             or its own; its decks, on request; its bot
subgraph                    ``.bbsubgraph``  its nested Custom subgraphs
duel strategy               ``.bbduel``      its Custom player strategies
player strategy             ``.bbplayer``    nothing
deck                        ``.bbdeck``      nothing
==========================  ===============  ==================================================

Custom items are packed; Official ones are named in ``requires``. A link the sender's
library cannot resolve (a subgraph deleted since a bot linked it) is neither: the summary
says so, and a bot still runs the snapshot it carries.

A bot's ``template`` link travels as it is. A receiver who has that template gets setup
steps and updates; one who doesn't still has a bot that runs.

**Nothing here reads settings.** Which Bot Combat Strategy a bot exports with is the
caller's to pass, chosen from the active pick, and the pick itself never travels; option
values are never in a bot file at all. ``test_share_export`` proves no settings value
reaches a file.

Qt-free: the export summary is :mod:`bartlebot.gui.export_dialog`.
"""

from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from bartlebot import bot_roots, strategy_store
from bartlebot.bot.schema import BotConfig, RegionModel, SubgraphDefinition
from bartlebot.bot.strategy_models import BotCombatStrategy, DeckPreset, DuelCombatStrategy, PlayerCombatStrategy
from bartlebot.shared import share_file
from bartlebot.shared.share_file import (
    DECK,
    DUEL_STRATEGY,
    PLAYER_STRATEGY,
    SHARED_EXTENSIONS,
    SHARED_FILE_TYPES,
    SHARED_NOUNS,
    Collector,
    Disposition,
    Entry,
    ShareFile,
    collect_deck,
    collect_duel,
    shareable_deck_data,
)
from bartlebot.shared.store_files import Origin, slugify
from bartlebot.shared.vendor import PRODUCT
from bartlebot.store_roots import declared_id
from bartlebot.strategy_store import StrategyKind
from bartlebot.subgraph_store import SubgraphLibrary

logger = logging.getLogger(__name__)

__all__ = [
    "BOT",
    "BOT_COMBAT_STRATEGY",
    "DECK",
    "DUEL_STRATEGY",
    "EXTENSIONS",
    "FILE_TYPES",
    "NOUNS",
    "PLAYER_STRATEGY",
    "SUBGRAPH",
    "Disposition",
    "Entry",
    "ExportPlan",
    "Sources",
    "app_version",
    "export_bot",
    "export_bot_strategy",
    "export_deck",
    "export_duel",
    "export_player",
    "export_subgraph",
]

#: BartleBot's own kinds, as a share file names them.
BOT = "bot"
BOT_COMBAT_STRATEGY = "bot_combat_strategy"
SUBGRAPH = "subgraph"

#: The extension a share file takes, by its main item's kind: BartleBot's own, then the vendor's.
EXTENSIONS: dict[str, str] = {
    BOT: ".bbot",
    BOT_COMBAT_STRATEGY: ".bbcombat",
    SUBGRAPH: ".bbsubgraph",
    **SHARED_EXTENSIONS,
}

#: What each kind is called in a sentence.
NOUNS: dict[str, str] = {
    BOT: "bot",
    BOT_COMBAT_STRATEGY: "Bot Combat Strategy",
    SUBGRAPH: "subgraph",
    **SHARED_NOUNS,
}

#: What a file of each kind is called: the save dialog's filter, and the installer's type (#27).
FILE_TYPES: dict[str, str] = {
    BOT: "BartleBot bot",
    BOT_COMBAT_STRATEGY: "BartleBot Bot Combat Strategy",
    SUBGRAPH: "BartleBot subgraph",
    **SHARED_FILE_TYPES,
}


def app_version() -> str:
    """This build's version, as a share file records it."""
    try:
        return importlib.metadata.version("bartlebot")
    except importlib.metadata.PackageNotFoundError:
        return "0+unknown"


@dataclass
class Sources:
    """Where Export reads each kind from, and where each item is: the session's stores, or a test's.

    Every lookup answers the item as the sender has it now (the user's copy over the
    Official one) and its origin, or None when nothing holds it.
    """

    subgraphs: SubgraphLibrary
    #: The strategy store's roots, in its argument convention; None for the real ones.
    strategy_root: str | Path | None = None
    strategy_user_root: str | Path | None = None
    #: The two bot folders; None for the real ones.
    bots_shipped: Path | None = None
    bots_user: Path | None = None

    def subgraph(self, subgraph_id: str) -> tuple[SubgraphDefinition, Origin] | None:
        defn = self.subgraphs.get(subgraph_id)
        return None if defn is None else (defn, self.subgraphs.origin(subgraph_id))

    def strategy_origin(self, kind: StrategyKind, item_id: str) -> Origin:
        return strategy_store.origin_of(kind, item_id, self.strategy_root, self.strategy_user_root)

    def library(self, kind: StrategyKind, item_id: str):
        """A library duel strategy, player strategy or deck, and its origin."""
        try:
            entry = strategy_store.load_library_entry(kind, item_id, self.strategy_root, self.strategy_user_root)
        except FileNotFoundError:
            return None
        except Exception:
            logger.warning("Could not read the %s strategy %s to export it", kind.value, item_id, exc_info=True)
            return None
        return entry.item, self.strategy_origin(kind, item_id)

    def bot(self, bot_id: str) -> tuple[BotConfig, Origin] | None:
        """The bot declaring ``bot_id`` in the library, and whether it is Official."""
        shipped = self.bots_shipped if self.bots_shipped is not None else bot_roots.shipped_bots_dir()
        user = self.bots_user if self.bots_user is not None else bot_roots.user_bots_dir()
        for found in bot_roots.library_files(shipped, user):
            if declared_id(found.path) != bot_id:
                continue
            try:
                return BotConfig.load(found.path), Origin.SHIPPED if found.shipped else Origin.USER
            except Exception:
                logger.warning("Could not read the bot %s to export it", found.path, exc_info=True)
                return None
        return None


@dataclass
class ExportPlan:
    """One export, gathered and ready to write: the file, and the summary's lines."""

    kind: str
    name: str
    share: ShareFile
    entries: list[Entry] = field(default_factory=list)

    @property
    def extension(self) -> str:
        return EXTENSIONS[self.kind]

    @property
    def sends_anything(self) -> bool:
        """Whether the file packs anything. When everything is Official, the receiver has it all already."""
        return bool(self.share.items)

    def suggested_file_name(self) -> str:
        """``boss-farmer.bbot``: the main item's name, made safe for a file."""
        return f"{slugify(self.name) or self.kind.replace('_', '-')}{self.extension}"

    def write(self, path: str | Path) -> None:
        share_file.write(self.share, path)


def _plan(collector: Collector, kind: str, name: str) -> ExportPlan:
    return ExportPlan(kind, name, collector.share_file(PRODUCT, app_version()), list(collector.entries))


def _in_hand(origin: Origin) -> Origin:
    """An item the caller holds that no store does, such as a Bot Combat Strategy's own, is Custom."""
    return Origin.USER if origin is Origin.MISSING else origin


# ---------------------------------------------------------------------------
# Following links
# ---------------------------------------------------------------------------


def _collect_subgraph_links(collector: Collector, regions: Iterable[RegionModel], sources: Sources) -> None:
    """Add every subgraph the linked ``regions`` name, and the ones each of those nests."""
    for region in regions:
        if region.source is None:
            continue
        subgraph_id = region.source.subgraph_id
        found = sources.subgraph(subgraph_id)
        if found is None:
            collector.note_missing(SUBGRAPH, subgraph_id, region.label)
            continue
        defn, origin = found
        if collector.add(SUBGRAPH, defn, origin):
            _collect_subgraph_links(collector, defn.regions, sources)


def _collect_bot(collector: Collector, bot: BotConfig, origin: Origin, sources: Sources) -> None:
    if collector.add(BOT, bot, origin):
        _collect_subgraph_links(collector, bot.regions, sources)


def _library_player(sources: Sources):
    return lambda pcs_id: sources.library(StrategyKind.PLAYER, pcs_id)


def shareable_bot_strategy(bcs: BotCombatStrategy, *, include_decks: bool) -> BotCombatStrategy:
    """``bcs`` as it travels: its decks as a deck file carries one, or none at all."""
    decks = {n: shareable_deck_data(d) for n, d in bcs.player_decks.items()} if include_decks else {}
    return bcs.model_copy(deep=True, update={"player_decks": decks})


def _collect_bot_strategy(
    collector: Collector, bcs: BotCombatStrategy, sources: Sources, *, include_decks: bool, follow_bot: bool
) -> None:
    """Add ``bcs``, the library strategies it and its own duels link, and, with ``follow_bot``, its bot."""
    shaped = shareable_bot_strategy(bcs, include_decks=include_decks)
    origin = _in_hand(sources.strategy_origin(StrategyKind.BOT, bcs.id))
    if not collector.add(BOT_COMBAT_STRATEGY, shaped, origin):
        return
    own_duels = {d.id for d in bcs.duel_strategies}
    own_players = {p.id for p in bcs.player_strategies}
    linked = [a.duel_combat_strategy_id for a in bcs.state_assignments if a.duel_combat_strategy_id]
    if bcs.default_strategy_id:
        linked.append(bcs.default_strategy_id)
    for duel_id in dict.fromkeys(linked):
        if duel_id in own_duels:
            continue
        found = sources.library(StrategyKind.DUEL, duel_id)
        if found is None:
            collector.note_missing(DUEL_STRATEGY, duel_id)
            continue
        collect_duel(collector, *found, _library_player(sources), inside=own_players)
    # The profile's own duels ride inside it, but the library player strategies they link do not.
    for duel in bcs.duel_strategies:
        for pcs_id in dict.fromkeys(duel.linked_pcs_ids()):
            if pcs_id in own_players:
                continue
            found = sources.library(StrategyKind.PLAYER, pcs_id)
            if found is None:
                collector.note_missing(PLAYER_STRATEGY, pcs_id)
            else:
                collector.add(PLAYER_STRATEGY, *found)
    if follow_bot:
        found_bot = sources.bot(bcs.bot_id)
        if found_bot is None:
            collector.note_missing(BOT, bcs.bot_id)
        else:
            _collect_bot(collector, *found_bot, sources)


# ---------------------------------------------------------------------------
# One export per kind
# ---------------------------------------------------------------------------


def export_bot(
    bot: BotConfig,
    sources: Sources,
    *,
    official: bool,
    combat: BotCombatStrategy | None = None,
    include_decks: bool = True,
) -> ExportPlan:
    """A bot file: ``bot`` as saved, its subgraphs, and ``combat`` when given.

    ``official`` says whether ``bot`` is an Official one, which is named rather than packed:
    exporting one sends only what rides with it.
    """
    collector = Collector(BOT, bot.id)
    _collect_bot(collector, bot, Origin.SHIPPED if official else Origin.USER, sources)
    if combat is not None:
        _collect_bot_strategy(collector, combat, sources, include_decks=include_decks, follow_bot=False)
    return _plan(collector, BOT, bot.name)


def export_bot_strategy(bcs: BotCombatStrategy, sources: Sources, *, include_decks: bool = True) -> ExportPlan:
    """A Bot Combat Strategy file: ``bcs``, the strategies it links, its decks on request, and its bot."""
    collector = Collector(BOT_COMBAT_STRATEGY, bcs.id)
    _collect_bot_strategy(collector, bcs, sources, include_decks=include_decks, follow_bot=True)
    return _plan(collector, BOT_COMBAT_STRATEGY, bcs.name)


def export_subgraph(defn: SubgraphDefinition, sources: Sources) -> ExportPlan:
    """A subgraph file: ``defn`` and the subgraphs it nests."""
    collector = Collector(SUBGRAPH, defn.id)
    if collector.add(SUBGRAPH, defn, _in_hand(sources.subgraphs.origin(defn.id))):
        _collect_subgraph_links(collector, defn.regions, sources)
    return _plan(collector, SUBGRAPH, defn.name)


def export_duel(duel: DuelCombatStrategy, sources: Sources, *, profile: BotCombatStrategy | None = None) -> ExportPlan:
    """A duel strategy file: ``duel`` and the player strategies it links.

    ``profile`` is the Bot Combat Strategy ``duel`` belongs to when it is one of its own:
    its links are found there first, as a run finds them, and packed as Custom items.
    """
    own = {p.id: p for p in profile.player_strategies} if profile is not None else {}

    def find_player(pcs_id: str):
        if pcs_id in own:
            return own[pcs_id], Origin.USER
        return sources.library(StrategyKind.PLAYER, pcs_id)

    collector = Collector(DUEL_STRATEGY, duel.id)
    collect_duel(collector, duel, _in_hand(sources.strategy_origin(StrategyKind.DUEL, duel.id)), find_player)
    return _plan(collector, DUEL_STRATEGY, duel.name)


def export_player(pcs: PlayerCombatStrategy, sources: Sources) -> ExportPlan:
    """A player strategy file: ``pcs`` alone."""
    collector = Collector(PLAYER_STRATEGY, pcs.id)
    collector.add(PLAYER_STRATEGY, pcs, _in_hand(sources.strategy_origin(StrategyKind.PLAYER, pcs.id)))
    return _plan(collector, PLAYER_STRATEGY, pcs.name)


def export_deck(preset: DeckPreset, sources: Sources) -> ExportPlan:
    """A deck file: ``preset`` alone, without the fields that are its owner's (:func:`shareable_deck_data`)."""
    collector = Collector(DECK, preset.id)
    collect_deck(collector, preset, _in_hand(sources.strategy_origin(StrategyKind.DECK, preset.id)))
    return _plan(collector, DECK, preset.name)
