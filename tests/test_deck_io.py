"""Tests for bartlebot.deck_io.

The memory offsets these rely on are game-build specific (see the module
docstring); what is tested here is the logic layered on top of them -- the grid
arithmetic that replaced the unbounded page-discovery loop, and the toggle
planning that decides which item card slots to click.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bartlebot.bot.strategy_models import DeckData
from bartlebot.deck_io import (
    SLOTS_PER_PAGE,
    ItemSlot,
    active_item_counts,
    apply_deck,
    apply_substitutions,
    count_names,
    page_count,
    parse_tiered_name,
    plan_item_toggles,
    plan_normal_cards,
    read_deck,
    resolve_tier,
    write_deck,
)
from bartlebot.nodes.timing import DECK_APPLY_TIMEOUT


def slot(name: str, index: int, active: bool = False) -> ItemSlot:
    return ItemSlot(name=name, index=index, active=active)


def _async_returning(value):
    """Build a zero-arg coroutine function that yields ``value``.

    The memory objects this module reads are async all the way down, so the
    fakes have to be too.
    """

    async def _call():
        return value

    return _call


class FakeDeckBuilder:
    """Minimal DeckBuilder stand-in that records calls and rejects the unsafe ones."""

    #: Template names the fake account's spell list contains. Anything a deck
    #: asks for outside this is treated as not owned, exactly as in the game.
    spell_list: list[str] = ["Tempest", "Frenzy", "Epic", "Sharpened Blade", "Storm Lord"]

    def __init__(self):
        self.calls: list[str] = []
        self.added: dict[str, int] | None = None
        self.client = SimpleNamespace(root_window=object())

    async def refresh_deck_page(self) -> None:
        self.calls.append("refresh_deck_page")

    async def get_graphical_spell_cards(self) -> list:
        self.calls.append("get_graphical_spell_cards")

        def _spell(name: str):
            template = SimpleNamespace(name=_async_returning(name))
            return SimpleNamespace(spell_template=_async_returning(template))

        return [_spell(n) for n in self.spell_list]

    async def clear_deck(self) -> None:
        self.calls.append("clear_deck")

    async def view_deck_cards(self) -> None:
        self.calls.append("view_deck_cards")

    async def _add_all_normal_cards(self, section: dict[str, int]) -> None:
        self.calls.append("_add_all_normal_cards")
        self.added = dict(section)

    # -- forbidden: these hang or corrupt the spell list ----------------

    async def view_tc_cards(self) -> None:
        raise AssertionError("must not switch to the treasure card view")

    async def _scan_all_item_pages(self):
        raise AssertionError("must not scan item pages by clicking")

    async def clear_full_deck(self) -> None:
        raise AssertionError("must not call clear_full_deck")

    async def set_deck_preset(self, preset: dict) -> None:
        raise AssertionError("must not call set_deck_preset")

    async def get_deck_preset(self) -> dict:
        raise AssertionError("must not call get_deck_preset")


# ---------------------------------------------------------------------------
# Grid arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("index", "expected_page", "expected_slot"),
    [
        (0, 0, 0),  # first card, first page
        (7, 0, 7),  # end of row 1
        (8, 0, 8),  # start of row 2 -- the second enabled card in the probe run
        (15, 0, 15),  # last slot of page 0
        (16, 1, 0),  # first card of page 1
        (33, 2, 1),
    ],
)
def test_item_slot_grid_position(index: int, expected_page: int, expected_slot: int):
    s = slot("Card", index)
    assert s.page == expected_page
    assert s.slot_index == expected_slot


@pytest.mark.parametrize(
    ("total", "expected"),
    [(0, 1), (1, 1), (16, 1), (17, 2), (32, 2), (33, 3)],
)
def test_page_count(total: int, expected: int):
    """17 cards is the probe run's layout: one full page plus one."""
    assert page_count(total) == expected


def test_slots_per_page_is_the_observed_grid():
    assert SLOTS_PER_PAGE == 16


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def test_count_names_tallies_duplicates():
    assert count_names(["Fire", "Ice", "Fire"]) == {"Fire": 2, "Ice": 1}


def test_active_item_counts_ignores_inactive():
    slots = [
        slot("Sharpened Blade - Tear", 0, active=True),
        slot("Wand-Storm-180", 5),
        slot("Wand-Storm-180", 8, active=True),
    ]
    assert active_item_counts(slots) == {"Sharpened Blade - Tear": 1, "Wand-Storm-180": 1}


