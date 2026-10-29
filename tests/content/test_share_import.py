"""Share files: Import (#171). Matching, the defaults, what Import as copy rewrites, and what is refused.

A sender and a receiver, each a ``tmp_path`` pair of a shipped root and a user root, so a
file goes from one library to another the way it does between two users. Nothing here
is a real character.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bartleby.shared import share_file
from bartleby.shared.content_hash import content_sha256
from bartleby.shared.share_match import Held, copy_id, copy_ids, copy_outcome, match
from bartleby.shared.strategy_models import (
    DeckData,
    DeckPreset,
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
)

from bartlebot.bot.schema import BotConfig, RegionModel, StateModel, SubgraphSource
from bartlebot.combat.bot_strategy_models import BotCombatStrategy, FightRef, StateAssignment
from bartlebot.combat.strategy_store import (
    list_all_bot_strategies,
    load_bot_strategy,
    load_duel_strategy,
    load_player_strategy,
    save_bot_strategy,
    save_duel_strategy,
    save_player_strategy,
)
from bartlebot.content import bot_roots, share_export, share_import
from bartlebot.content.share_export import (
    BOT,
    BOT_COMBAT_STRATEGY,
    DECK,
    DUEL_STRATEGY,
    PLAYER_STRATEGY,
    SUBGRAPH,
    Sources,
)
from bartlebot.content.share_import import Action, Outcome, Refused
from bartlebot.content.subgraphs.subgraph_store import SubgraphLibrary, load_subgraph, save_subgraph
from bartlebot.settings_manager import SettingsManager
from tests.content.subgraphs.test_subgraph_store import _defn

WORLD = "01_wizard_city"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class Library:
    """One user's install and data: a shipped root and a user root, for every store at once."""

    def __init__(self, base: Path) -> None:
        self.shipped = base / "shipped"
        self.user = base / "user"
        (self.shipped / "bots" / WORLD).mkdir(parents=True)
        (self.user / "bots").mkdir(parents=True)

    def sources(self) -> Sources:
        return Sources(
            subgraph_library=SubgraphLibrary(self.shipped, self.user),
            strategy_root=self.shipped,
            strategy_user_root=self.user,
            bots_shipped=self.shipped / "bots",
            bots_user=self.user / "bots",
        )

    def subgraph(self, defn) -> None:
        save_subgraph(defn, self.shipped, self.user)

    def ship_subgraph(self, defn) -> None:
        defn.save(self.shipped / "subgraphs" / f"{defn.id}.json")

    def ship(self, folder: str, item) -> None:
        item.save(self.shipped / "strategies" / folder / f"{item.id}.json")

    def bot(self, bot: BotConfig, name: str = "farm", folder: str | None = WORLD) -> Path:
        path = self.user / "bots" / (folder or "") / f"{name}.json"
        bot.save(path)
        return path

    def files(self) -> dict[str, bytes]:
        """Every file under the user root, with its bytes: what an import may change."""
        return {str(p.relative_to(self.user)): p.read_bytes() for p in sorted(self.user.rglob("*")) if p.is_file()}

    def bots(self) -> dict[str, BotConfig]:
        return {b.id: b for p in sorted((self.user / "bots").rglob("*.json")) if (b := BotConfig.load(p))}


@pytest.fixture
def sender(tmp_path) -> Library:
    return Library(tmp_path / "sender")


@pytest.fixture
def receiver(tmp_path) -> Library:
    return Library(tmp_path / "receiver")


def _region(region_id: str, subgraph_id: str) -> RegionModel:
    return RegionModel(
        id=region_id, label=region_id, members=[], color=1, source=SubgraphSource(subgraph_id=subgraph_id, version=1)
    )


def _bot(bot_id: str = "bot-1", name: str = "Boss Farmer", *links: str, **kw) -> BotConfig:
    data = {
        "id": bot_id,
        "name": name,
        "initial": "a",
        "recovery_state": "a",
        "states": [StateModel(id="a", node_type="relog")],
        "transitions": [],
        "regions": [_region(f"r{i}", link) for i, link in enumerate(links)],
    }
    data.update(kw)
    return BotConfig(**data)


def _combat(bot_id: str = "bot-1") -> BotCombatStrategy:
    return BotCombatStrategy(
        id="bcs-1",
        name="Solo",
        bot_id=bot_id,
        state_assignments=[StateAssignment(fights=[FightRef(state="a")], duel_combat_strategy_id="d-mine")],
        duel_strategies=[DuelCombatStrategy(id="d-own", name="One-off")],
    )


def _sender_setup(sender: Library) -> tuple[BotConfig, BotCombatStrategy]:
    """The done-when's file: a bot using two Custom subgraphs, with a Custom Bot Combat Strategy."""
    sender.subgraph(_defn("sg-a", "Loop A"))
    sender.subgraph(_defn("sg-b", "Loop B"))
    save_player_strategy(PlayerCombatStrategy(id="p-mine", name="My Hitter"), sender.shipped, sender.user)
    save_duel_strategy(
        DuelCombatStrategy(
            id="d-mine",
            name="My Boss",
            player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id="p-mine")],
        ),
        sender.shipped,
        sender.user,
    )
    bot = _bot("bot-1", "Boss Farmer", "sg-a", "sg-b")
    sender.bot(bot)
    return bot, _combat()


