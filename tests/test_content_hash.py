"""The one content hash, the histories every saved item keeps, and "Official updated" (#169).

Pure functions first (:mod:`bartlebot.shared.content_hash`), then each store that records
a history on save, then the surfaces that read it. Nothing here is a real character.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bartlebot import bot_roots
from bartlebot.bot.schema import BotConfig, StateModel
from bartlebot.bot.strategy_models import BotCombatStrategy
from bartlebot.paths import project_root
from bartlebot.shared.content_hash import (
    HISTORY_CAP,
    content_sha256,
    next_history,
    official_updated,
    record_save,
)
from bartlebot.shared.share_file import shareable_content
from bartlebot.shared.strategy_models import (
    DeckData,
    DeckPreset,
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
)
from bartlebot.store_roots import Origin, pill_text
from bartlebot.strategy_store import (
    StrategyKind,
    fingerprint,
    load_bot_strategy,
    load_duel_strategy,
    save_bot_strategy,
    save_duel_strategy,
    save_player_strategy,
)
from bartlebot.strategy_workset import StrategyWorkingSet
from bartlebot.subgraph_store import SubgraphLibrary, load_subgraph, save_subgraph
from tests.test_subgraph_store import _defn


def _bot(**kw) -> BotConfig:
    data = {
        "id": "bot-1",
        "name": "Farm",
        "initial": "a",
        "recovery_state": "a",
        "states": [StateModel(id="a", node_type="relog")],
        "transitions": [],
    }
    data.update(kw)
    return BotConfig(**data)


def _items():
    """One of every kind that keeps a history, each saved and loaded by its own model."""
    return [
        _bot(),
        _defn(),
        PlayerCombatStrategy(id="p1", name="Hitter", dsl="any<damage>"),
        DuelCombatStrategy(id="d1", name="Boss", player_assignments=[PlayerAssignment(players=[1], dsl="pass")]),
        DeckPreset(id="k1", name="Storm", deck=DeckData(normal={"Storm Lord": 2})),
        BotCombatStrategy(id="b1", name="Mine", bot_id="bot-1"),
    ]


# ---------------------------------------------------------------------------
# The hash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("item", _items(), ids=lambda i: type(i).__name__)
def test_hashing_saving_unchanged_and_hashing_again_gives_the_same_value(item, tmp_path):
    before = content_sha256(item)
    path = tmp_path / "item.json"
    item.save(path)
    assert content_sha256(type(item).load(path)) == before


@pytest.mark.parametrize("item", _items(), ids=lambda i: type(i).__name__)
def test_the_hash_is_of_content_not_bytes(item, tmp_path):
    """A file reformatted, keys reordered and a default spelled out, says the same thing."""
    path = tmp_path / "item.json"
    item.save(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    reordered = dict(reversed(list(data.items())))
    path.write_text(json.dumps(reordered, indent=7), encoding="utf-8")
    assert content_sha256(type(item).load(path)) == content_sha256(item)


@pytest.mark.parametrize("item", _items(), ids=lambda i: type(i).__name__)
def test_the_history_fields_are_left_out_of_the_hash(item):
    before = content_sha256(item)
    item.history = ["a" * 64]
    if "official_sha256" in type(item).model_fields:
        item.official_sha256 = "b" * 64
    item.sent_sha256 = "c" * 64
    assert content_sha256(item) == before
    item.name = "Something else"
    assert content_sha256(item) != before


@pytest.mark.parametrize("item", _items(), ids=lambda i: type(i).__name__)
def test_an_empty_history_stays_out_of_the_file(item, tmp_path):
    item.save(tmp_path / "item.json")
    data = json.loads((tmp_path / "item.json").read_text(encoding="utf-8"))
    assert "history" not in data and "official_sha256" not in data and "sent_sha256" not in data


def test_a_nested_field_named_history_is_content():
    bot = _bot(states=[StateModel(id="a", node_type="relog", params={"history": 1})])
    other = _bot(states=[StateModel(id="a", node_type="relog", params={"history": 2})])
    assert content_sha256(bot) != content_sha256(other)


# ---------------------------------------------------------------------------
# The history
# ---------------------------------------------------------------------------


def test_a_save_that_changes_nothing_adds_nothing():
    assert next_history(["x"], "same", "same") == ["x"]


def test_a_save_puts_what_it_replaced_first():
    assert next_history(["older"], "old", "new") == ["old", "older"]


def test_content_edited_back_moves_its_hash_to_the_front_once():
    assert next_history(["b", "a"], "a", "c") == ["a", "b"]


def test_the_history_is_capped_and_drops_the_oldest():
    full = [f"{i:064x}" for i in range(HISTORY_CAP)]
    after = next_history(full, "new-one", "current")
    assert len(after) == HISTORY_CAP
    assert after[0] == "new-one" and full[-1] not in after


def test_record_save_without_anything_replaced_keeps_the_items_history():
    item = _defn()
    item.history = ["kept"]
    record_save(item)
    assert item.history == ["kept"] and item.official_sha256 is None


@pytest.mark.parametrize("item", _items(), ids=lambda i: type(i).__name__)
def test_the_sent_hash_is_kept_by_a_save_that_changes_nothing_and_dropped_by_one_that_does(item):
    """It stands for the item as import wrote it, so an edit of the receiver's own ends it."""
    item.sent_sha256 = "c" * 64
    stored = item.model_copy(deep=True)

    record_save(item, stored=stored)
    assert item.sent_sha256 == "c" * 64

    item.name = "Edited"
    record_save(item, stored=stored)
    assert item.sent_sha256 is None