# ---------------------------------------------------------------------------
# Toggle planning
# ---------------------------------------------------------------------------


def test_plan_no_changes_when_already_correct():
    slots = [slot("Blade", 0, active=True), slot("Blade", 1)]
    assert plan_item_toggles(slots, {"Blade": 1}) == []


def test_plan_activates_missing_copies():
    slots = [slot("Blade", 0), slot("Blade", 1), slot("Blade", 2)]
    plan = plan_item_toggles(slots, {"Blade": 2})
    assert [s.index for s in plan] == [0, 1]


def test_plan_deactivates_extra_copies():
    slots = [slot("Blade", 0, active=True), slot("Blade", 1, active=True)]
    plan = plan_item_toggles(slots, {"Blade": 1})
    assert len(plan) == 1
    assert plan[0].active is True


def test_plan_deactivates_everything_not_wanted():
    slots = [slot("Blade", 0, active=True), slot("Trap", 1, active=True)]
    plan = plan_item_toggles(slots, {"Blade": 1})
    assert [s.index for s in plan] == [1]


def test_plan_skips_cards_the_account_lacks():
    """Gear changes, so a saved deck naming an absent card is expected."""
    slots = [slot("Blade", 0)]
    plan = plan_item_toggles(slots, {"Blade": 1, "Gone With The Gear": 2})
    assert [s.index for s in plan] == [0]


def test_plan_caps_at_available_copies():
    slots = [slot("Blade", 0), slot("Blade", 1)]
    plan = plan_item_toggles(slots, {"Blade": 5})
    assert [s.index for s in plan] == [0, 1]


def test_plan_is_ordered_by_index_for_a_forward_sweep():
    slots = [slot("A", 20), slot("B", 3), slot("C", 17)]
    plan = plan_item_toggles(slots, {"A": 1, "B": 1, "C": 1})
    assert [s.index for s in plan] == [3, 17, 20]
    assert [s.page for s in plan] == [0, 1, 1]


def test_plan_empty_desired_clears_all_active():
    slots = [slot("Blade", 0, active=True), slot("Trap", 9, active=True), slot("Shield", 4)]
    plan = plan_item_toggles(slots, {})
    assert [s.index for s in plan] == [0, 9]


# ---------------------------------------------------------------------------
# write_deck orchestration
# ---------------------------------------------------------------------------