def _export(plan, tmp_path: Path, name: str = "sent") -> Path:
    path = tmp_path / f"{name}{plan.extension}"
    plan.write(path)
    return path


def _send_bot(sender: Library, tmp_path: Path, bot: BotConfig, combat=None, name: str = "sent") -> Path:
    plan = share_export.export_bot(bot, sender.sources(), official=False, combat=combat, folder=WORLD)
    return _export(plan, tmp_path, name)


def _plan(receiver: Library, path: Path, choices=None):
    return share_import.plan_import(share_import.read(path), receiver.sources(), choices)


def _import(receiver: Library, path: Path, choices=None):
    sources = receiver.sources()
    plan = share_import.plan_import(share_import.read(path), sources, choices)
    result = share_import.apply_import(plan, sources)
    return plan, result


def _outcomes(plan) -> dict[tuple[str, str], Outcome]:
    return {(r.kind, r.id): r.outcome for r in plan.rows}


# ---------------------------------------------------------------------------
# The done-when
# ---------------------------------------------------------------------------


def test_a_bot_with_two_subgraphs_and_its_combat_strategy_arrives_whole_with_no_question(sender, receiver, tmp_path):
    bot, bcs = _sender_setup(sender)
    path = _send_bot(sender, tmp_path, bot, bcs)

    plan, result = _import(receiver, path)

    assert not plan.asks, "nothing conflicts, so nothing is asked"
    assert set(_outcomes(plan).values()) == {Outcome.NEW}
    assert share_import.headline(plan) == (
        "Import 'Boss Farmer', with a player strategy, a duel strategy, 2 subgraphs and a Bot Combat Strategy?"
    )
    got = receiver.bots()["bot-1"]
    assert [r.source.subgraph_id for r in got.regions] == ["sg-a", "sg-b"]
    assert {load_subgraph(s, receiver.shipped, receiver.user).name for s in ("sg-a", "sg-b")} == {"Loop A", "Loop B"}
    assert load_bot_strategy("bcs-1", receiver.shipped, receiver.user).bot_id == "bot-1"
    assert load_duel_strategy("d-mine", receiver.shipped, receiver.user).name == "My Boss"
    assert load_player_strategy("p-mine", receiver.shipped, receiver.user).name == "My Hitter"
    assert result.bot_paths["bot-1"].parent.name == WORLD, "it lands in the world folder it was sent from"


def test_the_second_of_two_bots_sharing_a_subgraph_imports_with_no_question_about_it(sender, receiver, tmp_path):
    sender.subgraph(_defn("sg", "Shared Loop"))
    first, second = _bot("bot-1", "First", "sg"), _bot("bot-2", "Second", "sg")
    sender.bot(first, "first")
    sender.bot(second, "second")
    first_file = _send_bot(sender, tmp_path, first, name="first")
    second_file = _send_bot(sender, tmp_path, second, name="second")

    _import(receiver, first_file)
    plan, _ = _import(receiver, second_file)

    assert not plan.asks
    assert _outcomes(plan) == {(BOT, "bot-2"): Outcome.NEW, (SUBGRAPH, "sg"): Outcome.SAME}
    assert share_import.headline(plan) == "Import 'Second'?"


def test_the_second_bot_after_a_conflict_finds_the_copy_the_first_made_and_asks_nothing(sender, receiver, tmp_path):
    sender.subgraph(_defn("sg", "Their Loop"))
    receiver.subgraph(_defn("sg", "My Loop"))
    first, second = _bot("bot-1", "First", "sg"), _bot("bot-2", "Second", "sg")
    sender.bot(first, "first")
    sender.bot(second, "second")

    first_plan, _ = _import(receiver, _send_bot(sender, tmp_path, first, name="first"))
    assert first_plan.asks, "the first one conflicts"
    plan, _ = _import(receiver, _send_bot(sender, tmp_path, second, name="second"))

    assert not plan.asks
    assert plan.row(SUBGRAPH, "sg").outcome is Outcome.COPIED
    copy = first_plan.row(SUBGRAPH, "sg").target_id
    assert receiver.bots()["bot-2"].regions[0].source.subgraph_id == copy, "both bots are on the one copy"


def test_importing_the_same_file_twice_changes_nothing_the_second_time(sender, receiver, tmp_path):
    bot, bcs = _sender_setup(sender)
    receiver.subgraph(_defn("sg-a", "My own Loop A"))  # a conflict, imported as a copy
    path = _send_bot(sender, tmp_path, bot, bcs)

    _import(receiver, path)
    before = receiver.files()
    plan, result = _import(receiver, path)

    assert not plan.asks
    assert not plan.writes_anything
    assert result.rows == []
    assert receiver.files() == before
    assert share_import.headline(plan) == "You already have 'Boss Farmer' and everything that comes with it."


