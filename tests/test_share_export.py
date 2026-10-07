"""Share files and Export (#170): the format, what each kind collects, and what never travels.

Every store is a ``tmp_path`` pair, a shipped root and a user root, so Official and
Custom are told apart the way the app tells them. Nothing here is a real character.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from bartlebot import share_export
from bartlebot.bot.schema import BotConfig, BotOptionModel, RegionModel, StateModel, SubgraphSource, TemplateRef
from bartlebot.bot.strategy_models import (
    BotCombatStrategy,
    DeckData,
    DeckPreset,
    DuelCombatStrategy,
    FightRef,
    PlayerAssignment,
    PlayerCombatStrategy,
    StateAssignment,
)
from bartlebot.settings_manager import SettingsManager
from bartlebot.share_export import (
    BOT,
    BOT_COMBAT_STRATEGY,
    DECK,
    DUEL_STRATEGY,
    EXTENSIONS,
    PLAYER_STRATEGY,
    SUBGRAPH,
    Disposition,
    Sources,
)
from bartlebot.shared import share_file
from bartlebot.shared.content_hash import content_sha256
from bartlebot.strategy_store import save_bot_strategy, save_deck_preset, save_duel_strategy, save_player_strategy
from bartlebot.subgraph_store import SubgraphLibrary, save_subgraph
from tests.test_subgraph_store import _defn

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class Roots:
    """A shipped root and a user root, for every store at once."""

    def __init__(self, tmp_path: Path) -> None:
        self.shipped = tmp_path / "shipped"
        self.user = tmp_path / "user"
        self.shipped.mkdir()
        self.user.mkdir()

    def sources(self) -> Sources:
        return Sources(
            subgraph_library=SubgraphLibrary(self.shipped, self.user),
            strategy_root=self.shipped,
            strategy_user_root=self.user,
            bots_shipped=self.shipped / "bots",
            bots_user=self.user / "bots",
        )

    def ship_subgraph(self, defn) -> None:
        defn.save(self.shipped / "subgraphs" / f"{defn.id}.json")

    def user_subgraph(self, defn) -> None:
        save_subgraph(defn, self.shipped, self.user)

    def ship(self, folder: str, item) -> None:
        item.save(self.shipped / "strategies" / folder / f"{item.id}.json")


@pytest.fixture
def roots(tmp_path) -> Roots:
    return Roots(tmp_path)


def _region(region_id: str, subgraph_id: str, label: str = "") -> RegionModel:
    return RegionModel(
        id=region_id,
        label=label or region_id,
        members=[],
        color=1,
        source=SubgraphSource(subgraph_id=subgraph_id, version=1),
    )


def _bot(**kw) -> BotConfig:
    data = {
        "id": "bot-1",
        "name": "Boss Farmer",
        "description": "Farms the boss.",
        "initial": "a",
        "recovery_state": "a",
        "states": [StateModel(id="a", node_type="relog")],
        "transitions": [],
    }
    data.update(kw)
    return BotConfig(**data)


def _nesting(sg_id: str, name: str, *links: str):
    defn = _defn(sg_id, name)
    defn.regions = [_region(f"r{i}", link) for i, link in enumerate(links)]
    return defn


def _kinds(plan, disposition: Disposition) -> set[tuple[str, str]]:
    return {(e.kind, e.id) for e in plan.entries if e.disposition is disposition}


def _packed(plan) -> dict[tuple[str, str], share_file.PackedItem]:
    return {(i.kind, i.id): i for i in plan.share.items}


# ---------------------------------------------------------------------------
# The format
# ---------------------------------------------------------------------------


def test_a_share_file_says_its_format_the_app_and_its_main_item(roots, tmp_path):
    plan = share_export.export_player(PlayerCombatStrategy(id="p1", name="Hitter"), roots.sources())
    path = tmp_path / plan.suggested_file_name()
    plan.write(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["format"] == share_file.SHARE_FORMAT == 1
    assert data["app"] == "BartleBot"
    assert data["app_version"] == share_export.app_version()
    assert data["main"] == {"kind": PLAYER_STRATEGY, "id": "p1"}
    assert share_file.read(path) == plan.share


def test_every_kind_has_its_own_extension():
    assert EXTENSIONS == {
        BOT: ".bbot",
        BOT_COMBAT_STRATEGY: ".bbcombat",
        SUBGRAPH: ".bbsubgraph",
        DUEL_STRATEGY: ".bbduel",
        PLAYER_STRATEGY: ".bbplayer",
        DECK: ".bbdeck",
    }
    assert set(share_file.SHARED_EXTENSIONS) == {DUEL_STRATEGY, PLAYER_STRATEGY, DECK}, "the vendor's three"


def test_the_suggested_name_is_the_main_items_name_and_its_kinds_extension(roots):
    plan = share_export.export_bot(_bot(name="Boss Farmer: Jade Oni"), roots.sources(), official=False)
    assert plan.suggested_file_name() == "boss-farmer-jade-oni.bbot"


def test_a_packed_item_carries_its_content_its_hash_and_its_history_but_never_its_official_base(roots):
    pcs = PlayerCombatStrategy(id="p1", name="Hitter", dsl="any<damage>", history=["h2", "h1"])
    pcs.official_sha256 = "base"
    plan = share_export.export_player(pcs, roots.sources())

    (item,) = plan.share.items
    assert item.history == ["h2", "h1"]
    assert "history" not in item.content and "official_sha256" not in item.content
    assert item.content["dsl"] == "any<damage>"
    assert item.sha256 == content_sha256(pcs)
    assert item.sha256 == content_sha256(PlayerCombatStrategy.model_validate(item.content)), "checkable from content"


# ---------------------------------------------------------------------------
# Bots
# ---------------------------------------------------------------------------


def test_a_bot_packs_its_nested_custom_subgraphs_and_names_the_official_ones(roots):
    """The done-when: every nested Custom subgraph is packed, every Official one named."""
    roots.ship_subgraph(_defn("official-leaf", "Sync Players"))
    roots.user_subgraph(_nesting("inner", "Inner", "official-leaf"))
    roots.user_subgraph(_nesting("outer", "Outer", "inner", "official-leaf"))
    roots.user_subgraph(_defn("other", "Other"))
    bot = _bot(regions=[_region("r1", "outer"), _region("r2", "other")])

    plan = share_export.export_bot(bot, roots.sources(), official=False)

    assert set(_packed(plan)) == {(BOT, "bot-1"), (SUBGRAPH, "outer"), (SUBGRAPH, "inner"), (SUBGRAPH, "other")}
    assert [(r.kind, r.id) for r in plan.share.requires] == [(SUBGRAPH, "official-leaf")]
    assert plan.share.requires[0].sha256 == content_sha256(_defn("official-leaf", "Sync Players"))
    assert plan.share.main == share_file.ItemRef(kind=BOT, id="bot-1")


def test_each_subgraph_is_packed_once_however_many_links_reach_it(roots):
    roots.user_subgraph(_defn("leaf", "Leaf"))
    roots.user_subgraph(_nesting("a", "A", "leaf"))
    bot = _bot(regions=[_region("r1", "a"), _region("r2", "leaf"), _region("r3", "a")])

    plan = share_export.export_bot(bot, roots.sources(), official=False)

    assert [i.id for i in plan.share.items] == ["bot-1", "a", "leaf"]


def test_a_modified_official_subgraph_is_packed_as_a_copy(roots):
    roots.ship_subgraph(_defn("sg", "Sync Players"))
    roots.user_subgraph(_defn("sg", "Sync Players, mine"))

    plan = share_export.export_bot(_bot(regions=[_region("r", "sg")]), roots.sources(), official=False)

    item = _packed(plan)[(SUBGRAPH, "sg")]
    assert item.overrides_official is True
    assert item.content["name"] == "Sync Players, mine"
    assert plan.share.requires == []
    assert _kinds(plan, Disposition.COPY) == {(SUBGRAPH, "sg")}


def test_a_subgraph_missing_from_the_library_is_noted_and_neither_packed_nor_named(roots):
    plan = share_export.export_bot(_bot(regions=[_region("r", "gone", "Old Loop")]), roots.sources(), official=False)

    assert set(_packed(plan)) == {(BOT, "bot-1")}
    assert plan.share.requires == []
    (missing,) = [e for e in plan.entries if e.disposition is Disposition.MISSING]
    assert (missing.kind, missing.id, missing.name) == (SUBGRAPH, "gone", "Old Loop")


def test_a_bots_template_link_travels_as_it_is(roots):
    bot = _bot(template=TemplateRef(id="t_boss", version=3))
    plan = share_export.export_bot(bot, roots.sources(), official=False)
    assert _packed(plan)[(BOT, "bot-1")].content["template"] == {"id": "t_boss", "version": 3}


def test_an_official_bot_is_named_and_sends_only_what_rides_with_it(roots):
    plan = share_export.export_bot(_bot(), roots.sources(), official=True)
    assert plan.share.items == []
    assert [(r.kind, r.id) for r in plan.share.requires] == [(BOT, "bot-1")]
    assert not plan.sends_anything

    with_combat = share_export.export_bot(
        _bot(), roots.sources(), official=True, combat=BotCombatStrategy(id="b1", name="Mine", bot_id="bot-1")
    )
    assert with_combat.sends_anything
    assert set(_packed(with_combat)) == {(BOT_COMBAT_STRATEGY, "b1")}


# ---------------------------------------------------------------------------
# Bot Combat Strategies
# ---------------------------------------------------------------------------


def _combat_setup(roots: Roots) -> BotCombatStrategy:
    """A profile linking a Custom library duel, an Official one, and one of its own."""
    roots.ship("player", PlayerCombatStrategy(id="p-official", name="Default Hitter", dsl="any<damage>"))
    roots.ship(
        "duel",
        DuelCombatStrategy(
            id="d-official",
            name="Default Boss",
            player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id="p-official")],
        ),
    )
    save_player_strategy(PlayerCombatStrategy(id="p-mine", name="My Hitter"), roots.shipped, roots.user)
    save_player_strategy(PlayerCombatStrategy(id="p-own-link", name="Linked by my own"), roots.shipped, roots.user)
    save_duel_strategy(
        DuelCombatStrategy(
            id="d-mine",
            name="My Boss",
            player_assignments=[
                PlayerAssignment(players=[1], player_combat_strategy_id="p-mine"),
                PlayerAssignment(players=[2], player_combat_strategy_id="p-official"),
            ],
        ),
        roots.shipped,
        roots.user,
    )
    return BotCombatStrategy(
        id="b1",
        name="Solo",
        bot_id="bot-1",
        description="How I fight it.",
        default_strategy_id="d-official",
        state_assignments=[
            StateAssignment(fights=[FightRef(state="a")], duel_combat_strategy_id="d-mine"),
            StateAssignment(fights=[FightRef(state="b")], duel_combat_strategy_id="d-own"),
        ],
        duel_strategies=[
            DuelCombatStrategy(
                id="d-own",
                name="One-off",
                player_assignments=[
                    PlayerAssignment(players=[1], player_combat_strategy_id="p-own"),
                    PlayerAssignment(players=[2], player_combat_strategy_id="p-own-link"),
                ],
            )
        ],
        player_strategies=[PlayerCombatStrategy(id="p-own", name="Own")],
        player_decks={
            1: DeckData(normal={"Storm Lord": 2}, tc={"Old": 1}, archmastery_school="Storm", apply_archmastery=True)
        },
    )


def test_a_bot_with_its_combat_strategy_packs_the_custom_library_strategies_and_names_the_official(roots):
    bcs = _combat_setup(roots)

    plan = share_export.export_bot(_bot(), roots.sources(), official=False, combat=bcs)

    assert set(_packed(plan)) == {
        (BOT, "bot-1"),
        (BOT_COMBAT_STRATEGY, "b1"),
        (DUEL_STRATEGY, "d-mine"),
        (PLAYER_STRATEGY, "p-mine"),
        (PLAYER_STRATEGY, "p-own-link"),
    }
    assert {(r.kind, r.id) for r in plan.share.requires} == {
        (DUEL_STRATEGY, "d-official"),
        (PLAYER_STRATEGY, "p-official"),
    }
    content = _packed(plan)[(BOT_COMBAT_STRATEGY, "b1")].content
    assert [d["id"] for d in content["duel_strategies"]] == ["d-own"], "its own duels ride inside it"
    assert [p["id"] for p in content["player_strategies"]] == ["p-own"]


def test_a_combat_strategys_decks_travel_without_the_owners_fields_or_not_at_all(roots):
    bcs = _combat_setup(roots)
    sources = roots.sources()

    with_decks = _packed(share_export.export_bot_strategy(bcs, sources))[(BOT_COMBAT_STRATEGY, "b1")]
    deck = with_decks.content["player_decks"]["1"]
    assert deck["normal"] == {"Storm Lord": 2}
    assert deck["archmastery_school"] == "Storm"
    assert "tc" not in deck and "apply_archmastery" not in deck

    without = _packed(share_export.export_bot_strategy(bcs, sources, include_decks=False))[(BOT_COMBAT_STRATEGY, "b1")]
    assert "player_decks" not in without.content
    assert bcs.player_decks[1].apply_archmastery is True, "the user's own strategy is untouched"


def test_a_combat_strategy_names_or_packs_its_bot_and_the_bots_subgraphs(roots):
    roots.user_subgraph(_defn("sg", "Loop"))
    bot = _bot(regions=[_region("r", "sg")])
    bot.save(roots.user / "bots" / "farm.json")
    bcs = BotCombatStrategy(id="b1", name="Solo", bot_id="bot-1")

    plan = share_export.export_bot_strategy(bcs, roots.sources())
    assert plan.share.main == share_file.ItemRef(kind=BOT_COMBAT_STRATEGY, id="b1")
    assert set(_packed(plan)) == {(BOT_COMBAT_STRATEGY, "b1"), (BOT, "bot-1"), (SUBGRAPH, "sg")}

    (roots.user / "bots" / "farm.json").unlink()
    bot.save(roots.shipped / "bots" / "farm.json")
    plan = share_export.export_bot_strategy(bcs, roots.sources())
    assert [(r.kind, r.id) for r in plan.share.requires] == [(BOT, "bot-1")], "an Official bot is named"

    (roots.shipped / "bots" / "farm.json").unlink()
    plan = share_export.export_bot_strategy(bcs, roots.sources())
    assert _kinds(plan, Disposition.MISSING) == {(BOT, "bot-1")}


# ---------------------------------------------------------------------------
# Subgraphs, duels, players and decks
# ---------------------------------------------------------------------------


def test_a_subgraph_packs_its_nested_custom_subgraphs(roots):
    roots.ship_subgraph(_defn("official", "Official"))
    roots.user_subgraph(_defn("leaf", "Leaf"))
    outer = _nesting("outer", "Outer", "leaf", "official")
    roots.user_subgraph(outer)

    plan = share_export.export_subgraph(outer, roots.sources())

    assert [i.id for i in plan.share.items] == ["outer", "leaf"]
    assert [r.id for r in plan.share.requires] == ["official"]
    assert plan.extension == ".bbsubgraph"


def test_a_duel_packs_its_custom_player_strategies(roots):
    _combat_setup(roots)
    duel = roots.sources().library(share_export.StrategyKind.DUEL, "d-mine")[0]

    plan = share_export.export_duel(duel, roots.sources())

    assert [(i.kind, i.id) for i in plan.share.items] == [(DUEL_STRATEGY, "d-mine"), (PLAYER_STRATEGY, "p-mine")]
    assert [r.id for r in plan.share.requires] == ["p-official"]


def test_a_combat_strategys_own_duel_exports_with_its_own_player_strategies_packed(roots):
    bcs = _combat_setup(roots)

    plan = share_export.export_duel(bcs.duel_strategies[0], roots.sources(), profile=bcs)

    assert [(i.kind, i.id) for i in plan.share.items] == [
        (DUEL_STRATEGY, "d-own"),
        (PLAYER_STRATEGY, "p-own"),
        (PLAYER_STRATEGY, "p-own-link"),
    ]


def test_a_deck_file_holds_one_preset_without_tc_or_apply_archmastery(roots):
    preset = DeckPreset(
        id="k1",
        name="Storm Deck",
        description="For the boss.",
        tags=["Hitter"],
        deck=DeckData(normal={"Storm Lord": 2}, item={"Tempest": 1}, tc={"Old": 3}, apply_archmastery=False),
    )
    save_deck_preset(preset, roots.shipped, roots.user)

    plan = share_export.export_deck(preset, roots.sources())

    (item,) = plan.share.items
    assert item.content["name"] == "Storm Deck"
    assert item.content["description"] == "For the boss."
    assert item.content["tags"] == ["Hitter"]
    assert item.content["deck"] == {"normal": {"Storm Lord": 2}, "item": {"Tempest": 1}}
    assert item.sha256 == content_sha256(DeckPreset.model_validate(item.content))
    assert plan.extension == ".bbdeck"


def test_an_official_item_exported_alone_sends_nothing(roots):
    pcs = PlayerCombatStrategy(id="p1", name="Default Hitter")
    roots.ship("player", pcs)
    plan = share_export.export_player(pcs, roots.sources())
    assert not plan.sends_anything
    assert [r.id for r in plan.share.requires] == ["p1"]


def test_the_summary_lists_every_item_with_the_users_own_names_and_notes(roots):
    bcs = _combat_setup(roots)
    plan = share_export.export_bot(_bot(), roots.sources(), official=False, combat=bcs)

    by_id = {e.id: e for e in plan.entries}
    assert by_id["bot-1"].name == "Boss Farmer" and by_id["bot-1"].description == "Farms the boss."
    assert by_id["b1"].description == "How I fight it."
    assert by_id["d-official"].disposition is Disposition.NAMED
    assert [e.id for e in plan.entries][0] == "bot-1", "the main item leads"


# ---------------------------------------------------------------------------
# What never travels
# ---------------------------------------------------------------------------


_SENTINEL = "SETTINGS-SENTINEL-7f3a"


def _fill_settings_with_sentinels(bot_id: str, bcs_id: str) -> None:
    settings = SettingsManager.instance()
    settings.set_option_override(bot_id, "laps", _SENTINEL)
    settings.set_option_override(bot_id, "Safety Delay", _SENTINEL)
    settings.set("general", "default_safety_delay", value=_SENTINEL)
    settings.set_active_strategy(bot_id, bcs_id)
    settings.set("favorite_strategies", value={"duel": [_SENTINEL, "d-mine"]})


def test_no_settings_value_can_reach_a_share_file(roots, tmp_path):
    """The done-when: every kind's file is the same whatever settings hold, and holds none of it."""
    bcs = _combat_setup(roots)
    bot = _bot(options=[BotOptionModel(name="laps", type="int", default=1)])
    bot.save(roots.user / "bots" / "farm.json")
    save_bot_strategy(bcs, roots.shipped, roots.user)
    preset = DeckPreset(id="k1", name="Deck", deck=DeckData(normal={"Storm Lord": 1}))
    sources = roots.sources()
    duel = sources.library(share_export.StrategyKind.DUEL, "d-mine")[0]
    pcs = sources.library(share_export.StrategyKind.PLAYER, "p-mine")[0]

    def every_file() -> list[str]:
        plans = [
            share_export.export_bot(bot, sources, official=False, combat=bcs),
            share_export.export_bot_strategy(bcs, sources),
            share_export.export_subgraph(_defn(), sources),
            share_export.export_duel(duel, sources),
            share_export.export_player(pcs, sources),
            share_export.export_deck(preset, sources),
        ]
        texts = []
        for n, plan in enumerate(plans):
            path = tmp_path / f"{n}{plan.extension}"
            plan.write(path)
            texts.append(path.read_text(encoding="utf-8"))
        return texts

    before = every_file()
    _fill_settings_with_sentinels(bot.id, bcs.id)
    after = every_file()

    assert after == before
    assert not any(_SENTINEL in text for text in after)


@pytest.mark.parametrize("module", ["bartlebot/share_export.py", "bartlebot/shared/share_file.py"])
def test_nothing_that_writes_a_share_file_imports_settings(module):
    from bartlebot.paths import project_root

    tree = ast.parse((project_root() / module).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not imported & {"bartlebot.settings_manager", "bartlebot.option_values"}