async def test_write_deck_clears_then_adds_normal_cards(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()
    await write_deck(db, DeckData(normal={"Tempest": 4}))
    assert db.calls == [
        "refresh_deck_page",
        "view_deck_cards",
        "get_graphical_spell_cards",
        "clear_deck",
        "view_deck_cards",
        "_add_all_normal_cards",
    ]
    assert db.added == {"Tempest": 4}


async def test_write_deck_reads_the_spell_list_before_clearing(monkeypatch):
    """The ordering invariant: nothing that can fail on a card name runs after the clear.

    _add_all_normal_cards raises on the first name it cannot place, so planning
    afterwards left the wizard with an emptied deck.
    """
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()

    await write_deck(db, DeckData(normal={"Tempest": 4}))

    assert db.calls.index("get_graphical_spell_cards") < db.calls.index("clear_deck")


async def test_write_deck_skips_unowned_cards_instead_of_emptying_the_deck(monkeypatch):
    """One card the account lacks must cost that card, not the entire deck."""
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()

    await write_deck(db, DeckData(normal={"Tempest": 2, "Firestorm Blade": 4}))

    assert db.added == {"Tempest": 2}


async def test_write_deck_adds_nothing_when_no_card_is_owned(monkeypatch):
    """Degrades to "deck left cleared" rather than raising out of the apply."""
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()

    await write_deck(db, DeckData(normal={"Firestorm Blade": 4}))

    assert db.added is None
    assert "clear_deck" in db.calls


async def test_write_deck_empty_clears_without_adding(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()
    await write_deck(db, DeckData())
    assert db.calls == ["refresh_deck_page", "clear_deck", "view_deck_cards"]
    assert db.added is None


async def test_write_deck_ignores_legacy_treasure_cards(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    db = FakeDeckBuilder()
    await write_deck(db, DeckData(normal={"Tempest": 1}, tc={"Feint": 3}))
    assert db.added == {"Tempest": 1}


async def test_write_deck_skips_when_current_deck_already_matches(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _fake_item_slots)
    db = FakeDeckBuilder()
    deck = DeckData(normal={"Tempest": 2}, item={"Sharpened Blade - Tear": 1})
    await write_deck(db, deck)
    assert db.calls == ["refresh_deck_page"]
    assert db.added is None


async def test_write_deck_rewrites_when_item_cards_differ(monkeypatch):
    """Even with matching normal cards, a mismatched item loadout must still trigger a rewrite."""
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _fake_item_slots)
    db = FakeDeckBuilder()
    deck = DeckData(normal={"Tempest": 2}, item={"Sharpened Blade - Tear": 2})
    await write_deck(db, deck)
    assert "clear_deck" in db.calls
    assert db.added == {"Tempest": 2}


async def test_read_deck_never_reports_treasure_cards(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _fake_deck_names)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _fake_item_slots)
    db = FakeDeckBuilder()
    result = await read_deck(db)
    assert result["normal"] == {"Tempest": 2}
    assert result["item"] == {"Sharpened Blade - Tear": 1}
    assert result["tc"] == {}


async def test_write_deck_propagates_failures(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)

    class Failing(FakeDeckBuilder):
        async def clear_deck(self) -> None:
            raise RuntimeError("deck window not open")

    with pytest.raises(RuntimeError, match="deck window not open"):
        await write_deck(Failing(), DeckData(normal={"Tempest": 1}))


# ---------------------------------------------------------------------------
# apply_deck: the single entry point shared by the manual and pre-run paths
# ---------------------------------------------------------------------------


class _CtxDeckBuilder(FakeDeckBuilder):
    """FakeDeckBuilder usable as `async with DeckBuilder(client)`."""

    instances: list[_CtxDeckBuilder] = []

    def __init__(self, client=None):
        super().__init__()
        self.entered = False
        self.exited = False
        _CtxDeckBuilder.instances.append(self)

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *_exc):
        self.exited = True
        return False


@pytest.fixture
def fake_deck_builder(monkeypatch):
    """Patch the DeckBuilder that apply_deck imports lazily."""
    _CtxDeckBuilder.instances = []
    monkeypatch.setattr(
        "wizwalker.extensions.scripting.deck_builder.DeckBuilder",
        _CtxDeckBuilder,
    )
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)
    return _CtxDeckBuilder


async def test_apply_deck_opens_the_builder_and_writes(fake_deck_builder):
    await apply_deck(object(), DeckData(normal={"Tempest": 4}))

    db = fake_deck_builder.instances[0]
    assert db.entered and db.exited
    assert db.calls == [
        "refresh_deck_page",
        "view_deck_cards",
        "get_graphical_spell_cards",
        "clear_deck",
        "view_deck_cards",
        "_add_all_normal_cards",
    ]
    assert db.added == {"Tempest": 4}


async def test_apply_deck_never_uses_set_deck_preset(fake_deck_builder):
    """The pre-run path used to call DeckBuilder.set_deck_preset(), which reaches
    the treasure card view and can page forward forever. FakeDeckBuilder raises on
    it, so this passing means apply_deck goes through write_deck instead."""
    await apply_deck(object(), DeckData(normal={"Tempest": 1}, item={"Sharpened Blade - Tear": 1}))

    assert "set_deck_preset" not in fake_deck_builder.instances[0].calls


async def test_apply_deck_times_out(monkeypatch):
    """A stuck apply must not hang the run forever — this is what the pre-run path
    was missing entirely."""

    class Hanging(_CtxDeckBuilder):
        async def refresh_deck_page(self) -> None:
            await asyncio.sleep(60)

    _CtxDeckBuilder.instances = []
    monkeypatch.setattr("wizwalker.extensions.scripting.deck_builder.DeckBuilder", Hanging)

    with pytest.raises(TimeoutError):
        await apply_deck(object(), DeckData(normal={"Tempest": 1}), timeout=0.05)