def test_a_conflicting_subgraph_imported_as_a_copy_keeps_each_bot_on_its_own(sender, receiver, tmp_path):
    sender.subgraph(_defn("sg", "Their Loop"))
    theirs = _bot("bot-1", "Their Farmer", "sg")
    sender.bot(theirs)
    receiver.subgraph(_defn("sg", "My Loop"))
    mine = _bot("bot-mine", "My Farmer", "sg")
    mine_path = receiver.bot(mine, "mine")
    mine_bytes = mine_path.read_bytes()

    plan = _plan(receiver, _send_bot(sender, tmp_path, theirs))
    row = plan.row(SUBGRAPH, "sg")
    assert row.outcome is Outcome.CONFLICT
    assert row.action is Action.COPY, "a conflict defaults to Import as copy"
    assert row.choices == (Action.COPY, Action.REPLACE, Action.KEEP)
    assert row.affected == ["My Farmer"], "it names the receiver's bots the conflict touches"
    share_import.apply_import(plan, receiver.sources())

    assert mine_path.read_bytes() == mine_bytes, "the receiver's own bot is untouched"
    assert load_subgraph("sg", receiver.shipped, receiver.user).name == "My Loop"
    copy = load_subgraph(row.target_id, receiver.shipped, receiver.user)
    assert copy.name == "Their Loop"
    assert receiver.bots()["bot-1"].regions[0].source.subgraph_id == row.target_id, "the imported bot is on the copy"