def test_a_sent_hash_never_travels_in_a_share_file():
    item = _defn()
    item.sent_sha256 = "c" * 64
    assert "sent_sha256" not in shareable_content(item)


def test_the_first_save_of_an_override_descends_from_the_official_one():
    official = _defn(name="Official")
    official.history = ["ancient"]
    mine = _defn(name="Mine")
    record_save(mine, shipped=official)
    assert mine.history == [content_sha256(official), "ancient"]
    assert mine.official_sha256 == content_sha256(official)


def test_a_later_save_of_an_override_keeps_its_official_base():
    official = _defn(name="Official")
    stored = _defn(name="Mine")
    record_save(stored, shipped=official)
    base = stored.official_sha256
    newer_official = _defn(name="Official, fixed")
    again = _defn(name="Mine, again")
    record_save(again, stored=stored, shipped=newer_official)
    assert again.official_sha256 == base, "the base is what the override was made from, not today's"
    assert again.history[0] == content_sha256(stored)


def test_with_one_root_nothing_is_official():
    item = _defn()
    item.official_sha256 = "stale"
    record_save(item, stored=_defn())
    assert item.official_sha256 is None


def test_official_updated_only_when_the_official_one_changed_since():
    official = _defn(name="Official")
    mine = _defn(name="Mine")
    record_save(mine, shipped=official)
    assert not official_updated(mine, official)
    assert official_updated(mine, _defn(name="Official, fixed"))
    assert not official_updated(mine, None)
    legacy = _defn(name="Mine")
    assert not official_updated(legacy, _defn(name="Official, fixed")), "no record, no claim"


# ---------------------------------------------------------------------------
# Bots
# ---------------------------------------------------------------------------


def test_every_shipped_bot_saved_unchanged_still_round_trips_byte_identical(tmp_path):
    """Save records history from the file it replaces; on an unchanged bot that is nothing."""
    churned = []
    for path in sorted((project_root() / "bots").rglob("*.json")):
        dest = tmp_path / "copy.json"
        dest.write_bytes(path.read_bytes())
        config = BotConfig.load(dest)
        bot_roots.record_save(config, dest)
        config.save(dest)
        if json.loads(dest.read_text(encoding="utf-8")) != json.loads(path.read_text(encoding="utf-8")):
            churned.append(path.name)
    assert not churned


def test_saving_a_changed_bot_records_what_it_replaced(tmp_path):
    path = tmp_path / "farm.json"
    first = _bot()
    first.save(path)
    edited = BotConfig.load(path)
    edited.name = "Farm II"
    bot_roots.record_save(edited, path)
    edited.save(path)
    assert BotConfig.load(path).history == [content_sha256(first)]
    assert edited.history == [content_sha256(first)], "the open bot says what was written"


def test_saving_over_another_bots_file_is_not_its_past(tmp_path):
    path = tmp_path / "farm.json"
    _bot(id="someone-else").save(path)
    mine = _bot(history=["own"])
    bot_roots.record_save(mine, path)
    assert mine.history == ["own"]


def test_make_my_own_copy_starts_a_fresh_history():
    from bartlebot.bot_fork import make_fork  # noqa: PLC0415

    official = _bot(history=["x"])
    fork = make_fork(official, "Mine")
    assert fork.history == []
    assert fork.forked_from.sha256 == content_sha256(official)


# ---------------------------------------------------------------------------
# Subgraphs
# ---------------------------------------------------------------------------


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "shipped", tmp_path / "user"