async def test_apply_deck_closes_the_builder_on_timeout(monkeypatch):
    """The deck window must still be released when the apply is cancelled."""

    class Hanging(_CtxDeckBuilder):
        async def refresh_deck_page(self) -> None:
            await asyncio.sleep(60)

    _CtxDeckBuilder.instances = []
    monkeypatch.setattr("wizwalker.extensions.scripting.deck_builder.DeckBuilder", Hanging)

    with pytest.raises(TimeoutError):
        await apply_deck(object(), DeckData(normal={"Tempest": 1}), timeout=0.05)

    assert Hanging.instances[0].exited


async def test_apply_deck_propagates_write_failures(fake_deck_builder, monkeypatch):
    class Failing(_CtxDeckBuilder):
        async def clear_deck(self) -> None:
            raise RuntimeError("deck window not open")

    _CtxDeckBuilder.instances = []
    monkeypatch.setattr("wizwalker.extensions.scripting.deck_builder.DeckBuilder", Failing)

    with pytest.raises(RuntimeError, match="deck window not open"):
        await apply_deck(object(), DeckData(normal={"Tempest": 1}))

    assert Failing.instances[0].exited


async def test_apply_deck_defaults_to_the_shared_timeout(fake_deck_builder):
    """Both call sites rely on the default rather than passing their own."""
    import inspect

    default = inspect.signature(apply_deck).parameters["timeout"].default
    assert default == DECK_APPLY_TIMEOUT


# -- monkeypatch helpers ---------------------------------------------------


async def _fake_window(*_args, **_kwargs):
    return object()


async def _no_item_slots(*_args, **_kwargs):
    return []


async def _no_normal_cards(*_args, **_kwargs):
    return []


async def _fake_item_slots(*_args, **_kwargs):
    return [slot("Sharpened Blade - Tear", 0, active=True), slot("Wand-Storm-180", 5)]


async def _fake_deck_names(*_args, **_kwargs):
    return ["Tempest", "Tempest"]


# ---------------------------------------------------------------------------
# Tiered spell names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "base", "tier", "path"),
    [
        ("Storm Lord - T05 - A", "Storm Lord", 5, "A"),
        ("Storm Lord - T01 - B", "Storm Lord", 1, "B"),
        ("Tempest", "Tempest", None, None),
        ("Sharpened Blade", "Sharpened Blade", None, None),
    ],
)
def test_parse_tiered_name(name: str, base: str, tier: int | None, path: str | None):
    parsed = parse_tiered_name(name)
    assert (parsed.base, parsed.tier, parsed.path) == (base, tier, path)


def test_parse_tiered_name_treats_an_unparseable_tier_as_a_plain_name():
    """Guessing wrong would substitute an unrelated spell, so bail to 'plain'."""
    parsed = parse_tiered_name("Weird - Table - A")

    assert parsed.base == "Weird - Table - A"
    assert not parsed.is_tiered


def test_parse_tiered_name_handles_a_tier_with_no_path():
    parsed = parse_tiered_name("Something - T03")

    assert (parsed.base, parsed.tier, parsed.path) == ("Something", 3, None)


# ---------------------------------------------------------------------------
# resolve_tier — fall back to a lower owned tier, never across paths
# ---------------------------------------------------------------------------


def test_resolve_tier_prefers_an_exact_match():
    owned = ["Storm Lord - T03 - A", "Storm Lord - T05 - A"]

    assert resolve_tier("Storm Lord - T05 - A", owned) == "Storm Lord - T05 - A"


def test_resolve_tier_falls_back_to_the_highest_owned_tier_below():
    owned = ["Storm Lord - T01 - A", "Storm Lord - T03 - A", "Storm Lord - T02 - A"]

    assert resolve_tier("Storm Lord - T05 - A", owned) == "Storm Lord - T03 - A"


def test_resolve_tier_never_crosses_the_upgrade_path():
    """- A and - B are different spells, not stronger and weaker ones."""
    owned = ["Storm Lord - T05 - B"]

    assert resolve_tier("Storm Lord - T05 - A", owned) is None


def test_resolve_tier_does_not_upgrade():
    """The saved deck states what the strategy was tuned for, pips included."""
    owned = ["Storm Lord - T05 - A"]

    assert resolve_tier("Storm Lord - T03 - A", owned) is None


def test_resolve_tier_ignores_other_base_spells():
    owned = ["Glowbug Squall - T05 - A"]

    assert resolve_tier("Storm Lord - T05 - A", owned) is None


