"""Condition attributes: the parser accepts the documented ones and refuses the rest (#214).

`?(boss.hp > 0)` used to parse and read false on every turn, which looks exactly like a
condition that doesn't hold. Lives in wizsprinter (`combat_api.MEMBER_ATTRIBUTES`,
`combat_config_parser`, `sprinty_combat`), which has no suite of its own, so it is covered
here, as in `test_deck_remaining.py`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from lark import Lark
from lark.exceptions import VisitError
from wizwalker.extensions.wizsprinter.combat_backends.combat_api import (
    DECK_ATTRIBUTES,
    MEMBER_ATTRIBUTES,
    ComparisonOp,
    Condition,
    ConditionTarget,
    TargetType,
    hanging_attributes,
)
from wizwalker.extensions.wizsprinter.combat_backends.combat_config_parser import (
    TreeToConfig,
    get_sprinty_grammar,
)
from wizwalker.extensions.wizsprinter.sprinty_combat import SprintyCombat


@pytest.fixture(scope="module")
def parse():
    """Every priority's condition in a DSL, in order. One grammar per module."""
    parser = Lark(get_sprinty_grammar())

    def _parse(dsl: str):
        config = TreeToConfig().transform(parser.parse(dsl))
        return [priority.condition for line in config.infinite_rounds for priority in line.priorities]

    return _parse


def _refusal(parse, dsl: str) -> str:
    with pytest.raises(VisitError) as caught:
        parse(dsl)
    return str(caught.value.orig_exc)


# ---------------------------------------------------------------------------
# What parses
# ---------------------------------------------------------------------------


def test_every_documented_attribute_parses_on_a_member(parse):
    names = list(MEMBER_ATTRIBUTES) + list(hanging_attributes())
    conditions = parse("\n".join(f"?(enemy.{name} >= 1) pass" for name in names))

    assert [c.attribute for c in conditions] == names


@pytest.mark.parametrize("subject", ["self", "boss", "enemy(1)", "ally(last)", "any(enemies)", "avg(allies)"])
def test_a_percentage_parses_on_an_attribute_with_a_maximum(parse, subject):
    for name, max_name in MEMBER_ATTRIBUTES.items():
        if max_name:
            (condition,) = parse(f"?({subject}.{name} < 50%) pass")
            assert condition.is_percent


def test_deck_remaining_parses(parse):
    (condition,) = parse("?(deck.remaining >= 2) pass")

    assert condition.attribute in DECK_ATTRIBUTES


def test_only_health_and_mana_take_a_percentage():
    assert {name for name, max_name in MEMBER_ATTRIBUTES.items() if max_name} == {"health", "mana"}


# ---------------------------------------------------------------------------
# What is refused
# ---------------------------------------------------------------------------


def test_an_unknown_attribute_is_refused_and_named(parse):
    message = _refusal(parse, "?(boss.hp > 0) pass")

    assert "Unknown condition attribute 'hp'" in message
    assert "health" in message


def test_a_close_misspelling_names_the_attribute_meant(parse):
    assert _refusal(parse, "?(self.beneficial_aura_ == 0) pass").endswith("Did you mean 'beneficial_auras'?")


def test_an_attribute_in_an_and_clause_is_checked_too(parse):
    assert "'mna'" in _refusal(parse, "?(self.health > 0 && self.mna > 10) pass")


def test_an_attribute_in_a_later_priority_is_checked_too(parse):
    assert "'lvl'" in _refusal(parse, "any<damage> @ enemy | ?(enemy.lvl > 100) pass")


@pytest.mark.parametrize("name", ["power_pips", "level", "is_boss", "max_health", "charms"])
def test_a_percentage_on_an_attribute_with_no_maximum_is_refused(parse, name):
    assert f"'{name}' takes no %" in _refusal(parse, f"?(self.{name} < 50%) pass")


@pytest.mark.parametrize(
    "dsl, said",
    [
        ("?(deck.health > 2) pass", "deck.health reads nothing"),
        ("?(deck.remaining > 50%) pass", "deck.remaining takes no %"),
        ("?(self.remaining > 2) pass", "'remaining' is read on deck only"),
    ],
)
def test_an_attribute_on_the_wrong_subject_is_refused(parse, dsl, said):
    assert said in _refusal(parse, dsl)


# ---------------------------------------------------------------------------
# What the evaluator reads
# ---------------------------------------------------------------------------


def _value(v):
    async def _read():
        return v

    return _read


def _combat(member) -> SprintyCombat:
    combat = SprintyCombat.__new__(SprintyCombat)

    async def get_client_member():
        return member

    combat.get_client_member = get_client_member
    return combat


def _self(attribute: str, op: ComparisonOp, value: float, is_percent: bool = False) -> Condition:
    return Condition(ConditionTarget(TargetType.type_self), attribute, op, value, is_percent)


async def test_a_percentage_divides_by_the_attribute_the_table_names():
    combat = _combat(SimpleNamespace(health=_value(2000), max_health=_value(5000)))

    assert await combat.evaluate_condition(_self("health", ComparisonOp.lt, 50.0, True)) is True
    assert await combat.evaluate_condition(_self("health", ComparisonOp.lt, 30.0, True)) is False


async def test_a_member_method_outside_the_table_is_never_called():
    """A condition built in code bypasses the parser, so the evaluator keeps to the table
    as well: `name` is a CombatMember method, but not something a condition reads."""

    async def name():
        raise AssertionError("read a method outside MEMBER_ATTRIBUTES")

    combat = _combat(SimpleNamespace(name=name))

    assert await combat.evaluate_condition(_self("name", ComparisonOp.ne, 0.0)) is False


async def test_a_percentage_built_in_code_on_an_attribute_with_no_maximum_is_false():
    combat = _combat(SimpleNamespace(power_pips=_value(3)))

    assert await combat.evaluate_condition(_self("power_pips", ComparisonOp.lt, 50.0, True)) is False