def test_a_subgraph_override_records_its_history_and_official_base(tmp_path):
    shipped, user = _roots(tmp_path)
    official = _defn(name="Official")
    official.save(shipped / "subgraphs" / "a.json")
    save_subgraph(_defn(name="Mine"), shipped, user)
    stored = load_subgraph("sg-a", shipped, user)
    assert stored.official_sha256 == content_sha256(official)
    assert stored.history == [content_sha256(official)]

    save_subgraph(_defn(name="Mine, again"), shipped, user)
    again = load_subgraph("sg-a", shipped, user)
    assert again.official_sha256 == content_sha256(official)
    assert again.history == [content_sha256(stored), content_sha256(official)]


def test_an_official_subgraph_changed_under_an_override_says_so(tmp_path):
    shipped, user = _roots(tmp_path)
    _defn(name="Official").save(shipped / "subgraphs" / "a.json")
    library = SubgraphLibrary(shipped, user)
    library.save(_defn(name="Mine"))
    assert library.origin("sg-a") is Origin.SHIPPED_OVERRIDE
    assert not library.official_updated("sg-a"), "the Official one did not change"

    _defn(name="Official", version=2).save(shipped / "subgraphs" / "a.json")
    library.reload()
    assert library.official_updated("sg-a")

    library.delete("sg-a")
    assert not library.official_updated("sg-a"), "reverted, so nothing is overridden"


def test_an_unedited_official_or_custom_subgraph_is_never_updated(tmp_path):
    shipped, user = _roots(tmp_path)
    _defn(name="Official").save(shipped / "subgraphs" / "a.json")
    library = SubgraphLibrary(shipped, user)
    library.save(_defn("sg-b", name="Custom"))
    assert not library.official_updated("sg-a")
    assert not library.official_updated("sg-b")
    assert not library.official_updated("nope")


def test_in_developer_mode_a_subgraph_records_no_history_and_overrides_nothing(tmp_path):
    """The file is a shipped one: a history on it would change with every save (#182)."""
    save_subgraph(_defn(name="One"), tmp_path)
    save_subgraph(_defn(name="Two"), tmp_path)
    stored = load_subgraph("sg-a", tmp_path)
    assert stored.history == []
    assert stored.official_sha256 is None


def test_a_custom_subgraph_keeps_a_history(tmp_path):
    shipped, user = _roots(tmp_path)
    save_subgraph(_defn(name="One"), shipped, user)
    save_subgraph(_defn(name="Two"), shipped, user)
    assert load_subgraph("sg-a", shipped, user).history == [content_sha256(_defn(name="One"))]


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def _duel(name: str = "Boss") -> DuelCombatStrategy:
    return DuelCombatStrategy(id="d1", name=name, player_assignments=[PlayerAssignment(players=[1], dsl="pass")])


def test_a_strategy_override_records_its_history_and_official_base(tmp_path):
    shipped, user = _roots(tmp_path)
    official = _duel("Official")
    save_duel_strategy(official, shipped)
    save_duel_strategy(_duel("Mine"), shipped, user)
    stored = load_duel_strategy("d1", shipped, user)
    assert stored.official_sha256 == content_sha256(official)
    assert stored.history == [content_sha256(official)]


def test_a_custom_strategy_keeps_a_history_and_no_base(tmp_path):
    shipped, user = _roots(tmp_path)
    first = PlayerCombatStrategy(id="p1", name="Hitter", dsl="any<damage>")
    fp = save_player_strategy(first, shipped, user)
    edited = PlayerCombatStrategy(id="p1", name="Hitter", dsl="pass")
    save_player_strategy(edited, shipped, user, expected=fp)
    assert edited.history == [content_sha256(first)] and edited.official_sha256 is None


def test_a_bot_combat_strategy_keeps_a_history(tmp_path):
    shipped, user = _roots(tmp_path)
    first = BotCombatStrategy(id="b1", name="Mine", bot_id="bot-1")
    save_bot_strategy(first, shipped, user)
    edited = BotCombatStrategy(id="b1", name="Mine", bot_id="bot-1", description="Fire first")
    save_bot_strategy(edited, shipped, user)
    assert load_bot_strategy("b1", shipped, user).history == [content_sha256(first)]
    save_bot_strategy(edited, shipped, user)
    assert load_bot_strategy("b1", shipped, user).history == [content_sha256(first)], "unchanged adds nothing"


def test_in_developer_mode_a_strategy_records_no_history(tmp_path):
    first = PlayerCombatStrategy(id="p1", name="Hitter", dsl="any<damage>")
    fp = save_player_strategy(first, tmp_path)
    edited = PlayerCombatStrategy(id="p1", name="Hitter", dsl="pass")
    save_player_strategy(edited, tmp_path, expected=fp)
    assert edited.history == []