def test_resolve_tier_returns_none_for_a_plain_name_not_owned():
    assert resolve_tier("Tempest", ["Storm Lord - T05 - A"]) is None


def test_resolve_tier_with_nothing_owned():
    assert resolve_tier("Storm Lord - T05 - A", []) is None


# ---------------------------------------------------------------------------
# plan_normal_cards — filter before the deck is cleared
# ---------------------------------------------------------------------------


def test_plan_normal_keeps_cards_the_account_owns():
    plan = plan_normal_cards({"Tempest": 2, "Frenzy": 1}, ["Tempest", "Frenzy", "Epic"])

    assert plan.addable == {"Tempest": 2, "Frenzy": 1}
    assert plan.missing == []


def test_plan_normal_drops_cards_the_account_lacks():
    """The bug this exists for: one unowned card used to empty the whole deck."""
    plan = plan_normal_cards({"Tempest": 2, "Firestorm Blade": 4}, ["Tempest"])

    assert plan.addable == {"Tempest": 2}
    assert plan.missing == ["Firestorm Blade"]


def test_plan_normal_keeps_a_tiered_card_when_the_base_is_owned():
    """Which tiers exist is unreadable until the flyout opens, so keep and resolve later."""
    plan = plan_normal_cards({"Storm Lord - T05 - A": 1}, ["Storm Lord"])

    assert plan.addable == {"Storm Lord - T05 - A": 1}
    assert plan.missing == []


def test_plan_normal_drops_a_tiered_card_when_the_base_is_not_owned():
    plan = plan_normal_cards({"Storm Lord - T05 - A": 1}, ["Tempest"])

    assert plan.addable == {}
    assert plan.missing == ["Storm Lord - T05 - A"]


def test_plan_normal_skips_non_positive_counts():
    plan = plan_normal_cards({"Tempest": 0, "Frenzy": -1, "Epic": 2}, ["Tempest", "Frenzy", "Epic"])

    assert plan.addable == {"Epic": 2}
    assert plan.missing == []


def test_plan_normal_missing_is_sorted_for_a_stable_warning():
    plan = plan_normal_cards({"Zebra": 1, "Aardvark": 1}, [])

    assert plan.missing == ["Aardvark", "Zebra"]


def test_plan_normal_on_an_empty_deck():
    plan = plan_normal_cards({}, ["Tempest"])

    assert plan.addable == {}
    assert plan.missing == []


# ---------------------------------------------------------------------------
# apply_substitutions
# ---------------------------------------------------------------------------


def test_apply_substitutions_rewrites_names():
    result = apply_substitutions(
        {"Storm Lord - T05 - A": 1, "Tempest": 2},
        {"Storm Lord - T05 - A": "Storm Lord - T03 - A"},
    )

    assert result == {"Storm Lord - T03 - A": 1, "Tempest": 2}


def test_apply_substitutions_merges_counts_that_collapse_onto_one_variant():
    """T05 and T04 both falling back to T03 must want two T03s, not one."""
    result = apply_substitutions(
        {"Storm Lord - T05 - A": 1, "Storm Lord - T04 - A": 2},
        {"Storm Lord - T05 - A": "Storm Lord - T03 - A", "Storm Lord - T04 - A": "Storm Lord - T03 - A"},
    )

    assert result == {"Storm Lord - T03 - A": 3}


def test_apply_substitutions_is_a_no_op_without_substitutions():
    addable = {"Tempest": 2}

    assert apply_substitutions(addable, {}) == addable


# ---------------------------------------------------------------------------
# Tier substitution through write_deck
# ---------------------------------------------------------------------------


class TieredDeckBuilder(FakeDeckBuilder):
    """FakeDeckBuilder that also serves a tier flyout.

    ``owned_tiers`` is what the flyout shows once the base spell is clicked --
    the only place tier ownership is legible, which is why the substitution runs
    at apply time rather than being planned from the saved deck alone.
    """

    owned_tiers: list[str] = ["Storm Lord - T01 - A", "Storm Lord - T03 - A"]

    def __init__(self):
        super().__init__()
        self._deck_config_window = object()
        self.client = SimpleNamespace(
            root_window=object(),
            mouse_handler=_FakeMouse(),
        )

    def calcuate_position_of_card_in_page(self, cards: list, name: str) -> tuple[int, int]:
        return 0, cards.index(name) + 1

    async def calculate_card_position(self, card_index: int) -> tuple[int, int]:
        return (card_index, 0)

    async def _go_to_spell_page(self, page: int) -> None:
        self.calls.append(f"_go_to_spell_page:{page}")

    async def get_graphical_tiered_spell_cards(self) -> list:
        self.calls.append("get_graphical_tiered_spell_cards")

        def _spell(name: str):
            template = SimpleNamespace(name=_async_returning(name))
            return SimpleNamespace(spell_template=_async_returning(template))

        return [_spell(n) for n in self.owned_tiers]