def test_a_file_from_a_newer_app_is_refused_and_writes_nothing(sender, receiver, tmp_path):
    bot, _ = _sender_setup(sender)
    path = _send_bot(sender, tmp_path, bot)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["format"] = share_file.SHARE_FORMAT + 1
    data["app_version"] = "1.2.0"
    data["items"][0]["content"]["a_field_from_the_future"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    before = receiver.files()

    with pytest.raises(Refused) as refused:
        share_import.read(path)

    assert str(refused.value) == "Made with a newer BartleBot (1.2.0). Update BartleBot, then import again."
    assert receiver.files() == before


@pytest.mark.parametrize("theirs", ["missing", "older"])
def test_a_file_needing_a_newer_official_item_is_refused_with_the_update_message(sender, receiver, tmp_path, theirs):
    sender.ship_subgraph(_defn("official", "Sync Players", version=2))
    if theirs == "older":
        receiver.ship_subgraph(_defn("official", "Sync Players", version=1))
    bot = _bot("bot-1", "Boss Farmer", "official")
    sender.bot(bot)
    share = share_import.read(_send_bot(sender, tmp_path, bot))
    share.app_version = "1.2.0"

    with pytest.raises(Refused, match=r"Made with a newer BartleBot \(1\.2\.0\)\. Update BartleBot"):
        share_import.plan_import(share, receiver.sources())
    assert receiver.bots() == {}


def test_an_official_item_newer_here_than_the_senders_is_fine(sender, receiver, tmp_path):
    old = _defn("official", "Sync Players", version=1)
    sender.ship_subgraph(old)
    newer = _defn("official", "Sync Players", version=2)
    newer.history = [content_sha256(old)]
    receiver.ship_subgraph(newer)
    bot = _bot("bot-1", "Boss Farmer", "official")
    sender.bot(bot)

    plan, _ = _import(receiver, _send_bot(sender, tmp_path, bot))

    assert _outcomes(plan) == {(BOT, "bot-1"): Outcome.NEW}
    assert receiver.bots()["bot-1"].regions[0].source.subgraph_id == "official"


def test_no_step_asks_for_an_id(sender, receiver, tmp_path):
    """Every choice is an action on a row; the plan takes nothing typed."""
    sender.subgraph(_defn("sg", "Their Loop"))
    receiver.subgraph(_defn("sg", "My Loop"))
    bot = _bot("bot-1", "Boss Farmer", "sg")
    sender.bot(bot)
    plan = _plan(receiver, _send_bot(sender, tmp_path, bot))
    assert all(isinstance(a, Action) for r in plan.rows for a in r.choices)
    assert all(text and "id" not in text.lower().split() for text in share_import.ACTION_TEXT.values())


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def test_a_newer_copy_of_the_receivers_item_defaults_to_update_naming_the_bots_it_changes(sender, receiver, tmp_path):
    old = _defn("sg", "Loop")
    receiver.subgraph(old)
    receiver.bot(_bot("bot-mine", "My Farmer", "sg"), "mine")
    sender.subgraph(old)
    sender.subgraph(_defn("sg", "Loop, improved"))  # its history now holds the old hash
    bot = _bot("bot-1", "Boss Farmer", "sg")
    sender.bot(bot)

    plan = _plan(receiver, _send_bot(sender, tmp_path, bot))
    row = plan.row(SUBGRAPH, "sg")
    assert row.outcome is Outcome.NEWER
    assert row.choices == (Action.UPDATE, Action.COPY, Action.KEEP)
    assert row.affected == ["My Farmer"]
    assert share_import.affected_text(row) == "Used by My Farmer"
    share_import.apply_import(plan, receiver.sources())

    got = load_subgraph("sg", receiver.shipped, receiver.user)
    assert got.name == "Loop, improved"
    assert got.history == [content_sha256(old)], "written with the history it arrived with"


def test_the_receivers_newer_copy_is_kept_without_asking(sender, receiver, tmp_path):
    old = _defn("sg", "Loop")
    sender.subgraph(old)
    receiver.subgraph(old)
    receiver.subgraph(_defn("sg", "Loop, mine since"))
    bot = _bot("bot-1", "Boss Farmer", "sg")
    sender.bot(bot)

    plan = _plan(receiver, _send_bot(sender, tmp_path, bot))

    assert plan.row(SUBGRAPH, "sg").outcome is Outcome.OLDER
    assert not plan.asks
    share_import.apply_import(plan, receiver.sources())
    assert load_subgraph("sg", receiver.shipped, receiver.user).name == "Loop, mine since"


def test_keep_mine_and_replace_leave_the_imported_bot_on_the_receivers_id(sender, receiver, tmp_path):
    sender.subgraph(_defn("sg", "Their Loop"))
    receiver.subgraph(_defn("sg", "My Loop"))
    bot = _bot("bot-1", "Boss Farmer", "sg")
    sender.bot(bot)
    path = _send_bot(sender, tmp_path, bot)

    keep = _plan(receiver, path, {(SUBGRAPH, "sg"): Action.KEEP})
    assert keep.row(SUBGRAPH, "sg").target_id == "sg"
    assert keep.row(BOT, "bot-1").item.regions[0].source.subgraph_id == "sg"

    _import(receiver, path, {(SUBGRAPH, "sg"): Action.REPLACE})
    assert load_subgraph("sg", receiver.shipped, receiver.user).name == "Their Loop"
    assert receiver.bots()["bot-1"].regions[0].source.subgraph_id == "sg"


def test_importing_a_file_again_after_keep_mine_writes_nothing(sender, receiver, tmp_path):
    sender.subgraph(_defn("sg", "Their Loop"))
    receiver.subgraph(_defn("sg", "My Loop"))
    bot = _bot("bot-1", "Boss Farmer", "sg")
    sender.bot(bot)
    path = _send_bot(sender, tmp_path, bot)

    _import(receiver, path, {(SUBGRAPH, "sg"): Action.KEEP})
    before = receiver.files()
    plan, result = _import(receiver, path)

    assert plan.row(BOT, "bot-1").outcome is Outcome.SAME
    assert plan.row(SUBGRAPH, "sg").action is Action.KEEP
    assert not plan.writes_anything
    assert result.rows == []
    assert receiver.files() == before


def test_a_choice_a_row_does_not_allow_takes_its_default(sender, receiver, tmp_path):
    bot, _ = _sender_setup(sender)
    plan = _plan(receiver, _send_bot(sender, tmp_path, bot), {(BOT, "bot-1"): Action.REPLACE})
    assert plan.row(BOT, "bot-1").action is Action.ADD


def test_an_item_with_an_official_id_never_shadows_it_and_comes_in_as_a_copy(sender, receiver, tmp_path):
    receiver.ship_subgraph(_defn("sg", "Sync Players"))
    sender.subgraph(_defn("sg", "Sync Players, theirs"))  # the sender has no Official one: it's theirs
    bot = _bot("bot-1", "Boss Farmer", "sg")
    sender.bot(bot)

    plan, _ = _import(receiver, _send_bot(sender, tmp_path, bot))

    row = plan.row(SUBGRAPH, "sg")
    assert row.outcome is Outcome.OFFICIAL
    assert row.choices == (Action.COPY,)
    assert not plan.asks
    assert not (receiver.user / "subgraphs").exists() or all(
        json.loads(p.read_text(encoding="utf-8"))["id"] != "sg" for p in (receiver.user / "subgraphs").glob("*.json")
    ), "no override of the Official subgraph was written"
    assert receiver.bots()["bot-1"].regions[0].source.subgraph_id == row.target_id


def test_a_change_to_an_official_item_comes_in_as_a_copy_even_without_the_official_one(sender, receiver, tmp_path):
    sender.ship_subgraph(_defn("sg", "Sync Players"))
    sender.subgraph(_defn("sg", "Sync Players, changed"))
    bot = _bot("bot-1", "Boss Farmer", "sg")
    sender.bot(bot)

    plan = _plan(receiver, _send_bot(sender, tmp_path, bot))

    row = plan.row(SUBGRAPH, "sg")
    (packed,) = [i for i in plan.share.items if i.kind == SUBGRAPH]
    assert packed.overrides_official
    assert row.outcome is Outcome.OFFICIAL
    assert row.target_id == copy_id(SUBGRAPH, "sg", packed.sha256), "the copy's id is worked out, the same every time"


def test_an_official_bot_is_never_written_over(sender, receiver, tmp_path):
    official = _bot("bot-1", "Boss Farmer")
    official.save(receiver.shipped / "bots" / WORLD / "farm.json")
    theirs = _bot("bot-1", "Boss Farmer", description="Theirs.")
    sender.bot(theirs)

    plan, result = _import(receiver, _send_bot(sender, tmp_path, theirs))

    row = plan.row(BOT, "bot-1")
    assert row.outcome is Outcome.OFFICIAL
    assert BotConfig.load(receiver.shipped / "bots" / WORLD / "farm.json") == official
    assert result.bot_paths[row.target_id].is_relative_to(receiver.user)


def test_a_copy_is_named_apart_from_the_receivers_and_starts_with_no_history(sender, receiver, tmp_path):
    sent = _defn("sg", "Loop")
    sent.description = "theirs"
    sender.subgraph(sent)
    sender.subgraph(sent.model_copy(update={"description": "theirs, again"}))
    receiver.subgraph(_defn("sg", "Loop"))
    bot = _bot("bot-1", "Boss Farmer", "sg")
    sender.bot(bot)

    plan, _ = _import(receiver, _send_bot(sender, tmp_path, bot))

    copy = load_subgraph(plan.row(SUBGRAPH, "sg").target_id, receiver.shipped, receiver.user)
    assert copy.name == "Loop (2)"
    assert copy.history == []


def test_an_added_item_keeps_the_history_it_arrived_with(sender, receiver, tmp_path):
    sender.subgraph(_defn("sg", "Loop"))
    sender.subgraph(_defn("sg", "Loop, edited"))
    bot = _bot("bot-1", "Boss Farmer", "sg")
    sender.bot(bot)

    _import(receiver, _send_bot(sender, tmp_path, bot))

    assert load_subgraph("sg", receiver.shipped, receiver.user).history == [content_sha256(_defn("sg", "Loop"))]


def test_nested_subgraphs_follow_a_copy_inside_the_file(sender, receiver, tmp_path):
    leaf = _defn("leaf", "Leaf")
    sender.subgraph(leaf)
    outer = _defn("outer", "Outer")
    outer.regions = [_region("r0", "leaf")]
    sender.subgraph(outer)
    receiver.subgraph(_defn("leaf", "My Leaf"))
    bot = _bot("bot-1", "Boss Farmer", "outer")
    sender.bot(bot)

    plan, _ = _import(receiver, _send_bot(sender, tmp_path, bot))

    leaf_copy = plan.row(SUBGRAPH, "leaf").target_id
    assert [r.kind for r in plan.rows] == [SUBGRAPH, SUBGRAPH, BOT], "the nested one first"
    assert load_subgraph("outer", receiver.shipped, receiver.user).regions[0].source.subgraph_id == leaf_copy


# ---------------------------------------------------------------------------
# Combat
# ---------------------------------------------------------------------------


def test_a_copied_duel_and_bot_are_followed_by_the_combat_strategy(sender, receiver, tmp_path):
    bot, bcs = _sender_setup(sender)
    mine = receiver.bot(_bot("bot-1", "My Farmer", description="Mine."), "mine")
    save_player_strategy(PlayerCombatStrategy(id="p-mine", name="Mine too"), receiver.shipped, receiver.user)
    path = _send_bot(sender, tmp_path, bot, bcs)

    plan, _ = _import(receiver, path)

    player = plan.row(PLAYER_STRATEGY, "p-mine").target_id
    duel = plan.row(DUEL_STRATEGY, "d-mine")
    new_bot = plan.row(BOT, "bot-1").target_id
    assert player != "p-mine" and new_bot != "bot-1"
    assert duel.outcome is Outcome.NEW, "it was rewritten before it was matched"
    assert load_duel_strategy("d-mine", receiver.shipped, receiver.user).linked_pcs_ids() == [player]
    imported = load_bot_strategy("bcs-1", receiver.shipped, receiver.user)
    assert imported.bot_id == new_bot
    assert BotConfig.load(mine).name == "My Farmer", "the receiver's own bot is untouched"


def test_a_copied_combat_strategy_gives_its_own_strategies_new_ids_and_keeps_them_linked(sender, receiver, tmp_path):
    bcs = _combat()
    bcs.default_strategy_id = "d-own"
    save_bot_strategy(bcs, sender.shipped, sender.user)
    sender.bot(_bot())
    receiver.bot(_bot(), "farm")
    save_bot_strategy(bcs.model_copy(update={"description": "Mine."}), receiver.shipped, receiver.user)
    plan = share_export.export_bot_strategy(load_bot_strategy("bcs-1", sender.shipped, sender.user), sender.sources())
    path = _export(plan, tmp_path)

    imported, _ = _import(receiver, path, {(BOT_COMBAT_STRATEGY, "bcs-1"): Action.COPY})

    copy = load_bot_strategy(imported.row(BOT_COMBAT_STRATEGY, "bcs-1").target_id, receiver.shipped, receiver.user)
    assert copy.name == "Solo (2)"
    (own,) = copy.duel_strategies
    assert own.id != "d-own"
    assert copy.default_strategy_id == own.id


def test_an_imported_combat_strategy_is_the_active_pick_only_for_a_bot_with_none(sender, receiver, tmp_path):
    bot, bcs = _sender_setup(sender)
    path = _send_bot(sender, tmp_path, bot, bcs)
    settings = SettingsManager.instance()

    _, result = _import(receiver, path)
    assert share_import.assign_active_picks(result, settings) == ["bot-1"]
    assert settings.get_active_strategy("bot-1") == "bcs-1"

    settings.set_active_strategy("bot-1", "my-own")
    other = bcs.model_copy(update={"id": "bcs-2", "name": "Duo"})
    save_bot_strategy(other, sender.shipped, sender.user)
    _, result = _import(receiver, _export(share_export.export_bot_strategy(other, sender.sources()), tmp_path, "two"))
    assert result.strategies_added == [("bcs-2", "bot-1")]
    assert share_import.assign_active_picks(result, settings) == []
    assert settings.get_active_strategy("bot-1") == "my-own", "an import never changes how an existing bot fights"
    assert {b.id for b in list_all_bot_strategies(receiver.shipped, receiver.user)} == {"bcs-1", "bcs-2"}


def test_a_combat_strategy_for_an_official_bot_names_it_in_the_headline(sender, receiver, tmp_path):
    official = _bot("bot-1", "Boss Farmer")
    official.save(sender.shipped / "bots" / WORLD / "farm.json")
    official.save(receiver.shipped / "bots" / WORLD / "farm.json")
    bcs = BotCombatStrategy(id="bcs-1", name="Solo", bot_id="bot-1")
    plan = share_export.export_bot(official, sender.sources(), official=True, combat=bcs)

    imported = _plan(receiver, _export(plan, tmp_path))

    assert imported.main is None
    assert share_import.headline(imported) == "You already have 'Boss Farmer'. Import a Bot Combat Strategy for it?"


def test_a_deck_arrives_in_the_library(sender, receiver, tmp_path):
    preset = DeckPreset(id="k1", name="Storm Deck", schools=["Storm"], deck=DeckData(normal={"Storm Lord": 2}))
    plan = share_export.export_deck(preset, sender.sources())

    imported, _ = _import(receiver, _export(plan, tmp_path))

    assert imported.row(DECK, "k1").outcome is Outcome.NEW
    from bartlebot.combat.strategy_store import load_deck_preset

    got = load_deck_preset("k1", receiver.shipped, receiver.user)
    assert got.deck.normal == {"Storm Lord": 2} and got.schools == ["Storm"]


def test_an_update_of_a_shared_library_item_changed_by_another_product_writes_nothing_after_it(
    sender, receiver, tmp_path
):
    old = PlayerCombatStrategy(id="p1", name="Hitter")
    save_player_strategy(old, receiver.shipped, receiver.user)
    first = save_player_strategy(old, sender.shipped, sender.user)
    save_player_strategy(
        PlayerCombatStrategy(id="p1", name="Hitter", dsl="any<damage>"), sender.shipped, sender.user, expected=first
    )
    sent = load_player_strategy("p1", sender.shipped, sender.user)
    plan = share_import.plan_import(
        share_import.read(_export(share_export.export_player(sent, sender.sources()), tmp_path)), receiver.sources()
    )
    assert plan.row(PLAYER_STRATEGY, "p1").action is Action.UPDATE
    # Another product saves it between the summary and Import.
    save_player_strategy(
        PlayerCombatStrategy(id="p1", name="Hitter, elsewhere"),
        receiver.shipped,
        receiver.user,
        expected=share_import.holding(receiver.sources(), PLAYER_STRATEGY, "p1").fingerprint,
    )

    with pytest.raises(share_import.strategy_library.ChangedOnDisk):
        share_import.apply_import(plan, receiver.sources())
    assert load_player_strategy("p1", receiver.shipped, receiver.user).name == "Hitter, elsewhere"


# ---------------------------------------------------------------------------
# Refused
# ---------------------------------------------------------------------------


def _file_with(sender: Library, tmp_path: Path, bot: BotConfig) -> Path:
    sender.bot(bot)
    return _send_bot(sender, tmp_path, bot)


def test_a_bot_naming_a_restricted_zone_is_refused_with_the_reason_a_run_gives(sender, receiver, tmp_path):
    bot = _bot(
        states=[StateModel(id="a", node_type="enter_zone", label="Go", params={"zone": "WizardCity/WC_Duel_Arena_New"})]
    )
    path = _file_with(sender, tmp_path, bot)
    with pytest.raises(Refused) as refused:
        _plan(receiver, path)
    assert "does not automate" in str(refused.value)
    assert "• Go: WizardCity/WC_Duel_Arena_New" in str(refused.value)
    assert receiver.bots() == {}


def test_a_subgraph_naming_a_restricted_zone_is_refused_too(sender, receiver, tmp_path):
    defn = _defn("sg", "Loop")
    defn.states[1].params = {"to_zone": "WizardCity/WC_Duel_Arena_New"}
    sender.subgraph(defn)
    with pytest.raises(Refused, match="does not automate"):
        _plan(receiver, _export(share_export.export_subgraph(defn, sender.sources()), tmp_path))


def test_an_unknown_node_type_is_refused(sender, receiver, tmp_path):
    path = _file_with(sender, tmp_path, _bot(states=[StateModel(id="a", node_type="fly_to_the_moon")]))
    with pytest.raises(Refused, match="fly_to_the_moon"):
        _plan(receiver, path)


def test_an_invalid_item_is_refused(sender, receiver, tmp_path):
    path = _file_with(sender, tmp_path, _bot())
    data = json.loads(path.read_text(encoding="utf-8"))
    data["items"][0]["content"]["initial"] = "nowhere"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(Refused, match="isn't a valid bot"):
        _plan(receiver, path)


def test_a_newer_library_format_inside_a_file_is_refused(sender, receiver, tmp_path):
    plan = share_export.export_player(PlayerCombatStrategy(id="p1", name="Hitter"), sender.sources())
    path = _export(plan, tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["items"][0]["content"]["format"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(Refused, match="Update BartleBot"):
        _plan(receiver, path)


def test_an_unknown_kind_is_refused_as_from_a_newer_app(sender, receiver, tmp_path):
    path = _file_with(sender, tmp_path, _bot())
    data = json.loads(path.read_text(encoding="utf-8"))
    data["main"] = {"kind": "template", "id": "t1"}
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(Refused, match="Update BartleBot"):
        _plan(receiver, path)


@pytest.mark.parametrize("text", ["not json", "[]", '{"format": 1}'])
def test_a_file_that_is_not_a_share_file_is_refused(tmp_path, text):
    path = tmp_path / "x.bbot"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(Refused, match="isn't a file BartleBot can import"):
        share_import.read(path)


def test_a_folder_the_receiver_does_not_have_puts_the_bot_at_the_top(sender, receiver, tmp_path):
    bot = _bot()
    sender.bot(bot)
    plan = share_export.export_bot(bot, sender.sources(), official=False, folder="../../outside")
    _, result = _import(receiver, _export(plan, tmp_path))
    assert result.bot_paths["bot-1"].parent == receiver.user / "bots"


def test_the_folder_travels_beside_the_content_and_is_never_hashed(sender, tmp_path):
    bot = _bot()
    plan = share_export.export_bot(bot, sender.sources(), official=False, folder=WORLD)
    (item,) = plan.share.items
    assert item.folder == WORLD and "folder" not in item.content
    assert item.sha256 == content_sha256(bot)
    others = share_export.export_player(PlayerCombatStrategy(id="p1", name="Hitter"), sender.sources())
    path = _export(others, tmp_path)
    assert "folder" not in json.loads(path.read_text(encoding="utf-8"))["items"][0], "left out when there is none"


def test_an_official_item_changed_outside_a_save_still_imports_from_an_older_sender(sender, receiver, tmp_path):
    """No history records the change: the sender's build is older, so the difference is not theirs to refuse (#179)."""
    sender.ship_subgraph(_defn("official", "Sync Players", version=1))
    receiver.ship_subgraph(_defn("official", "Sync Players", version=2))
    bot = _bot("bot-1", "Boss Farmer", "official")
    sender.bot(bot)
    path = _send_bot(sender, tmp_path, bot)

    share = share_import.read(path)
    share.app_version = "0.0.1"
    plan = share_import.plan_import(share, receiver.sources())
    assert _outcomes(plan) == {(BOT, "bot-1"): Outcome.NEW}

    share.app_version = "99.0.0"
    with pytest.raises(Refused, match=r"Made with a newer BartleBot \(99\.0\.0\)"):
        share_import.plan_import(share, receiver.sources())


def test_a_file_from_the_other_product_keeps_the_hash_check(sender, receiver, tmp_path):
    """No version of ours to compare with: the sender's hash must be the receiver's, or in its history."""
    sender.ship_subgraph(_defn("official", "Sync Players", version=1))
    receiver.ship_subgraph(_defn("official", "Sync Players", version=2))
    bot = _bot("bot-1", "Boss Farmer", "official")
    sender.bot(bot)
    share = share_import.read(_send_bot(sender, tmp_path, bot))
    share.app = "OtherApp"
    share.app_version = "0.0.1"

    with pytest.raises(Refused, match=r"Made with a newer OtherApp"):
        share_import.plan_import(share, receiver.sources())


# ---------------------------------------------------------------------------
# The next version of what came in changed
# ---------------------------------------------------------------------------


def _resave(sender: Library, bot: BotConfig, **changes) -> BotConfig:
    """The sender's next version of ``bot``, saved in place as the app saves one, so its history holds the last."""
    path = sender.user / "bots" / WORLD / "farm.json"
    later = bot.model_copy(deep=True, update=changes)
    bot_roots.record_save(later, path)
    later.save(path)
    return later


def _conflicted_first_import(sender: Library, receiver: Library, tmp_path: Path):
    """A bot linking a subgraph the receiver has their own of: the subgraph comes in as a copy, the bot on it."""
    sender.subgraph(_defn("sg", "Their Loop"))
    receiver.subgraph(_defn("sg", "My Loop"))
    bot = _bot("bot-1", "Boss Farmer", "sg")
    sender.bot(bot)
    plan, _ = _import(receiver, _send_bot(sender, tmp_path, bot, name="first"))
    return bot, plan.row(SUBGRAPH, "sg").target_id


def test_a_bot_on_a_copy_records_the_hash_it_was_sent_with(sender, receiver, tmp_path):
    bot, copy = _conflicted_first_import(sender, receiver, tmp_path)

    got = receiver.bots()["bot-1"]
    assert got.regions[0].source.subgraph_id == copy
    assert got.sent_sha256 == content_sha256(bot)
    assert content_sha256(got) != content_sha256(bot), "its links were rewritten"


def test_the_next_version_of_a_bot_on_a_copy_is_an_update_not_another_copy(sender, receiver, tmp_path):
    """The bullet from the plan: the bot comes in as Update, still on the copy, and nothing is copied twice."""
    bot, copy = _conflicted_first_import(sender, receiver, tmp_path)
    later = _resave(sender, bot, name="Boss Farmer, faster")

    plan, _ = _import(receiver, _send_bot(sender, tmp_path, later, name="second"))

    row = plan.row(BOT, "bot-1")
    assert (row.outcome, row.action) == (Outcome.NEWER, Action.UPDATE)
    assert plan.row(SUBGRAPH, "sg").outcome is Outcome.COPIED
    got = receiver.bots()
    assert list(got) == ["bot-1"], "no copy of the bot"
    assert got["bot-1"].name == "Boss Farmer, faster"
    assert got["bot-1"].regions[0].source.subgraph_id == copy
    assert got["bot-1"].sent_sha256 == content_sha256(later)


def test_a_bot_on_a_copy_the_receiver_changed_is_theirs_and_comes_in_as_a_copy(sender, receiver, tmp_path):
    bot, _copy = _conflicted_first_import(sender, receiver, tmp_path)
    path = next((receiver.user / "bots").rglob("*.json"))
    mine = BotConfig.load(path)
    mine.description = "My notes"
    bot_roots.record_save(mine, path)
    mine.save(path)
    assert BotConfig.load(path).sent_sha256 is None, "a save that changes it drops the sent hash"
    later = _resave(sender, bot, name="Boss Farmer, faster")

    plan = _plan(receiver, _send_bot(sender, tmp_path, later, name="second"))

    assert plan.row(BOT, "bot-1").outcome is Outcome.CONFLICT


def test_the_next_version_of_a_copy_updates_that_copy(sender, receiver, tmp_path):
    bot, copy = _conflicted_first_import(sender, receiver, tmp_path)
    sender.subgraph(_defn("sg", "Their Loop, improved"))
    receiver.bot(_bot("bot-mine", "My Farmer", copy), "mine")
    before = load_subgraph(copy, receiver.shipped, receiver.user)

    plan, _ = _import(receiver, _send_bot(sender, tmp_path, bot, name="second"))

    row = plan.row(SUBGRAPH, "sg")
    assert (row.outcome, row.action, row.target_id) == (Outcome.NEWER, Action.UPDATE, copy)
    assert row.affected == ["Boss Farmer", "My Farmer"], "it names the bots on the copy"
    got = load_subgraph(copy, receiver.shipped, receiver.user)
    assert (got.id, got.name) == (copy, before.name), "the copy keeps its id and its name"
    assert got.states == load_subgraph("sg", sender.shipped, sender.user).states
    assert got.history == [content_sha256(before)]
    assert got.sent_sha256 == content_sha256(load_subgraph("sg", sender.shipped, sender.user))
    assert load_subgraph("sg", receiver.shipped, receiver.user).name == "My Loop", "the receiver's own is untouched"
    assert sorted(receiver.sources().subgraphs.ids()) == sorted(["sg", copy]), "no second copy"
    assert receiver.bots()["bot-1"].regions[0].source.subgraph_id == copy


def test_importing_the_updated_copys_file_again_changes_nothing(sender, receiver, tmp_path):
    bot, _copy = _conflicted_first_import(sender, receiver, tmp_path)
    sender.subgraph(_defn("sg", "Their Loop, improved"))
    path = _send_bot(sender, tmp_path, bot, name="second")
    _import(receiver, path)
    before = receiver.files()

    plan, _ = _import(receiver, path)

    assert not plan.writes_anything
    assert plan.row(SUBGRAPH, "sg").outcome is Outcome.COPIED
    assert receiver.files() == before


def test_a_copy_the_receiver_changed_is_never_updated(sender, receiver, tmp_path):
    bot, copy = _conflicted_first_import(sender, receiver, tmp_path)
    library = receiver.sources().subgraphs
    library.save(library.get(copy).model_copy(update={"description": "Mine now"}))
    sender.subgraph(_defn("sg", "Their Loop, improved"))

    plan = _plan(receiver, _send_bot(sender, tmp_path, bot, name="second"))

    row = plan.row(SUBGRAPH, "sg")
    assert (row.outcome, row.action) == (Outcome.CONFLICT, Action.COPY)
    assert row.target_id not in (copy, "sg")


def test_matching_treats_the_hash_an_item_was_sent_with_as_its_own():
    relinked = Held("relinked", ("older",), sent_sha256="as-sent")

    assert match("later", ["as-sent", "older"], relinked) is Outcome.NEWER
    assert match("relinked-again", [], relinked, sent="as-sent") is Outcome.SAME
    assert match("later", ["unrelated"], relinked) is Outcome.CONFLICT
    assert match("later", ["as-sent"], Held("edited", ("relinked",))) is Outcome.CONFLICT, "edited: no sent hash"


def test_a_copy_stands_for_its_item_only_while_unchanged():
    assert copy_outcome("v2", ["v1"], Held("copy", (), sent_sha256="v1")) is Outcome.NEWER
    assert copy_outcome("v2", ["v1"], Held("copy", (), sent_sha256="v2")) is Outcome.COPIED
    assert copy_outcome("v2", ["v1"], Held("copy", ("before",))) is None
    assert copy_ids(SUBGRAPH, "sg", "v2", ["v1", "v2"]) == [copy_id(SUBGRAPH, "sg", h) for h in ("v2", "v1")]