def _reship(duel: DuelCombatStrategy, shipped: Path) -> None:
    """Replace the shipped duel, as an app update would."""
    save_duel_strategy(duel, shipped, expected=fingerprint(StrategyKind.DUEL, duel.id, shipped))


def _workset(shipped: Path, user: Path) -> StrategyWorkingSet:
    return StrategyWorkingSet.from_store(root=shipped, user_root=user)


def test_a_strategy_override_whose_official_one_changed_says_so(tmp_path):
    shipped, user = _roots(tmp_path)
    save_duel_strategy(_duel("Official"), shipped)
    save_duel_strategy(_duel("Mine"), shipped, user)
    assert not _workset(shipped, user).official_updated(StrategyKind.DUEL, "d1")

    _reship(_duel("Official, fixed"), shipped)
    ws = _workset(shipped, user)
    assert ws.status(StrategyKind.DUEL, "d1") is Origin.SHIPPED_OVERRIDE
    assert ws.official_updated(StrategyKind.DUEL, "d1")
    ws.revert(StrategyKind.DUEL, "d1")
    assert not ws.official_updated(StrategyKind.DUEL, "d1"), "reverted to the new Official one"


def test_an_override_edited_back_to_the_official_content_reads_as_official(tmp_path):
    """Its history and base differ from the shipped file's; its content does not."""
    shipped, user = _roots(tmp_path)
    save_duel_strategy(_duel("Official"), shipped)
    save_duel_strategy(_duel("Mine"), shipped, user)
    ws = _workset(shipped, user)
    ws.get(StrategyKind.DUEL, "d1").name = "Official"
    assert ws.status(StrategyKind.DUEL, "d1") is Origin.SHIPPED
    ws.commit()
    assert ws.origin(StrategyKind.DUEL, "d1") is Origin.SHIPPED, "Save removed the user copy"


# ---------------------------------------------------------------------------
# The pill
# ---------------------------------------------------------------------------


def test_the_pill_adds_official_updated_to_a_modified_official_item_only():
    assert pill_text(Origin.SHIPPED_OVERRIDE, official_updated=True) == "Official (modified) · Official updated"
    assert pill_text(Origin.SHIPPED_OVERRIDE) == "Official (modified)"
    assert pill_text(Origin.SHIPPED, official_updated=True) == "Official"
    assert pill_text(Origin.USER, official_updated=True) == "Custom"


def test_the_source_pill_widget_says_it_and_explains(qapp):
    from bartlebot.gui.widgets.source_pill import OFFICIAL_UPDATED_TIP, source_pill  # noqa: PLC0415

    pill = source_pill(Origin.SHIPPED_OVERRIDE, official_updated=True)
    assert pill.text() == "Official (modified) · Official updated"
    assert pill.toolTip() == OFFICIAL_UPDATED_TIP
    assert source_pill(Origin.SHIPPED_OVERRIDE).text() == "Official (modified)"


def test_the_strategy_dialog_card_and_note_say_official_updated(qapp, tmp_path):
    from bartlebot.gui.combat_strategies.dialog import OFFICIAL_UPDATED_NOTE, CombatStrategyDialog  # noqa: PLC0415

    shipped, user = _roots(tmp_path)
    save_duel_strategy(_duel("Official"), shipped)
    save_duel_strategy(_duel("Mine"), shipped, user)
    _reship(_duel("Official, fixed"), shipped)
    dlg = CombatStrategyDialog(_workset(shipped, user), StrategyKind.DUEL, current_id="d1")
    page = dlg._pages[StrategyKind.DUEL]
    assert page.card_for("d1").source_text == "Official (modified) · Official updated"
    assert page.about.origin_text == OFFICIAL_UPDATED_NOTE
    assert page._del_btn.isEnabled(), "Revert sits beside the note"
    dlg.deleteLater()


def test_a_duplicated_strategy_starts_fresh(tmp_path):
    shipped, user = _roots(tmp_path)
    save_duel_strategy(_duel("Official"), shipped)
    save_duel_strategy(_duel("Mine"), shipped, user)
    ws = _workset(shipped, user)
    copy = ws.duplicate(StrategyKind.DUEL, "d1")
    assert copy.history == [] and copy.official_sha256 is None


def test_a_duplicated_bot_combat_strategy_starts_fresh():
    from bartlebot.combat_profile import duplicate_profile  # noqa: PLC0415

    bcs = BotCombatStrategy(id="b1", name="Mine", bot_id="bot-1", history=["x"])
    assert duplicate_profile(bcs, "Copy").history == []