class _FakeMouse:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def click(self, *_args, **_kwargs):
        return None

    async def click_window(self, *_args, **_kwargs):
        return None


def _tier_patches(monkeypatch):
    monkeypatch.setattr("bartlebot.deck_io._maybe_get_named_window", _fake_window)
    monkeypatch.setattr("bartlebot.deck_io.read_deck_card_names", _no_normal_cards)
    monkeypatch.setattr("bartlebot.deck_io.read_item_slots", _no_item_slots)


async def test_write_deck_substitutes_a_lower_owned_tier(monkeypatch):
    """A T05 deck on a T03 account gets T03, not an empty deck."""
    _tier_patches(monkeypatch)
    db = TieredDeckBuilder()

    await write_deck(db, DeckData(normal={"Storm Lord - T05 - A": 1, "Tempest": 3}))

    assert db.added == {"Storm Lord - T03 - A": 1, "Tempest": 3}


async def test_write_deck_keeps_an_exactly_owned_tier(monkeypatch):
    _tier_patches(monkeypatch)
    db = TieredDeckBuilder()
    db.owned_tiers = ["Storm Lord - T03 - A", "Storm Lord - T05 - A"]

    await write_deck(db, DeckData(normal={"Storm Lord - T05 - A": 1}))

    assert db.added == {"Storm Lord - T05 - A": 1}


async def test_write_deck_drops_a_tiered_card_with_no_usable_variant(monkeypatch):
    """Only the wrong-path variant is owned, so the card is skipped, not swapped."""
    _tier_patches(monkeypatch)
    db = TieredDeckBuilder()
    db.owned_tiers = ["Storm Lord - T05 - B"]

    await write_deck(db, DeckData(normal={"Storm Lord - T05 - A": 1, "Tempest": 2}))

    assert db.added == {"Tempest": 2}


async def test_write_deck_opens_the_flyout_once_per_base_spell(monkeypatch):
    """Two tiers of one spell must not cost two open/close cycles."""
    _tier_patches(monkeypatch)
    db = TieredDeckBuilder()
    db.owned_tiers = ["Storm Lord - T01 - A", "Storm Lord - T03 - A"]

    await write_deck(db, DeckData(normal={"Storm Lord - T03 - A": 1, "Storm Lord - T01 - A": 2}))

    assert db.calls.count("get_graphical_tiered_spell_cards") == 1


async def test_write_deck_merges_tiers_collapsing_onto_one_variant(monkeypatch):
    _tier_patches(monkeypatch)
    db = TieredDeckBuilder()
    db.owned_tiers = ["Storm Lord - T01 - A"]

    await write_deck(db, DeckData(normal={"Storm Lord - T05 - A": 1, "Storm Lord - T03 - A": 2}))

    assert db.added == {"Storm Lord - T01 - A": 3}


async def test_write_deck_survives_an_unreadable_flyout(monkeypatch):
    """A probe failure must not abort the apply -- the deck is cleared right after."""
    _tier_patches(monkeypatch)

    class BrokenFlyout(TieredDeckBuilder):
        async def get_graphical_tiered_spell_cards(self):
            raise RuntimeError("tier list window missing")

    db = BrokenFlyout()

    await write_deck(db, DeckData(normal={"Storm Lord - T05 - A": 1, "Tempest": 2}))

    # Falls back to the saved names untouched rather than losing the whole deck.
    assert db.added == {"Storm Lord - T05 - A": 1, "Tempest": 2}


async def test_write_deck_skips_the_flyout_when_no_tiered_cards_are_wanted(monkeypatch):
    _tier_patches(monkeypatch)
    db = TieredDeckBuilder()

    await write_deck(db, DeckData(normal={"Tempest": 4}))

    assert "get_graphical_tiered_spell_cards" not in db.calls
